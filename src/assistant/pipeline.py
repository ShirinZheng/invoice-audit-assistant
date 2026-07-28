"""Invoice audit orchestration.

Classification, extraction, policy decisions, enterprise checks, tool writes,
and citation validation are deterministic. DeepSeek is optional and may improve
the prose, but it can never override the audited decision.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from src.config import get_settings
from src.invoices.classifier import classify_template
from src.invoices.extractor import extract_fields
from src.llm.deepseek_provider import DeepSeekProvider, LLMResponseError
from src.policy.engine import decide, is_restricted_request
from src.rag.retriever import Chunk, Retriever
from src.schemas import AssistantResponse, Citation, ToolCallRecord
from src.tools.enterprise_tools import execute_tool

SYSTEM_PROMPT = (
    "You are an enterprise invoice audit assistant. Explain deterministic audit "
    "results clearly and cite only the supplied company-policy passages. Never "
    "assist with invoice fabrication, record tampering, approval bypass, duplicate "
    "reimbursement, or other fraudulent activity. The deterministic policy result "
    "is authoritative and must not be changed."
)


class StructuredResponseProvider(Protocol):
    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: type,
    ) -> dict[str, Any]: ...


def _record_tool(
    records: list[ToolCallRecord],
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = execute_tool(name, arguments)
    records.append(ToolCallRecord(tool_name=name, arguments=arguments, result=result))
    return result


def _run_deterministic_tools(
    invoice_id: str,
    fields: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[ToolCallRecord]]:
    """Run read-only compliance checks before the policy decision."""

    results: dict[str, dict[str, Any]] = {}
    records: list[ToolCallRecord] = []

    calls: list[tuple[str, dict[str, Any]]] = []
    if fields.get("seller_tax_id"):
        calls.append(("query_vendor_status", {"seller_tax_id": fields["seller_tax_id"]}))
    if fields.get("project_id"):
        calls.append(("check_project_budget", {"project_id": fields["project_id"]}))
    calls.append(
        (
            "check_duplicate_invoice",
            {
                "invoice_id": invoice_id,
                "seller_tax_id": str(fields.get("seller_tax_id", "")),
                "total_amount": float(fields.get("total_amount", 0) or 0),
            },
        )
    )

    for name, arguments in calls:
        results[name] = _record_tool(records, name, arguments)
    return results, records


def _run_decision_tools(
    invoice_id: str,
    action: str,
    reasons: list[str],
    records: list[ToolCallRecord],
) -> None:
    """Execute policy-mandated writes, never model-selected writes."""

    reason = " ".join(reasons)
    if action == "escalate_approval":
        _record_tool(
            records,
            "create_approval_ticket",
            {"invoice_id": invoice_id, "reason": reason, "level": "manager"},
        )
    _record_tool(
        records,
        "write_audit_log",
        {"invoice_id": invoice_id, "action": action, "detail": reason},
    )


def _quote_from_chunk(chunk: Chunk) -> str:
    lines = [line.strip() for line in chunk.text.splitlines() if line.strip()]
    prose = next((line for line in lines if not line.startswith("#")), chunk.text)
    return prose[:320]


def _default_citations(chunks: list[Chunk], limit: int = 2) -> list[Citation]:
    return [
        Citation(
            source_id=chunk.source_id,
            file=chunk.file,
            quote=_quote_from_chunk(chunk),
        )
        for chunk in chunks[:limit]
    ]


def _contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def _reason_in_chinese(reason: str) -> str:
    replacements = (
        ("Missing required fields:", "缺少必填字段："),
        ("Invoice total must be greater than zero.", "发票总额必须大于零。"),
        ("total_amount is not a valid number.", "价税合计不是有效数字。"),
        ("Amount (", "金额（"),
        (" plus tax (", "）加税额（"),
        (" does not equal total (", "）不等于价税合计（"),
        ("Vendor is blocked in the internal compliance system.", "供应商处于黑名单。"),
        ("Vendor compliance status is under review.", "供应商合规状态正在审核中。"),
        ("Invoice appears to be a duplicate submission.", "发票疑似重复提交。"),
        ("Invoice total exceeds the remaining project budget.", "发票总额超过项目剩余预算。"),
        ("Invoice template could not be classified.", "无法识别发票类型。"),
        ("All deterministic policy checks passed.", "所有确定性政策检查均已通过。"),
        (
            "The request asks for a prohibited or non_compliant operation.",
            "请求涉及被禁止的违规操作。",
        ),
    )
    translated = reason
    for source, target in replacements:
        translated = translated.replace(source, target)
    if translated.startswith("金额（"):
        translated = translated.replace(").", "）。")
    translated = re.sub(
        r"Total ([\d.]+) exceeds the ([a-z_]+) category limit of ([\d.]+)\.",
        r"发票总额 \1 超过 \2 类别限额 \3。",
        translated,
    )
    return re.sub(
        r"Total ([\d.]+) exceeds the escalation threshold of ([\d.]+)\.",
        r"发票总额 \1 超过升级审批阈值 \2。",
        translated,
    )


def _retrieval_query(
    query: str,
    template_type: str,
    action: str,
    reasons: list[str],
) -> str:
    if not _contains_chinese(query):
        return f"{query} {template_type} {action} {' '.join(reasons)} invoice reimbursement policy"
    hint = {
        "approve_initial_review": "基本要求 通过初审",
        "require_more_info": "缺少必填字段 补充资料",
        "escalate_approval": "限额 项目预算 升级审批",
        "reject": "金额不一致 黑名单供应商 重复报销 拒绝",
        "restricted_operation": "伪造 篡改 绕过审批 违规请求",
    }[action]
    return f"{query} 发票 报销 {template_type} {hint}"


def _offline_response(
    *,
    invoice_id: str,
    template_type: str,
    fields: dict[str, Any],
    risk_level: str,
    action: str,
    reasons: list[str],
    records: list[ToolCallRecord],
    chunks: list[Chunk],
    query: str,
    warnings: list[str] | None = None,
) -> AssistantResponse:
    chinese = _contains_chinese(query)
    if chinese:
        risk_text = {
            "low": "低",
            "medium": "中",
            "high": "高",
            "critical": "严重",
        }[risk_level]
        translated_reasons = [_reason_in_chinese(reason) for reason in reasons]
        action_text = {
            "approve_initial_review": "通过初审",
            "require_more_info": "需要补充资料",
            "escalate_approval": "需要升级审批",
            "reject": "拒绝报销",
            "restricted_operation": "已拒绝违规请求",
        }[action]
        answer = (
            f"审计结论：{action_text}。风险等级：{risk_text}。"
            f"主要依据：{'；'.join(translated_reasons)}"
        )
        rationale = "审计结论由确定性规则、供应商/预算/重复报销检查共同生成。"
    else:
        action_text = {
            "approve_initial_review": "approved for initial review",
            "require_more_info": "more information is required",
            "escalate_approval": "escalated approval is required",
            "reject": "reimbursement is rejected",
            "restricted_operation": "the non-compliant request was refused",
        }[action]
        answer = (
            f"Audit result: {action_text}. Risk level: {risk_level}. Reason: {' '.join(reasons)}"
        )
        rationale = (
            "The result was produced by deterministic policy rules and the "
            "vendor, budget, and duplicate-submission checks."
        )

    return AssistantResponse(
        invoice_id=invoice_id,
        template_type=template_type,
        extracted_fields=fields,
        risk_level=risk_level,
        action=action,
        tool_calls=records,
        answer=answer,
        citations=_default_citations(chunks),
        rationale=rationale,
        errors=[],
        warnings=warnings or [],
        processing_mode="offline",
    )


def run_assistant(
    invoice_text: str,
    query: str,
    provider: StructuredResponseProvider | None = None,
    retriever: Retriever | None = None,
    use_llm: bool | None = None,
    use_llm_tool_planning: bool | None = None,
) -> AssistantResponse:
    """Audit one OCR-normalized invoice.

    ``use_llm_tool_planning`` is accepted for backward compatibility but no
    longer used: state-changing tools are selected by policy, never by an LLM.
    """

    del use_llm_tool_planning
    settings = get_settings()
    retriever = retriever or Retriever()
    use_llm = (
        provider is not None or settings.audit_mode == "deepseek" if use_llm is None else use_llm
    )

    template_type = classify_template(invoice_text)
    fields = extract_fields(invoice_text)
    invoice_id = str(fields.get("invoice_id", "UNKNOWN"))
    restricted = is_restricted_request(query)

    tool_results: dict[str, dict[str, Any]] = {}
    tool_records: list[ToolCallRecord] = []
    if not restricted:
        tool_results, tool_records = _run_deterministic_tools(invoice_id, fields)

    decision = decide(template_type, fields, query, tool_results)
    if not restricted:
        _run_decision_tools(
            invoice_id,
            decision.action,
            decision.reasons,
            tool_records,
        )

    chunks = retriever.retrieve(
        _retrieval_query(
            query,
            template_type,
            decision.action,
            decision.reasons,
        )
    )

    if not use_llm:
        return _offline_response(
            invoice_id=invoice_id,
            template_type=template_type,
            fields=fields,
            risk_level=decision.risk_level,
            action=decision.action,
            reasons=decision.reasons,
            records=tool_records,
            chunks=chunks,
            query=query,
        )

    provider = provider or DeepSeekProvider.from_settings()
    kb_context = "\n\n".join(
        f"[source_id={chunk.source_id}] [file={chunk.file}]\n{chunk.text}" for chunk in chunks
    )
    context = (
        f"Invoice text:\n{invoice_text}\n\n"
        f"User query: {query}\n\n"
        "Authoritative deterministic analysis:\n"
        f"- invoice_id: {invoice_id}\n"
        f"- template_type: {template_type}\n"
        f"- extracted_fields: {json.dumps(fields, ensure_ascii=False)}\n"
        f"- risk_level: {decision.risk_level}\n"
        f"- action: {decision.action}\n"
        f"- reasons: {json.dumps(decision.reasons, ensure_ascii=False)}\n"
        f"- tool trace: {json.dumps([r.model_dump() for r in tool_records], ensure_ascii=False)}\n\n"
        f"Policy passages:\n{kb_context}"
    )
    user_prompt = (
        f"{context}\n\n"
        "Return the final response using the required JSON schema. Explain the "
        "authoritative decision in the user's language. Use only verbatim policy "
        "quotes from the supplied passages. Set errors and warnings to empty "
        "arrays and processing_mode to 'deepseek'."
    )

    try:
        raw = provider.generate_json(SYSTEM_PROMPT, user_prompt, AssistantResponse)
        response = AssistantResponse.model_validate(raw)
    except LLMResponseError as exc:
        return _offline_response(
            invoice_id=invoice_id,
            template_type=template_type,
            fields=fields,
            risk_level=decision.risk_level,
            action=decision.action,
            reasons=decision.reasons,
            records=tool_records,
            chunks=chunks,
            query=query,
            warnings=[f"DeepSeek generation failed; deterministic offline explanation used: {exc}"],
        )

    response.invoice_id = invoice_id
    response.template_type = template_type
    response.extracted_fields = fields
    response.risk_level = decision.risk_level
    response.action = decision.action
    response.tool_calls = tool_records
    response.citations = _validate_citations(response.citations, chunks)
    response.errors = []
    response.processing_mode = "deepseek"
    return response


def _validate_citations(
    citations: list[Citation],
    chunks: list[Chunk],
) -> list[Citation]:
    """Keep only exact quotes from the cited source chunk."""

    by_id = {chunk.source_id: chunk for chunk in chunks}
    valid: list[Citation] = []
    for citation in citations:
        chunk = by_id.get(citation.source_id)
        if chunk and citation.quote and citation.quote in chunk.text:
            valid.append(
                Citation(
                    source_id=chunk.source_id,
                    file=chunk.file,
                    quote=citation.quote,
                )
            )
    return valid
