"""Build auditable chosen/rejected pairs from corrected v18 failures.

Input is JSONL with prompt, rejected, chosen, category and split.  This tool deliberately
refuses held-out/evaluation prompts, identical pairs and malformed JSON so benchmark answers
cannot leak into a later DPO/ORPO run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

ALLOWED_CATEGORIES = {
    "duplicate_parts", "illegal_feature", "bad_dimension", "dependency_loop",
    "pitch_mismatch", "truncated_json", "schema_violation", "missing_assembly",
}
FORBIDDEN_SPLITS = {"heldout", "held-out", "evaluation", "eval", "test"}


def _object(value: Any, field: str, line: int) -> dict[str, Any]:
    if isinstance(value, str):
        try: value = json.loads(value)
        except json.JSONDecodeError as exc: raise ValueError(f"line {line}: {field} is malformed JSON") from exc
    if not isinstance(value, dict): raise ValueError(f"line {line}: {field} must be a JSON object")
    return value


def build_pairs(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs=[]; seen=set()
    for line,row in enumerate(rows,1):
        split=str(row.get("split","train")).lower()
        if split in FORBIDDEN_SPLITS: raise ValueError(f"line {line}: held-out/evaluation data is forbidden")
        prompt=str(row.get("prompt") or "").strip()
        if not prompt: raise ValueError(f"line {line}: prompt is required")
        category=str(row.get("category") or "")
        if category not in ALLOWED_CATEGORIES: raise ValueError(f"line {line}: unknown failure category {category!r}")
        rejected=_object(row.get("rejected"),"rejected",line); chosen=_object(row.get("chosen"),"chosen",line)
        rejected_text=json.dumps(rejected,sort_keys=True,separators=(",",":"))
        chosen_text=json.dumps(chosen,sort_keys=True,separators=(",",":"))
        if rejected_text==chosen_text: raise ValueError(f"line {line}: chosen and rejected are identical")
        key=hashlib.sha256(prompt.encode()).hexdigest()
        if key in seen: raise ValueError(f"line {line}: duplicate prompt")
        seen.add(key)
        pairs.append({"prompt":prompt,"chosen":chosen_text,"rejected":rejected_text,
                      "failure_category":category,"prompt_sha256":key,"source":"corrected-v18-failure"})
    return pairs


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("input",type=Path); parser.add_argument("output",type=Path)
    args=parser.parse_args()
    rows=[json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    pairs=build_pairs(rows)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in pairs),encoding="utf-8")
    print(json.dumps({"pairs":len(pairs),"output":str(args.output),"held_out_added":False}))

if __name__ == "__main__": main()
