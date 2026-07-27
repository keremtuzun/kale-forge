"""Local reranking. A self-hosted cross-encoder (BGE reranker) when available; otherwise a
no-op passthrough. NEVER calls an external reranking API."""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class Reranker(Protocol):
    @property
    def provider(self) -> str: ...

    def rerank(self, query: str, candidates: list[str]) -> list[float]: ...


class NoopReranker:
    @property
    def provider(self) -> str:
        return "noop"

    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        return [0.0] * len(candidates)


class CrossEncoderReranker:
    """Self-hosted cross-encoder reranker (e.g. BAAI/bge-reranker-v2-m3), run locally."""

    def __init__(self, model_name: str):
        from sentence_transformers import CrossEncoder  # noqa: PLC0415

        self._name = model_name
        self._model = CrossEncoder(model_name)

    @property
    def provider(self) -> str:
        return f"local-cross-encoder:{self._name}"

    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        if not candidates:
            return []
        pairs = [[query, c] for c in candidates]
        return [float(s) for s in self._model.predict(pairs)]


def get_reranker() -> Reranker:
    model_name = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    try:
        return CrossEncoderReranker(model_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cross-encoder reranker unavailable (%s); using no-op reranker", exc)
        return NoopReranker()
