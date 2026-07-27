"""Evaluate a Kale design model on held-out design prompts using measurable contracts."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import mean
from typing import Any

from mlx_lm import load, stream_generate
from mlx_lm.sample_utils import make_repetition_penalty


REQUIRED = {
    "design_type", "title", "requirements", "assumptions", "architecture", "interfaces",
    "calculations", "cad_plan", "manufacturing", "verification", "risks", "questions", "limitations",
}
DETAIL_TERMS = {
    "datum", "tolerance", "clearance", "interface", "mate", "assembly", "load", "service",
    "verification", "manufacturing", "collision", "fastener", "wiring", "risk",
}
UNSAFE_CLAIMS = ("is certified", "fully compliant", "guaranteed safe", "fabrication-ready", "perfect design")


def _generate_first_json(model: Any, tokenizer: Any, prompt: str, max_tokens: int,
                         processors: list[Any]) -> str:
    """Stop decoding after one balanced top-level JSON object."""
    text = ""
    processed = 0
    started = False
    depth = 0
    in_string = False
    escaped = False
    for response in stream_generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=max_tokens,
        logits_processors=processors,
    ):
        text += response.text
        while processed < len(text):
            char = text[processed]
            processed += 1
            if not started:
                if char == "{":
                    started = True
                    depth = 1
                continue
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[:processed]
    return text


def _extract_json(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _score(prompt: str, text: str) -> dict[str, float]:
    obj = _extract_json(text)
    if obj is None:
        return {"json_valid": 0.0, "contract": 0.0, "detail": 0.0, "actionability": 0.0,
                "uncertainty": 0.0, "precision": 0.0, "overall": 0.0}
    flat = json.dumps(obj).lower()
    contract = len(REQUIRED.intersection(obj)) / len(REQUIRED)
    detail = len([term for term in DETAIL_TERMS if term in flat]) / len(DETAIL_TERMS)
    list_fields = ("interfaces", "calculations", "manufacturing", "verification", "risks", "questions")
    actionability = mean(min(len(obj.get(key, [])) / (3 if key != "verification" else 4), 1.0) for key in list_fields)
    uncertainty = mean([
        1.0 if len(obj.get("assumptions", [])) >= 2 else 0.0,
        1.0 if len(obj.get("questions", [])) >= 2 else 0.0,
        1.0 if obj.get("limitations") else 0.0,
        1.0 if any(word in flat for word in ("confirm", "pending", "assumption", "verify")) else 0.0,
    ])
    prompt_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", prompt))
    number_recall = len([number for number in prompt_numbers if number in flat]) / max(len(prompt_numbers), 1)
    no_unsafe = 0.0 if any(claim in flat for claim in UNSAFE_CLAIMS) else 1.0
    precision = mean([number_recall, no_unsafe])
    overall = mean([contract, detail, actionability, uncertainty, precision])
    return {"json_valid": 1.0, "contract": contract, "detail": detail, "actionability": actionability,
            "uncertainty": uncertainty, "precision": precision, "overall": overall}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--adapter")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--max-tokens", type=int, default=2200)
    parser.add_argument("--repetition-penalty", type=float, default=1.02)
    parser.add_argument("--repetition-context", type=int, default=64)
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    rows = [json.loads(line) for line in Path(args.dataset).read_text().splitlines() if line.strip()][:args.limit]
    model, tokenizer = load(args.model, adapter_path=args.adapter)
    records = []
    for index, row in enumerate(rows, 1):
        messages = row["messages"][:2]
        processors = [make_repetition_penalty(args.repetition_penalty, args.repetition_context)]
        attempts = []
        for attempt in range(args.attempts):
            attempt_messages = list(messages)
            if attempt:
                attempt_messages.append({
                    "role": "user",
                    "content": (
                        "The prior attempt was invalid or incomplete. Return one concise JSON object only, under 1400 tokens. "
                        "Include every required top-level key exactly once: " + ", ".join(sorted(REQUIRED)) + ". "
                        "Do not repeat objects or list items; preserve unknowns as assumptions, questions, or pending calculations."
                    ),
                })
            prompt = tokenizer.apply_chat_template(attempt_messages, tokenize=False, add_generation_prompt=True)
            candidate = _generate_first_json(model, tokenizer, prompt, args.max_tokens, processors)
            candidate_score = _score(messages[-1]["content"], candidate)
            attempts.append({"response": candidate, "scores": candidate_score})
            if candidate_score["json_valid"] and candidate_score["contract"] >= 0.99:
                break
        best = max(attempts, key=lambda item: item["scores"]["overall"])
        text, score = best["response"], best["scores"]
        records.append({"id": row.get("metadata", {}).get("id", str(index)), "prompt": messages[-1]["content"],
                        "response": text, "scores": score, "attempt_count": len(attempts)})
        print(f"[{index}/{len(rows)}] json={score['json_valid']:.0f} overall={score['overall']:.3f}", flush=True)

    metrics = {key: mean(record["scores"][key] for record in records) for key in records[0]["scores"]}
    result = {"model": args.model, "adapter": args.adapter, "samples": len(records), "metrics": metrics,
              "records": records}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
