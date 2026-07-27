"""In-memory vector store with cosine search and optional SQLite persistence."""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

VALID_KINDS = {"component", "net", "rule", "datasheet_section", "project_summary", "finding"}


@dataclass
class StoredItem:
    kind: str
    ref: str
    text: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoredItem:
    item: StoredItem
    score: float


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class VectorStore:
    def __init__(self) -> None:
        self._items: list[StoredItem] = []

    def add(self, kind: str, ref: str, text: str, vector: list[float], metadata: Optional[dict] = None) -> None:
        self._items.append(StoredItem(kind=kind, ref=ref, text=text, vector=vector, metadata=metadata or {}))

    def all(self) -> list[StoredItem]:
        return list(self._items)

    def search(self, query_vector: list[float], k: int = 8, kind_filter: Optional[set[str]] = None) -> list[ScoredItem]:
        scored = [
            ScoredItem(item=it, score=_cosine(query_vector, it.vector))
            for it in self._items
            if kind_filter is None or it.kind in kind_filter
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:k]

    # --- persistence (optional) ---
    def persist(self, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS embeddings "
            "(kind TEXT, ref TEXT, text TEXT, vector TEXT, metadata TEXT)"
        )
        conn.execute("DELETE FROM embeddings")
        conn.executemany(
            "INSERT INTO embeddings VALUES (?, ?, ?, ?, ?)",
            [(it.kind, it.ref, it.text, json.dumps(it.vector), json.dumps(it.metadata)) for it in self._items],
        )
        conn.commit()
        conn.close()

    @classmethod
    def load(cls, db_path: str) -> "VectorStore":
        store = cls()
        conn = sqlite3.connect(db_path)
        for kind, ref, text, vector, metadata in conn.execute(
            "SELECT kind, ref, text, vector, metadata FROM embeddings"
        ):
            store.add(kind, ref, text, json.loads(vector), json.loads(metadata))
        conn.close()
        return store
