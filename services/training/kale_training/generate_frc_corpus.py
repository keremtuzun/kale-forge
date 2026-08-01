"""Generate the FRC parts-and-techniques SFT corpus.

Every example is derived from the SAME catalog the runtime uses
(`app.services.frc_parts` and `app.services.frc_robot_knowledge`), so the model is trained
on exactly the vocabulary, part numbers, ratios and design rules the Design Studio will hand
it at inference time.  Training data and runtime knowledge cannot drift apart.

Ten example families:

* ``design_intent`` — the real inference task: a team request in, one schema-valid design
  intent JSON out.  This is the family that stops the model from answering every prompt with
  the same robot.
* ``season_rules``  — the field and the construction rules as *derivations*: what the number
  is, where it comes from, and what it forces the design to do.
* ``strategy``      — game analysis: which archetype, and the scoring arithmetic behind it.
* ``binder``        — technical-binder subsystem write-ups: requirement, options considered,
  the calculation that chose between them, how it was validated, what is still open.
* ``parts_qa``      — dimensioned facts about every catalog part.
* ``electrical``    — channel, breaker and wire-gauge planning off the power budget.
* ``technique``     — why a build technique exists and what it prevents.
* ``diagnosis``     — a competition symptom in, the likely cause and fix out.
* ``cad_geometry``  — a request and one subsystem in, that subsystem's assembly out as
  dimensioned parts in Kale's CAD schema.  This is the family that takes the model from
  naming a mechanism to describing the parts it is made of.
* ``cad_qa``        — cut lists, travel, exit velocity, winch torque and joint geometry,
  every number computed from the same CAD tree rather than remembered.

Every family is season-conditioned, and that is the point of this revision.  The same request
in 2026 and in 2025 has to produce a different robot, because the perimeter budget, the goal
height and the gamepiece are different — so the corpus presents the season alongside the
request and the target answer is derived from that season's numbers.  A model trained this way
learns "read the constraints, then design", which transfers to a season it has never seen; a
model trained on one season's answers learns that season.

No vendor prose, CAD, mesh or user data is copied. Published figures are restated as the
nominal engineering facts they are, always with the verification caveat attached.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "services" / "analysis"))

from app.services import frc_parts  # noqa: E402
from app.services.frc_cad import CAD_VERSION, SYSTEM_CAD  # noqa: E402
from app.services.frc_robot_knowledge import REFERENCES, TECHNIQUES  # noqa: E402
from app.services.frc_season import (  # noqa: E402
    SEASON_VERSION,
    SELECTABLE,
    SEASONS,
    design_targets,
    element,
    frame_budget,
    max_square_frame,
    optimal_launch_angle,
    prompt_block,
    required_exit_fps,
    scoring_math,
    surface_speed_for_exit,
)
from app.services.security import fence_user_content  # noqa: E402
from app.services.robot_spec import (  # noqa: E402
    ARM_TYPES,
    CLIMBER_TYPES,
    ELEVATOR_TYPES,
    HOPPER_TYPES,
    INTAKE_TYPES,
    SHOOTER_TYPES,
    SYSTEM_DESIGN,
    build_robot_spec,
    intent_user_message,
)

SYSTEM_KNOWLEDGE = (
    "You are Kale Forge Design Model, a self-hosted FRC hardware copilot. Answer with the specific "
    "part, number, gauge or technique — never a generality. State the figure, then the "
    "engineering reason, then what still has to be verified against the vendor drawing or the "
    "current game manual. Never claim rule compliance, structural safety or fabrication "
    "readiness."
)


def _fenced(prompt: str) -> str:
    """Use the runtime's own fencing so the training format matches inference byte for byte."""
    return fence_user_content(prompt)


def _row(system: str, user: str, assistant: str, family: str, index: int) -> dict[str, Any]:
    return {
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user},
                     {"role": "assistant", "content": assistant}],
        "metadata": {"id": f"frc-{family}-{index:05d}", "family": family,
                     "source": "kale-frc-catalog", "license": "internal", "reviewed": True,
                     "catalog_version": frc_parts.PARTS_VERSION},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Design intent — the task the Design Studio actually calls
# ─────────────────────────────────────────────────────────────────────────────
_SEASON_WORDS = {
    "2026-rebuilt": ["2026", "REBUILT", "fuel"],
    "2025-reefscape": ["2025", "REEFSCAPE", "coral and algae"],
    "2024-crescendo": ["2024", "CRESCENDO", "note"],
    "offseason": ["off-season", "training", "reference"],
}

_ROLE_TEMPLATES = [
    "We are a {experience} team. Design a {season} robot on a {w} × {l} inch frame that {goal}.",
    "Our strategy this year is {goal}. {season} rules, {w} inch frame, {experience} team.",
    "Design a {season} competition robot. Frame {w} × {l} in. Priority: {goal}. We are {experience}.",
    "{experience} team, {season} season. We want a {w} inch robot that {goal}. What should we build?",
]
_GOALS = [
    "cycles gamepieces from the floor as fast as possible",
    "scores from range and never touches the floor",
    "plays defence and still scores a reliable climb",
    "does one job perfectly instead of three jobs badly",
    "can score at every height without a shooter",
    "climbs in under five seconds at the buzzer",
    "feeds partners and stays out of traffic",
]
_EXPERIENCE = ["a rookie", "a second-year", "an experienced", "a resource-limited",
               "a well-resourced", "a small five-student"]

# What the strongest 2026 REBUILT robots actually do, written as goals a team would state —
# archetypes derived from how the game rewards play, never copied robots. Sampling these at a
# higher rate for 2026 teaches the model what "competitive this season" means in mechanisms:
# shoot-on-the-move turrets, trench-height packaging, five-second climbs, midline feeding.
_ELITE_GOALS_2026 = [
    "cycles fuel nonstop and scores while moving, the way the top REBUILT robots do",
    "holds the trench lane: full speed under the trench, intake to shot in under two seconds",
    "runs a turret so the drivetrain never has to stop or rotate to aim",
    "wins the endgame with a five-second L3 climb after playing full-field offense",
    "feeds a partner from the midline all match and never gets caught in traffic",
    "empties a full hopper in under eight seconds without a single jam",
    "plays high-pressure defence and still steals a climb at the buzzer",
    "shoots from behind the trench so defenders can never reach it",
]


