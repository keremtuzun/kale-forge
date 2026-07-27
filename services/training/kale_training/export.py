"""Assemble a versioned dataset from synthetic + reviewed examples with manifest + splits.

CLI: python -m kale_training.export --version v1 --inputs datasets/synthetic/v0/synthetic.jsonl
"""
from __future__ import annotations

import argparse
from pathlib import Path

from kale_training.dedup import deduplicate
from kale_training.jsonl import read_jsonl, write_jsonl
from kale_training.manifest import build_manifest, write_manifest
from kale_training.schema import TrainingExample
from kale_training.secret_scan import scan_example
from kale_training.split import split_dataset

REPO_ROOT = Path(__file__).resolve().parents[3]
PROCESSED = REPO_ROOT / "datasets" / "processed"
QUARANTINE = REPO_ROOT / "datasets" / "raw" / "quarantine"

ALLOWED_LICENSES = {"internal", "user-consented", "CC0", "CC-BY", "MIT", "Apache-2.0"}


def build_dataset(version: str, input_paths: list[str], seed: int = 42,
                  require_reviewed: bool = False) -> dict:
    raw: list[TrainingExample] = []
    for p in input_paths:
        raw.extend(read_jsonl(p))

    kept: list[TrainingExample] = []
    quarantined: list[dict] = []
    for ex in raw:
        row = ex.model_dump()
        secrets = scan_example(row)
        if secrets:
            quarantined.append({"id": ex.metadata.id, "reasons": secrets})
            continue
        if ex.metadata.license not in ALLOWED_LICENSES:
            quarantined.append({"id": ex.metadata.id, "reasons": [{"type": "license", "value": ex.metadata.license}]})
            continue
        if require_reviewed and not ex.metadata.reviewed:
            continue
        kept.append(ex)

    deduped, removed = deduplicate(kept)
    splits = split_dataset(deduped, seed=seed)

    out_dir = PROCESSED / version
    out_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in splits.items():
        write_jsonl(rows, out_dir / f"{split}.jsonl")
    write_jsonl(deduped, out_dir / "dataset.jsonl")

    if quarantined:
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        import json

        (QUARANTINE / f"{version}-quarantine.json").write_text(json.dumps(quarantined, indent=2))

    manifest = build_manifest(deduped, version)
    manifest["duplicates_removed"] = removed
    manifest["quarantined"] = len(quarantined)
    manifest["split_counts"] = {k: len(v) for k, v in splits.items()}
    write_manifest(manifest, out_dir)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble a Kale training dataset version")
    parser.add_argument("--version", required=True)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--require-reviewed", action="store_true")
    args = parser.parse_args()
    manifest = build_dataset(args.version, args.inputs, args.seed, args.require_reviewed)
    print(f"dataset {args.version}: {manifest['row_count']} rows "
          f"(removed {manifest['duplicates_removed']} dupes, quarantined {manifest['quarantined']})")
    print(f"  splits: {manifest['split_counts']}")
    print(f"  content_hash: {manifest['content_hash']}")


if __name__ == "__main__":
    main()
