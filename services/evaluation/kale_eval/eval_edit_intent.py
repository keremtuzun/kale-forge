"""Held-out edit-intent evaluation: the new edit adapter vs the served model.

For each held-out design_edit row the candidate sees the exact runtime user message and its
answer is scored two ways:
  valid   — parses as one JSON object with a legal subsystems list and drive_type
  fields  — of the fields the deterministic truth fixes (subsystems set, drive_type,
            mechanism types, stages), the fraction the candidate got exactly right

Usage:
  python services/evaluation/kale_eval/eval_edit_intent.py --rows 30 \
      --adapter models/adapters/kale-edit-qwen05b-v1 \
      --served http://127.0.0.1:8001
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "site"))

from app.services.robot_spec import INTENT_SCHEMA, _first_json_object  # noqa: E402

FIELDS = ("drive_type", "intake_type", "hopper_type", "shooter_type", "arm_type",
          "climber_type", "elevator_architecture", "elevator_stages")


def score(candidate: dict | None, truth: dict) -> tuple[bool, float]:
    if not isinstance(candidate, dict):
        return False, 0.0
    subs = candidate.get("subsystems")
    valid = isinstance(subs, list) and isinstance(candidate.get("drive_type"), str)
    checks: list[bool] = [set(subs or []) == set(truth.get("subsystems") or [])]
    for field in FIELDS:
        if field in truth:
            checks.append(candidate.get(field) == truth[field])
    return valid, sum(checks) / len(checks)


def eval_adapter(rows: list[dict], adapter: str) -> list[tuple[bool, float]]:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    base = "Qwen/Qwen2.5-0.5B-Instruct"
    tokenizer = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, dtype=torch.float32)
    model = PeftModel.from_pretrained(model, str(REPO_ROOT / adapter))
    model.eval()
    out = []
    for i, row in enumerate(rows):
        ids = tokenizer.apply_chat_template(row["messages"][:-1], tokenize=True,
                                            add_generation_prompt=True, return_tensors="pt")
        if hasattr(ids, "input_ids"):
            ids = ids["input_ids"]
        with torch.no_grad():
            gen = model.generate(ids, max_new_tokens=220, do_sample=False,
                                 pad_token_id=tokenizer.eos_token_id)
        text = tokenizer.decode(gen[0][ids.shape[1]:], skip_special_tokens=True)
        truth = json.loads(row["messages"][-1]["content"])
        out.append(score(_first_json_object(text), truth))
        print(f"  adapter {i + 1}/{len(rows)} valid={out[-1][0]} fields={out[-1][1]:.2f}",
              flush=True)
    return out


def eval_served(rows: list[dict], served: str) -> list[tuple[bool, float]]:
    out = []
    for i, row in enumerate(rows):
        body = json.dumps({"system": row["messages"][0]["content"],
                           "user": row["messages"][1]["content"],
                           "json_schema": INTENT_SCHEMA, "max_tokens": 900,
                           "temperature": 0.0, "stream": False}).encode()
        request = urllib.request.Request(f"{served}/v1/generate", data=body,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
        truth = json.loads(row["messages"][-1]["content"])
        try:
            with urllib.request.urlopen(request, timeout=240) as resp:
                answer = json.loads(resp.read().decode())
            candidate = answer.get("json") or _first_json_object(answer.get("text") or "")
        except Exception as exc:
            print(f"  served {i + 1}/{len(rows)} ERROR {exc}", flush=True)
            out.append((False, 0.0))
            continue
        out.append(score(candidate, truth))
        print(f"  served {i + 1}/{len(rows)} valid={out[-1][0]} fields={out[-1][1]:.2f}",
              flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=30)
    ap.add_argument("--adapter", default="models/adapters/kale-edit-qwen05b-v1")
    ap.add_argument("--served", default="http://127.0.0.1:8001")
    ap.add_argument("--data", default="datasets/processed/design-edit-v1/val.jsonl")
    args = ap.parse_args()
    rows = []
    with open(REPO_ROOT / args.data, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    rows = rows[: args.rows]
    print(f"evaluating {len(rows)} held-out edit rows", flush=True)
    started = time.time()
    served = eval_served(rows, args.served)
    adapter = eval_adapter(rows, args.adapter)
    report = {}
    for name, results in (("served-v18", served), ("edit-adapter-0.5b", adapter)):
        report[name] = {"valid": round(sum(v for v, _ in results) / len(results), 3),
                        "field_accuracy": round(sum(f for _, f in results) / len(results), 3)}
    report["rows"] = len(rows)
    report["minutes"] = round((time.time() - started) / 60, 1)
    out_path = REPO_ROOT / "models" / "evaluations" / "edit-intent-v1.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