def _intent_example(rng: random.Random, index: int) -> dict[str, Any]:
    # Seasons are sampled with the two selectable ones weighted up: those are what teams
    # actually ask for, and the older ones are there so the model does not learn that a
    # season word it has not seen means "ignore the season".
    profile_key = rng.choice(["2026-rebuilt", "2026-rebuilt", "2026-rebuilt",
                              "2025-reefscape", "2025-reefscape",
                              "2024-crescendo", "offseason"])
    season_data = SEASONS[profile_key]
    # Frames are sampled across and *past* the season's budget on purpose: a third of the
    # examples ask for a frame that does not fit, and the target has to be the resized one
    # with the reason attached. Learning to say "that is over the budget, here is what fits"
    # is worth more than learning any particular frame size.
    width = rng.choice([24, 26, 26, 27, 27, 28, 28, 29, 30])
    length = width if rng.random() < 0.75 else rng.choice([24, 26, 27, 28, 30])
    budget = frame_budget(season_data, float(width), float(length))
    width, length = budget["width_in"], budget["length_in"]
    profile = {"default_subsystems": season_data["default_subsystems"]}
    season = rng.choice(_SEASON_WORDS[profile_key])
    goal = rng.choice(_GOALS)
    if profile_key == "2026-rebuilt" and rng.random() < 0.45:
        goal = rng.choice(_ELITE_GOALS_2026)
    experience = rng.choice(_EXPERIENCE)
    prompt = rng.choice(_ROLE_TEMPLATES).format(
        experience=experience, season=season, w=f"{width:g}", l=f"{length:g}", goal=goal)

    subsystems = list(profile["default_subsystems"])
    if "climb" in goal and "climber" not in subsystems:
        subsystems.append("climber")
    if "without a shooter" in goal or "never touches the floor" not in goal and rng.random() < 0.2:
        subsystems = [name for name in subsystems if name != "shooter"] or ["intake"]
    if "one job perfectly" in goal:
        subsystems = subsystems[:2]
    if "from range" in goal and "shooter" not in subsystems:
        subsystems.append("shooter")
    # A shooter that has to keep firing needs something feeding it, and a hopper with nothing
    # to feed is dead weight. Tying them together is a rule the model should learn, not a
    # coincidence in the data.
    if "shooter" in subsystems and "hopper" not in subsystems and rng.random() < 0.6:
        subsystems.append("hopper")
    if "turret" in goal and "shooter" not in subsystems:
        subsystems.append("shooter")
    if "feeds a partner" in goal:
        # A feeder carries fuel without shooting it: hopper stays, shooter goes.
        subsystems = [name for name in subsystems if name != "shooter"]
        if "hopper" not in subsystems:
            subsystems.append("hopper")
    elif "shooter" not in subsystems:
        subsystems = [name for name in subsystems if name != "hopper"]
    order = ("intake", "hopper", "shooter", "elevator", "arm", "climber")
    subsystems = [name for name in order if name in subsystems]

    drive = "swerve" if rng.random() < 0.78 else rng.choice(["west-coast", "swerve-ready", "tank"])

    # A third of requests name their drivetrain hardware, the way real teams do. The named
    # module forces drive_type to swerve and injects catalog-true numbers into the notes,
    # so "MK5i at R3 on Krakens" maps to real ratios instead of a guess.
    named_module = None
    if rng.random() < 0.34:
        module_key = rng.choice(["mk5i", "mk5i", "mk4i", "mk4i", "mk4n", "mk5n", "maxswerve"])
        named_module = frc_parts.SWERVE_MODULES[module_key]
        ratio_label = rng.choice(list(named_module["drive_ratios"]))
        ratio_value = named_module["drive_ratios"][ratio_label]
        hw_motor = frc_parts.MOTORS[rng.choice(["kraken_x60", "kraken_x60", "neo_vortex"])]
        prompt += rng.choice([
            f" We run {named_module['name']} modules at {ratio_label} on {hw_motor['name']}s.",
            f" Drivetrain is already decided: {named_module['name']} at {ratio_label} with "
            f"{hw_motor['name']} drive motors.",
            f" We own {named_module['name']} swerve with {hw_motor['name']}s geared {ratio_label}.",
        ])
        drive = "swerve"

    intake_type = rng.choice(INTAKE_TYPES)
    hopper_type = rng.choice(HOPPER_TYPES)
    shooter_type = rng.choice(SHOOTER_TYPES)
    arm_type = rng.choice(ARM_TYPES)
    climber_type = rng.choice(CLIMBER_TYPES)
    elevator_arch = rng.choice(ELEVATOR_TYPES)
    stages = rng.choice([1, 2, 2, 3])

    # Sampled phrasing pools: the engineering content repeats, the wording must not.
    # A model trained on one fixed sentence per concept memorizes the sentence; sampled
    # variants force it to learn the reasoning instead of the string.
    notes = []
    if "rookie" in experience or "resource-limited" in experience:
        notes.append(rng.choice([
            f"{intake_type} keeps the part count low — a {experience.split()[-1]} team "
            "finishes a simple mechanism and iterates, rather than debugging a complex one.",
            f"With {experience.split()[-1]} resources, the {intake_type} is the right call: fewer "
            "parts to make, fewer to break, and iteration is cheap.",
            f"Pick the {intake_type} for buildability — a mechanism you finish in week three "
            "beats a clever one you are still debugging at the event.",
        ]))
    if named_module is not None:
        speed = frc_parts.free_speed_fps(hw_motor, ratio_value, named_module["wheel_in"])
        notes.append(rng.choice([
            f"{named_module['name']} at {ratio_label} ({ratio_value}:1) on {hw_motor['name']}s "
            f"gives a {speed:.1f} ft/s unloaded ceiling — honour the stated hardware and gear "
            "the software speed limit from measured, not theoretical, numbers.",
            f"The stated {named_module['name']} modules fix the corner envelopes at "
            f"{named_module['plate_in'][0]:g} in plates with {named_module['drop_in']:g} in of "
            f"drop; keep the {ratio_label} pinion and package everything else around them.",
            f"Respect the team's {named_module['name']} + {hw_motor['name']} drivetrain as a "
            f"fixed decision: {ratio_label} is {ratio_value}:1 for roughly {speed:.1f} ft/s "
            "free speed, so tune autonomy and current limits to that, not to a redesign.",
        ]))
    elif drive == "swerve":
        notes.append(rng.choice([
            "Swerve costs a real fraction of the budget and the build season; it is worth "
            "it only if the drive team gets practice time before the first event.",
            "Budget swerve honestly: the modules are the cheap part, the drive practice hours "
            "are the expensive one — commit to both or pick a simpler drivebase.",
            "Swerve pays off only with driver reps. Schedule chassis handoff early enough that "
            "the drive team gets real practice time before competition.",
        ]))
    else:
        notes.append(rng.choice([
            f"A {drive} drivebase frees budget and build hours for the scoring mechanism.",
            f"The {drive} drivebase is deliberate: every hour and dollar it saves goes into "
            "the mechanism that actually scores.",
            f"Choosing {drive} keeps the drivetrain boring and reliable so the interesting "
            "engineering budget lands on the scoring path.",
        ]))
    if "climber" in subsystems:
        notes.append(rng.choice([
            f"{climber_type} needs a ratchet on the drum — motor brake mode does not hold "
            "after the match ends.",
            f"Fit a mechanical ratchet to the {climber_type} winch: brake mode stops holding "
            "the instant power cuts at the buzzer.",
            f"The {climber_type} must hold mechanically, not electrically — a drum ratchet or "
            "brake carries the robot after the match ends, motor brake mode does not.",
        ]))
    if "elevator" in subsystems:
        notes.append(rng.choice([
            f"A {stages}-stage {elevator_arch} elevator keeps each stage rigid; verify the "
            "rigging stage by stage before the first powered run.",
            f"Build the {stages}-stage {elevator_arch} elevator as rigid stages with sliders "
            "between them, and walk the rigging through by hand before powering it.",
            f"On the {elevator_arch} elevator, check each of the {stages} stages moves freely "
            "and in sync under hand power first — rigging a stage out of sync tears itself apart.",
        ]))
    notes.append(rng.choice([
        "Reserve the module corners and the battery volume before sketching any mechanism.",
        "Block out the swerve corners and the battery envelope first; mechanisms get whatever "
        "volume is left.",
        "First CAD move: reserve the corner modules and battery volume — everything else "
        "packages around them.",
    ]))

    # Season-derived notes. Each one is arithmetic off this season's own numbers, so the same
    # sentence pattern produces a different — and correct — statement in a different year.
    # This is the family's most important job: the reasoning is the constant, not the answer.
    rules = season_data["rules"]
    if budget["scaled"]:
        notes.append(rng.choice([
            f"The requested frame was over {season_data['label']}'s {rules['perimeter_in']:g} in "
            f"perimeter budget ({rules['perimeter_rule']}); {width:g} × {length:g} in is the "
            f"nearest legal frame with the same proportions.",
            f"Resized to {width:g} × {length:g} in: {rules['perimeter_rule']} allows "
            f"{rules['perimeter_in']:g} in of perimeter this season, so the frame as asked for "
            f"would not pass inspection.",
            f"Frame perimeter is a rule, not a preference — {rules['perimeter_in']:g} in in "
            f"{season_data['label']} means at most {max_square_frame(season_data):g} in square.",
        ]))
    goal_el = element(season_data, "HUB") or element(season_data, "REEF")
    if goal_el and "shooter" in subsystems and goal_el.get("opening_front_edge_in"):
        rim = goal_el["opening_front_edge_in"]
        release = rules["start_height_in"] * 0.72
        design_range = goal_el.get("design_range_ft", 15.0)
        angle = optimal_launch_angle(release, rim, design_range)
        exit_fps = required_exit_fps(angle, release, rim, design_range)
        notes.append(rng.choice([
            f"The {goal_el['name']} opening sits at {rim:g} in, so from a ~{release:.0f} in "
            f"release the cheapest {design_range:g} ft shot leaves at {angle:g}° and about "
            f"{exit_fps:g} ft/s — size the flywheel from that, not from a target RPM.",
            f"Shot sizing: {rim:g} in of goal height at {design_range:g} ft needs roughly "
            f"{exit_fps:g} ft/s of exit velocity at {angle:g}°, which is "
            f"{surface_speed_for_exit(exit_fps, counter_rotating=False):g} ft/s of surface "
            f"speed on a single hooded wheel and half that with a counter-rotating pair.",
            f"Aim the hood range around {angle:g}° — that is 45° + ½·atan(Δh/d) for a "
            f"{rim:g} in goal at {design_range:g} ft, and it is the shot that needs the least "
            f"flywheel energy.",
        ]))
    if "hopper" in subsystems:
        gp = season_data["gamepiece"]
        notes.append(rng.choice([
            f"The hopper exit lane is one {gp['name']} wide — {gp['diameter_in']:g} in plus "
            f"clearance — and gated on a beam-break, not a timer; two pieces arriving together "
            f"is what jams a shooter.",
            f"Bulk {gp['name']} handling is a serialisation problem: many lanes in, one lane "
            f"out, with the index wheels standing about "
            f"{gp['diameter_in'] * 0.085:.2f} in proud of the floor.",
            f"Size the hopper by volume and derate hard — {gp['diameter_in']:g} in spheres pour "
            f"at roughly 60% packing, and the real limit is what the exit clears without jamming.",
        ]))
    trench = element(season_data, "TRENCH")
    if trench:
        notes.append(rng.choice([
            f"Decide the {trench['underpass_in'][1]:g} in trench question at architecture time: "
            f"fitting under it is a whole-robot constraint, not a trim you apply in week five.",
            f"If this robot is meant to use the trench, everything lives under "
            f"{trench['underpass_in'][1]:g} in — hopper volume and shot angle both pay for it.",
        ]))
    tower = element(season_data, "TOWER")
    if tower and "climber" in subsystems:
        rungs = tower["rungs_in"]
        notes.append(rng.choice([
            f"Climb reach from a {rules['start_height_in']:g} in stowed height: L2 needs about "
            f"{rungs['L2'] - rules['start_height_in']:.0f} in above the frame, L3 about "
            f"{rungs['L3'] - rules['start_height_in']:.0f} in. The rungs are 18 in apart, so "
            f"one more stage of travel buys one more level.",
            f"Size the climber on the L3 rung at {rungs['L3']:g} in, then check it stows inside "
            f"{rules['start_height_in']:g} in — the stowed check is the one that fails at inspection.",
        ]))

    risks = [
        rng.choice([
            "Weight: every added subsystem is 8–18 lb — check the roll-up before detailing.",
            "Mass creep: subsystems land at 8–18 lb each, so run the weight roll-up before "
            "committing to detail design.",
            "Watch the scale — each mechanism adds 8–18 lb and the total shows up all at once "
            "at inspection.",
        ]),
        rng.choice([
            "Wiring the mechanisms last leads to unserviceable harnesses and match-day failures.",
            "Leaving the harness for last produces wiring you cannot service between matches.",
            "Plan wire routing with the mechanism, not after it — retrofitted harnesses fail "
            "on the field.",
        ]),
    ]
    if "shooter" in subsystems:
        risks.append(rng.choice([
            "Shooter recovery time, not top speed, will limit the cycle rate.",
            "The cycle-rate ceiling is flywheel recovery between shots, not peak RPM.",
            "Expect the shooter's spin-up recovery, not its top speed, to set how fast you score.",
        ]))
    if "intake" in subsystems:
        risks.append(rng.choice([
            "Intake compression that is never measured is the most common cause of "
            "inconsistent pickup.",
            "Unmeasured intake compression is the usual culprit behind fifty-percent pickup.",
            "If intake compression is set by eye instead of measured, expect inconsistent "
            "acquisition all event.",
        ]))
    if drive == "swerve":
        risks.append(rng.choice([
            "Eight controllers on one CAN bus will saturate it; plan a second bus.",
            "Eight drivetrain controllers saturate a single CAN bus — budget a CANivore from "
            "the start.",
            "Plan a dedicated drivetrain CAN FD bus; eight modules of status frames overwhelm "
            "the roboRIO bus.",
        ]))

    if rules["propulsion_motors"] < 6 and drive in {"west-coast", "tank"}:
        risks.append(rng.choice([
            f"{rules['propulsion_rule']} allows {rules['propulsion_motors']} propulsion motors "
            f"this season — a six-motor drop-centre drivebase does not pass, so plan the "
            f"gearboxes around {rules['propulsion_motors']}.",
            f"Watch the motor count: {rules['propulsion_motors']} propulsion motors maximum, "
            f"which rules out the traditional six-motor west-coast layout.",
        ]))

    intent = {"subsystems": subsystems, "drive_type": drive, "intake_type": intake_type,
              "hopper_type": hopper_type,
              "shooter_type": shooter_type, "arm_type": arm_type, "climber_type": climber_type,
              "elevator_architecture": elevator_arch, "elevator_stages": stages,
              "pneumatics": bool(rng.random() < 0.25),
              "design_notes": notes[:6], "risks": risks[:4]}

    stated = {"season": profile_key, "frame_in": [width, length], "drive_type": None,
              "subsystems_stated": {}}
    # The vocabulary block and the season block both come from the runtime modules, so training
    # and inference present the model with byte-identical context; this is the same function
    # the Design Studio calls.
    user = intent_user_message(prompt, stated, profile_key)
    return _row(SYSTEM_DESIGN, user, json.dumps(intent, separators=(",", ":"), sort_keys=True),
                "design_intent", index)


# ─────────────────────────────────────────────────────────────────────────────
# 1b. Season rules — the field and the rulebook as derivations
#
# The temptation with a season is to write down its numbers and train on the list.  That
# produces a model that recites 2026 and is useless in 2027.  Every answer here states the
# figure, where it comes from, and *what it forces the design to do* — and the last part is
# the transferable skill.  All of it is computed from `frc_season`, so the corpus and the
# Design Studio can never disagree about how tall the goal is.
# ─────────────────────────────────────────────────────────────────────────────
_RULE_QUESTIONS = [
    "What does {label} actually constrain about the robot?",
    "We are starting {label}. What are the numbers that decide the architecture?",
    "Give me the design constraints for {label} and where each one comes from.",
    "What has to be true of a {label} robot before we cut anything?",
]

_FIELD_QUESTIONS = [
    "How high is the {element} in {label}, and what does that mean for the mechanism?",
    "Tell me about the {element} in {label} and how it sizes our design.",
    "What do we need to know about the {element} this season?",
]


