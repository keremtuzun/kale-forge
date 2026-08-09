"""JSONL read/write/validate for training examples."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from kale_training.schema import TrainingExample


def write_jsonl(examples: list[TrainingExample], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(ex.model_dump_json() + "\n")
    return len(examples)


def read_jsonl(path: str | Path) -> Iterator[TrainingExample]:
    with Path(path).open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield TrainingExample.model_validate_json(line)


def validate_file(path: str | Path) -> list[str]:
    """Return a list of per-line error strings ([] if all rows valid)."""
    errors: list[str] = []
    with Path(path).open() as fh:
        for i, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                TrainingExample.model_validate_json(line)
            except (ValidationError, json.JSONDecodeError) as exc:
                errors.append(f"line {i}: {exc}")
    return errors
