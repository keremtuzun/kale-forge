"""Evaluate a PEFT design adapter on a CUDA host using the v15 CAD scorecard.

This is the Transformers/PEFT equivalent of eval_cad_adapter.py, intended for the
free Lightning T4 used to train v18.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from eval_cad_adapter import (
    CAD_PROMPTS,
    HELD_OUT_PROMPTS,
    INTENT_PROMPTS,
    STOCK_SECTIONS,
    _first_json,
    score_cad,
    score_intent,
)

MODEL = "Qwen/Qwen3-4B-Instruct-2507"


def run(adapter: Path, repo: Path, max_tokens: int = 2600) -> dict:
    sys.path.insert(0, str(repo / "apps" / "kale-demo"))
    from app.services.frc_cad import STRUCTURE, SYSTEM_CAD
    from app.services.frc_season import SEASONS, frame_budget
    from app.services.robot_spec import (
        ARM_TYPES,
        CLIMBER_TYPES,
        ELEVATOR_TYPES,
        HOPPER_TYPES,
        INTAKE_TYPES,
        SHOOTER_TYPES,
        SYSTEM_DESIGN,
        intent_user_message,
    )

    STOCK_SECTIONS.clear()
    for key, part in STRUCTURE.items():
        if key.startswith("tube_") and "section_in" in part:
            width, height = part["section_in"]
            STOCK_SECTIONS.update({
                (round(width, 3), round(height, 3)),
                (round(height, 3), round(width, 3)),
            })
    vocab = {
        "intake_type": set(INTAKE_TYPES),
        "hopper_type": set(HOPPER_TYPES),
        "shooter_type": set(SHOOTER_TYPES),
        "arm_type": set(ARM_TYPES),
        "climber_type": set(CLIMBER_TYPES),
        "elevator_architecture": set(ELEVATOR_TYPES),
        "drive_type": {"swerve", "swerve-ready", "west-coast", "tank"},
    }

    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    base = AutoModelForCausalLM.from_pretrained(
        MODEL, quantization_config=quantization, device_map="auto"
    )
    model = PeftModel.from_pretrained(base, str(adapter))
    model.eval()

    def ask(system: str, user: str, limit: int) -> str:
        inputs = tokenizer.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(model.device)
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=limit,
                do_sample=True,
                temperature=0.3,
                top_p=0.9,
                pad_token_id=tokenizer.eos_token_id,
            )
        return tokenizer.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)

    started = time.time()
    intent_results, signatures = [], set()
    for season_key, prompt in INTENT_PROMPTS:
        season = SEASONS[season_key]
        budget = frame_budget(season, *season["frame"])
        stated = {
            "season": season_key,
            "frame_in": [budget["width_in"], budget["length_in"]],
            "drive_type": None,
            "subsystems_stated": {},
        }
        output = ask(SYSTEM_DESIGN, intent_user_message(prompt, stated, season_key), 2000)
        result = score_intent(_first_json(output), vocab)
        result.update(prompt=prompt[:70], season=season_key)
        if result.get("signature"):
            signatures.add(result.pop("signature"))
        intent_results.append(result)

    def evaluate_cad(prompts):
        results = []
        for subsystem, season_key, prompt in prompts:
            output = ask(
                SYSTEM_CAD,
                f"Give me the {subsystem} assembly for this robot at part level.\n\n"
                f"Season: {SEASONS[season_key]['label']}\n\nRobot:\n{prompt}",
                max_tokens,
            )
            result = score_cad(_first_json(output))
            result.update(subsystem=subsystem, season=season_key, prompt=prompt[:70])
            results.append(result)
        return results

    cad_results = evaluate_cad(CAD_PROMPTS)
    held_results = evaluate_cad(HELD_OUT_PROMPTS)
    return {
        "model": MODEL,
        "adapter": str(adapter),
        "elapsed_s": round(time.time() - started, 1),
        "intent": {
            "prompts": len(intent_results),
            "valid_json": sum(bool(row.get("parsed")) for row in intent_results),
            "enum_clean": sum(bool(row.get("clean")) for row in intent_results),
            "distinct_designs": len(signatures),
            "detail": intent_results,
        },
        "cad_held_out": {
            "prompts": len(held_results),
            "valid_json": sum(bool(row.get("parsed")) for row in held_results),
            "schema_clean": sum(bool(row.get("clean")) for row in held_results),
            "mean_features": round(
                sum(row.get("feature_count", 0) for row in held_results) / len(held_results), 1
            ),
            "detail": held_results,
        },
        "cad": {
            "prompts": len(cad_results),
            "valid_json": sum(bool(row.get("parsed")) for row in cad_results),
            "has_features": sum(bool(row.get("has_features")) for row in cad_results),
            "schema_clean": sum(bool(row.get("clean")) for row in cad_results),
            "mean_features": round(
                sum(row.get("feature_count", 0) for row in cad_results) / len(cad_results), 1
            ),
            "detail": cad_results,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-tokens", type=int, default=2600)
    args = parser.parse_args()
    report = run(Path(args.adapter), Path(args.repo), args.max_tokens)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "detail"}, indent=2))


if __name__ == "__main__":
    main()
