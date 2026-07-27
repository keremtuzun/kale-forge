"""Dataset version manifests: row counts, source mix, content hash, lineage."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from kale_training.schema import TrainingExample


def build_manifest(
    examples: list[TrainingExample], version: str, parent_version: str | None = None,
    created_at: str | None = None,
) -> dict:
    payload = "\n".join(ex.model_dump_json() for ex in examples)
    content_hash = hashlib.sha256(payload.encode()).hexdigest()
    source_mix = dict(Counter(ex.metadata.source for ex in examples))
    safety_mix = dict(Counter(ex.metadata.safety_class for ex in examples))
    reviewed = sum(1 for ex in examples if ex.metadata.reviewed)
    return {
        "version": version,
        "parent_version": parent_version,
        "row_count": len(examples),
        "reviewed_count": reviewed,
        "source_mix": source_mix,
        "safety_mix": safety_mix,
        "content_hash": content_hash,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


def write_manifest(manifest: dict, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2))
    return path
