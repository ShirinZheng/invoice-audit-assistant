"""Additional resilience, file-loading, and optional-provider tests."""

from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import BaseModel
from pypdf import PdfWriter

from src.api.app import app
from src.assistant.pipeline import run_assistant
from src.config import redact
from src.invoices.file_loader import (
    InvoiceLoadError,
    load_invoice_bytes,
    load_invoice_path,
)
from src.llm.deepseek_provider import (
    DeepSeekProvider,
    LLMResponseError,
    _summarize_trace_payload,
)
from src.rag.retriever import build_index

client = TestClient(app)

INVOICE = """MEAL INVOICE
Invoice ID: EXT-001
Date: 2026-07-28
Seller: Example Restaurant
Seller Tax ID: EXT-TAX-001
Project ID: PRJ-1003
Amount: 100.00
Tax: 6.00
Total: 106.00
"""


class TinyResponse(BaseModel):
    value: int


class FakeProvider:
    def generate_json(self, system_prompt, user_prompt, response_schema):
        return {
            "invoice_id": "MODEL-TRIED-TO-CHANGE-IT",
            "template_type": "unknown",
            "extracted_fields": {},
            "risk_level": "critical",
            "action": "reject",
            "tool_calls": [],
            "answer": "Model-written explanation.",
            "citations": [{"source_id": "missing#0", "file": "wrong.md", "quote": "not real"}],
            "rationale": "Model rationale.",
            "errors": [],
            "warnings": [],
            "processing_mode": "deepseek",
        }


class FailingProvider:
    def generate_json(self, system_prompt, user_prompt, response_schema):
        raise LLMResponseError("simulated outage")


def test_model_cannot_override_deterministic_decision():
    response = run_assistant(INVOICE, "audit this", provider=FakeProvider(), use_llm=True)
    assert response.invoice_id == "EXT-001"
    assert response.action == "approve_initial_review"
    assert response.risk_level == "low"
    assert response.processing_mode == "deepseek"
    assert response.citations == []


def test_model_failure_falls_back_offline():
    response = run_assistant(INVOICE, "audit this", provider=FailingProvider(), use_llm=True)
    assert response.action == "approve_initial_review"
    assert response.processing_mode == "offline"
    assert response.warnings and "simulated outage" in response.warnings[0]


def test_text_decoding_and_path_loading(tmp_path):
    utf8 = load_invoice_bytes(INVOICE.encode(), "invoice.txt")
    assert utf8.text.startswith("MEAL INVOICE")

    chinese = "发票号码：中文-001".encode("gb18030")
    assert "中文-001" in load_invoice_bytes(chinese, "invoice.txt").text

    path = tmp_path / "invoice.md"
    path.write_text(INVOICE)
    assert load_invoice_path(path).filename == "invoice.md"


@pytest.mark.parametrize(
    ("data", "filename", "message"),
    [
        (b"", "empty.txt", "为空"),
        (b"binary", "invoice.exe", "不支持"),
        (b"\xff\xfe\xfa", "invoice.txt", "编码"),
    ],
)
def test_invalid_files_are_rejected(data, filename, message):
    with pytest.raises(InvoiceLoadError, match=message):
        load_invoice_bytes(data, filename)


def test_missing_path_is_rejected(tmp_path):
    with pytest.raises(InvoiceLoadError, match="不存在"):
        load_invoice_path(tmp_path / "missing.txt")


def test_blank_pdf_requests_image_ocr():
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    with pytest.raises(InvoiceLoadError, match="没有可提取"):
        load_invoice_bytes(stream.getvalue(), "scan.pdf")


def test_image_ocr(monkeypatch):
    import pytesseract

    stream = BytesIO()
    Image.new("RGB", (40, 40), "white").save(stream, format="PNG")
    monkeypatch.setattr(pytesseract, "image_to_string", lambda image, lang: INVOICE)

    loaded = load_invoice_bytes(stream.getvalue(), "invoice.png")
    assert loaded.pages == 1
    assert loaded.warnings
    assert "EXT-001" in loaded.text


def test_deepseek_json_validation_and_repair(monkeypatch):
    provider = DeepSeekProvider(
        api_key="test-only-key",
        base_url="https://example.invalid",
        model="test",
    )
    replies = iter(
        [
            SimpleNamespace(content='{"value": "wrong"}'),
            SimpleNamespace(content='{"value": 2}'),
        ]
    )
    monkeypatch.setattr(provider, "_chat", lambda *args, **kwargs: next(replies))
    assert provider.generate_json("system", "user", TinyResponse) == {"value": 2}


def test_deepseek_json_failure_after_repair(monkeypatch):
    provider = DeepSeekProvider(
        api_key="test-only-key",
        base_url="https://example.invalid",
        model="test",
    )
    monkeypatch.setattr(
        provider,
        "_chat",
        lambda *args, **kwargs: SimpleNamespace(content="not json"),
    )
    with pytest.raises(LLMResponseError, match="after repair"):
        provider.generate_json("system", "user", TinyResponse)


def test_deepseek_tool_call_parsing(monkeypatch):
    provider = DeepSeekProvider(
        api_key="test-only-key",
        base_url="https://example.invalid",
        model="test",
    )
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="demo", arguments='{"value": 1}'),
    )
    monkeypatch.setattr(
        provider,
        "_chat",
        lambda *args, **kwargs: SimpleNamespace(content=None, tool_calls=[tool_call]),
    )
    result = provider.generate_with_tools("system", "user", [])
    assert result["tool_calls"][0]["arguments"] == {"value": 1}


def test_trace_summary_and_redaction(monkeypatch):
    payload = {
        "request_id": "req-1",
        "messages": [{"role": "user", "content": "sensitive invoice"}],
        "tools": [{"function": {"name": "lookup"}}],
        "content": "private response",
        "tool_calls": [{"id": "1", "name": "lookup", "arguments": "{}"}],
        "raw": "{}",
        "error": "bad",
        "attempt": 1,
    }
    summary = _summarize_trace_payload(payload)
    assert summary["messages"][0]["characters"] == len("sensitive invoice")
    assert "sensitive invoice" not in json.dumps(summary)

    monkeypatch.setenv("DEMO_SECRET_TOKEN", "very-secret-value")
    assert "very-secret-value" not in redact("token=very-secret-value")


def test_build_index_writes_json_manifest(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "policy.md").write_text(
        "# Policy\n\nInvoices must contain all required fields.",
        encoding="utf-8",
    )
    output = tmp_path / "index.json"
    assert build_index(kb, output) == 1
    payload = json.loads(output.read_text())
    assert payload["format_version"] == 1
    assert payload["documents"][0]["sha256"]


def test_demo_api_flows():
    listing = client.get("/api/v1/demo-invoices")
    assert listing.status_code == 200
    assert len(listing.json()) >= 40

    audit = client.post(
        "/api/v1/demo-invoices/INV-0001/audit",
        json={"query": "请审核", "use_deepseek": False},
    )
    assert audit.status_code == 200
    assert audit.json()["invoice_id"] == "INV-0001"

    missing = client.post(
        "/api/v1/demo-invoices/INV-9999/audit",
        json={"query": "audit", "use_deepseek": False},
    )
    assert missing.status_code == 404