def _season_math_examples(rng: random.Random, start: int, count: int) -> list[dict[str, Any]]:
    """Sampled sizing problems whose answers are worked, not recalled.

    This is the family that carries the generalisation. Every other example teaches the model
    facts about a season; these teach it the four calculations a season is *used for* —
    perimeter budget, launch angle and exit velocity, climb reach, hopper capacity — with the
    inputs sampled so no answer can be memorised. A model that has done two hundred of these
    can do the two hundred and first for a game that does not exist yet.
    """
    rows: list[dict[str, Any]] = []
    for offset in range(count):
        key = rng.choice(SELECTABLE)
        season = SEASONS[key]
        rules = season["rules"]
        kind = rng.choice(["frame", "shot", "climb", "hopper"])

        if kind == "frame":
            width = rng.choice([24, 25, 26, 27, 28, 29, 30, 31])
            length = rng.choice([24, 26, 27, 28, 30, 32])
            budget = frame_budget(season, float(width), float(length))
            perimeter = 2 * (width + length)
            question = (f"Is a {width} × {length} in frame legal in {season['label']}, and if "
                        f"not what is?")
            if budget["fits"]:
                answer = (f"Yes. {width} × {length} in is {perimeter:g} in of perimeter against "
                          f"{rules['perimeter_rule']}'s {rules['perimeter_in']:g} in budget, with "
                          f"{rules['perimeter_in'] - perimeter:g} in to spare. The perimeter is "
                          f"2 × (width + length), so you could go to "
                          f"{max_square_frame(season):g} in square if you wanted the volume — "
                          f"but a bigger frame is heavier and harder to defend, so spend the "
                          f"budget on the dimension the mechanism actually needs.")
            else:
                answer = (f"No. {width} × {length} in is {perimeter:g} in of perimeter and "
                          f"{rules['perimeter_rule']} allows {rules['perimeter_in']:g} in this "
                          f"season, so it is {perimeter - rules['perimeter_in']:g} in over and "
                          f"fails inspection. Scaling both sides to hold the proportions gives "
                          f"{budget['width_in']:g} × {budget['length_in']:g} in "
                          f"({budget['perimeter_in']:g} in). If the width matters more than the "
                          f"length, keep {width} in and cut the length to "
                          f"{rules['perimeter_in'] / 2 - width:g} in instead — the budget is a "
                          f"sum, so you choose which dimension spends it.")

        elif kind == "shot":
            goal = element(season, "HUB") or element(season, "REEF")
            rim = float(goal.get("opening_front_edge_in")
                        or (goal.get("levels_in") or {}).get("L4") or 72.0)
            release = float(rng.choice([14, 18, 20, 22, 24, 26, 28]))
            distance = float(rng.choice([8, 10, 12, 15, 18, 20, 24, 28]))
            angle = optimal_launch_angle(release, rim, distance)
            exit_fps = required_exit_fps(angle, release, rim, distance)
            single = surface_speed_for_exit(exit_fps, counter_rotating=False)
            pair = surface_speed_for_exit(exit_fps, counter_rotating=True)
            wheel_in = float(rng.choice([3, 4, 4, 5, 6]))
            rpm = single * 12 * 60 / (3.141592653589793 * wheel_in)
            question = (f"We want to score into the {goal.get('name', 'goal')} from {distance:g} "
                        f"ft with a {release:g} in release height. What does the shooter need?")
            answer = (
                f"Work it back from the geometry. The opening is at {rim:g} in, so the ball has "
                f"to gain {rim - release:g} in over {distance:g} ft.\n\n"
                f"1. Cheapest launch angle is 45° + ½·atan(Δh/d) = **{angle:g}°**. That is the "
                f"angle needing the least energy, so it is what the hood range should be centred on.\n"
                f"2. At that angle the required exit velocity is **{exit_fps:g} ft/s**.\n"
                f"3. Exit velocity is about half the flywheel surface speed for a piece squeezed "
                f"between one wheel and a fixed hood, and close to the full surface speed for a "
                f"counter-rotating pair. So you need **{single:g} ft/s** of surface speed with a "
                f"single hooded wheel, or **{pair:g} ft/s** with a pair.\n"
                f"4. On a {wheel_in:g} in wheel, {single:g} ft/s is about "
                f"**{rpm:.0f} RPM** at the wheel — pick the reduction from your motor's free "
                f"speed to land there with headroom, not at the ceiling.\n\n"
                f"That is a vacuum trajectory: no drag, no Magnus. A foam ball loses real range "
                f"to air, so treat it as the optimistic ceiling, build in headroom, and tune on "
                f"the field.")

        elif kind == "climb":
            tower = element(season, "TOWER") or element(season, "CAGE")
            stowed = float(rng.choice([24, 26, 28, 29, 30, 34, 38, 42]))
            stowed = min(stowed, rules["start_height_in"])
            if tower.get("rungs_in"):
                rungs = tower["rungs_in"]
                target_name = rng.choice(list(rungs))
                target_h = rungs[target_name]
                where = f"the {target_name} rung at {target_h:g} in"
            else:
                target_name = rng.choice(["shallow", "deep"])
                target_h = tower.get(f"{target_name}_bottom_in", 30.0)
                where = f"the {target_name} cage at {target_h:g} in"
            reach = max(0.0, target_h - stowed)
            weight = float(rng.choice([95, 100, 105, 110, 115]))
            paths = rng.choice([1, 2, 2])
            drum = rng.choice([1.0, 1.25, 1.5, 2.0])
            tension = weight / paths
            torque = tension * 4.4482 * (drum / 2 * 0.0254)
            question = (f"Our robot stows at {stowed:g} in and weighs {weight:g} lb. Size the "
                        f"climber for {where} in {season['label']}.")
            answer = (
                f"Reach first, then torque.\n\n"
                f"1. {target_h:g} in target minus a {stowed:g} in stowed height is "
                f"**{reach:g} in of extension** above the frame"
                + (" — reachable without extending, so this is a drive-on rather than a lift."
                   if reach <= 0 else ".") + "\n"
                f"2. Rope tension is robot weight ÷ load paths = {weight:g} ÷ {paths} = "
                f"**{tension:.1f} lbf**. Two paths halve the tension and stop the robot swinging "
                f"on one point, which is what pulls a hook out.\n"
                f"3. Drum torque is tension × drum radius = {tension:.1f} lbf × {drum / 2:g} in = "
                f"**{torque:.2f} N·m**. The drum radius is a lever working against you: a bigger "
                f"drum takes more rope and costs you reduction.\n"
                f"4. Divide that by what the motors give at a safe duty point — not at stall — "
                f"to get the reduction, then check the climb finishes inside the endgame window.\n\n"
                f"Hold it mechanically. A ratchet on the drum carries the robot after the buzzer; "
                f"motor brake mode stops holding the moment power cuts. And check the stowed "
                f"height against {rules['height_rule']}'s {rules['start_height_in']:g} in with "
                f"the real bumpers on — that is the check that fails at inspection.")

        else:
            gp = season["gamepiece"]
            floor_w = float(rng.choice([16, 18, 20, 22, 24]))
            floor_d = float(rng.choice([10, 12, 14, 16, 18]))
            wall = float(rng.choice([8, 10, 12, 14]))
            fill = wall * 0.8
            piece_volume = 3.141592653589793 / 6 * gp["diameter_in"] ** 3
            capacity = int(floor_w * floor_d * fill * 0.60 / piece_volume)
            question = (f"How many {gp['name']} fit in a {floor_w:g} × {floor_d:g} in hopper "
                        f"with {wall:g} in walls?")
            answer = (
                f"About **{capacity}** — and the number matters less than how you get it.\n\n"
                f"1. Usable height is not the wall height. Nobody fills to the brim and driving "
                f"throws the top layer out, so take {wall:g} × 0.8 = {fill:.1f} in.\n"
                f"2. Usable volume is {floor_w:g} × {floor_d:g} × {fill:.1f} = "
                f"{floor_w * floor_d * fill:.0f} in³.\n"
                f"3. Randomly poured spheres pack at about 60%, not the 74% of a stacked lattice "
                f"they will never form in a hopper. That is {floor_w * floor_d * fill * 0.6:.0f} in³ "
                f"of gamepiece.\n"
                f"4. One {gp['diameter_in']:g} in piece is π/6 × d³ = {piece_volume:.0f} in³, so "
                f"{floor_w * floor_d * fill * 0.6:.0f} ÷ {piece_volume:.0f} ≈ **{capacity}**.\n\n"
                f"Treat that as an upper bound. The real limit is almost always what the exit "
                f"lane clears without jamming, not what the box holds — so build the exit first, "
                f"measure throughput with the hopper full, and size the box to match.")

        rows.append(_row(SYSTEM_KNOWLEDGE, question, answer, "season_rules", start + offset))
    return rows


def _season_rule_examples(rng: random.Random, start: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    index = start
    for key in SELECTABLE + ["2024-crescendo"]:
        season = SEASONS[key]
        label = season["label"]
        rules = season["rules"]

        # The whole constraint set, as a derivation table.
        targets = design_targets(season, robot_height_in=rules["start_height_in"])
        lines = [f"{label}. {season['summary']}", ""]
        lines += [f"- **{row['target']}: {row['value']}** (from {row['from']}). {row['means']}"
                  for row in targets]
        lines += ["", season["verify"]]
        rows.append(_row(SYSTEM_KNOWLEDGE, rng.choice(_RULE_QUESTIONS).format(label=label),
                         "\n".join(lines), "season_rules", index))
        index += 1

        # One example per field element, because a mechanism is sized against one element at
        # a time and that is how the question arrives at a design review.
        for item in season["field"]["elements"]:
            detail: list[str] = [item["note"]]
            if item.get("opening_front_edge_in"):
                rim = item["opening_front_edge_in"]
                release = rules["start_height_in"] * 0.72
                for range_ft in (goal_range for goal_range in
                                 (item.get("design_range_ft", 15.0), item.get("long_range_ft", 25.0))):
                    angle = optimal_launch_angle(release, rim, range_ft)
                    exit_fps = required_exit_fps(angle, release, rim, range_ft)
                    detail.append(
                        f"From a {release:.0f} in release, a {range_ft:g} ft shot is cheapest at "
                        f"{angle:g}° and needs about {exit_fps:g} ft/s of exit velocity — "
                        f"{surface_speed_for_exit(exit_fps, counter_rotating=False):g} ft/s of "
                        f"surface speed on a single wheel against a fixed hood, or "
                        f"{surface_speed_for_exit(exit_fps, counter_rotating=True):g} ft/s with a "
                        f"counter-rotating pair.")
                detail.append("Those are vacuum trajectories — no drag, no Magnus. Treat them as "
                              "the optimistic ceiling and tune on the field.")
            if item.get("rungs_in"):
                rungs = item["rungs_in"]
                detail.append("From a {:g} in stowed height the reach above the frame is ".format(
                    rules["start_height_in"]) + ", ".join(
                    f"{name} {max(0.0, height - rules['start_height_in']):.0f} in"
                    for name, height in rungs.items()) + ".")
            if item.get("levels_in"):
                levels = item["levels_in"]
                detail.append("Reach above the bellypan for each level is roughly " + ", ".join(
                    f"{name} {height - 3:.0f} in" for name, height in levels.items())
                    + " — the elevator has to deliver the tallest of those with the gamepiece held.")
            if item.get("underpass_in"):
                w, h = item["underpass_in"]
                detail.append(f"Everything on a robot that uses it lives under {h:g} in and "
                              f"within {w:g} in of width, including bumpers and any stowed mechanism.")
            if item.get("ramp_deg"):
                detail.append(f"Breakover matters more than ground clearance: a long wheelbase "
                              f"lands its belly on a {item['size_in'][2]:g} in crest even with "
                              f"clearance to spare. Check the diagonal.")
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             rng.choice(_FIELD_QUESTIONS).format(element=item["name"], label=label),
                             " ".join(detail) + f" {season['verify']}", "season_rules", index))
            index += 1

        # Scoring, as arithmetic rather than a table read aloud.
        if season["scoring"]:
            lines = [f"{label} scoring:"]
            lines += [f"- {row['phase'].upper()}: {row['action']} — {row['points']} points"
                      + (f" ({row['note']})" if row.get("note") else "")
                      for row in season["scoring"]]
            lines.append("")
            lines.append("Ranking points: " + "; ".join(
                f"{row['name']} {row['rp']} RP" + (f" at {row['threshold']}" if row.get("threshold") else "")
                for row in season["ranking"]))
            if scoring_math(season):
                lines += ["", "What that means:"] + [f"- {item}" for item in scoring_math(season)]
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             rng.choice([f"How does scoring work in {label}, and what is worth building for?",
                                         f"Break down the {label} scoring table for us.",
                                         f"What should we prioritise given the {label} point values?"]),
                             "\n".join(lines), "season_rules", index))
            index += 1

        # Cross-season comparison: the single best defence against memorising one year.
        for other_key in SELECTABLE:
            if other_key == key:
                continue
            other = SEASONS[other_key]
            diffs = []
            for field, name, unit in (("perimeter_in", "frame perimeter", "in"),
                                      ("start_height_in", "starting height", "in"),
                                      ("extension_in", "extension allowance", "in"),
                                      ("propulsion_motors", "propulsion motors", "")):
                a, b = rules[field], other["rules"][field]
                if a != b:
                    diffs.append(f"{name} {a:g}{unit} → {b:g}{unit}")
            if not diffs:
                continue
            answer = (
                f"Do not carry the frame over. Moving from {label} to {other['label']} changes "
                + "; ".join(diffs) + ". "
                + (f"A {max_square_frame(season):g} in square frame was the largest legal one in "
                   f"{label}; in {other['label']} it is {max_square_frame(other):g} in. "
                   if rules["perimeter_in"] != other["rules"]["perimeter_in"] else "")
                + f"The gamepiece changes too — {season['gamepiece']['name']} at "
                f"{season['gamepiece']['diameter_in']:g} in versus "
                f"{other['gamepiece']['name']} at {other['gamepiece']['diameter_in']:g} in — so "
                f"the intake, the storage and the scoring mechanism are all different problems. "
                f"What does transfer is the drivetrain, the electrical layout and the build "
                f"techniques. {other['verify']}")
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             rng.choice([
                                 f"Can we reuse our {label} chassis for {other['label']}?",
                                 f"What changes between {label} and {other['label']}?",
                                 f"We built for {label}. What breaks if we build the same robot for "
                                 f"{other['label']}?"]),
                             answer, "season_rules", index))
            index += 1
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 1c. Strategy — archetype selection with the arithmetic that justifies it
# ─────────────────────────────────────────────────────────────────────────────
_STRATEGY_QUESTIONS = [
    "We are {experience}. What should we build this season and why?",
    "Talk us through the archetypes for {label}. We are {experience}.",
    "{experience} team. Which robot archetype fits {label} best for us?",
    "Help us pick a strategy for {label}. Context: we are {experience}.",
]


