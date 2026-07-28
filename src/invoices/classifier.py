"""Deterministic invoice template classification (no LLM calls)."""

from __future__ import annotations

TEMPLATE_TYPES = ("vat_special", "vat_general", "travel", "meal", "office_supplies")

_KEYWORDS: dict[str, list[str]] = {
    "vat_special": [
        "VAT SPECIAL INVOICE",
        "special vat",
        "增值税专用发票",
    ],
    "vat_general": [
        "VAT GENERAL INVOICE",
        "general vat",
        "增值税普通发票",
        "电子普通发票",
    ],
    "travel": [
        "TRAVEL INVOICE",
        "itinerary",
        "flight",
        "train ticket",
        "hotel",
        "行程单",
        "机票",
        "火车票",
        "住宿",
    ],
    "meal": [
        "MEAL INVOICE",
        "restaurant",
        "catering",
        "banquet",
        "餐饮",
        "饭店",
        "餐厅",
    ],
    "office_supplies": [
        "OFFICE SUPPLIES INVOICE",
        "stationery",
        "office supplies",
        "办公用品",
        "文具",
    ],
}


def classify_template(text: str) -> str:
    """Classify the invoice template from raw text.

    Header markers (first line) win; otherwise keyword scoring decides.
    Returns "unknown" when nothing matches.
    """
    lowered = text.lower()
    first_line = text.strip().splitlines()[0].lower() if text.strip() else ""

    for template, keywords in _KEYWORDS.items():
        if any(keyword.lower() in first_line for keyword in keywords):
            return template

    best, best_score = "unknown", 0
    for template, keywords in _KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in lowered)
        if score > best_score:
            best, best_score = template, score
    return best
