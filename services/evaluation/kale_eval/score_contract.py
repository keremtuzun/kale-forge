"""Score the CAD contract on the defects it is supposed to stop, v15 deployed vs v18 hardened.

Why this exists rather than another adapter scorecard: the Design Studio that users reach at
kaleai.vercel.app never samples a model for geometry (`apps/site/api/studio.py` builds every
design with `use_model=False`).  Geometry comes from the deterministic compiler, so the thing
that decides whether a served design is trustworthy is the contract standing between a spec
and the compiler — not the adapter's token-level accuracy.

The measurement has two halves, and both matter:

* **Recall** — of the defect classes the v18 evaluation actually produced, how many does each
  contract stop before geometry compiles?  Cases marked `observed` are taken from real
  recorded generations in `models/evaluations/kale-design-v18-cad-repair-50.json`; the
  repetition loops reproduce the feature the model repeated until it exhausted its budget.
* **False positives** — how many legitimate designs does each contract reject?  A stricter
  validator that refuses real work is a regression, so recall is only meaningful beside this.

Run:

    python -m kale_eval.score_contract --out models/evaluations/kale-contract-v18-vs-v15.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "apps" / "site"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.cad_contract import (  # noqa: E402
    normalize_cad as hardened_normalize,
    validate_cad as hardened_validate,
    validate_parametric_design as hardened_validate_design,
)
from app.services.frc_season import SELECTABLE  # noqa: E402
from app.services.robot_spec import build_robot_spec  # noqa: E402
from kale_eval.contract_baseline_v15 import (  # noqa: E402
    normalize_cad as baseline_normalize,
    validate_cad as baseline_validate,
)

REFERENCE_PROMPT = "28 inch swerve with intake hopper shooter elevator and climber"

GOOD_PROMPTS = [
    "We are a rookie team. Design a robot on a 27 x 27 inch frame that cycles gamepieces from the floor as fast as possible.",
    "MK5i swerve on Krakens at R2 with a ground-to-feeder tunnel intake and a deep climb.",
    "27 inch REEFSCAPE robot, three-stage cascade elevator, wristed carriage arm, no shooter.",
    "27x27 west-coast drivebase on Krakens. Defence bot, nothing else.",
    "Experienced team, 27 inch swerve, turreted dual flywheel hooded shooter, circular spindexer and an active floor sweeper with indexer.",
    "Just an MK4n swerve module on a Kraken X60.",
    "Resource-limited team, 26x26, horizontal series-roller intake and a four-bar linkage arm.",
    "26x29 robot with a staged accelerator-and-flywheel shooter, a twin-lane belt hopper and dual telescoping winch hooks.",
    "Design a 28 x 28 inch REBUILT robot with an over-bumper intake and a hooded shooter.",
    "28x28 REEFSCAPE robot with a 3 stage belt-rigged cascade tower.",
    "30x28 swerve robot, bolted 2x1 tube frame.",
    "26x26 robot with a ground-to-feeder tunnel intake.",
    "28 inch robot with a variable-hood flywheel shooter.",
    "27x27 REBUILT robot with a serpentine tunnel indexer.",
    "26x29 REBUILT robot with a paddle-wheel agitator hopper.",
    "29 inch robot with a winch-driven carriage climb.",
    "28x28 robot with a double-jointed arm reaching 24 in.",
    "27 inch robot with dual telescoping winch hooks.",
    "Build me a 48 x 48 inch robot with everything on it.",
    "tiny 12x12 robot",
    "robot",
    "a robot with an elevator an arm a shooter an intake a hopper a climber and a turret",
    "40x40 tank drive with a 10 stage elevator reaching 300 inches",
    "1x1 inch nano robot with a 99 stage elevator",
]


def _find(cad: dict[str, Any], kind: str):
    for assembly in cad["assemblies"]:
        for feature in assembly["features"]:
            if feature.get("t") == kind:
                return assembly, feature
    raise AssertionError(f"no {kind!r} feature in the reference robot")


def _repeat(kind: str, times: int, label: str) -> Callable[[dict], None]:
    """Reproduce a degenerate repetition loop: one body emitted over and over in one place."""
    def mutate(cad: dict[str, Any]) -> None:
        assembly, feature = _find(cad, kind)
        for index in range(times):
            clone = deepcopy(feature)
            clone["n"] = f"{label} {index + 1}"
            assembly["features"].append(clone)
    return mutate


# Each case is (id, origin, what it is, mutation).  `observed` means the defect was produced by
# the v18 adapter in a recorded evaluation run; `synthetic` means it is a class the contract
# claims to cover that the recorded run happened not to hit.
CASES: list[tuple[str, str, str, Callable[[dict], None]]] = [
    ("repetition-loop-elevator", "observed",
     "primary CAD case 1: 'carriage rail plate' repeated to the 2600-token budget",
     _repeat("plate", 14, "carriage rail plate")),
    ("repetition-loop-hopper", "observed",
     "primary CAD case 3: 'hopper guide post' repeated to the 2600-token budget",
     _repeat("plate", 12, "hopper guide post")),
    ("repetition-loop-swerve", "observed",
     "primary CAD case 7: 'module standoff' repeated to the 2600-token budget",
     _repeat("plate", 16, "module standoff")),
    ("gear-pitch-mismatch", "observed",
     "held-out arm: 24 teeth at 20 DP declared pd 0.96 (a 1.2 in pitch circle)",
     lambda cad: _find(cad, "gear")[1].update(teeth=24, dp=20.0, pd=0.96)),
    ("non-stock-section", "observed",
     "held-out hopper: a 24 x 1 in structural section that no supplier carries",
     lambda cad: _find(cad, "tube")[1].update(sec=[24.0, 1.0])),
    ("illegal-feature-rocker", "observed",
     "held-out shooter: seven 'rocker' features, a type no renderer knows",
     lambda cad: _find(cad, "plate")[1].update(t="rocker")),
    ("illegal-feature-winch-ratchet", "observed",
     "primary shooter: 'winch ratchet' and 'guard' outside the closed vocabulary",
     lambda cad: _find(cad, "plate")[1].update(t="winch ratchet")),
    ("sprocket-pitch-mismatch", "synthetic",
     "a 16-tooth #25 sprocket declaring a 0.9 in pitch circle instead of 1.281",
     lambda cad: _find(cad, "sprocket")[1].update(teeth=16, pitch_in=0.25, pd=0.9)),
    ("unbounded-repetition", "synthetic",
     "a repeat count of 4096 that would compile into a hang",
     lambda cad: _find(cad, "tube")[1].update(rep={"n": 4096, "step": [1, 0, 0]})),
    ("repetition-without-step", "synthetic",
     "eight copies of a part with a zero step, all landing on top of each other",
     lambda cad: _find(cad, "tube")[1].update(rep={"n": 8, "step": [0, 0, 0]})),
    ("negative-dimension", "synthetic",
     "a negative tube length",
     lambda cad: _find(cad, "tube")[1].update(len=-2.0)),
    ("nested-bad-dimension", "synthetic",
     "a non-finite number nested inside a feature rather than at a known key",
     lambda cad: _find(cad, "plate")[1].update(size=[20.0, float("inf"), 3.0])),
    ("oversize-dimension", "synthetic",
     "a 5000 in member, past the compiler's safety bound",
     lambda cad: _find(cad, "tube")[1].update(len=5000.0)),
    ("malformed-section", "synthetic",
     "a section given as strings, which the deployed validator coerced with a bare float()",
     lambda cad: _find(cad, "tube")[1].update(sec=["two", "one"])),
    ("malformed-length", "synthetic",
     "a tube length given as prose",
     lambda cad: _find(cad, "tube")[1].update(len="24 inches")),
    ("malformed-repetition-step", "synthetic",
     "a repetition step containing a string",
     lambda cad: _find(cad, "tube")[1].update(rep={"n": 4, "step": ["x", 0, 0]})),
    ("duplicate-assembly-id", "synthetic",
     "two assemblies sharing one id, so later edits address the wrong one",
     lambda cad: cad["assemblies"].append(deepcopy(cad["assemblies"][0]))),
    ("duplicate-assembly-content", "synthetic",
     "one mechanism emitted twice under different ids, compiled into the same space",
     lambda cad: cad["assemblies"].append(
         {**deepcopy(cad["assemblies"][5]), "id": "intake_copy"})),
]

# Parametric-design defects.  The deployed site never ran this validator at all: the v15 bundle
# called compact_design_spec() and used the result unchecked.
DESIGN_CASES: list[tuple[str, str, Callable[[dict], None]]] = [
    ("design-dependency-cycle", "a mechanism graph that depends on itself",
     lambda d: d["mechanisms"].append({"id": "intake", "kind": "hopper",
                                       "architecture": "belt", "depends_on": ["intake"]})),
    ("design-duplicate-node", "two mechanisms sharing one node id",
     lambda d: d["mechanisms"].append(deepcopy(d["mechanisms"][0]))),
    ("design-dangling-dependency", "a mechanism depending on something that does not exist",
     lambda d: d["mechanisms"][0].update(depends_on=["turbo_encabulator"])),
    ("design-frame-out-of-range", "a 96 in frame, outside any legal perimeter budget",
     lambda d: d["frame"].update(width_in=96.0)),
]


def _catches(validate, normalize, cad: dict[str, Any]) -> tuple[bool, str]:
    """A contract 'catches' a defect only by reporting it.  Raising is a 500, not a catch."""
    try:
        errors = validate(normalize(cad))
    except Exception as exc:  # noqa: BLE001
        return False, f"crashed: {type(exc).__name__}"
    return bool(errors), (errors[0][:100] if errors else "no error reported")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="models/evaluations/kale-contract-v18-vs-v15.json")
    args = parser.parse_args()

    reference = build_robot_spec(REFERENCE_PROMPT, use_model=False)
    reference_cad = reference["cad"]
    reference_design = reference["parametric_design"]

    rows = []
    for case_id, origin, description, mutation in CASES:
        cad = deepcopy(reference_cad)
        mutation(cad)
        base_caught, base_note = _catches(baseline_validate, baseline_normalize, cad)
        hard_caught, hard_note = _catches(hardened_validate, hardened_normalize, cad)
        rows.append({"case": case_id, "origin": origin, "defect": description,
                     "layer": "cad",
                     "v15_deployed": {"caught": base_caught, "detail": base_note},
                     "v18_hardened": {"caught": hard_caught, "detail": hard_note}})

    for case_id, description, mutation in DESIGN_CASES:
        design = deepcopy(reference_design)
        mutation(design)
        hard_caught, hard_note = _catches(hardened_validate_design, lambda d: d, design)
        rows.append({"case": case_id, "origin": "synthetic", "defect": description,
                     "layer": "parametric_design",
                     # The v15 bundle imported no parametric validator, so nothing could be caught.
                     "v15_deployed": {"caught": False, "detail": "validator not wired into the site"},
                     "v18_hardened": {"caught": hard_caught, "detail": hard_note}})

    # False positives: every contract must still pass real designs.
    seasons = [""] + [s["key"] if isinstance(s, dict) else s for s in SELECTABLE]
    good, base_fp, hard_fp = 0, 0, 0
    for prompt, season in itertools.product(GOOD_PROMPTS, seasons):
        spec = build_robot_spec(prompt, use_model=False, season=season)
        good += 1
        if _catches(baseline_validate, baseline_normalize, spec["cad"])[0]:
            base_fp += 1
        if _catches(hardened_validate, hardened_normalize, spec["cad"])[0]:
            hard_fp += 1

    total = len(rows)
    base_caught = sum(1 for r in rows if r["v15_deployed"]["caught"])
    hard_caught = sum(1 for r in rows if r["v18_hardened"]["caught"])
    observed = [r for r in rows if r["origin"] == "observed"]
    report = {
        "measurement": "CAD contract defect recall, v15 deployed vs v18 hardened",
        "note": ("Scored offline against recorded v18 evaluation failures and the deterministic "
                 "compiler. This measures the contract that guards kaleai.vercel.app, not a "
                 "fresh sampling run of the adapter."),
        "reference_prompt": REFERENCE_PROMPT,
        "defects_total": total,
        "v15_deployed": {"caught": base_caught, "recall": round(base_caught / total, 3),
                         "false_positives": base_fp},
        "v18_hardened": {"caught": hard_caught, "recall": round(hard_caught / total, 3),
                         "false_positives": hard_fp},
        "observed_defects": {
            "total": len(observed),
            "v15_deployed": sum(1 for r in observed if r["v15_deployed"]["caught"]),
            "v18_hardened": sum(1 for r in observed if r["v18_hardened"]["caught"]),
        },
        "legitimate_designs_checked": good,
        "improvement_factor": round(hard_caught / base_caught, 2) if base_caught else None,
        "cases": rows,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"defects scored         {total}")
    print(f"v15 deployed caught    {base_caught}/{total}  "
          f"(recall {base_caught / total:.0%}, {base_fp} false positives)")
    print(f"v18 hardened caught    {hard_caught}/{total}  "
          f"(recall {hard_caught / total:.0%}, {hard_fp} false positives)")
    print(f"observed v18 defects   v15 {report['observed_defects']['v15_deployed']}"
          f"/{len(observed)} -> v18 {report['observed_defects']['v18_hardened']}/{len(observed)}")
    print(f"legitimate designs     {good} checked, both contracts must pass all")
    print(f"improvement factor     {report['improvement_factor']}x")
    print(f"\nwrote {out}")
    for row in rows:
        if not row["v18_hardened"]["caught"]:
            print(f"  STILL UNCAUGHT  {row['case']}: {row['v18_hardened']['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