def _strategy_examples(rng: random.Random, start: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    index = start
    for key in SELECTABLE:
        season = SEASONS[key]
        if not season["archetypes"]:
            continue
        for experience in _EXPERIENCE:
            lines = [f"{season['label']}: {season['summary']}", "",
                     "The archetypes worth considering:", ""]
            for archetype in season["archetypes"]:
                lines += [f"**{archetype['name']}** — {archetype['shape']}",
                          f"- For: {archetype['why']}",
                          f"- Against: {archetype['against']}", ""]
            lines += ["The arithmetic behind the choice:", ""]
            lines += [f"- {item}" for item in scoring_math(season)]
            # The recommendation follows the constraint the team actually stated, which is the
            # thing a strategy answer is for. Resource-limited teams get told to do less.
            simple = any(word in experience for word in ("rookie", "resource-limited", "five-student"))
            pick = season["archetypes"][-1 if simple else 0]
            lines += ["", f"For {experience} team: **{pick['name']}**. "
                          + ("Depth beats breadth when hours are the scarce resource — one "
                             "mechanism that works every match is worth more than three that "
                             "half-work, and it is the version you can actually finish, wire "
                             "and practise with."
                             if simple else
                             "You have the hours to absorb the complexity, and the flexibility "
                             "pays back across a whole event rather than in one match.")]
            lines += ["", "Whatever you pick, decide it in week one and stop revisiting it. The "
                          "cost of switching archetypes in week four is the season."]
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             rng.choice(_STRATEGY_QUESTIONS).format(
                                 label=season["label"], experience=experience),
                             "\n".join(lines), "strategy", index))
            index += 1
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 1d. Technical binder — the format a design review actually wants
#
# A binder entry is not a description of a mechanism, it is an argument for it: here is the
# requirement, here is what we considered, here is the number that decided it, here is how we
# know it works, here is what is still open.  Training on that shape is what turns a model
# that lists parts into one that justifies them — and the numbers come from the same spec the
# Design Studio produces, so they are derived rather than asserted.
# ─────────────────────────────────────────────────────────────────────────────
_BINDER_QUESTIONS = [
    "Write the technical binder entry for the {label} on this robot.",
    "We need a design review write-up for the {label}. Requirement, options, calculation, validation, open risks.",
    "Document the {label} the way a judge would want to read it.",
    "Give me the binder page for the {label}: why this design, what decided it, how we validated it.",
]

_BINDER_OPTIONS: dict[str, list[str]] = {
    "drivetrain": ["a four-module swerve drivebase", "a six-wheel west-coast drop-centre",
                   "a four-wheel tank"],
    "intake": ["a fixed under-bumper roller", "an over-the-bumper pivot", "a four-bar deploy"],
    "hopper": ["a rotating-floor spindexer", "a belt-floor hopper", "a serpentine tunnel"],
    "shooter": ["a fixed hooded shooter", "a turreted hooded shooter", "a staged barrel"],
    "elevator": ["a single-stage lift", "a cascade tower", "a continuous tower"],
    "manipulator": ["a single-segment arm", "a double-jointed arm", "a four-bar linkage"],
    "climber": ["a single pivoting hook", "dual telescoping hooks", "a winch-driven carriage"],
}


