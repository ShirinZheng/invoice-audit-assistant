"""Application configuration.

The assistant is usable offline by default. DeepSeek is an optional enhancement
and is only enabled when explicitly requested and configured.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

SOURCE_ROOT = Path(__file__).resolve().parents[1]
INSTALLED_ROOT = Path(sys.prefix) / "share" / "invoice-audit-assistant"
PROJECT_ROOT = SOURCE_ROOT if (SOURCE_ROOT / "data").is_dir() else INSTALLED_ROOT
DATA_DIR = PROJECT_ROOT / "data"
RUNS_DIR = Path(
    os.getenv(
        "AUDIT_RUNS_DIR",
        str(SOURCE_ROOT / "runs" if PROJECT_ROOT == SOURCE_ROOT else Path.cwd() / "runs"),
    )
).resolve()
LLM_TRACE_DIR = RUNS_DIR / "llm_traces"
TOOL_TRACE_DIR = RUNS_DIR / "tool_traces"
KB_DIR = DATA_DIR / "knowledge_base"
INVOICE_DIR = DATA_DIR / "invoices" / "raw"
INDEX_PATH = DATA_DIR / "index" / "tfidf_index.json"
EVAL_CASES_PATH = PROJECT_ROOT / "evals" / "cases" / "cases.json"
BASELINE_PATH = PROJECT_ROOT / "evals" / "baseline" / "baseline_results.json"

API_KEY_ERROR = (
    "DEEPSEEK_API_KEY is required when DeepSeek mode is enabled. "
    "Set it in the project .env file or use offline mode."
)


class ConfigurationError(RuntimeError):
    """Raised when required configuration (e.g. the DeepSeek API key) is missing."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(SOURCE_ROOT / ".env", Path.cwd() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "发票智能审计助手"
    audit_mode: Literal["offline", "deepseek"] = "offline"
    llm_provider: str = "deepseek"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_temperature: float = 0.0
    deepseek_max_tokens: int = 4096

    rag_top_k: int = 4
    eval_regression_threshold: float = 0.03
    trace_enabled: bool = True
    trace_include_content: bool = False
    max_upload_bytes: int = 10 * 1024 * 1024

    def require_api_key(self) -> None:
        if not self.deepseek_api_key:
            raise ConfigurationError(API_KEY_ERROR)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


_SENSITIVE_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def redact(text: str) -> str:
    """Redact values of protected environment variables from a string.

    The API key must never appear in logs or traces.
    """
    out = text
    for name, value in os.environ.items():
        if not value or len(value) < 6:
            continue
        if any(marker in name.upper() for marker in _SENSITIVE_MARKERS):
            out = out.replace(value, "[REDACTED]")
    settings = get_settings()
    if settings.deepseek_api_key:
        out = out.replace(settings.deepseek_api_key, "[REDACTED]")
    return out
