"""Safe local RAG retriever over ``data/knowledge_base``.

The index is rebuilt in memory from Markdown on startup. A human-readable JSON
manifest can be generated for diagnostics; no executable pickle is loaded.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.config import INDEX_PATH, KB_DIR, get_settings


@dataclass
class Chunk:
    source_id: str
    file: str
    text: str


def _split_chunks(text: str) -> list[str]:
    """Split a markdown document into paragraph chunks."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in parts if len(p) > 30]


def load_chunks(kb_dir: Path = KB_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(kb_dir.glob("*.md")):
        for i, part in enumerate(_split_chunks(path.read_text(encoding="utf-8"))):
            chunks.append(Chunk(source_id=f"{path.stem}#{i}", file=path.name, text=part))
    return chunks


def build_index(kb_dir: Path = KB_DIR, index_path: Path = INDEX_PATH) -> int:
    """Validate the knowledge base and write a safe JSON index manifest."""
    chunks = load_chunks(kb_dir)
    if not chunks:
        raise RuntimeError(
            f"No knowledge base documents found in {kb_dir}. Run scripts/seed_demo_data.py first."
        )
    documents = []
    for path in sorted(kb_dir.glob("*.md")):
        raw = path.read_bytes()
        documents.append(
            {
                "file": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        )
    payload = {
        "format_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "documents": documents,
        "chunks": [{"source_id": c.source_id, "file": c.file, "text": c.text} for c in chunks],
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(chunks)


class Retriever:
    def __init__(self, kb_dir: Path = KB_DIR) -> None:
        self._chunks = load_chunks(kb_dir)
        if not self._chunks:
            raise RuntimeError(f"No knowledge base documents found in {kb_dir}.")
        # Character n-grams work for both English and Chinese policy text.
        self._vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(2, 5),
            lowercase=True,
            max_features=50_000,
        )
        self._matrix = self._vectorizer.fit_transform([c.text for c in self._chunks])

    def retrieve(self, query: str, top_k: int | None = None) -> list[Chunk]:
        top_k = get_settings().rag_top_k if top_k is None else top_k
        if top_k <= 0 or not query.strip():
            return []
        query_vec = self._vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self._matrix)[0]
        ranked = scores.argsort()[::-1][:top_k]
        return [self._chunks[i] for i in ranked if scores[i] > 0]
