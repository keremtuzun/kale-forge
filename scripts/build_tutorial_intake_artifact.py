"""Build the week-3/week-4 tutorial intake as Kale CAD and FeatureScript artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "apps" / "site"
sys.path.insert(0, str(SITE))

from app.services.cad_contract import structural_report, transmission_report  # noqa: E402
from app.services.frc_featurescript import build_featurescript  # noqa: E402
from app.services.robot_spec import build_robot_spec  # noqa: E402


DEFAULT_PROMPT = (
    "27 x 27 inch 2026 REBUILT robot on MK4i swerve with a dual-roller "
    "over-bumper intake, circular spindexer, turreted hooded shooter, and "
    "telescoping climber"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts" / "tutorial-intake-2026")
    args = parser.parse_args()

    spec = build_robot_spec(args.prompt, use_model=False, season="2026-rebuilt")
    structure = structural_report(spec["cad"])
    transmission = transmission_report(spec["cad"])
    if not structure["ok"] or not transmission["ok"]:
        print(json.dumps({"structural": structure, "transmission": transmission}, indent=2))
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    intake = next(a for a in spec["cad"]["assemblies"] if a["id"] == "intake")
    summary = {
        "source_lessons": ["3.hafta", "4.hafta"],
        "method": [
            "master pivot and roller datums",
            "mirrored side plates",
            "bearing-supported shafts",
            "outboard drive plate and standoffs",
            "pitch-derived pulley centre distance",
            "belt path and tensioner",
            "tangent indexer handoff",
            "assembly and interference verification",
        ],
        "prompt": args.prompt,
        "design_name": spec["name"],
        "intake_feature_count": len(intake["features"]),
        "structural": structure,
        "transmission": transmission,
        "rule_report": spec["rule_report"],
    }
    (args.output / "robot-spec.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (args.output / "tutorial-intake.featurescript").write_text(
        build_featurescript(spec, spec["name"]), encoding="utf-8"
    )
    (args.output / "verification.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), **summary}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
