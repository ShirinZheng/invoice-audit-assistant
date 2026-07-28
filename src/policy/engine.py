"""Deterministic rule-based policy decision engine (no LLM calls)."""

from __future__ import annotations

import re
from typing import Any

from src.schemas import PolicyDecision

# Requests that must always be restricted, regardless of invoice content.
# The patterns intentionally describe an action and its target together to
# avoid blocking benign questions such as "How does the policy prevent fraud?".
SENSITIVE_PATTERNS = (
    r"\b(?:create|make|generate)\b.{0,40}\b(?:invalid|fake|forged|fabricated)\b.{0,20}\binvoice\b",
    r"\b(?:fabricate|forge|fake|counterfeit)\b.{0,40}\b(?:invoice|receipt|contract|signature)\b",
    r"\b(?:alter|change|edit|inflate|increase)\b.{0,40}\b(?:invoice|receipt|amount|total|date)\b",
    r"\b(?:hide|delete|remove|tamper)\b.{0,40}\b(?:audit|trail|log|record|expense)\b",
    r"\bsplit\b.{0,30}\b(?:invoice|expense)\b.{0,50}\b(?:avoid|bypass|evade)\b",
    r"\b(?:avoid|bypass|skip|evade)\b.{0,40}\b(?:approval|review|threshold|workflow)\b",
    r"\b(?:personal expense|private expense)\b.{0,40}\b(?:business|reimburse|approve)\b",
    r"\b(?:approve|reimburse)\b.{0,40}\b(?:without (?:a )?receipt|twice|two times|again)\b",
    r"\b(?:reimburse|submit)\b.{0,40}\b(?:twice|two times|again|duplicate)\b",
    r"\bbackdate\b.{0,40}\b(?:invoice|receipt|request)\b",
    r"(?:伪造|虚开|假造).{0,20}(?:发票|收据|合同|签名)",
    r"(?:篡改|修改|抬高|虚增).{0,20}(?:发票|金额|总额|日期)",
    r"(?:隐藏|删除|销毁|篡改).{0,20}(?:审计|日志|记录|费用)",
    r"(?:拆分|拆票).{0,30}(?:绕过|规避|逃避).{0,20}(?:审批|限额|门槛)",
    r"(?:绕过|跳过|规避).{0,20}(?:审批|审核|流程|限额)",
    r"(?:个人|私人).{0,10}(?:消费|费用).{0,20}(?:公司|业务|报销)",
    r"(?:重复|二次|两次).{0,10}(?:报销|提交)",
)

# Per-category single-invoice amount limits.
CATEGORY_LIMITS = {
    "meal": 500.0,
    "travel": 5000.0,
    "office_supplies": 2000.0,
    "vat_general": 10000.0,
    "vat_special": 50000.0,
}

ESCALATION_THRESHOLD = 10000.0
REQUIRED_FIELDS = ("invoice_id", "seller_name", "total_amount", "invoice_date")


def is_restricted_request(query: str) -> bool:
    lowered = query.lower()
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in SENSITIVE_PATTERNS)


def decide(
    template_type: str,
    fields: dict[str, Any],
    query: str,
    tool_results: dict[str, dict[str, Any]] | None = None,
) -> PolicyDecision:
    """Apply audit policy rules and return risk level + action."""
    tool_results = tool_results or {}
    reasons: list[str] = []

    if is_restricted_request(query):
        return PolicyDecision(
            risk_level="critical",
            action="restricted_operation",
            reasons=["The request asks for a prohibited or non_compliant operation."],
        )

    missing = [f for f in REQUIRED_FIELDS if f not in fields]
    if missing:
        reasons.append(f"Missing required fields: {', '.join(missing)}.")
        return PolicyDecision(risk_level="medium", action="require_more_info", reasons=reasons)

    total = fields.get("total_amount")
    total_value = float(total) if isinstance(total, (int, float)) else None
    if total_value is None:
        reasons.append("total_amount is not a valid number.")
        return PolicyDecision(risk_level="medium", action="require_more_info", reasons=reasons)
    if total_value <= 0:
        return PolicyDecision(
            risk_level="high",
            action="reject",
            reasons=["Invoice total must be greater than zero."],
        )

    # Cross-check amount + tax = total when all three are present.
    amount = fields.get("amount")
    tax = fields.get("tax_amount")
    if isinstance(amount, (int, float)) and isinstance(tax, (int, float)):
        mismatch = abs((amount + tax) - total_value)
        if mismatch > 0.01:
            reasons.append(
                f"Amount ({amount}) plus tax ({tax}) does not equal total ({total_value})."
            )
            return PolicyDecision(
                risk_level="high",
                action="reject",
                reasons=reasons,
            )

    # Tool-result driven rules.
    vendor = tool_results.get("query_vendor_status", {})
    if vendor.get("status") == "blocked":
        reasons.append("Vendor is blocked in the internal compliance system.")
        return PolicyDecision(risk_level="critical", action="reject", reasons=reasons)
    if vendor.get("status") == "under_review":
        reasons.append("Vendor compliance status is under review.")
        return PolicyDecision(risk_level="high", action="escalate_approval", reasons=reasons)

    duplicate = tool_results.get("check_duplicate_invoice", {})
    if duplicate.get("is_duplicate"):
        reasons.append("Invoice appears to be a duplicate submission.")
        return PolicyDecision(risk_level="high", action="reject", reasons=reasons)

    budget = tool_results.get("check_project_budget", {})
    if (
        budget
        and budget.get("remaining_budget") is not None
        and total_value > float(budget["remaining_budget"])
    ):
        reasons.append("Invoice total exceeds the remaining project budget.")
        return PolicyDecision(risk_level="high", action="escalate_approval", reasons=reasons)

    # Amount-based rules.
    limit = CATEGORY_LIMITS.get(template_type)
    if limit is not None and total_value > limit:
        reasons.append(
            f"Total {total_value} exceeds the {template_type} category limit of {limit}."
        )
        return PolicyDecision(risk_level="high", action="escalate_approval", reasons=reasons)

    if total_value > ESCALATION_THRESHOLD:
        reasons.append(
            f"Total {total_value} exceeds the escalation threshold of {ESCALATION_THRESHOLD}."
        )
        return PolicyDecision(risk_level="high", action="escalate_approval", reasons=reasons)

    if template_type == "unknown":
        reasons.append("Invoice template could not be classified.")
        return PolicyDecision(risk_level="medium", action="require_more_info", reasons=reasons)

    reasons.append("All deterministic policy checks passed.")
    return PolicyDecision(risk_level="low", action="approve_initial_review", reasons=reasons)
