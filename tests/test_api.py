"""API and Web user-flow tests. No external API key or network required."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.app import app

client = TestClient(app)


def test_home_and_health():
    home = client.get("/")
    assert home.status_code == 200
    assert "发票智能审计助手" in home.text
    assert home.headers["x-content-type-options"] == "nosniff"

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"


def test_public_config_never_exposes_api_key():
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    payload = response.json()
    assert "deepseek_api_key" not in payload
    assert ".pdf" in payload["supported_extensions"]


def test_text_audit_flow():
    response = client.post(
        "/api/v1/audits",
        json={
            "invoice_text": """MEAL INVOICE
Invoice ID: API-001
Date: 2026-07-28
Seller: Test Restaurant
Seller Tax ID: TEST-TAX-001
Project ID: PRJ-1003
Amount: 100.00
Tax: 6.00
Total: 106.00
Currency: CNY
""",
            "query": "请审核这张发票",
            "use_deepseek": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["invoice_id"] == "API-001"
    assert payload["action"] == "approve_initial_review"
    assert payload["processing_mode"] == "offline"
    assert payload["citations"]


def test_text_file_upload_flow():
    invoice = b"""OFFICE SUPPLIES INVOICE
Invoice ID: FILE-001
Date: 2026-07-28
Seller: Example Supplies
Seller Tax ID: TAX-FILE-001
Project ID: PRJ-1003
Amount: 200.00
Tax: 12.00
Total: 212.00
"""
    response = client.post(
        "/api/v1/audits/file",
        files={"file": ("invoice.txt", invoice, "text/plain")},
        data={"query": "audit this invoice", "use_deepseek": "false"},
    )
    assert response.status_code == 200
    assert response.json()["invoice_id"] == "FILE-001"


def test_unsupported_upload_is_rejected():
    response = client.post(
        "/api/v1/audits/file",
        files={"file": ("invoice.exe", b"not an invoice", "application/octet-stream")},
        data={"query": "audit this invoice", "use_deepseek": "false"},
    )
    assert response.status_code == 422
    assert "不支持" in response.json()["detail"]


def test_deepseek_requires_server_side_configuration():
    response = client.post(
        "/api/v1/audits",
        json={
            "invoice_text": "Invoice ID: X",
            "query": "audit",
            "use_deepseek": True,
        },
    )
    assert response.status_code == 503


def test_demo_path_cannot_traverse():
    response = client.post(
        "/api/v1/demo-invoices/..%2F.env/audit",
        json={"query": "audit", "use_deepseek": False},
    )
    assert response.status_code == 404
