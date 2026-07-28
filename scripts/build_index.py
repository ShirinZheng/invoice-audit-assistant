#!/usr/bin/env python
"""Build the local TF-IDF RAG index over data/knowledge_base/."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import INDEX_PATH  # noqa: E402
from src.rag.retriever import build_index  # noqa: E402


def main() -> None:
    count = build_index()
    print(f"Indexed {count} chunks -> {INDEX_PATH}")


if __name__ == "__main__":
    main()