def _binder_examples(rng: random.Random, start: int, requests: list[tuple[str, str]],
                     cap: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    index = start
    for request, season_key in requests:
        spec = build_robot_spec(request, use_model=False, season=season_key)
        season = SEASONS[spec["season"]["key"]]
        for block_key, label in (("drivetrain", "drivetrain"), ("intake", "intake"),
                                 ("hopper", "hopper"), ("shooter", "shooter"),
                                 ("elevator", "elevator"), ("manipulator", "arm"),
                                 ("climber", "climber")):
            block = spec.get(block_key) or {}
            # The drivetrain block has no "included" flag; a binder entry only makes sense
            # when something is actually driven — installed modules or a west-coast gearbox.
            if block_key == "drivetrain":
                if not (block.get("module_count") or block.get("type") == "west-coast"):
                    continue
            elif not block.get("included"):
                continue
            options = _BINDER_OPTIONS.get(block_key, [])
            lines = [f"## {label.title()} — {block.get('type', block.get('architecture', ''))}", ""]

            # Requirement: what the season asks this subsystem to do.
            requirement = _binder_requirement(block_key, spec, season)
            lines += [f"**Requirement.** {requirement}", ""]

            # Options: what was on the table and why the others lost.
            if options:
                lines += [f"**Options considered.** {', '.join(options)}. "
                          + rng.choice([
                              "The choice below is the one whose failure mode we can live with.",
                              "All three are buildable; the deciding factor is the calculation "
                              "that follows, not preference.",
                              "We picked on the number below, not on what looked impressive.",
                          ]), ""]

            # Calculation: derived from the spec, never asserted.
            lines += ["**Calculation.**"] + [f"- {item}" for item in
                                             _binder_calculation(block_key, spec, season)] + [""]

            # Validation and risks.
            lines += ["**Validation.** " + _binder_validation(block_key, rng), ""]
            lines += ["**Open risks.**"] + [f"- {item}" for item in _binder_risks(block_key, spec)]
            lines += ["", f"Every dimension above is concept geometry sized against the "
                          f"{season['label']} figures. Confirm each COTS part against its vendor "
                          f"drawing and each constraint against the current manual before "
                          f"fabrication."]
            user = (rng.choice(_BINDER_QUESTIONS).format(label=label)
                    + f"\n\nSeason: {season['label']}\n\nRobot:\n" + _fenced(request))
            rows.append(_row(SYSTEM_KNOWLEDGE, user, "\n".join(lines), "binder", index))
            index += 1
    if cap is not None and len(rows) > cap:
        # Trim after a shuffle so the cap drops rows evenly rather than whole late requests.
        rng.shuffle(rows)
        rows = rows[:cap]
    return rows


def _cad_binder_audit_examples(rng: random.Random, start: int,
                               requests: list[tuple[str, str]],
                               cap: int = 240) -> list[dict[str, Any]]:
    """Teach one answer to reconcile requirements, binder claims, and actual CAD.

    The older binder family is intentionally prose-first while ``cad_geometry`` is
    structure-first.  This bridge family makes the model prove every important claim against
    the generated tree: it names the exact parts, dimensions, interfaces, calculation inputs,
    and a measurable validation step.  Targets are still produced from Kale's deterministic
    spec/CAD pipeline, so the added detail cannot invent a part that the model does not contain.
    """
    rows: list[dict[str, Any]] = []
    index = start
    for request, season_key in requests:
        spec = build_robot_spec(request, use_model=False, season=season_key)
        cad = spec.get("cad") or {}
        frame = spec.get("frame") or {}
        assemblies = cad.get("assemblies") or []
        for assembly in assemblies:
            features = assembly.get("features") or []
            if not features:
                continue
            dims: list[str] = []
            for feature in features[:12]:
                kind = feature.get("t", "part")
                if feature.get("sec") and feature.get("len") is not None:
                    measure = (f"{feature['sec'][0]:g} x {feature['sec'][1]:g} in section, "
                               f"{feature.get('wall', 0):g} in wall, {feature['len']:g} in long")
                elif feature.get("size"):
                    measure = " x ".join(f"{value:g}" for value in feature["size"]) + " in"
                elif feature.get("dia") is not None:
                    measure = f"{feature['dia']:g} in diameter"
                    if feature.get("len") is not None:
                        measure += f", {feature['len']:g} in long"
                elif feature.get("teeth") is not None:
                    measure = (f"{feature['teeth']} teeth; pitch diameter "
                               f"{feature.get('pd', 0):g} in")
                else:
                    measure = "catalog envelope; verify against the vendor drawing"
                at = feature.get("at") or [0, 0, 0]
                dims.append(
                    f"- `{feature.get('id', feature.get('n', 'part'))}` — "
                    f"{feature.get('n', kind)} ({kind}), {measure}, origin "
                    f"[{at[0]:g}, {at[1]:g}, {at[2]:g}] in."
                )

            perimeter = 2 * (float(frame.get("width_in") or 27)
                             + float(frame.get("length_in") or 27))
            lines = [
                f"## CAD-to-binder audit — {assembly.get('name', assembly.get('id', 'assembly'))}",
                "",
                f"**Requirement trace.** This assembly belongs to a {frame.get('width_in', 27):g} x "
                f"{frame.get('length_in', 27):g} in robot for {spec['season']['label']}. "
                f"The frame perimeter is {perimeter:g} in; compare it with the season limit "
                f"before freezing interfaces.",
                "",
                f"**Model evidence.** The source tree contains {len(features)} separately named "
                "parts in this assembly. The first twelve are:",
                *dims,
                "",
                "**Interface contract.** Preserve the assembly origin, shaft/roller axes, bearing "
                "bores, fastener access, and clearance to the frame envelope. A revision is not "
                "complete until every changed interface is propagated to both mating parts.",
                "",
                "**Calculation trace.** Recompute derived measures from their inputs: gear pitch "
                "diameter = teeth / diametral pitch; pulley pitch diameter = teeth x pitch / pi; "
                "tube inside size = outside size - 2 x wall. Do not copy the previous result after "
                "editing an input.",
                "",
                "**Validation.** Regenerate the CAD tree, run interference and travel checks, "
                "compare the cut list with every tube feature, then inspect one physical or vendor "
                "reference for each critical interface. Record the measured value, tolerance, "
                "method, owner, and date.",
                "",
                "**Open status.** This is dimensioned concept geometry. Vendor dimensions, loads, "
                "tolerances, current rules, and proof-test results remain verification items until "
                "evidence is attached.",
            ]
            prompt = (
                "Audit this subsystem by cross-checking its technical-binder claims against the "
                "actual parametric CAD. Name the parts and measures, show the derivations, identify "
                "interfaces, and give a verification plan.\n\n"
                f"Season: {spec['season']['label']}\nRobot:\n{_fenced(request)}\n"
                f"Subsystem: {assembly.get('name', assembly.get('id', 'assembly'))}"
            )
            rows.append(_row(SYSTEM_KNOWLEDGE, prompt, "\n".join(lines),
                             "cad_binder_audit", index))
            index += 1
    rng.shuffle(rows)
    return rows[:cap]


def _binder_requirement(key: str, spec: dict[str, Any], season: dict[str, Any]) -> str:
    gp = season["gamepiece"]
    rules = season["rules"]
    if key == "drivetrain":
        frame = spec.get("frame") or {}
        return (f"Move a {frame.get('width_in', 27):g} × {frame.get('length_in', 27):g} in robot "
                f"at competition weight across the field faster than the defence can rotate, "
                f"inside the {rules['propulsion_motors']}-propulsion-motor limit. Free speed is "
                f"the ceiling, not the requirement — the requirement is acceleration out of every "
                f"scoring position with the battery sagging, repeated for the whole match.")
    if key == "intake":
        return (f"Acquire {gp['name']} from the floor at driving speed without stopping. "
                f"{gp['handling']} The roller has to reach past the bumper and out-run the "
                f"drivebase's approach speed, and deployed it may not exceed the "
                f"{rules['extension_in']:g} in extension allowance ({rules['extension_rule']}).")
    if key == "hopper":
        return (f"Hold enough {gp['name']} to be worth a trip across the field, and deliver them "
                f"one at a time to the shooter at a rate the shooter can absorb. Bulk storage is "
                f"a serialisation problem: the mechanism has to turn an unordered pile into a "
                f"single ordered lane, and stay un-jammed while the robot crosses obstacles.")
    if key == "shooter":
        goal = element(season, "HUB") or element(season, "REEF")
        height = (goal.get("opening_front_edge_in")
                  or (goal.get("levels_in") or {}).get("L4") or 0)
        return (f"Deliver {gp['name']} into the {goal.get('name', 'goal')} at "
                f"{height:g} in from a useful working distance, repeatably, and recover fast "
                f"enough between shots that the cycle rate is set by the hopper rather than by "
                f"the flywheel.")
    if key == "elevator":
        reef = element(season, "REEF")
        top = max((reef.get("levels_in") or {}).values(), default=0)
        return (f"Lift the end effector to {top:g} in with a {gp['name']} held, and return to "
                f"the collection height fast enough to keep cycling. It has to stow inside the "
                f"{rules['start_height_in']:g} in starting height.")
    if key == "manipulator":
        return (f"Present the {gp['name']} at the scoring geometry with the arm's own weight "
                f"and the payload cantilevered, holding position without drift. The worst case "
                f"is holding horizontal at full extension, not moving.")
    if key == "climber":
        tower = element(season, "TOWER") or element(season, "CAGE")
        if tower.get("rungs_in"):
            target = max(tower["rungs_in"].values())
            where = f"the {max(tower['rungs_in'], key=lambda k: tower['rungs_in'][k])} rung at {target:g} in"
        else:
            target = tower.get("deep_bottom_in", 30.0)
            where = f"the deep cage at {target:g} in"
        return (f"Lift the whole robot onto {where} inside the endgame window, hold it after "
                f"power cuts at the buzzer, and stow inside the {rules['start_height_in']:g} in "
                f"starting height before the match.")
    return "Perform its function within the season's envelope and weight budget."


def _binder_calculation(key: str, spec: dict[str, Any], season: dict[str, Any]) -> list[str]:
    block = spec.get(key) or {}
    if key == "drivetrain":
        lines = [f"Free speed = motor free RPM ÷ {block.get('drive_ratio', 6.0):g} drive "
                 f"reduction × π × {block.get('wheel_diameter_in', 4):g} in wheel = "
                 f"{block.get('free_speed_fps', 0):g} ft/s"
                 + (f" at the {block.get('drive_ratio_label', 'selected')} ratio"
                    if block.get("drive_ratio_label") else "") + ".",
                 f"Stall thrust ≈ stall torque × reduction × motor count ÷ wheel radius = "
                 f"{block.get('stall_thrust_lbf', 0):g} lbf across "
                 f"{block.get('drive_motors', 4)} × {block.get('motor', 'drive motor')} — an "
                 f"upper bound that ignores traction, so the usable number is the friction "
                 f"limit, roughly weight × 1.1 on clean carpet."]
        if block.get("module_count"):
            lines.append(f"{block.get('module_count')} × {block.get('module', 'swerve module')} "
                         f"({block.get('module_mass_lb', 0):g} lb of modules) with "
                         f"{block.get('steer_ratio', 0):g}:1 steering; the drive encoder lives "
                         f"in the module — {block.get('encoder', 'integrated encoder')}.")
        else:
            lines.append(f"{block.get('module', 'drop-centre gearbox')} with the centre wheel "
                         f"dropped ~{block.get('center_drop_in', 0.125):g} in so the robot turns "
                         f"on four contact patches instead of six.")
        return lines
    if key == "intake":
        return [f"Roller surface speed = free RPM ÷ reduction × π × diameter = "
                f"{block['roller_surface_speed_fps']:g} ft/s off a {block['motor']} through "
                f"{block['gear_reduction']} on a {block['roller_diameter_in']:g} in roller. That "
                f"has to exceed the approach speed or the intake pushes the piece away.",
                f"Compression is set by the {block['roller_center_distance_in']:g} in centre "
                f"distance, giving {block['compression_in']:g} in of squish on "
                f"{block['compliant_wheel']} — a dimension cut into the plates, not a feel.",
                f"{block['width_in']:g} in of intake width across "
                f"{block['roller_count']} roller{'s' if block['roller_count'] > 1 else ''}, "
                f"powered by {block['power_path']}."]
    if key == "hopper":
        return [f"Capacity = floor area × 80% of wall height × 60% random-pour packing ÷ one "
                f"gamepiece volume = {block['floor_width_in']:g} × {block['floor_depth_in']:g} × "
                f"{block['wall_height_in'] * 0.8:.1f} in at 60% ÷ a "
                f"{block['gamepiece_diameter_in']:g} in sphere ≈ "
                f"**{block['capacity_estimate']} pieces**.",
                f"Feed rate ≈ {block['feed_rate_per_s']:g} per second from "
                f"{block['wheel_count']} × {block['wheel_diameter_in']:g} in index wheels through "
                f"{block['gear_reduction']} off a {block['motor']}.",
                f"Exit lane is {block['exit_lane_width_in']:g} in — one "
                f"{block['gamepiece_diameter_in']:g} in piece plus clearance, never two.",
                f"Index wheels stand {block['wheel_proud_in']:g} in proud of the floor: flush "
                f"and the piece rides the floor and slips, higher and it climbs over."]
    if key == "shooter":
        shot = block.get("shot") or {}
        lines = [f"Surface speed = {block['motor']} free RPM ÷ {block['gear_reduction']} × π × "
                 f"{block['flywheel_diameter_in']:g} in = "
                 f"{block['flywheel_surface_speed_fps']:g} ft/s.",
                 f"Exit velocity is "
                 + ("the full surface speed with counter-rotating stages"
                    if block.get("stacked") else
                    "about half the surface speed — the piece leaves at the mean of a moving "
                    "wheel and a stationary hood")
                 + f", so {block['exit_velocity_fps']:g} ft/s.",
                 f"{block['flywheel_mass_lb']:g} lb of flywheel gives roughly "
                 f"{block['spinup_time_s']:g} s of recovery between shots; recovery time, not "
                 f"peak RPM, sets the cycle rate."]
        if shot:
            lines.append(
                f"Required: a {shot['design_range_ft']:g} ft shot into a "
                f"{shot['target_height_in']:g} in opening from a "
                f"{shot['release_height_in']:g} in release is cheapest at "
                f"{shot['optimal_angle_deg']:g}° and needs {shot['required_exit_fps']:g} ft/s. "
                + ("This design clears it."
                   if shot["makes_design_range"] else
                   "This design does not clear it — raise the surface speed or lower the reduction."))
        return lines
    if key == "elevator":
        return [f"{block['stages']} stages × {block['stage_travel_in']:g} in of stage travel = "
                f"{block['travel_in']:g} in at the carriage, moving "
                f"{block['carriage_speed_multiple']}× the drum payout.",
                f"{block['stage_overlap_in']:g} in of overlap remains at full extension — that "
                f"overlap, with a bearing block at each end of it, is the only thing resisting "
                f"the tip moment.",
                f"Rigged in {block['rigging']} off a {block['drum_diameter_in']:g} in drum "
                f"through {block['reduction']}, sized on holding torque at full extension rather "
                f"than free speed."]
    if key == "manipulator":
        return [f"Holding torque at full extension ≈ {block['holding_torque_nm']:g} N·m, which "
                f"is what the {block['reduction']} reduction is sized on.",
                f"{block['reach_in']:g} in of reach in {block['segments']} segment"
                f"{'s' if block['segments'] > 1 else ''} "
                f"({', '.join(f'{v:g} in' for v in block['segment_lengths_in'])}) on a "
                f"{block['shaft']} dead axle in double shear.",
                f"Sweep {block['shoulder_pivot_deg'][0]}–{block['shoulder_pivot_deg'][1]}° with "
                f"metal hard stops just outside the software limits."]
    if key == "climber":
        return [f"Rope tension = robot weight ÷ load paths = {block['lift_load_lb']:g} lb ÷ "
                f"{block['load_paths']} = {block['rope_tension_lbf']:g} lbf.",
                f"Drum torque = tension × drum radius = {block['rope_tension_lbf']:g} lbf × "
                f"{block['winch_drum_diameter_in'] / 2:g} in = {block['drum_torque_nm']:g} N·m, "
                f"which is why the winch runs {block['reduction']} off "
                f"{block['motor_count']}× {block['motor']}.",
                f"{block['travel_in']:g} in of extension from a "
                f"{block['stowed_height_in']:g} in stowed height, held by {block['ratchet']} "
                f"rather than motor brake mode — brake mode stops holding when power cuts."]
    return []


def _binder_validation(key: str, rng: random.Random) -> str:
    return {
        "drivetrain": rng.choice([
            "Drive a full match on a charged battery, logging bus voltage and loop times; the "
            "sprint speed that matters is the one measured in the last thirty seconds.",
            "Time a field-length sprint both directions and measure pushing force against a "
            "wall on a scale — free speed and stall thrust are ceilings, these are the floors.",
        ]),
        "intake": rng.choice([
            "Bench rig at the design compression, then floor tests at full drive speed in both "
            "directions. Count acquisitions out of fifty, not out of five.",
            "Measure compression with feeler stock rather than by eye, then run pickup trials at "
            "the fastest approach the drivers actually use.",
        ]),
        "hopper": rng.choice([
            "Fill it to capacity and drive the real field obstacles — throughput measured with a "
            "half-empty hopper is fiction, and the bump is what packs it solid.",
            "Count pieces per second out of the exit gate with the hopper full, then repeat after "
            "driving over the obstacle, which is when a bridge forms.",
        ]),
        "shooter": rng.choice([
            "Shoot groups of ten at each of three distances, logging bus voltage and the "
            "at-speed flag. Consistency between shots matters more than any single shot.",
            "Measure recovery time between shots with a full feed rather than trusting the "
            "setpoint, and re-tune with worn gamepieces — foam compresses more as it ages.",
        ]),
        "elevator": rng.choice([
            "Walk every stage through by hand before the first powered run, then run to both "
            "limits under current limit and check the overlap at full extension.",
            "Verify the rigging stage by stage unpowered — a cascade a stage out of sync tears "
            "itself apart the first time it is driven.",
        ]),
        "manipulator": rng.choice([
            "Hold horizontal at full extension with the payload and measure the deflection and "
            "the holding current. Both should be boring.",
            "Cycle the full sweep two hundred times with the payload, checking for backlash "
            "growth at the shoulder — that is where rounded hex shows up first.",
        ]),
        "climber": rng.choice([
            "Pull-test the engagement at twice the expected load before trusting it at the "
            "buzzer, then climb with the robot at competition weight and cut power at the top.",
            "Climb the real geometry, hold for thirty seconds with the motors disabled, and "
            "confirm the ratchet — not brake mode — is what is carrying the robot.",
        ]),
    }.get(key, "Test it at competition weight against the real field geometry.")


def _binder_risks(key: str, spec: dict[str, Any]) -> list[str]:
    common = ["Mass: this subsystem is in the roll-up as an estimate. Weigh the built article.",
              "Harness routing was planned with the mechanism; verify service loops at every "
              "moving joint before the first event."]
    specific = {
        "drivetrain": ["Tread wears fastest on the inside modules during defence; measure it "
                       "between matches, not between events.",
                       "Brushless drive motors mask a failing encoder until odometry drifts — "
                       "watch the module deltas in the log, not the driver's impression."],
        "intake": ["Compliant wheels wear and change compression across an event — keep spares "
                   "and re-measure between days.",
                   "The deployed arm is the most-hit part of the robot; the pivot and its hard "
                   "stops are the first things to inspect after a match."],
        "hopper": ["Jamming is the failure mode, and it happens under load in a match, not on "
                   "the bench. The access panel exists because of that.",
                   "Capacity is volumetric and optimistic; the exit lane is the real limit."],
        "shooter": ["Range is computed without drag, so real range is shorter. Treat the number "
                    "as a ceiling.",
                    "Battery sag between shots moves the setpoint; gate the feeder on a measured "
                    "at-speed condition, never a timer."],
        "elevator": ["Slop multiplies at the carriage; preload the bearing blocks and re-check "
                     "after the first competition day.",
                     "Extension has to be re-checked against the current manual and team updates."],
        "manipulator": ["Reversing shock load rounds hex. Watch the shoulder for backlash growth.",
                        "Software limits fail with a dead encoder; the metal stops are what "
                        "actually protect the mechanism."],
        "climber": ["A hook that binds on entry in the last ten seconds costs the climb; test "
                    "engagement tired and in a hurry, not carefully.",
                    "The stowed height is an inspection item — check it with the real bumpers on."],
    }
    rule_fails = [f"{row['check']} currently fails {row['rule']}: {row['detail']}. {row['fix']}"
                  for row in spec.get("rule_check", []) if not row["ok"]]
    return specific.get(key, []) + common[:1] + rule_fails


# ─────────────────────────────────────────────────────────────────────────────
# 2. Parts knowledge — every catalog entry, several phrasings each
# ─────────────────────────────────────────────────────────────────────────────
def _parts_examples(rng: random.Random, start: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    index = start

    for key, part in frc_parts.ELECTRONICS.items():
        w, d, h = part["envelope_in"]
        questions = [
            f"How much room do I need to reserve for the {part['name']} on the bellypan?",
            f"What is the {part['name']} and how do I mount it?",
            f"Give me the packaging facts for the {part['name']}.",
        ]
        extra = ""
        if "channels" in part:
            channels = part["channels"]
            extra = (f" It provides {channels['high_current']} high-current channels rated to "
                     f"{channels['high_current_max_a']} A")
            if channels.get("low_current"):
                extra += (f" and {channels['low_current']} low-current channels rated to "
                          f"{channels['low_current_max_a']} A.")
            else:
                extra += "; small loads run on low-amperage breakers in standard channels."
        if "rating_a" in part:
            extra += f" It is rated {part['rating_a']} A."
        if "power" in part:
            power = part["power"]
            extra += (f" Power it from {power['source']} on {power['gauge_awg']} AWG "
                      f"(≤{power['max_a']} A).")
        features = " ".join(f"{item.capitalize()}." for item in part.get("features", []))
        answer = (f"{part['name']} — {part['vendor']}"
                  f"{', ' + part['sku'] if part.get('sku') else ''}. Reserve a "
                  f"{w:g} × {d:g} × {h:g} in envelope and budget {part['mass_lb']} lb."
                  f"{extra} Mounting: {part.get('mount', 'bolt-down')}. {features} "
                  f"{part.get('verify', '')}")
        rows.append(_row(SYSTEM_KNOWLEDGE, rng.choice(questions), answer.strip(), "parts_qa", index))
        index += 1

    for key, motor in frc_parts.MOTORS.items():
        questions = [
            f"What are the {motor['name']} specs and what breaker does it need?",
            f"Should we use the {motor['name']}? Give me the numbers.",
            f"{motor['name']} — free speed, stall torque, stall current, controller?",
        ]
        wire = frc_parts.wire_for_breaker(motor["breaker_a"])
        answer = (f"{motor['name']} ({motor['vendor']}): {motor['free_rpm']} RPM free speed, "
                  f"{motor['stall_nm']} N·m stall torque, {motor['stall_a']} A stall current at 12 V, "
                  f"{motor['mass_lb']} lb. Runs on the {motor['controller']}. Put it on a "
                  f"{motor['breaker_a']} A channel with {wire['preferred_awg']} AWG. "
                  f"{motor['notes'].capitalize()}. {frc_parts.MOTOR_VERIFY}")
        rows.append(_row(SYSTEM_KNOWLEDGE, rng.choice(questions), answer, "parts_qa", index))
        index += 1

    for key, module in frc_parts.SWERVE_MODULES.items():
        ratios = ", ".join(f"{label} {value}:1" for label, value in module["drive_ratios"].items())
        default_motor = frc_parts.MOTORS["neo_550" if key == "maxswerve" else "kraken_x60"]
        speeds = "; ".join(
            f"{label} → {frc_parts.free_speed_fps(frc_parts.MOTORS['kraken_x60'], value, module['wheel_in']):.1f} ft/s"
            for label, value in list(module["drive_ratios"].items())[:3])
        questions = [
            f"Tell me about the {module['name']} swerve module.",
            f"What ratios does the {module['name']} come in and how fast is it on Krakens?",
            f"We are choosing between swerve modules — make the case for the {module['name']}.",
        ]
        answer = (f"{module['name']} ({module['vendor']}): {module['wheel_in']:g} in wheel, "
                  f"{module['plate_in'][0]:g} × {module['plate_in'][1]:g} in mounting footprint, "
                  f"{module['drop_in']:g} in below the mounting face, {module['mass_lb']} lb bare. "
                  f"Drive ratios: {ratios}. Azimuth {module['steer_ratio']:.2f}:1 with "
                  f"{module['motor_orientation']}, {module['encoder']}. On a Kraken X60 the "
                  f"theoretical free speeds are {speeds} — an unloaded ceiling, not an achievable "
                  f"number. {module['notes'].capitalize()}. Steer motor is typically a "
                  f"{default_motor['name']}. {module['verify']}")
        rows.append(_row(SYSTEM_KNOWLEDGE, rng.choice(questions), answer, "parts_qa", index))
        index += 1

    for group, label in ((frc_parts.STRUCTURE, "structure"), (frc_parts.TRANSMISSION, "transmission")):
        for key, item in group.items():
            question = f"When do we use {item['name']} and what is it for?"
            details = []
            if "material" in item:
                details.append(f"Material: {item['material']}.")
            if "section_in" in item:
                details.append(f"Section {item['section_in'][0]:g} × {item['section_in'][1]:g} in, "
                               f"{item.get('wall_in', 0):g} in wall, "
                               f"{item.get('mass_lb_per_ft', 0)} lb/ft.")
            if "pitch_mm" in item:
                details.append(f"{item['pitch_mm']} mm pitch, widths "
                               f"{'/'.join(str(w) for w in item['widths_mm'])} mm.")
            if "pitch_in" in item:
                details.append(f"{item['pitch_in']} in pitch.")
            if "stages" in item:
                details.append(f"Available reductions: {item['stages']}.")
            answer = (f"{item['name']}"
                      f"{' (' + item['vendor'] + ')' if item.get('vendor') else ''}. "
                      f"{' '.join(details)} Use it for: {item['use']}.")
            rows.append(_row(SYSTEM_KNOWLEDGE, question, answer.strip(), "parts_qa", index))
            index += 1

    for row in frc_parts.WIRE_TABLE:
        question = (f"What wire gauge goes on a {row['breaker_a']} A channel?")
        answer = (f"A {row['breaker_a']} A channel needs at least {row['gauge_awg']} AWG; use "
                  f"{row['preferred_awg']} AWG in practice for the voltage drop and the crimp "
                  f"quality. That covers {row['use']}. Size wire from the breaker, never from the "
                  f"motor — the breaker decides the most current that wire will ever carry. "
                  f"Battery leads and the run through the "
                  f"{frc_parts.ELECTRONICS['main_breaker']['rating_a']} A main breaker are "
                  f"{frc_parts.BATTERY_LEAD_AWG} AWG.")
        rows.append(_row(SYSTEM_KNOWLEDGE, question, answer, "parts_qa", index))
        index += 1

    for reference in REFERENCES:
        question = f"What can we learn from the {reference['name']}?"
        answer = (f"{reference['name']} ({reference['kind'].replace('_', ' ')}) is a public "
                  f"reference at {reference['url']}. The transferable lesson is: "
                  f"{reference['lesson']}. Study the pattern and rebuild the geometry yourself — "
                  f"do not copy another team's CAD into your robot without checking their "
                  f"licence and attribution terms.")
        rows.append(_row(SYSTEM_KNOWLEDGE, question, answer, "parts_qa", index))
        index += 1

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 3. Electrical planning off the real power budget
# ─────────────────────────────────────────────────────────────────────────────
def _electrical_examples(rng: random.Random, start: int, count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(count):
        distributor = rng.choice(["pdh", "pdh", "pdp"])
        module_count = rng.choice([4, 4, 0])
        drive_motor = rng.choice(["kraken_x60", "neo_vortex", "falcon_500"])
        mech = [(rng.choice(["neo", "neo_vortex", "kraken_x60", "neo_550"]), name)
                for name in rng.sample(["intake", "shooter", "elevator", "arm", "climber"],
                                       rng.randint(1, 4))]
        pneumatics = rng.random() < 0.3
        spec = {
            "drivetrain": {"motor_key": drive_motor, "module_count": module_count,
                           "steer_motor_key": drive_motor},
            "pneumatics": {"included": pneumatics},
        }
        for motor_key, name in mech:
            key = "manipulator" if name == "arm" else name
            spec[key] = {"included": True, "motor_key": motor_key,
                         "motor_count": 2 if name in {"shooter", "climber"} else 1}
        loads = frc_parts.motor_loads(spec)
        budget = frc_parts.power_budget(loads, distributor)

        described = ", ".join(f"{frc_parts.MOTORS[key]['name']} on the {name}" for key, name in mech)
        question = (f"Plan the power distribution for a robot with "
                    f"{module_count} swerve modules on {frc_parts.MOTORS[drive_motor]['name']}s, "
                    f"{described}{', and a compressor' if pneumatics else ''}. "
                    f"We are using the {frc_parts.ELECTRONICS[distributor]['name']}.")
        lines = [f"{budget['distributor']}: {budget['channels_used']} channels used, "
                 f"{budget['high_current_free']} high-current and {budget['low_current_free']} "
                 f"low-current channels free.", ""]
        lines += [f"- Ch {row['channel']}: {row['load']} — {row['breaker_a']} A breaker, "
                  f"{row['wire_awg']} AWG" for row in budget["assignments"]]
        lines.append("")
        lines.append(f"Everything runs through the {budget['main_breaker_a']} A main breaker on "
                     f"{budget['battery_lead_awg']} AWG battery leads.")
        lines += [f"- {note}" for note in budget["notes"]]
        if budget["unassigned"]:
            lines.append(f"- Over capacity: {', '.join(budget['unassigned'])} will not fit. Add a "
                         "second distribution module or cut load.")
        rows.append(_row(SYSTEM_KNOWLEDGE, question, "\n".join(lines), "electrical", start + offset))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 4. Techniques and 5. diagnosis
# ─────────────────────────────────────────────────────────────────────────────
_TECHNIQUE_QUESTIONS = [
    "Why does '{name}' matter?",
    "Our mentor said to do '{name}'. What is the reasoning?",
    "Explain '{name}' and what goes wrong without it.",
    "We are designing the {area}. What is '{name}' and how do we apply it?",
]


def _technique_examples(rng: random.Random, start: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    index = start
    for area, items in TECHNIQUES.items():
        for item in items:
            question = rng.choice(_TECHNIQUE_QUESTIONS).format(name=item["name"], area=area)
            answer = (f"{item['name']} — {item['why']} How to apply it: {item['how']} The failure "
                      f"it prevents: {item['pitfall']}")
            rows.append(_row(SYSTEM_KNOWLEDGE, question, answer, "technique", index))
            index += 1
    return rows


_DIAGNOSES = [
    ("The roboRIO reboots whenever we accelerate hard.",
     "That is a brownout, not a code bug. Summed stall current across the drivetrain is pulling "
     "the bus below the roboRIO's minimum. Set per-motor supply current limits, check every "
     "crimp from the battery through the main breaker to the distribution hub, and confirm the "
     "battery is charged and not near end of life. A 12 Ah-internal-resistance SLA sags hard "
     "under an eight-motor stall.", "electrical"),
    ("We keep losing communications mid-match.",
     "Treat it as an RF and connection problem before touching code. Move the radio high and "
     "clear of aluminium and motor bundles, check that the antennas are unobstructed on both "
     "faces, and verify the radio's power lead is strain-relieved — a wire that vibrates loose "
     "looks exactly like a field fault.", "electrical"),
    ("Our intake picks up the gamepiece maybe half the time.",
     "Measure the compression instead of adjusting it by eye. Start near 0.5 in on compliant "
     "wheels, then change one variable at a time on a test rig. Also check that the roller "
     "surface speed exceeds the approach speed — an intake that is too slow pushes the "
     "gamepiece away.", "intake"),
    ("The shooter is accurate in the pit but inconsistent in matches.",
     "Look at recovery time and bus voltage, not the setpoint. Between shots the flywheel has to "
     "return to speed against a sagging battery. Add flywheel inertia, give the controller "
     "headroom, and gate the feeder on a measured at-speed condition rather than a timer.",
     "shooter"),
    ("Our arm sags and cannot hold position at full extension.",
     "The reduction was almost certainly sized on speed rather than holding torque. The worst "
     "case is holding horizontal at full extension. Recompute the reduction from that holding "
     "torque, add gravity feedforward, and check the shoulder shaft — rounded hex under "
     "reversing load shows up as backlash. MAXSpline or a keyed shaft fixes it.", "arm"),
    ("The elevator binds partway up.",
     "Binding is nearly always slop or rigging, not power. Check that each stage is a rigid "
     "subassembly with opposed, preloaded bearing blocks, and verify the cascade rigging stage "
     "by stage — a stage out of sync tears itself apart. Then check the tube for a twist.",
     "elevator"),
    ("The robot drops off the rung after the match ends.",
     "Motor brake mode stops holding when power cuts at the buzzer. You need a mechanical "
     "ratchet on the winch drum. Also verify the load path: the tower has to tie into two rails "
     "and the bellypan in shear, not hang off a single crossmember.", "climber"),
    ("Our swerve drives into the wall the moment we enable.",
     "Azimuth zeroing. The absolute encoder offsets do not match the physical zero. Store the "
     "offsets in code, build a physical zeroing jig so the procedure is repeatable, and re-verify "
     "after any belt or module service.", "drivetrain"),
    ("Random drivetrain stutter that the code team cannot reproduce.",
     "Check CAN bus utilisation before the code. Eight drive and steer controllers plus "
     "mechanism controllers on one bus will saturate it. Move the drivetrain to a dedicated CAN "
     "FD bus on a CANivore and leave mechanisms on the roboRIO bus.", "drivetrain"),
    ("We are 9 lb over weight a week before the event.",
     "Cut structure before you cut a subsystem, in this order: pocket the bellypan and every "
     "plate, thin non-structural standoffs, replace 0.125 in tube with 0.100 in where loads are "
     "low, and shorten the climber tower to the minimum stowed height. Weigh after each change "
     "— estimates are for trade studies, the scale is the truth.", "chassis"),
    ("A wire broke at the crimp on our intake pivot during a match.",
     "The harness had no service loop. Leave slack at every moving joint and anchor the bundle on "
     "both sides of the joint so the motion never loads a crimp. Re-crimp with the right die and "
     "pull-test each one.", "electrical"),
    ("Inspection failed us on the main breaker.",
     "It has to be reachable and visible without removing anything, and it must be the single "
     "point of disconnect — nothing may tap power upstream of it. Rebracket it to an outside "
     "frame edge and remove any accessory wired ahead of it.", "electrical"),
    ("Our intake jams or grabs two gamepieces at once.",
     "Add a controlled handoff instead of one fast roller. Run series rollers into a positive "
     "indexer stage with a beam-break so exactly one piece advances to a known position, and gate "
     "the next intake on that sensor. Also check the roller-to-roller center distance is squishing "
     "the piece about 0.5 in, not pinching two.", "intake"),
    ("The intake spins but shoves the gamepiece away instead of pulling it in.",
     "The roller surface speed is below the robot's approach speed, so the contact point pushes "
     "outward. Gear the roller (surface speed = roller RPM x pi x diameter) to about 1.5–2x your "
     "fastest approach speed, and confirm the wheel durometer actually grips this gamepiece — a "
     "wheel too hard skips across a rigid piece.", "intake"),
    ("Our over-bumper intake binds partway through its deploy.",
     "Route the roller power coaxially through the pivot so belt tension does not change across the "
     "arc, put metal hard stops at both ends of the deploy, and hold against them with a current "
     "limit. A belt that goes slack mid-arc or a plastic stop being ground by a 40 A motor is the "
     "usual cause.", "intake"),
]


def _diagnosis_examples(start: int) -> list[dict[str, Any]]:
    return [_row(SYSTEM_KNOWLEDGE, f"{symptom} What is going on and how do we fix it?",
                 answer, "diagnosis", start + offset)
            for offset, (symptom, answer, _area) in enumerate(_DIAGNOSES)]


# ─────────────────────────────────────────────────────────────────────────────
# 6. CAD geometry — the family that takes the model from "a shooter" to the parts
#    a shooter is made of.  Targets come from frc_cad, so the geometry the model is
#    trained to emit is byte-identical to the geometry the viewer and the BOM read.
# ─────────────────────────────────────────────────────────────────────────────
_CAD_PROMPTS = [
    "Give me the {label} assembly for this robot at part level.",
    "Break the {label} down into individual parts with dimensions and positions.",
    "I need the CAD geometry for the {label} — every tube, plate, bearing and shaft.",
    "Model the {label} for this design. Stock sections only, real bores.",
    "What parts make up the {label}, and where does each one sit?",
]

# Each request carries the season it belongs to, so the CAD families are conditioned the same
# way the Design Studio is. The pairing matters: a 27 in frame is a 2026 answer and a 28 in
# frame is a 2025 one, and a corpus that mixes them teaches the model that frame size is noise.
_CAD_REQUESTS: list[tuple[str, str]] = [
    ("28x28 REEFSCAPE robot, three-stage belt-rigged cascade tower, coaxial slapdown intake, "
     "wristed carriage arm and a deep climb.", "2025-reefscape"),
    ("27 inch REBUILT robot on MK4n modules with a dual-roller over-bumper intake, a circular "
     "spindexer and a turreted dual flywheel hooded shooter, plus dual telescoping winch hooks.",
     "2026-rebuilt"),
    ("Rookie team, 28 inch frame, ground-to-feeder tunnel intake and a two-stage continuous "
     "elevator. Keep it simple.", "2025-reefscape"),
    ("30x28 CRESCENDO robot, staged accelerator-and-flywheel shooter on Krakens, four-bar "
     "over-bumper intake, single pivoting hook climber.", "2024-crescendo"),
    ("26x26 low-profile robot with a horizontal series-roller intake, a belt-floor hopper and "
     "a fixed-angle flywheel shooter that fits under the trench.", "2026-rebuilt"),
    ("Experienced team, 28x28 on MK5i modules. Variable-hood flywheel shooter, active floor "
     "sweeper with indexer, winch-driven carriage climb.", "2025-reefscape"),
    ("29 inch robot, telescoping box elevator with a 3 stage lift, pivoting compliant-wheel "
     "intake and a four-bar linkage arm.", "2025-reefscape"),
    ("27x27 swerve robot, funnel-to-tower hopper, single flywheel backspin shooter, "
     "under-bumper roller intake, no climber.", "2026-rebuilt"),
    ("Just an MK4i swerve module on a Kraken X60 at L2.", "offseason"),
    ("26x29 west-coast drivebase on four Krakens with a twin-lane belt hopper feeding two "
     "fixed hooded shooters.", "2026-rebuilt"),
]

_CAD_LABELS = {"intake": "intake", "hopper": "hopper", "shooter": "shooter",
               "elevator": "elevator", "manipulator": "arm", "climber": "climber",
               "chassis": "chassis", "swerve_0": "swerve module", "drivetrain": "drivetrain",
               "electrical": "control system"}


# Mechanism types deliberately kept OUT of the CAD training families. The eval asks for them
# anyway: if the model can only build what it was shown, it memorised; if it can build these,
# it learned how a mechanism goes together. Nothing stops these appearing in `design_intent`
# — the model still has to know the names, it just never sees their geometry.
#
# The 2026 mechanisms are held out on the same principle and for a sharper reason: the point of
# training on this season is not to reproduce this season's robots. A model that can build a
# serpentine tunnel indexer it was never shown has learned what an indexer *is*.
CAD_HELD_OUT = {
    "ground-to-feeder tunnel intake",
    "variable-hood flywheel shooter",
    "telescoping box",
    "four-bar linkage arm",
    "winch-driven carriage climb",
    "serpentine tunnel indexer",
    "paddle-wheel agitator hopper",
}

_FRAMES = [(24, 24), (26, 26), (26, 30), (27, 27), (28, 24), (28, 28), (28, 30), (28, 32),
           (29, 29), (30, 28), (30, 30), (32, 28)]
_TEAM_VOICE = [
    "{w}x{l} {drive} robot with {mechs}.",
    "We want a {w} inch {drive} robot that runs {mechs}. Model it at part level.",
    "{drive} robot, {w} × {l} in frame, {mechs}. Show me the parts.",
    "Rookie-friendly {w}x{l} {drive} build: {mechs}.",
    "Experienced team, {w} inch {drive}. {mechs}, packaged tight.",
]


def _legal_frame(season_key: str, width: float, length: float) -> tuple[float, float]:
    """A frame that fits the season, so no CAD target is built on an illegal chassis."""
    budget = frame_budget(SEASONS[season_key], float(width), float(length))
    return budget["width_in"], budget["length_in"]


def _varied_cad_requests(rng: random.Random, count: int) -> list[tuple[str, str]]:
    """Sample many genuinely different robots, each paired with a season.

    Variety here is the whole point. A corpus built from one frame size and one mechanism
    combination teaches a model that a chassis *is* a fixed list of parts. Sampling frames,
    drives and mechanism mixes means the structure of the answer has to follow the request.
    Sampling the season on top of that means it has to follow the constraints too.
    """
    intakes = [t for t in INTAKE_TYPES if t not in CAD_HELD_OUT]
    hoppers = [t for t in HOPPER_TYPES if t not in CAD_HELD_OUT]
    shooters = [t for t in SHOOTER_TYPES if t not in CAD_HELD_OUT]
    elevators = [t for t in ELEVATOR_TYPES if t not in CAD_HELD_OUT]
    arms = [t for t in ARM_TYPES if t not in CAD_HELD_OUT]
    climbers = [t for t in CLIMBER_TYPES if t not in CAD_HELD_OUT]
    requests: list[tuple[str, str]] = []
    for _ in range(count):
        season_key = rng.choice(["2026-rebuilt", "2026-rebuilt", "2025-reefscape",
                                 "2025-reefscape", "offseason"])
        width, length = _legal_frame(season_key, *rng.choice(_FRAMES))
        drive = rng.choice(["swerve", "swerve", "swerve", "MK4i swerve", "MK4n swerve",
                            "MK5i swerve", "west-coast", "tank"])
        mechs: list[str] = []
        if rng.random() < 0.85:
            mechs.append(f"a {rng.choice(intakes)}")
        shoots = rng.random() < 0.55
        if shoots:
            mechs.append(f"a {rng.choice(shooters)}")
            if rng.random() < 0.7:
                mechs.append(f"a {rng.choice(hoppers)}")
        if rng.random() < 0.55:
            mechs.append(f"a {rng.randint(1, 3)} stage {rng.choice(elevators)} elevator")
        if rng.random() < 0.40:
            mechs.append(f"a {rng.choice(arms)}")
        if rng.random() < 0.50:
            mechs.append(rng.choice(climbers))
        if not mechs:
            mechs.append("a drivebase only")
        joined = ", ".join(mechs[:-1]) + (" and " + mechs[-1] if len(mechs) > 1 else mechs[0])
        requests.append((rng.choice(_TEAM_VOICE).format(
            w=f"{width:g}", l=f"{length:g}", drive=drive, mechs=joined), season_key))
    return requests


def _typed_cad_requests(rng: random.Random) -> list[tuple[str, str]]:
    """Cover every enumerated mechanism type at least once.

    The hand-written requests above read like real team asks but only touch a handful of the
    vocabulary. Walking the enums guarantees the model sees the geometry that belongs to each
    named type — that mapping is what stops a "ground-to-feeder tunnel intake" from being
    modelled as whatever intake the model saw most often.
    """
    requests: list[tuple[str, str]] = []
    for options, phrase in ((INTAKE_TYPES, "{} intake"), (HOPPER_TYPES, "{}"),
                            (SHOOTER_TYPES, "{}"),
                            (ELEVATOR_TYPES, "{} elevator"), (ARM_TYPES, "{}"),
                            (CLIMBER_TYPES, "{}")):
        for option in options:
            if option in CAD_HELD_OUT:
                continue
            season_key = rng.choice(["2026-rebuilt", "2025-reefscape", "offseason"])
            width, _ = _legal_frame(season_key, rng.choice([26, 27, 28, 29, 30]),
                                    rng.choice([26, 27, 28, 29, 30]))
            mechanism = phrase.format(option).replace("intake intake", "intake")
            requests.append((f"{width:g}x{width:g} swerve robot built around a {mechanism}. "
                             f"Model it at part level.", season_key))
    return requests


def _structure_signature(assembly: dict[str, Any]) -> tuple[Any, ...]:
    """What a model would learn to reproduce: the assembly's part list, ignoring dimensions."""
    return (assembly["id"].split("_")[0],
            tuple(f"{f['t']}:{f['n']}" for f in assembly["features"]))


def _cad_geometry_examples(rng: random.Random, start: int, count: int,
                           per_structure: int = 3) -> list[dict[str, Any]]:
    """One example per (request, assembly), capped so no single structure dominates.

    Sampling robots is not enough on its own. A swerve module is a catalog part and a control
    system is a fixed set of boxes, so hundreds of sampled robots still yield hundreds of
    near-identical targets for those assemblies — and a model trained on that learns to recite
    one part list rather than to derive it. Capping repeats per distinct structure keeps the
    breadth and drops the rote, which is the difference between a model that generalises to a
    mechanism it has not seen and one that has simply memorised the ones it has.
    """
    rows: list[dict[str, Any]] = []
    # Sample at least as many robots as the target row count: the per-structure cap discards
    # repeats, so a request pool smaller than the ask silently under-fills the family.
    requests = _CAD_REQUESTS + _typed_cad_requests(rng) + _varied_cad_requests(rng, max(260, count))
    pairs: list[tuple[str, str, dict[str, Any]]] = []
    for request, season_key in requests:
        spec = build_robot_spec(request, use_model=False, season=season_key)
        for assembly in spec["cad"]["assemblies"]:
            if assembly["id"] in _CAD_LABELS and assembly["features"]:
                pairs.append((request, season_key, assembly))
    rng.shuffle(pairs)

    seen: dict[tuple[Any, ...], int] = {}
    kept: list[tuple[str, str, dict[str, Any]]] = []
    for request, season_key, assembly in pairs:
        signature = _structure_signature(assembly)
        if seen.get(signature, 0) >= per_structure:
            continue
        seen[signature] = seen.get(signature, 0) + 1
        kept.append((request, season_key, assembly))
        if len(kept) >= count:
            break

    for offset, (request, season_key, assembly) in enumerate(kept):
        label = _CAD_LABELS[assembly["id"]]
        user = (rng.choice(_CAD_PROMPTS).format(label=label)
                + f"\n\nSeason: {SEASONS[season_key]['label']}\n\nRobot:\n"
                + _fenced(request))
        answer = json.dumps(assembly, separators=(",", ":"), sort_keys=False)
        rows.append(_row(SYSTEM_CAD, user, answer, "cad_geometry", start + offset))
    return rows


def _cad_qa_examples(rng: random.Random, start: int, extra: int = 0) -> list[dict[str, Any]]:
    """Dimensioned questions whose answers are computed from the same CAD tree.

    These are the questions a mentor asks at a design review, and every number in the answer
    is derived, not remembered — which is what stops the model inventing plausible dimensions.
    """
    rows: list[dict[str, Any]] = []
    index = start
    requests = _CAD_REQUESTS + _typed_cad_requests(rng)
    if extra:
        requests += _varied_cad_requests(rng, extra)
    for request, season_key in requests:
        spec = build_robot_spec(request, use_model=False, season=season_key)
        cad, frame = spec["cad"], spec["frame"]

        cuts = spec["cut_list"][:6]
        lines = [f"Cut list for the {frame['width_in']:g} × {frame['length_in']:g} in design "
                 f"({cad['feature_total']} modelled features across "
                 f"{len(cad['assemblies'])} assemblies):"]
        for row in cuts:
            lines.append(f"- {row['qty']}× {row['section_in'][0]:g}x{row['section_in'][1]:g}x"
                         f"{row['wall_in']:.3f} in tube at {row['length_in']:g} in — "
                         f"{', '.join(row['used_in'])}")
        lines.append("Every member is a catalog stock section so the list is orderable as written. "
                     "Add your own kerf and squaring allowance, and check each length against the "
                     "assembly before cutting.")
        rows.append(_row(SYSTEM_KNOWLEDGE,
                         f"Give me the tube cut list for this robot.\n\n{_fenced(request)}",
                         "\n".join(lines), "cad_qa", index))
        index += 1

        elevator = spec.get("elevator") or {}
        if elevator.get("included"):
            answer = (
                f"{elevator['stages']}-stage {elevator['architecture']}: "
                f"{elevator['stage_travel_in']:g} in of travel per stage gives "
                f"{elevator['travel_in']:g} in at the carriage, and the carriage moves "
                f"{elevator['carriage_speed_multiple']}× the drum payout. Keep "
                f"{elevator['stage_overlap_in']:g} in of overlap remaining at full extension — "
                f"that overlap, with a bearing block at each end of it, is the only thing "
                f"resisting the tip moment. The tower is two 2x1 uprights "
                f"{elevator['upright_span_in']:g} in apart, tied top and bottom, rigged in "
                f"{elevator['rigging']} off a {elevator['drum_diameter_in']:g} in drum through "
                f"{elevator['reduction']}. Size that reduction on holding torque at full "
                f"extension, not free speed, and verify the extension against the current manual.")
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             f"How much does the elevator actually travel, and what sets it?"
                             f"\n\n{_fenced(request)}", answer, "cad_qa", index))
            index += 1

        shooter = spec.get("shooter") or {}
        if shooter.get("included"):
            answer = (
                f"{shooter['flywheel_diameter_in']:g} in flywheels on {shooter['motor']}s through "
                f"{shooter['gear_reduction']} spin at a surface speed of about "
                f"{shooter['flywheel_surface_speed_fps']:g} ft/s. "
                + (f"With {shooter['flywheel_stages']} counter-rotating stages down the barrel the "
                   f"gamepiece leaves at roughly {shooter['exit_velocity_fps']:g} ft/s."
                   if shooter['stacked'] else
                   f"Squeezed between one wheel and a stationary hood the gamepiece leaves at "
                   f"about half that, {shooter['exit_velocity_fps']:g} ft/s — size the range on "
                   f"the exit velocity, not the surface speed.")
                + f" Compression is {shooter['compression_in']:g} in on a "
                f"{shooter['gamepiece_diameter_in']:g} in gamepiece, hood adjustable over "
                f"{shooter['hood_angle_deg'][0]}–{shooter['hood_angle_deg'][1]}°. "
                f"About {shooter['flywheel_mass_lb']:g} lb of flywheel gives roughly "
                f"{shooter['spinup_time_s']:g} s to recover between shots; recovery time is what "
                f"sets your cycle rate, not peak RPM. Verify on a real shot, not on paper.")
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             f"What does this shooter actually do — surface speed, exit velocity "
                             f"and recovery?\n\n{_fenced(request)}", answer, "cad_qa", index))
            index += 1

        climber = spec.get("climber") or {}
        if climber.get("included"):
            answer = (
                f"{climber['type']}: {climber['load_paths']} load path"
                f"{'s' if climber['load_paths'] > 1 else ''} carrying "
                f"{climber['lift_load_lb']:g} lb, so each rope sees about "
                f"{climber['rope_tension_lbf']:g} lbf. On a "
                f"{climber['winch_drum_diameter_in']:g} in drum that is "
                f"{climber['drum_torque_nm']:g} N·m at the drum, which is why the winch runs "
                f"{climber['reduction']} off {climber['motor_count']}× {climber['motor']}. "
                f"The drum radius is a lever working against you — a bigger drum takes more "
                f"rope but costs you reduction. {climber['travel_in']:g} in of extension from a "
                f"{climber['stowed_height_in']:g} in stowed height, held by "
                f"{climber['ratchet']} rather than motor brake mode. Pull-test the engagement "
                f"and check the stowed height against the current manual.")
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             f"Size the climber winch for me.\n\n{_fenced(request)}",
                             answer, "cad_qa", index))
            index += 1

        arm = spec.get("manipulator") or {}
        if arm.get("included"):
            segs = ", ".join(f"{v:g} in" for v in arm["segment_lengths_in"])
            answer = (
                f"{arm['type']} reaching {arm['reach_in']:g} in in "
                f"{arm['segments']} segment{'s' if arm['segments'] > 1 else ''} ({segs}), on a "
                f"{arm['shaft']} dead axle {arm['shoulder_height_in']:g} in above the bellypan "
                f"with a bearing block in each side plate so the shaft is in double shear. "
                f"Worst case is holding it horizontal at full extension, about "
                f"{arm['holding_torque_nm']:g} N·m, which is what the {arm['reduction']} "
                f"reduction is sized on — {arm['gravity_compensation']}. Shoulder sweep "
                f"{arm['shoulder_pivot_deg'][0]}–{arm['shoulder_pivot_deg'][1]}° with metal hard "
                f"stops just outside the software limits, ending in a {arm['end_effector']}. "
                f"Check the extension limit against the current manual.")
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             f"How is this arm actually built and what sets the reduction?"
                             f"\n\n{_fenced(request)}", answer, "cad_qa", index))
            index += 1

        intake = spec.get("intake") or {}
        if intake.get("included"):
            answer = (
                f"{intake['type']}: {intake['roller_count']} roller"
                f"{'s' if intake['roller_count'] > 1 else ''} of "
                f"{intake['roller_diameter_in']:g} in diameter on 1/2 in hex, "
                f"{intake['roller_center_distance_in']:g} in between centres — that centre "
                f"distance is what sets compression, {intake['compression_in']:g} in on the "
                f"gamepiece, not spring preload. {intake['compliant_wheel']} through "
                f"{intake['gear_reduction']} off a {intake['motor']} gives about "
                f"{intake['roller_surface_speed_fps']:g} ft/s of surface speed, which has to stay "
                f"above your approach speed or the intake pushes the piece away. Power arrives by "
                f"{intake['power_path']}, {intake['width_in']:g} in wide, deployed by "
                f"{intake['deploy']}. Bearings in both side plates on every roller shaft.")
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             f"Walk me through the intake geometry.\n\n{_fenced(request)}",
                             answer, "cad_qa", index))
            index += 1

        hopper = spec.get("hopper") or {}
        if hopper.get("included"):
            answer = (
                f"{hopper['type']}: a {hopper['floor_width_in']:g} × "
                f"{hopper['floor_depth_in']:g} in floor with {hopper['wall_height_in']:g} in "
                f"walls, funnelled into {hopper['lanes']} exit lane"
                f"{'s' if hopper['lanes'] > 1 else ''} of {hopper['exit_lane_width_in']:g} in — "
                f"one {hopper['gamepiece_diameter_in']:g} in gamepiece plus clearance, never two. "
                f"Capacity is volumetric: floor area × 80% of the wall height × 60% random-pour "
                f"packing ÷ one piece ≈ {hopper['capacity_estimate']} pieces. "
                f"{hopper['wheel_count']} × {hopper['wheel_diameter_in']:g} in index wheels stand "
                f"{hopper['wheel_proud_in']:g} in proud of the floor — flush and the piece rides "
                f"the floor and slips, higher and it climbs over — driven through "
                f"{hopper['gear_reduction']} off a {hopper['motor']} for roughly "
                f"{hopper['feed_rate_per_s']:g} pieces per second. {hopper['sensor']}, because a "
                f"timer feeds two and jams the shooter. {hopper['access']}. "
                f"{hopper['caveat']}")
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             f"How much does the hopper hold and how fast does it feed?"
                             f"\n\n{_fenced(request)}", answer, "cad_qa", index))
            index += 1

        # Rule check as its own question: "is this legal" is what a team asks before a design
        # review, and the answer has to name the rule and the number, not reassure.
        season = SEASONS[spec["season"]["key"]]
        checks = spec.get("rule_check") or []
        if checks:
            lines = [f"Checked against {season['label']}:"]
            lines += [f"- {row['check']} ({row['rule']}): "
                      f"{'passes' if row['ok'] else 'FAILS'} — {row['detail']}."
                      + ("" if row["ok"] else f" {row['fix']}")
                      for row in checks]
            lines.append("")
            lines.append("These are the four things a synthesised robot can actually get wrong. "
                         "Passing them means nothing was caught here — it is not an inspection, "
                         "and rules move by team update, so check the current manual.")
            rows.append(_row(SYSTEM_KNOWLEDGE,
                             f"Is this design legal for {season['label']}?\n\n{_fenced(request)}",
                             "\n".join(lines), "cad_qa", index))
            index += 1
    return rows


