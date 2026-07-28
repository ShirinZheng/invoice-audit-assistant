"""FastAPI application for the invoice audit assistant."""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.assistant.pipeline import run_assistant
from src.config import INVOICE_DIR, PROJECT_ROOT, get_settings
from src.invoices.extractor import extract_fields
from src.invoices.file_loader import (
    MAX_TEXT_CHARACTERS,
    SUPPORTED_EXTENSIONS,
    InvoiceLoadError,
    load_invoice_bytes,
)
from src.schemas import AssistantResponse

WEB_DIR = PROJECT_ROOT / "web"


class AuditRequest(BaseModel):
    invoice_text: str = Field(min_length=1, max_length=MAX_TEXT_CHARACTERS)
    query: str = Field(
        default="请审核这张发票是否可以报销，并说明依据。",
        min_length=1,
        max_length=2_000,
    )
    use_deepseek: bool = False


class DemoAuditRequest(BaseModel):
    query: str = Field(
        default="请审核这张发票是否可以报销，并说明依据。",
        min_length=1,
        max_length=2_000,
    )
    use_deepseek: bool = False


app = FastAPI(
    title="发票智能审计助手 API",
    summary="本地优先、规则可复现、引用可核验的发票初审服务",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


def _validate_deepseek_mode(use_deepseek: bool) -> None:
    if use_deepseek and not get_settings().deepseek_api_key:
        raise HTTPException(
            status_code=503,
            detail="DeepSeek 模式尚未配置，请设置 DEEPSEEK_API_KEY 或使用离线模式。",
        )


def _audit(invoice_text: str, query: str, use_deepseek: bool) -> AssistantResponse:
    _validate_deepseek_mode(use_deepseek)
    return run_assistant(invoice_text, query, use_llm=use_deepseek)


@app.get("/api/v1/health", tags=["system"])
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "mode": settings.audit_mode,
        "deepseek_configured": bool(settings.deepseek_api_key),
    }


@app.get("/api/v1/config", tags=["system"])
def public_config() -> dict:
    settings = get_settings()
    return {
        "app_name": settings.app_name,
        "default_mode": settings.audit_mode,
        "deepseek_configured": bool(settings.deepseek_api_key),
        "max_upload_bytes": settings.max_upload_bytes,
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
    }


@app.post("/api/v1/audits", response_model=AssistantResponse, tags=["audit"])
def audit_text(payload: AuditRequest) -> AssistantResponse:
    return _audit(payload.invoice_text, payload.query, payload.use_deepseek)


@app.post("/api/v1/audits/file", response_model=AssistantResponse, tags=["audit"])
async def audit_file(
    file: Annotated[UploadFile, File(description="发票文本、PDF 或图片")],
    query: Annotated[
        str,
        Form(min_length=1, max_length=2_000),
    ] = "请审核这张发票是否可以报销，并说明依据。",
    use_deepseek: Annotated[bool, Form()] = False,
) -> AssistantResponse:
    settings = get_settings()
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件不能超过 {settings.max_upload_bytes // 1024 // 1024} MB。",
        )
    try:
        loaded = load_invoice_bytes(data, file.filename or "invoice.txt", file.content_type)
    except InvoiceLoadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = _audit(loaded.text, query, use_deepseek)
    response.warnings.extend(loaded.warnings)
    return response


@app.get("/api/v1/demo-invoices", tags=["demo"])
def demo_invoices() -> list[dict]:
    results = []
    for path in sorted(INVOICE_DIR.glob("*.txt")):
        fields = extract_fields(path.read_text(encoding="utf-8"))
        results.append(
            {
                "invoice_id": fields.get("invoice_id", path.stem),
                "seller_name": fields.get("seller_name", "未知销售方"),
                "total_amount": fields.get("total_amount"),
                "category": fields.get("category"),
                "filename": path.name,
            }
        )
    return results


@app.post(
    "/api/v1/demo-invoices/{invoice_id}/audit",
    response_model=AssistantResponse,
    tags=["demo"],
)
def audit_demo(invoice_id: str, payload: DemoAuditRequest) -> AssistantResponse:
    normalized = invoice_id.upper()
    if not normalized.startswith("INV-") or not normalized[4:].isdigit():
        raise HTTPException(status_code=404, detail="示例发票不存在。")
    path = INVOICE_DIR / f"{normalized}.txt"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="示例发票不存在。")
    return _audit(path.read_text(encoding="utf-8"), payload.query, payload.use_deepseek)


if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    index_path = WEB_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Web 界面尚未构建。")
    return FileResponse(index_path)
