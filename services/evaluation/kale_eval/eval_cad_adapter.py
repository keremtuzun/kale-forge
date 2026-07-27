"""Score a design adapter on the two jobs the Design Studio actually asks of it.

Budget the token limits generously. A truncated completion produces no balanced JSON and
scores identically to a model that cannot do the task at all — an earlier run of this harness
reported six failures that were all the limit, not the model.

1. **Design intent.** A team request in, one schema-valid intent JSON out. Measures whether
   the model stays inside the enumerated vocabulary and whether different requests still
   produce different robots.
2. **CAD geometry.** A request and a subsystem in, that assembly's dimensioned parts out.
   This is the capability v9 adds, and it is scored on things that are checkable rather than
   on whether the answer reads well:

   * the output parses as one JSON object with the assembly keys;
   * every feature type is one the renderer knows;
   * every structural member is a catalog stock section (a 1.375 in tube is a hallucination
     no matter how confident the model is about it);
   * pitch diameter follows from tooth count — for a 20 DP gear, PD = teeth / 20 — so the
     model cannot invent a gear that could not be cut.

Run against two adapters to get a side-by-side scorecard:

    python eval_cad_adapter.py --adapters models/adapters/kale-design-qwen3-4b-v7 \\
                                          models/adapters/kale-design-qwen3-4b-v9 \\
                               --out models/evaluations/kale-design-v9-cad.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

MODEL = "mlx-community/Qwen3-4B-Instruct-2507-4bit"

# Sections the STRUCTURE catalog actually carries, as (width, height) in inches.
# Filled from the catalog once the repo is on sys.path; the literals are only a fallback
# for scoring a saved transcript without the repo present.
STOCK_SECTIONS: set[tuple[float, float]] = {(2.0, 1.0), (1.0, 1.0), (2.0, 2.0), (1.0, 2.0),
                  (1.5, 1.5), (1.5, 0.5), (0.5, 1.5)}
KNOWN_TYPES = {
    "tube", "plate", "gusset", "shaft", "bearing", "pulley", "sprocket", "gear", "bevel",
    "belt", "wheel", "motor", "gearbox", "standoff", "polycarb", "hardstop", "hook",
    "drum", "pawl", "tensioner", "hood", "envelope", "bolts", "component", "cable", "rope",
    "slide", "actuator", "brake",
}

INTENT_PROMPTS = [
    "We are a rookie team. Design a 2026 robot on a 28 x 28 inch frame that cycles gamepieces "
    "from the floor as fast as possible.",
    "MK5i swerve on Krakens at R2 with a ground-to-feeder tunnel intake and a deep climb.",
    "27 inch REEFSCAPE robot, three-stage cascade elevator, wristed carriage arm, no shooter.",
    "30x30 west-coast drivebase on six Krakens. Defence bot, nothing else.",
    "Experienced team, 28 inch swerve, turreted dual flywheel hooded shooter and an active "
    "floor sweeper with indexer.",
    "Just an MK4n swerve module on a Kraken X60.",
    "Resource-limited team, 26x26, horizontal series-roller intake and a four-bar linkage arm.",
    "29 inch robot with a staged accelerator-and-flywheel shooter and dual telescoping winch hooks.",
]

CAD_PROMPTS = [
    ("intake", "28x28 swerve robot built around a dual-roller over-bumper intake."),
    ("elevator", "28x28 REEFSCAPE robot with a 3 stage belt-rigged cascade tower."),
    ("shooter", "27 inch robot with a turreted dual flywheel hooded shooter on Krakens."),
    ("arm", "28x28 robot with a double-jointed arm reaching 24 in."),
    ("climber", "28 inch robot with dual telescoping winch hooks."),
    ("chassis", "30x28 swerve robot, bolted 2x1 tube frame."),
    ("swerve module", "Just an MK4i swerve module on a Kraken X60 at L2."),
    ("intake", "26x26 robot with a horizontal series-roller intake."),
]

# Mechanism types whose geometry is deliberately absent from the CAD training families
# (see CAD_HELD_OUT in generate_frc_corpus). Scoring these separately is the only way to tell
# a model that learned how a mechanism goes together from one that memorised the examples:
# a memoriser scores well above and collapses here.
HELD_OUT_PROMPTS = [
    ("intake", "26x26 robot with a ground-to-feeder tunnel intake."),
    ("shooter", "28 inch robot with a variable-hood flywheel shooter."),
    ("elevator", "28x28 robot with a 2 stage telescoping box elevator."),
    ("arm", "27 inch robot with a four-bar linkage arm reaching 22 in."),
    ("climber", "29 inch robot with a winch-driven carriage climb."),
]


def _first_json(text: str) -> dict[str, Any] | None:
    """Pull the first balanced JSON object out of a completion."""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_string, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def score_cad(obj: dict[str, Any] | None) -> dict[str, Any]:
    """Everything here is a check the geometry either passes or fails; nothing is a judgement."""
    if not isinstance(obj, dict):
        return {"parsed": False}
    features = obj.get("features")
    if not isinstance(features, list) or not features:
        return {"parsed": True, "has_features": False}
    unknown, bad_section, bad_pd, positioned = [], [], [], 0
    for f in features:
        if not isinstance(f, dict):
            continue
        kind = f.get("t")
        if kind not in KNOWN_TYPES:
            unknown.append(str(kind))
        at = f.get("at")
        if isinstance(at, list) and len(at) == 3 and all(isinstance(v, (int, float)) for v in at):
            positioned += 1
        if kind == "tube":
            # Every member, including telescoping stages, now comes off the nesting ladder,
            # so there is no exemption: a section outside the catalog is a hallucination.
            sec = f.get("sec")
            try:
                stock = (isinstance(sec, list) and len(sec) == 2
                         and (round(float(sec[0]), 3), round(float(sec[1]), 3)) in STOCK_SECTIONS)
            except (TypeError, ValueError):
                stock = False
            if not stock:
                bad_section.append(sec)
        if kind == "gear" and all(k in f for k in ("teeth", "dp", "pd")):
            try:
                expected = float(f["teeth"]) / float(f["dp"])
                if abs(expected - float(f["pd"])) > 0.05:
                    bad_pd.append({"teeth": f["teeth"], "dp": f["dp"], "pd": f["pd"]})
            except (TypeError, ValueError, ZeroDivisionError):
                bad_pd.append(f.get("n"))
    return {
        "parsed": True, "has_features": True, "feature_count": len(features),
        "schema_keys": all(k in obj for k in ("id", "name", "features")),
        "unknown_types": unknown, "non_stock_sections": bad_section,
        "inconsistent_pitch_diameters": bad_pd,
        "positioned_fraction": round(positioned / len(features), 3),
        "clean": not unknown and not bad_section and not bad_pd,
    }


def score_intent(obj: dict[str, Any] | None, vocab: dict[str, set[str]]) -> dict[str, Any]:
    if not isinstance(obj, dict):
        return {"parsed": False}
    violations = []
    for field, allowed in vocab.items():
        value = obj.get(field)
        if isinstance(value, str) and value not in allowed:
            violations.append({field: value})
    subs = obj.get("subsystems")
    return {
        "parsed": True,
        "has_subsystems": isinstance(subs, list) and bool(subs),
        "enum_violations": violations,
        "clean": not violations and isinstance(subs, list) and bool(subs),
        "signature": json.dumps({k: obj.get(k) for k in sorted(vocab)}, sort_keys=True),
    }


def run(adapter: Path | None, repo: Path, max_tokens: int,
        intent_n: int, cad_n: int, intent_tokens: int = 2000,
        held_n: int = len(HELD_OUT_PROMPTS)) -> dict[str, Any]:
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    # The repo has to be on the path before any app import; the system prompt and the stock
    # sections come from the runtime module so the eval can never grade against a stale copy.
    sys.path.insert(0, str(repo / "apps" / "kale-demo"))
    from app.services.frc_cad import SYSTEM_CAD, STRUCTURE
    from app.services.robot_spec import (ARM_TYPES, CLIMBER_TYPES, ELEVATOR_TYPES,
                                         INTAKE_TYPES, SHOOTER_TYPES, SYSTEM_DESIGN,
                                         intent_user_message)
    STOCK_SECTIONS.clear()
    for key, part in STRUCTURE.items():
        if key.startswith("tube_") and "section_in" in part:
            w, h = part["section_in"]
            STOCK_SECTIONS.update({(round(w, 3), round(h, 3)), (round(h, 3), round(w, 3))})
    vocab = {"intake_type": set(INTAKE_TYPES), "shooter_type": set(SHOOTER_TYPES),
             "arm_type": set(ARM_TYPES), "climber_type": set(CLIMBER_TYPES),
             "elevator_architecture": set(ELEVATOR_TYPES),
             "drive_type": {"swerve", "swerve-ready", "west-coast", "tank"}}

    started = time.time()
    model, tokenizer = load(MODEL, adapter_path=str(adapter) if adapter else None)
    sampler = make_sampler(temp=0.3)

    def ask(system: str, user: str, limit: int) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        return generate(model, tokenizer, prompt=prompt, max_tokens=limit,
                        sampler=sampler, verbose=False)

    intent_prompts = INTENT_PROMPTS[:intent_n]
    cad_prompts = CAD_PROMPTS[:cad_n]

    intent_results, signatures = [], set()
    for prompt in intent_prompts:
        # Build the message exactly as training and the Design Studio do; scoring a model on
        # a prompt shape it was never trained on measures the harness, not the model.
        stated = {"frame_in": [28, 28], "drive_type": None, "subsystems_stated": {}}
        out = ask(SYSTEM_DESIGN, intent_user_message(prompt, stated), intent_tokens)
        result = score_intent(_first_json(out), vocab)
        result["prompt"] = prompt[:70]
        if result.get("signature"):
            signatures.add(result.pop("signature"))
        intent_results.append(result)

    def run_cad(prompts):
        out_rows = []
        for subsystem, prompt in prompts:
            out = ask(SYSTEM_CAD,
                      f"Give me the {subsystem} assembly for this robot at part level.\n\n"
                      f"Robot:\n{prompt}", max_tokens)
            result = score_cad(_first_json(out))
            result.update(subsystem=subsystem, prompt=prompt[:70])
            out_rows.append(result)
        return out_rows

    cad_results = run_cad(cad_prompts)
    held_results = run_cad(HELD_OUT_PROMPTS[:held_n])

    return {
        "adapter": str(adapter) if adapter else "base (no adapter)",
        "elapsed_s": round(time.time() - started, 1),
        "intent": {
            "prompts": len(intent_prompts),
            "valid_json": sum(1 for r in intent_results if r.get("parsed")),
            "enum_clean": sum(1 for r in intent_results if r.get("clean")),
            "distinct_designs": len(signatures),
            "detail": intent_results,
        },
        # Held-out types are the generalisation signal; the gap between the two blocks is
        # the part of the score that is recall rather than understanding.
        "cad_held_out": {
            "prompts": len(held_results),
            "valid_json": sum(1 for r in held_results if r.get("parsed")),
            "schema_clean": sum(1 for r in held_results if r.get("clean")),
            "mean_features": round(
                sum(r.get("feature_count", 0) for r in held_results) / max(len(held_results), 1), 1),
            "detail": held_results,
        },
        "cad": {
            "prompts": len(cad_prompts),
            "valid_json": sum(1 for r in cad_results if r.get("parsed")),
            "has_features": sum(1 for r in cad_results if r.get("has_features")),
            "schema_clean": sum(1 for r in cad_results if r.get("clean")),
            "mean_features": round(
                sum(r.get("feature_count", 0) for r in cad_results) / max(len(cad_results), 1), 1),
            "detail": cad_results,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapters", nargs="+", required=True)
    # The repo root is three levels up from services/evaluation/kale_eval, but the script is
    # often run from a copy on local disk, where that walk runs off the top of the tree.
    here = Path(__file__).resolve()
    default_repo = str(here.parents[3]) if len(here.parents) > 3 else str(Path.cwd())
    parser.add_argument("--repo", default=default_repo)
    parser.add_argument("--max-tokens", type=int, default=2600)
    parser.add_argument("--intent-prompts", type=int, default=len(INTENT_PROMPTS))
    parser.add_argument("--cad-prompts", type=int, default=len(CAD_PROMPTS))
    parser.add_argument("--held-prompts", type=int, default=len(HELD_OUT_PROMPTS))
    parser.add_argument("--intent-tokens", type=int, default=2000)
    parser.add_argument("--out")
    args = parser.parse_args()

    repo = Path(args.repo)
    report = {"model": MODEL, "runs": []}
    for name in args.adapters:
        adapter = None if name in ("base", "none") else Path(name)
        print(f"→ {name}", flush=True)
        result = run(adapter, repo, args.max_tokens, args.intent_prompts,
                     args.cad_prompts, args.intent_tokens, args.held_prompts)
        report["runs"].append(result)
        print(json.dumps({k: v for k, v in result.items() if k != "runs"},
                         indent=2, default=str)[:1200], flush=True)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
