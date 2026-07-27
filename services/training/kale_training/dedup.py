"""Content-hash deduplication of training examples on (instruction, canonical input)."""
from __future__ import annotations

import hashlib
import json

from kale_training.schema import TrainingExample


def _key(ex: TrainingExample) -> str:
    canonical = json.dumps(
        {"instruction": ex.instruction, "input": ex.input.model_dump()},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def deduplicate(examples: list[TrainingExample]) -> tuple[list[TrainingExample], int]:
    seen: set[str] = set()
    out: list[TrainingExample] = []
    removed = 0
    for ex in examples:
        k = _key(ex)
        if k in seen:
            removed += 1
            continue
        seen.add(k)
        out.append(ex)
    return out, removed
