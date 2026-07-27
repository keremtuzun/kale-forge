"""Local, self-hosted embeddings. NEVER calls an external embedding API.

Primary: sentence-transformers BGE (downloaded open weights, run locally).
Fallback: a deterministic token-hash embedder for dev/CI where torch is unavailable —
flagged provider="hash-fallback" so callers know it is not a semantic model."""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def provider(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


class HashingEmbedder:
    """Deterministic token-hashing embedder. This is a DEVELOPMENT FALLBACK, not a semantic
    model: it captures lexical overlap only. Same input always yields the same vector."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    @property
    def name(self) -> str:
        return f"hashing-{self.dim}"

    @property
    def provider(self) -> str:
        return "hash-fallback"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in _TOKEN_RE.findall(text.lower()):
                h = hashlib.sha1(token.encode()).digest()
                idx = int.from_bytes(h[:4], "big") % self.dim
                sign = 1.0 if h[4] % 2 == 0 else -1.0
                vec[idx] += sign
                idx2 = int.from_bytes(h[5:9], "big") % self.dim
                vec[idx2] += sign * 0.5
            out.append(_l2_normalize(vec))
        return out


class SentenceTransformersEmbedder:
    """Wraps a self-hosted sentence-transformers model (e.g. BAAI/bge-small-en-v1.5).
    Weights are downloaded once and run locally; no inference API is contacted."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model_name = model_name
        self._model = SentenceTransformer(model_name)

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def provider(self) -> str:
        return "local-sentence-transformers"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vectors]


def get_embedder() -> Embedder:
    """Prefer the local sentence-transformers model; fall back to hashing if unavailable."""
    model_name = os.getenv("EMBEDDINGS_MODEL", "BAAI/bge-small-en-v1.5")
    try:
        return SentenceTransformersEmbedder(model_name)
    except Exception as exc:  # noqa: BLE001 — torch/model may be absent in dev
        logger.warning("sentence-transformers unavailable (%s); using hash-fallback embedder", exc)
        return HashingEmbedder()
