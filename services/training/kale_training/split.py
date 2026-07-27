"""Deterministic hash-based train/val/test split, stratified by safety_class."""
from __future__ import annotations

import hashlib
from collections import defaultdict

from kale_training.schema import TrainingExample


def _bucket(ex: TrainingExample, seed: int) -> float:
    canonical = f"{seed}:{ex.instruction}:{ex.metadata.id}"
    h = hashlib.sha256(canonical.encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def split_dataset(
    examples: list[TrainingExample], seed: int = 42,
    ratios: tuple[float, float, float] = (0.8, 0.1, 0.1),
) -> dict[str, list[TrainingExample]]:
    train_r, val_r = ratios[0], ratios[0] + ratios[1]
    strata: dict[str, list[TrainingExample]] = defaultdict(list)
    for ex in examples:
        strata[ex.metadata.safety_class].append(ex)

    out: dict[str, list[TrainingExample]] = {"train": [], "val": [], "test": []}
    for _cls, items in strata.items():
        for ex in items:
            b = _bucket(ex, seed)
            if b < train_r:
                out["train"].append(ex)
            elif b < val_r:
                out["val"].append(ex)
            else:
                out["test"].append(ex)
    return out
