"""Promote user feedback into training candidates. Consent-gated, privacy-filtered, deduped.

Reads a feedback JSONL export (rows with a consent flag) and produces training examples only
for consented, secret-free, reviewed rows. No DB dependency — operates on an export file.

CLI: python -m kale_training.promote_feedback --in feedback_export.jsonl --out datasets/reviewed/feedback.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kale_training.dedup import deduplicate
from kale_training.jsonl import write_jsonl
from kale_training.schema import ExampleInput, ExampleMetadata, ExampleOutput, TrainingExample
from kale_training.secret_scan import scan_text


def promote(rows: list[dict]) -> tuple[list[TrainingExample], dict]:
    stats = {"total": len(rows), "no_consent": 0, "secrets": 0, "kept": 0}
    candidates: list[TrainingExample] = []
    for row in rows:
        if not row.get("consent_to_train"):
            stats["no_consent"] += 1
            continue
        if scan_text(json.dumps(row, default=str)):
            stats["secrets"] += 1
            continue
        ex = TrainingExample(
            instruction="Incorporate this engineer correction into the design review.",
            input=ExampleInput(
                rule_findings=[{"rule_id": row.get("rule_id", ""), "title": row.get("finding_title", "")}],
                question=row.get("question"),
            ),
            output=ExampleOutput(
                summary=row.get("edited_explanation", ""),
                recommendations=[{"title": "Corrected fix", "detail": row.get("edited_fix", "")}]
                if row.get("edited_fix") else [],
                confidence=0.5,
                limitations=["Derived from a consented user correction; requires review before training use."],
            ),
            metadata=ExampleMetadata(
                id=row.get("id", ""), source="user_feedback", license="user-consented",
                reviewed=False, safety_class="general",
            ),
        )
        candidates.append(ex)
        stats["kept"] += 1
    deduped, removed = deduplicate(candidates)
    stats["duplicates_removed"] = removed
    return deduped, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote consented feedback to training candidates")
    parser.add_argument("--in", dest="inp", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.inp).read_text().splitlines() if line.strip()]
    examples, stats = promote(rows)
    write_jsonl(examples, args.out)
    print(f"promoted {stats['kept']} of {stats['total']} feedback rows "
          f"(no-consent {stats['no_consent']}, secrets {stats['secrets']}, "
          f"dupes {stats['duplicates_removed']}) -> {args.out}")
    print("NOTE: rows are reviewed=false; a human must review before they enter a training split.")


if __name__ == "__main__":
    main()
