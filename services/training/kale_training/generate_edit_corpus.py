"""design_edit corpus: teach the intent model to apply revisions precisely.

Every row is the EXACT runtime intent task (same system prompt, same user message builder,
same schema) for a prompt that carries a revision request. The target is what the fixed
deterministic engine produces for that prompt — so the model learns the runtime contract:
repeat the stated facts, honour the revision, change nothing else.

Rows are emitted in the Design Studio's native chat JSONL format:
    {"messages": [{"role": "system"|"user"|"assistant", "content": ...}], "family": ...}

Usage:
    python services/training/kale_training/generate_edit_corpus.py \
        --out datasets/processed/design-edit-v1 --count 1200 --seed 7
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SITE = REPO_ROOT / "apps" / "site"
sys.path.insert(0, str(SITE))

from app.services.robot_spec import (SYSTEM_DESIGN, _parse, build_robot_spec,  # noqa: E402
                                     intent_user_message)

SEASONS = ("2026-rebuilt", "2025-reefscape", "")

BASE_TEMPLATES = (
    "{size} inch REBUILT robot on {module} swerve, {intake} into a {hopper}, {shooter}, {climber}",
    "{size}x{size2} robot with {intake} and {shooter}, climber to L3",
    "swerve robot on {motor}s at {ratio} with {intake} and a {climber}",
    "west coast drivebase, {size} inches wide, with {intake} and {shooter}",
    "REEFSCAPE robot, three-stage cascade elevator to L4, {intake}, deep-cage climb",
    "defence robot on {module} modules, nothing else",
    "{size} in robot: {intake}, {hopper}, {shooter}",
    "compact {size}x{size2} bot on {module}, {intake}, single flywheel shooter, no climber",
    "robot with a turreted hooded shooter fed by a {hopper}, {intake}, {motor} drive",
    "just an {module} swerve drivebase, {size} inches square",
)

FILLERS = {
    "size": ("24", "25", "26", "27", "28", "29", "30"),
    "size2": ("24", "26", "27", "28", "30"),
    "module": ("MK4i", "MK4n", "MK5i", "MaxSwerve", "Thrifty"),
    "motor": ("Kraken X60", "Kraken X44", "NEO Vortex", "Falcon 500", "NEO"),
    "ratio": ("L1", "L2", "L3", "L2+"),
    "intake": ("dual-roller over-bumper intake", "coaxial slapdown intake",
               "under-bumper roller intake", "four-bar over-bumper intake",
               "pivoting compliant-wheel intake", "fast over-bumper intake"),
    "hopper": ("spindexer", "belt-floor hopper", "funnel-to-tower hopper",
               "twin-lane belt hopper", "oval spindexer"),
    "shooter": ("turreted hooded shooter", "dual flywheel hooded shooter",
                "stacked flywheel barrel shooter", "single flywheel shooter",
                "variable-hood flywheel shooter"),
    "climber": ("telescoping climber to L3", "deep-cage climber",
                "single pivoting hook climber", "winch-driven carriage climb"),
}

# Every edit family the deterministic engine applies. Each template is a realistic phrasing
# a team would actually type into the revision box.
EDIT_TEMPLATES = (
    "Make the frame {n1} inches wide",
    "make it {n1} by {n2}",
    "Make the frame {n1} inches long",
    "make the robot {n3} inches tall",
    "Remove the turret",
    "remove the climber",
    "remove the hopper",
    "no elevator",
    "drop the arm",
    "add a climber",
    "add an elevator",
    "{color} bumpers",
    "make the bumpers {color}",
    "Use a {stages}-stage elevator",
    "Change the drive ratio to {ratio}",
    "{ratio} ratio",
    "use {motor} motors",
    "switch to {module} modules",
    "west coast drive",
    "use {n4} inch wheels",
    "Use {n4} inch rollers on the intake",
    "{count} roller intake",
    "make the intake {n1} inches wide",
    "make the flywheel {n4} inches",
    "{n4} inch flywheel",
    "intake should stow inside the frame",
    "switch to a slapdown intake",
    "use a spindexer",
    "use a belt-floor hopper",
    "make it a fixed-angle shooter",
    "turret the shooter",
    "team {team}",
    "target weight {weight} lb",
    "no pneumatics",
    "use the PDP instead of the PDH",
    "deep cage climb",
    "{n1} inch frame, remove the turret",
    "make the frame {n1} wide and use {motor} motors",
    "{color} bumpers, {stages} stage elevator, remove the climber",
)

EDIT_FILLERS = {
    "n1": ("24", "25", "26", "27", "28", "29", "30"),
    "n2": ("24", "26", "28", "30"),
    "n3": ("26", "28", "30", "34", "40"),
    "n4": ("1.625", "2", "2.5", "3", "4", "5", "6"),
    "count": ("two", "three", "single", "dual"),
    "stages": ("1", "2", "3", "four"),
    "color": ("red", "blue", "black", "green", "orange", "purple", "white", "navy"),
    "ratio": ("L1", "L2", "L3", "L4"),
    "motor": ("Kraken X44", "Kraken X60", "NEO Vortex", "Falcon", "Minion"),
    "module": ("MK4n", "MK5i", "MaxSwerve", "MK4i"),
    "team": ("254", "1678", "7729", "8159", "33"),
    "weight": ("95", "105", "115", "120"),
}


def _fill(template: str, pool: dict, rng: random.Random) -> str:
    out = template
    for key, options in pool.items():
        while "{" + key + "}" in out:
            out = out.replace("{" + key + "}", rng.choice(options), 1)
    return out


def _truth_intent(spec: dict) -> dict:
    """The intent JSON the runtime schema wants, read off the deterministic build."""
    dt = spec.get("drivetrain") or {}
    drive = dt.get("type", "swerve")
    if drive == "swerve" and not dt.get("modules_included", True):
        drive = "swerve-ready"
    intent: dict = {"subsystems": spec.get("subsystems") or [], "drive_type": drive}
    for field, block, key in (("intake_type", "intake", "type"),
                              ("hopper_type", "hopper", "type"),
                              ("shooter_type", "shooter", "type"),
                              ("arm_type", "manipulator", "type"),
                              ("climber_type", "climber", "type"),
                              ("elevator_architecture", "elevator", "architecture")):
        blk = spec.get(block) or {}
        if blk.get("included") and blk.get(key):
            intent[field] = blk[key]
    elevator = spec.get("elevator") or {}
    if elevator.get("included") and elevator.get("stages"):
        intent["elevator_stages"] = int(elevator["stages"])
    if (spec.get("pneumatics") or {}).get("included"):
        intent["pneumatics"] = True
    return intent


def build_rows(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    rows, seen = [], set()
    attempts = 0
    while len(rows) < count and attempts < count * 8:
        attempts += 1
        base = _fill(rng.choice(BASE_TEMPLATES), FILLERS, rng)
        edit = _fill(rng.choice(EDIT_TEMPLATES), EDIT_FILLERS, rng)
        season = rng.choice(SEASONS)
        prompt = f"{base}\n\nRevision request: {edit}"
        if prompt in seen:
            continue
        seen.add(prompt)
        try:
            spec = build_robot_spec(prompt, use_model=False, season=season)
            parsed = _parse(prompt, season)
            stated = {"season": (spec.get("season") or {}).get("key", season or "2026-rebuilt"),
                      "frame_in": [parsed["width_in"], parsed["length_in"]],
                      "drive_type": parsed["drive_type"],
                      "subsystems_stated": {k: v for k, v in parsed["subsystems_stated"].items()
                                            if v is not None}}
            user = intent_user_message(prompt, stated, parsed["profile_key"])
            answer = json.dumps(_truth_intent(spec), ensure_ascii=False)
        except Exception:
            continue
        rows.append({"messages": [{"role": "system", "content": SYSTEM_DESIGN},
                                  {"role": "user", "content": user},
                                  {"role": "assistant", "content": answer}],
                     "family": "design_edit", "split": "train"})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="datasets/processed/design-edit-v1")
    ap.add_argument("--count", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rows = build_rows(args.count, args.seed)
    rng = random.Random(args.seed + 1)
    rng.shuffle(rows)
    n_val = max(24, len(rows) // 10)
    out = REPO_ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "val.jsonl", "w", encoding="utf-8") as fh:
        for row in rows[:n_val]:
            fh.write(json.dumps({**row, "split": "val"}, ensure_ascii=False) + "\n")
    with open(out / "train.jsonl", "w", encoding="utf-8") as fh:
        for row in rows[n_val:]:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {"family": "design_edit", "rows": len(rows), "train": len(rows) - n_val,
                "val": n_val, "seed": args.seed,
                "note": ("Targets generated by the deterministic engine AFTER the edit-"
                         "precision fixes; the model learns the same contract the runtime "
                         "enforces.")}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
