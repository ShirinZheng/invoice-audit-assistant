"""Pydantic schemas. Every LLM response is validated against these models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high", "critical"]
Action = Literal[
    "approve_initial_review",
    "require_more_info",
    "escalate_approval",
    "reject",
    "restricted_operation",
]


class Citation(BaseModel):
    source_id: str
    file: str
    quote: str


class ToolCallRecord(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class AssistantResponse(BaseModel):
    """The structured output contract for every assistant answer."""

    invoice_id: str
    template_type: str
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    action: Action
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    rationale: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    processing_mode: Literal["offline", "deepseek"] = "offline"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PolicyDecision(BaseModel):
    risk_level: RiskLevel
    action: Action
    reasons: list[str] = Field(default_factory=list)
