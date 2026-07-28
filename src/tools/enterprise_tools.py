"""Mock internal enterprise tools.

These simulate internal enterprise APIs deterministically (seeded by input),
so unit tests and eval grading are stable. The tools are mock; the LLM is not.
All executions are logged to runs/tool_traces/.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from src.config import TOOL_TRACE_DIR, redact

# Vendors seeded with fixed statuses; anything else derives deterministically
# from a hash of the tax id so repeated runs agree.
_VENDOR_STATUS_SEED = {
    "91330100MA27X8U12K": "blocked",
    "91330100MA27X8U34R": "under_review",
}

_PROJECT_BUDGETS = {
    "PRJ-1001": {"total_budget": 200000.0, "spent": 150000.0},
    "PRJ-1002": {"total_budget": 50000.0, "spent": 49500.0},
    "PRJ-1003": {"total_budget": 80000.0, "spent": 10000.0},
}

# Invoice ids registered as already-submitted (duplicates).
_SUBMITTED_INVOICES = {"INV-0007", "INV-0019"}


def _stable_hash(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest(), 16)


def query_vendor_status(seller_tax_id: str) -> dict[str, Any]:
    status = _VENDOR_STATUS_SEED.get(seller_tax_id)
    if status is None:
        status = "active" if _stable_hash(seller_tax_id) % 10 < 9 else "under_review"
    return {"seller_tax_id": seller_tax_id, "status": status}


def check_project_budget(project_id: str) -> dict[str, Any]:
    budget = _PROJECT_BUDGETS.get(project_id)
    if budget is None:
        total = 100000.0
        spent = float(_stable_hash(project_id) % 90000)
        budget = {"total_budget": total, "spent": spent}
    remaining = budget["total_budget"] - budget["spent"]
    return {
        "project_id": project_id,
        "total_budget": budget["total_budget"],
        "spent": budget["spent"],
        "remaining_budget": remaining,
    }


def check_duplicate_invoice(
    invoice_id: str, seller_tax_id: str = "", total_amount: float = 0.0
) -> dict[str, Any]:
    return {
        "invoice_id": invoice_id,
        "is_duplicate": invoice_id in _SUBMITTED_INVOICES,
    }


def create_approval_ticket(invoice_id: str, reason: str, level: str = "manager") -> dict[str, Any]:
    ticket_id = f"TICKET-{_stable_hash(invoice_id + reason) % 100000:05d}"
    return {
        "ticket_id": ticket_id,
        "invoice_id": invoice_id,
        "reason": reason,
        "level": level,
        "status": "open",
    }


def write_audit_log(invoice_id: str, action: str, detail: str = "") -> dict[str, Any]:
    entry_id = f"AUDIT-{_stable_hash(invoice_id + action + detail) % 100000:05d}"
    return {
        "entry_id": entry_id,
        "invoice_id": invoice_id,
        "action": action,
        "detail": detail,
        "status": "recorded",
    }


TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "query_vendor_status": query_vendor_status,
    "check_project_budget": check_project_budget,
    "check_duplicate_invoice": check_duplicate_invoice,
    "create_approval_ticket": create_approval_ticket,
    "write_audit_log": write_audit_log,
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_vendor_status",
            "description": "Query internal vendor compliance status by seller tax ID.",
            "parameters": {
                "type": "object",
                "properties": {"seller_tax_id": {"type": "string"}},
                "required": ["seller_tax_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_project_budget",
            "description": "Check remaining budget for an internal project by project ID.",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_duplicate_invoice",
            "description": "Check whether an invoice ID was already submitted for reimbursement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "seller_tax_id": {"type": "string"},
                    "total_amount": {"type": "number"},
                },
                "required": ["invoice_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_approval_ticket",
            "description": "Create an internal approval ticket for an invoice that needs escalation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "level": {"type": "string"},
                },
                "required": ["invoice_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_audit_log",
            "description": "Write an immutable audit log entry for an invoice audit decision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "action": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["invoice_id", "action"],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a mock tool by name and log the call to runs/tool_traces/."""
    if name not in TOOL_REGISTRY:
        result: dict[str, Any] = {"error": f"Unknown tool: {name}"}
    else:
        try:
            result = TOOL_REGISTRY[name](**arguments)
        except TypeError as exc:
            result = {"error": f"Invalid arguments for {name}: {exc}"}

    from src.config import get_settings

    if get_settings().trace_enabled:
        TOOL_TRACE_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "call_id": uuid.uuid4().hex[:12],
            "tool": name,
            "arguments": arguments,
            "result": result,
        }
        path = TOOL_TRACE_DIR / f"{time.strftime('%Y%m%d')}-tool-traces.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(redact(json.dumps(record, ensure_ascii=False)) + "\n")
    return result
