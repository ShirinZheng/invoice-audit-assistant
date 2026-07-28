"""Load invoice text from common local document formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path


class InvoiceLoadError(ValueError):
    """Raised when an uploaded invoice cannot be converted to text."""


@dataclass
class LoadedInvoice:
    text: str
    filename: str
    media_type: str
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | {".pdf"}
MAX_PDF_PAGES = 50
MAX_IMAGE_PIXELS = 25_000_000
MAX_TEXT_CHARACTERS = 100_000


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise InvoiceLoadError("文本编码无法识别，请转换为 UTF-8 后重试。")


def _load_pdf(data: bytes, filename: str) -> LoadedInvoice:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise InvoiceLoadError("PDF 支持未安装，请安装 pypdf。") from exc

    try:
        reader = PdfReader(BytesIO(data))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise InvoiceLoadError(f"PDF 不能超过 {MAX_PDF_PAGES} 页。")
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
    except InvoiceLoadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InvoiceLoadError(f"PDF 解析失败：{exc}") from exc
    if not text.strip():
        raise InvoiceLoadError("PDF 中没有可提取的文字；扫描件请转成 PNG/JPG 后使用 OCR 上传。")
    return LoadedInvoice(
        text=text.strip(),
        filename=filename,
        media_type="application/pdf",
        pages=len(reader.pages),
    )


def _load_image(data: bytes, filename: str) -> LoadedInvoice:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise InvoiceLoadError("图片 OCR 支持未安装，请安装 Pillow 和 pytesseract。") from exc

    try:
        image = Image.open(BytesIO(data))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise InvoiceLoadError("图片像素过大，请压缩到 2500 万像素以内。")
        try:
            text = pytesseract.image_to_string(image, lang="chi_sim+eng")
        except pytesseract.TesseractError:
            text = pytesseract.image_to_string(image, lang="eng")
    except InvoiceLoadError:
        raise
    except pytesseract.TesseractNotFoundError as exc:
        raise InvoiceLoadError(
            "未找到 Tesseract OCR。请安装系统 Tesseract，或使用 Docker 版本。"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise InvoiceLoadError(f"图片 OCR 失败：{exc}") from exc
    if not text.strip():
        raise InvoiceLoadError("OCR 未识别出文字，请上传更清晰的图片。")
    return LoadedInvoice(
        text=text.strip(),
        filename=filename,
        media_type=Image.MIME.get(image.format, "image/*"),
        pages=1,
        warnings=["图片内容由 OCR 识别，请人工核对关键金额和发票号码。"],
    )


def load_invoice_bytes(
    data: bytes,
    filename: str,
    media_type: str | None = None,
) -> LoadedInvoice:
    """Convert supported invoice bytes into normalized text."""

    safe_name = Path(filename or "invoice.txt").name
    extension = Path(safe_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise InvoiceLoadError(f"不支持 {extension or '无扩展名'} 文件；支持：{supported}")
    if not data:
        raise InvoiceLoadError("文件内容为空。")

    if extension in TEXT_EXTENSIONS:
        text = _decode_text(data).strip()
        if not text:
            raise InvoiceLoadError("文件内容为空。")
        if len(text) > MAX_TEXT_CHARACTERS:
            raise InvoiceLoadError(f"文本不能超过 {MAX_TEXT_CHARACTERS} 个字符。")
        return LoadedInvoice(
            text=text,
            filename=safe_name,
            media_type=media_type or "text/plain",
        )
    loaded = _load_pdf(data, safe_name) if extension == ".pdf" else _load_image(data, safe_name)
    if len(loaded.text) > MAX_TEXT_CHARACTERS:
        raise InvoiceLoadError(f"识别结果不能超过 {MAX_TEXT_CHARACTERS} 个字符。")
    return loaded


def load_invoice_path(path: Path) -> LoadedInvoice:
    """Load an invoice from a trusted local path."""

    if not path.is_file():
        raise InvoiceLoadError(f"文件不存在：{path}")
    return load_invoice_bytes(path.read_bytes(), path.name)
