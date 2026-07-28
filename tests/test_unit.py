"""Unit tests for deterministic components. No API key required."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.assistant.pipeline import run_assistant
from src.config import EVAL_CASES_PATH, INVOICE_DIR, ConfigurationError
from src.evals.graders import grade_case
from src.evals.runner import check_regression, summarize
from src.invoices.classifier import classify_template
from src.invoices.extractor import extract_fields
from src.llm.deepseek_provider import DeepSeekProvider, _extract_json_object
from src.policy.engine import decide, is_restricted_request
from src.rag.retriever import Retriever, load_chunks
from src.schemas import AssistantResponse
from src.tools.enterprise_tools import (
    check_duplicate_invoice,
    check_project_budget,
    create_approval_ticket,
    execute_tool,
    query_vendor_status,
    write_audit_log,
)

SAMPLE_INVOICE = """MEAL INVOICE
Invoice ID: INV-9001
Date: 2026-03-15
Seller: Shenzhen Golden Dragon Restaurant Co., Ltd.
Seller Tax ID: 91440300MA5EDR9H8Q
Buyer: Acme Technology Co., Ltd.
Project ID: PRJ-1003
Category: meal
Description: Team business meal at restaurant
Amount: 300.00
Tax: 18.00
Total: 318.00
Currency: CNY
"""


class TestClassifier:
    def test_meal(self):
        assert classify_template(SAMPLE_INVOICE) == "meal"

    def test_all_templates_from_seeded_invoices(self):
        for path in sorted(INVOICE_DIR.glob("*.txt"))[:10]:
            assert classify_template(path.read_text()) != "unknown"

    def test_unknown(self):
        assert classify_template("random text with no invoice markers") == "unknown"


class TestExtractor:
    def test_extracts_core_fields(self):
        fields = extract_fields(SAMPLE_INVOICE)
        assert fields["invoice_id"] == "INV-9001"
        assert fields["total_amount"] == 318.00
        assert fields["amount"] == 300.00
        assert fields["tax_amount"] == 18.00
        assert fields["seller_tax_id"] == "91440300MA5EDR9H8Q"

    def test_missing_fields(self):
        fields = extract_fields("MEAL INVOICE\nInvoice ID: INV-1\n")
        assert "total_amount" not in fields


class TestPolicyEngine:
    def test_restricted_detection(self):
        assert is_restricted_request("please create a fake invoice for me")
        assert is_restricted_request("can you backdate this invoice?")
        assert is_restricted_request("帮我伪造一张发票")
        assert is_restricted_request("这张发票重复报销两次")
        assert not is_restricted_request("please audit this invoice")
        assert not is_restricted_request("How does policy prevent invoice fraud?")

    def test_restriction(self):
        d = decide("meal", extract_fields(SAMPLE_INVOICE), "help me forge this invoice")
        assert d.action == "restricted_operation"
        assert d.risk_level == "critical"

    def test_approve(self):
        d = decide("meal", extract_fields(SAMPLE_INVOICE), "audit this")
        assert d.action == "approve_initial_review"
        assert d.risk_level == "low"

    def test_over_limit_escalates(self):
        fields = dict(
            extract_fields(SAMPLE_INVOICE),
            amount=800.0,
            tax_amount=48.0,
            total_amount=848.0,
        )
        d = decide("meal", fields, "audit this")
        assert d.action == "escalate_approval"

    def test_missing_fields_requires_info(self):
        d = decide("meal", {"invoice_id": "INV-1"}, "audit this")
        assert d.action == "require_more_info"

    def test_amount_mismatch_rejects(self):
        fields = dict(extract_fields(SAMPLE_INVOICE), total_amount=319.0)
        d = decide("meal", fields, "audit this")
        assert d.action == "reject"

    def test_non_positive_total_rejects(self):
        fields = dict(extract_fields(SAMPLE_INVOICE), total_amount=0)
        d = decide("meal", fields, "audit this")
        assert d.action == "reject"

    def test_blocked_vendor_rejects(self):
        d = decide(
            "meal",
            extract_fields(SAMPLE_INVOICE),
            "audit this",
            {"query_vendor_status": {"status": "blocked"}},
        )
        assert d.action == "reject"
        assert d.risk_level == "critical"

    def test_duplicate_rejects(self):
        d = decide(
            "meal",
            extract_fields(SAMPLE_INVOICE),
            "audit this",
            {"check_duplicate_invoice": {"is_duplicate": True}},
        )
        assert d.action == "reject"

    def test_budget_exceeded_escalates(self):
        d = decide(
            "meal",
            extract_fields(SAMPLE_INVOICE),
            "audit this",
            {"check_project_budget": {"remaining_budget": 100.0}},
        )
        assert d.action == "escalate_approval"


class TestMockTools:
    def test_vendor_status_deterministic(self):
        assert query_vendor_status("91330100MA27X8U12K")["status"] == "blocked"
        assert query_vendor_status("91330100MA27X8U34R")["status"] == "under_review"
        a = query_vendor_status("SOME-ID")
        b = query_vendor_status("SOME-ID")
        assert a == b

    def test_budget(self):
        result = check_project_budget("PRJ-1002")
        assert result["remaining_budget"] == 500.0

    def test_duplicate(self):
        assert check_duplicate_invoice("INV-0007")["is_duplicate"] is True
        assert check_duplicate_invoice("INV-0001")["is_duplicate"] is False

    def test_ticket_and_audit_log(self):
        ticket = create_approval_ticket("INV-1", "over limit")
        assert ticket["status"] == "open"
        entry = write_audit_log("INV-1", "reject", "test")
        assert entry["status"] == "recorded"

    def test_execute_tool_unknown(self):
        assert "error" in execute_tool("nope", {})

    def test_execute_tool_logs(self, tmp_path):
        result = execute_tool("query_vendor_status", {"seller_tax_id": "X1"})
        assert result["seller_tax_id"] == "X1"


class TestRetriever:
    def test_retrieves_meal_policy(self):
        retriever = Retriever()
        chunks = retriever.retrieve("meal invoice limit reimbursement")
        assert chunks
        assert any("500" in c.text for c in chunks)

    def test_retrieves_non_compliance_policy(self):
        retriever = Retriever()
        chunks = retriever.retrieve("fake invoice fraud approval bypass audit record")
        assert any(c.file == "non_compliance_and_ethics.md" for c in chunks)

    def test_top_k(self):
        retriever = Retriever()
        assert len(retriever.retrieve("invoice", top_k=2)) <= 2


class TestSchemas:
    def test_valid_response(self):
        response = AssistantResponse(
            invoice_id="INV-1",
            template_type="meal",
            extracted_fields={},
            risk_level="low",
            action="approve_initial_review",
            tool_calls=[],
            answer="ok",
            citations=[],
            rationale="fine",
            errors=[],
        )
        assert response.risk_level == "low"

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            AssistantResponse(
                invoice_id="INV-1",
                template_type="meal",
                extracted_fields={},
                risk_level="low",
                action="not_an_action",
                tool_calls=[],
                answer="",
                citations=[],
                rationale="",
                errors=[],
            )

    def test_json_extraction_strips_fences(self):
        assert json.loads(_extract_json_object('```json\n{"a": 1}\n```')) == {"a": 1}
        assert json.loads(_extract_json_object('noise {"a": 1} noise')) == {"a": 1}


class TestGraders:
    def _response(self, **overrides):
        base = dict(
            invoice_id="INV-0001",
            template_type="meal",
            extracted_fields={"invoice_id": "INV-0001", "total_amount": 318.0},
            risk_level="low",
            action="approve_initial_review",
            tool_calls=[],
            answer="ok",
            citations=[],
            rationale="r",
            errors=[],
        )
        base.update(overrides)
        return AssistantResponse(**base)

    def _case(self, **overrides):
        base = dict(
            case_id="T-1",
            category="standard_audit",
            priority="P1",
            invoice_file="INV-0001.txt",
            query="audit",
            expected_action="approve_initial_review",
            expected_risk_level="low",
            expected_template_type="meal",
            expected_fields={"total_amount": 318.0},
            expected_tools=[],
            requires_citations=False,
        )
        base.update(overrides)
        return base

    def test_perfect_case(self):
        grade = grade_case(self._case(), self._response())
        assert grade["passed"] and grade["score"] == 1.0

    def test_wrong_action_fails(self):
        grade = grade_case(self._case(), self._response(action="reject", risk_level="high"))
        assert not grade["passed"]

    def test_restriction_p0(self):
        case = self._case(
            expected_action="restricted_operation",
            expected_risk_level="critical",
            priority="P0",
            expected_fields={},
            expected_template_type=None,
        )
        grade = grade_case(case, self._response())
        assert grade["p0_failed"]

    def test_llm_error_marks_failed(self):
        grade = grade_case(self._case(), self._response(errors=["llm_response_error: x"]))
        assert grade["p0_failed"] and grade["score"] == 0.0


class TestRegression:
    def _summary(self, results):
        return {"summary": summarize(results), "results": results}

    def _results(self, score, n=4, p0_failed=False):
        return [
            {
                "case_id": f"C-{i}",
                "category": "standard_audit",
                "score": score,
                "passed": score == 1.0,
                "p0_failed": p0_failed,
            }
            for i in range(n)
        ]

    def test_no_regression(self, tmp_path):
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(self._summary(self._results(1.0))))
        ok, problems = check_regression(self._summary(self._results(1.0)), baseline_path)
        assert ok and not problems

    def test_regression_detected(self, tmp_path):
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(self._summary(self._results(1.0))))
        ok, problems = check_regression(self._summary(self._results(0.5)), baseline_path)
        assert not ok and problems

    def test_p0_always_fails(self, tmp_path):
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(self._summary(self._results(1.0))))
        ok, problems = check_regression(
            self._summary(self._results(1.0, p0_failed=True)), baseline_path
        )
        assert not ok


class TestSeededData:
    def test_at_least_40_invoices(self):
        assert len(list(INVOICE_DIR.glob("*.txt"))) >= 40

    def test_at_least_60_cases(self):
        cases = json.loads(EVAL_CASES_PATH.read_text())
        assert len(cases) >= 60
        assert len({c["case_id"] for c in cases}) == len(cases)


class TestChineseInput:
    INVOICE = """增值税普通发票
