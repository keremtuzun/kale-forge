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
    parser.add_argument(
        "--phase", choices=("repair1", "repair2", "repair3"), default="repair1"
    )
    args = parser.parse_args()
    source = REPO_ROOT / "datasets" / "processed" / "design-v18"
    versions = {
        "repair1": "design-v18-cad-repair",
        "repair2": "design-v18-cad-repair2",
        "repair3": "design-v18-cad-repair3",
    }
    version = versions[args.phase]
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
    if args.phase == "repair1":
        source_cad = cad
    elif args.phase == "repair2":
        source_cad = focused
    else:
        # v15's main weakness was CAD-wide schema compliance (0 clean primary and
        # held-out examples), not intent. Cover every subsystem while giving the
        # four observed truncation/repetition families additional weight.
        source_cad = cad + focused + focused
    repeats = {"repair1": 4, "repair2": 7, "repair3": 2}[args.phase]

    repaired: list[dict] = []
    for repeat in range(repeats):
        for row in source_cad:
            clone = json.loads(json.dumps(row))
            clone["messages"][0]["content"] = SYSTEM_CAD
            clone["metadata"]["family"] = "cad_geometry_repair"
            clone["metadata"]["id"] += f"-repair-{repeat}"
            if args.phase == "repair3":
                assembly = json.loads(clone["messages"][-1]["content"])
                features = assembly.get("features", [])[:24]
                seen: dict[str, int] = {}
                for feature in features:
                    base = str(feature.get("n") or feature.get("t") or "part").strip()
                    seen[base] = seen.get(base, 0) + 1
                    if seen[base] > 1:
                        feature["n"] = f"{base} {seen[base]}"
                assembly["features"] = features
                clone["messages"][-1]["content"] = json.dumps(
                    assembly, separators=(",", ":")
                )
                clone["metadata"]["repair"] = "unique_names_bounded_assembly"
            repaired.append(clone)

    # Preserve the already-excellent intent behavior while making direct CAD the dominant
    # continuation signal.
    if args.phase == "repair1":
        preservation = intent[:112]
    elif args.phase == "repair2":
        preservation = intent[:60] + other[:60]
    else:
        preservation = intent[:60]
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
            "other_cad_preservation": min(60, len(other)) if args.phase == "repair2" else 0,
        },
        "split_counts": {"train": len(train_rows), "valid": len(valid_rows)},
        "sha256": hashes,
        "purpose": (
            "Closed CAD vocabulary, complete JSON termination, and no duplicate parts."
            if args.phase == "repair1"
            else (
                "Observed-failure repair for elevator, hopper, swerve, and shooter termination."
                if args.phase == "repair2"
                else "Unique editable part names and bounded complete assemblies for observed repetition failures."
            )
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
