"""Deterministic field extraction from `key: value` invoice text (no LLM calls)."""

from __future__ import annotations

import re
from typing import Any

_FIELD_ALIASES: dict[str, list[str]] = {
    "invoice_id": [
        "invoice id",
        "invoice no",
        "invoice number",
        "发票号码",
        "发票号",
    ],
    "invoice_date": ["date", "invoice date", "开票日期", "日期"],
    "seller_name": [
        "seller",
        "seller name",
        "vendor",
        "vendor name",
        "销售方",
        "销售方名称",
        "商户名称",
    ],
    "seller_tax_id": [
        "seller tax id",
        "tax id",
        "vendor tax id",
        "销售方纳税人识别号",
        "纳税人识别号",
        "税号",
    ],
    "buyer_name": ["buyer", "buyer name", "购买方", "购买方名称"],
    "project_id": ["project id", "project", "项目编号", "项目"],
    "category": ["category", "expense category", "费用类别", "类别"],
    "description": ["description", "item", "items", "说明", "项目名称"],
    "amount": ["amount", "subtotal", "net amount", "金额", "不含税金额"],
    "tax_amount": ["tax", "tax amount", "vat amount", "税额"],
    "total_amount": [
        "total",
        "total amount",
        "grand total",
        "价税合计",
        "合计",
        "总金额",
    ],
    "currency": ["currency", "币种"],
    "payment_method": ["payment method", "payment", "支付方式"],
    "notes": ["notes", "remark", "remarks", "备注"],
}

_NUMERIC_FIELDS = {"amount", "tax_amount", "total_amount"}


def _parse_number(value: str) -> float | None:
    cleaned = re.sub(r"[^\d.\-]", "", value.replace(",", ""))
    if cleaned.count(".") > 1 or cleaned.count("-") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_fields(text: str) -> dict[str, Any]:
    """Parse `Key: Value` lines into a normalized field dict."""
    fields: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.replace("：", ":", 1)
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key_norm = key.strip().lower()
        value = value.strip()
        if not value:
            continue
        for field, aliases in _FIELD_ALIASES.items():
            if key_norm in aliases and field not in fields:
                if field in _NUMERIC_FIELDS:
                    number = _parse_number(value)
                    fields[field] = number if number is not None else value
                else:
                    fields[field] = value
                break
    return fields
