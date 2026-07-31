"""Build a small, CAD-heavy continuation corpus from the reviewed v18 data.

The repair pass reweights direct geometry targets and updates their system prompt with the
closed feature vocabulary and no-duplication rule. Held-out evaluation mechanisms remain
absent because only v18's training split is reused.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "kale-demo"))

from app.services.frc_cad import SYSTEM_CAD  # noqa: E402


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_rows(path: Path, rows: list[dict]) -> str:
    payload = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("repair1", "repair2"), default="repair1")
    args = parser.parse_args()
    source = REPO_ROOT / "datasets" / "processed" / "design-v18"
    version = "design-v18-cad-repair" if args.phase == "repair1" else "design-v18-cad-repair2"
    output = REPO_ROOT / "datasets" / "processed" / version
    output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(18002)

    source_train = read_rows(source / "train.jsonl")
    cad = [row for row in source_train if row["metadata"]["family"] == "cad_geometry"]
    intent = [row for row in source_train if row["metadata"]["family"] == "design_intent"]
    rng.shuffle(cad)
    rng.shuffle(intent)

    focus_ids = {"elevator", "hopper", "swerve_0", "shooter"}
    focused, other = [], []
    for row in cad:
        assembly = json.loads(row["messages"][-1]["content"])
        (focused if assembly.get("id") in focus_ids else other).append(row)
    source_cad = cad if args.phase == "repair1" else focused
    repeats = 4 if args.phase == "repair1" else 7

    repaired: list[dict] = []
    for repeat in range(repeats):
        for row in source_cad:
            clone = json.loads(json.dumps(row))
            clone["messages"][0]["content"] = SYSTEM_CAD
            clone["metadata"]["family"] = "cad_geometry_repair"
            clone["metadata"]["id"] += f"-repair-{repeat}"
            repaired.append(clone)

    # Preserve the already-excellent intent behavior while making direct CAD the dominant
    # continuation signal.
    preservation = intent[:112] if args.phase == "repair1" else intent[:60] + other[:60]
    mixed = repaired + preservation
    rng.shuffle(mixed)
    split = max(1, int(len(mixed) * 0.92))
    train_rows, valid_rows = mixed[:split], mixed[split:]

    hashes = {
        "train": write_rows(output / "train.jsonl", train_rows),
        "valid": write_rows(output / "valid.jsonl", valid_rows),
    }
    manifest = {
        "version": version,
        "seed": 18002,
        "source": "design-v18 train split only",
        "held_out_geometry_added": False,
        "family_counts": {
            "cad_geometry_repair": len(repaired),
            "design_intent": 112 if args.phase == "repair1" else 60,
            "other_cad_preservation": 0 if args.phase == "repair1" else min(60, len(other)),
        },
        "split_counts": {"train": len(train_rows), "valid": len(valid_rows)},
        "sha256": hashes,
        "purpose": (
            "Closed CAD vocabulary, complete JSON termination, and no duplicate parts."
            if args.phase == "repair1"
            else "Observed-failure repair for elevator, hopper, swerve, and shooter termination."
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