# ─────────────────────────────────────────────────────────────────────────────
def _load_existing(dataset_dir: Path) -> list[dict[str, Any]]:
    """Read every split of an already-generated corpus so it can be folded in."""
    rows: list[dict[str, Any]] = []
    for split in ("train", "valid", "test"):
        path = dataset_dir / f"{split}.jsonl"
        if not path.is_file():
            continue
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    if not rows:
        raise FileNotFoundError(f"no split files found under {dataset_dir}")
    return rows


def generate(output_dir: Path, intent_count: int = 320, electrical_count: int = 90,
             seed: int = 1701, merge: list[Path] | None = None,
             cad_count: int = 90, season_math_count: int = 260,
             binder_extra: int = 0, binder_cap: int | None = None,
             qa_extra: int = 0, cad_binder_count: int = 0) -> dict[str, Any]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    rows += [_intent_example(rng, index) for index in range(intent_count)]
    rows += _season_rule_examples(rng, len(rows))
    rows += _season_math_examples(rng, len(rows), season_math_count)
    rows += _strategy_examples(rng, len(rows))
    binder_requests = _CAD_REQUESTS + _typed_cad_requests(rng)[:24]
    if binder_extra:
        binder_requests += _varied_cad_requests(rng, binder_extra)
    rows += _binder_examples(rng, len(rows), binder_requests, cap=binder_cap)
    if cad_binder_count:
        audit_requests = _varied_cad_requests(rng, max(24, cad_binder_count // 4))
        rows += _cad_binder_audit_examples(
            rng, len(rows), audit_requests, cap=cad_binder_count)
    rows += _parts_examples(rng, len(rows))
    rows += _electrical_examples(rng, len(rows), electrical_count)
    rows += _technique_examples(rng, len(rows))
    rows += _diagnosis_examples(len(rows))
    rows += _cad_geometry_examples(rng, len(rows), cad_count)
    rows += _cad_qa_examples(rng, len(rows), extra=qa_extra)

    families: dict[str, int] = {}
    for row in rows:
        family = row["metadata"]["family"]
        families[family] = families.get(family, 0) + 1

    # Folding in the general design corpus keeps the broader hardware ability the model
    # already has; training on FRC alone would trade one narrowness for another.
    merged_from: list[str] = []
    for source in merge or []:
        existing = _load_existing(source)
        rows.extend(existing)
        families[f"merged:{source.name}"] = len(existing)
        merged_from.append(str(source))

    rng.shuffle(rows)
    total = len(rows)
    train_end, valid_end = round(total * 0.80), round(total * 0.90)
    splits = {"train": rows[:train_end], "valid": rows[train_end:valid_end], "test": rows[valid_end:]}

    output_dir.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, items in splits.items():
        payload = "".join(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n" for item in items)
        (output_dir / f"{name}.jsonl").write_text(payload)
        hashes[name] = hashlib.sha256(payload.encode()).hexdigest()

    manifest = {
        "version": output_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "generator": "kale_training.generate_frc_corpus",
        "seed": seed,
        "row_count": total,
        "family_counts": families,
        "merged_from": merged_from,
        "split_counts": {name: len(items) for name, items in splits.items()},
        "sha256": hashes,
        "catalog": frc_parts.catalog_digest(),
        "cad_schema": CAD_VERSION,
        "season_model": SEASON_VERSION,
        "seasons": {key: SEASONS[key]["label"] for key in SEASONS},
        "cad_held_out": sorted(CAD_HELD_OUT),
        "license": "internal",
        "provenance": ("Derived from Kale's own FRC parts catalog and technique library. No vendor "
                       "prose, CAD, meshes or user data copied. Published specifications are "
                       "restated as engineering facts with verification caveats attached."),
        "reference_policy": ("Public manuals and design libraries inform topic coverage only; "
                             "exact rules and vendor dimensions must be checked at use time."),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--intent-count", type=int, default=320)
    parser.add_argument("--electrical-count", type=int, default=90)
    parser.add_argument("--cad-count", type=int, default=90)
    parser.add_argument("--season-math-count", type=int, default=260)
    parser.add_argument("--binder-extra", type=int, default=0,
                        help="additional sampled robots feeding the binder family")
    parser.add_argument("--binder-cap", type=int, default=None,
                        help="hard cap on binder rows after sampling")
    parser.add_argument("--qa-extra", type=int, default=0,
                        help="additional sampled robots feeding the cad_qa family")
    parser.add_argument("--cad-binder-count", type=int, default=0,
                        help="CAD-grounded technical-binder audit rows")
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument("--merge", nargs="*", default=[],
                        help="existing processed dataset dirs to fold in (e.g. datasets/processed/design-v2)")
    args = parser.parse_args()
    manifest = generate(Path(args.out), args.intent_count, args.electrical_count, args.seed,
                        [Path(item) for item in args.merge], cad_count=args.cad_count,
                        season_math_count=args.season_math_count,
                        binder_extra=args.binder_extra, binder_cap=args.binder_cap,
                        qa_extra=args.qa_extra, cad_binder_count=args.cad_binder_count)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