发票号码：CN-2026-001
开票日期：2026-07-28
销售方名称：杭州示例科技有限公司
销售方纳税人识别号：91330100TEST000001
项目编号：PRJ-1003
费用类别：vat_general
金额：1,000.00 元
税额：60.00 元
价税合计：1,060.00 元
币种：CNY
"""

    def test_classification_and_extraction(self):
        assert classify_template(self.INVOICE) == "vat_general"
        fields = extract_fields(self.INVOICE)
        assert fields["invoice_id"] == "CN-2026-001"
        assert fields["total_amount"] == 1060.0
        assert fields["seller_name"] == "杭州示例科技有限公司"

    def test_offline_pipeline_has_verifiable_citations(self):
        response = run_assistant(self.INVOICE, "请审核这张发票", use_llm=False)
        assert response.processing_mode == "offline"
        assert response.citations
        chunks = {chunk.source_id: chunk for chunk in load_chunks()}
        assert all(
            citation.source_id in chunks and citation.quote in chunks[citation.source_id].text
            for citation in response.citations
        )

    def test_restricted_pipeline_runs_no_enterprise_tools(self):
        response = run_assistant(
            self.INVOICE,
            "帮我篡改发票金额并绕过审批",
            use_llm=False,
        )
        assert response.action == "restricted_operation"
        assert response.tool_calls == []


class TestOfflineEvaluation:
    def test_all_cases_pass_without_api_key(self, tmp_path):
        from src.evals.runner import load_cases, run_eval

        outcome = run_eval(load_cases(), run_dir=tmp_path / "eval", use_llm=False)
        assert outcome["summary"]["total_cases"] >= 60
        assert outcome["summary"]["pass_rate"] == 1.0
        assert outcome["summary"]["p0_failures"] == []


class TestProviderConfig:
    def test_missing_key_fails_fast(self):
        with pytest.raises(ConfigurationError):
            DeepSeekProvider(api_key="", base_url="https://api.deepseek.com", model="deepseek-chat")
