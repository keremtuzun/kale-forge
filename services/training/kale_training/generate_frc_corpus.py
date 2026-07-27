"""Generate the FRC parts-and-techniques SFT corpus.

Every example is derived from the SAME catalog the runtime uses
(`app.services.frc_parts` and `app.services.frc_robot_knowledge`), so the model is trained
on exactly the vocabulary, part numbers, ratios and design rules the Design Studio will hand
it at inference time.  Training data and runtime knowledge cannot drift apart.

Seven example families:

* ``design_intent`` — the real inference task: a team request in, one schema-valid design
  intent JSON out.  This is the family that stops the model from answering every prompt with
  the same robot.
* ``parts_qa``      — dimensioned facts about every catalog part.
* ``electrical``    — channel, breaker and wire-gauge planning off the power budget.
* ``technique``     — why a build technique exists and what it prevents.
* ``diagnosis``     — a competition symptom in, the likely cause and fix out.
* ``cad_geometry``  — a request and one subsystem in, that subsystem's assembly out as
  dimensioned parts in Kale's CAD schema.  This is the family that takes the model from
  naming a mechanism to describing the parts it is made of.
* ``cad_qa``        — cut lists, travel, exit velocity, winch torque and joint geometry,
  every number computed from the same CAD tree rather than remembered.

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
from app.services.frc_robot_knowledge import PROFILES, REFERENCES, TECHNIQUES  # noqa: E402
from app.services.security import fence_user_content  # noqa: E402
from app.services.robot_spec import (  # noqa: E402
    ARM_TYPES,
    CLIMBER_TYPES,
    ELEVATOR_TYPES,
    INTAKE_TYPES,
    SHOOTER_TYPES,
    SYSTEM_DESIGN,
    build_robot_spec,
    intent_vocabulary_block,
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
    "2026-low-profile": ["2026", "REBUILT", "fuel"],
    "2025-reefscape": ["2025", "REEFSCAPE", "coral and algae"],
    "2024-note-shooter": ["2024", "CRESCENDO", "note"],
    "reference-bare-swerve": ["off-season", "training", "reference"],
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


def _intent_example(rng: random.Random, index: int) -> dict[str, Any]:
    profile_key = rng.choice(list(PROFILES))
    profile = PROFILES[profile_key]
    width = rng.choice([26, 27, 28, 28, 28, 29, 30])
    length = width if rng.random() < 0.75 else rng.choice([26, 27, 28, 30])
    season = rng.choice(_SEASON_WORDS[profile_key])
    goal = rng.choice(_GOALS)
    experience = rng.choice(_EXPERIENCE)
    prompt = rng.choice(_ROLE_TEMPLATES).format(
        experience=experience, season=season, w=width, l=length, goal=goal)

    subsystems = list(profile["default_subsystems"])
    if "climb" in goal and "climber" not in subsystems:
        subsystems.append("climber")
    if "without a shooter" in goal or "never touches the floor" not in goal and rng.random() < 0.2:
        subsystems = [name for name in subsystems if name != "shooter"] or ["intake"]
    if "one job perfectly" in goal:
        subsystems = subsystems[:2]
    if "from range" in goal and "shooter" not in subsystems:
        subsystems.append("shooter")
    order = ("intake", "shooter", "elevator", "arm", "climber")
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

    intent = {"subsystems": subsystems, "drive_type": drive, "intake_type": intake_type,
              "shooter_type": shooter_type, "arm_type": arm_type, "climber_type": climber_type,
              "elevator_architecture": elevator_arch, "elevator_stages": stages,
              "pneumatics": bool(rng.random() < 0.25),
              "design_notes": notes[:5], "risks": risks[:4]}

    stated = {"frame_in": [width, length], "drive_type": None, "subsystems_stated": {}}
    # The vocabulary block comes from robot_spec so training and inference present the model
    # with byte-identical options; it is the same function the Design Studio calls.
    user = ("Design an FRC robot for this request. Fields already fixed by the team are given as "
            "'stated', repeat them unchanged and decide only the rest.\n\n"
            + intent_vocabulary_block()
            + f"\n\nstated: {stated}\n\nTeam request:\n" + _fenced(prompt))
    return _row(SYSTEM_DESIGN, user, json.dumps(intent, separators=(",", ":"), sort_keys=True),
                "design_intent", index)


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

_CAD_REQUESTS = [
    "28x28 REEFSCAPE robot, three-stage belt-rigged cascade tower, coaxial slapdown intake, "
    "wristed carriage arm and a deep climb.",
    "27 inch 2026 robot on MK4n modules with a dual-roller over-bumper intake, a turreted dual "
    "flywheel hooded shooter and dual telescoping winch hooks.",
    "Rookie team, 28 inch frame, ground-to-feeder tunnel intake and a two-stage continuous "
    "elevator. Keep it simple.",
    "30x28 CRESCENDO robot, staged accelerator-and-flywheel shooter on Krakens, four-bar "
    "over-bumper intake, single pivoting hook climber.",
    "26x26 low-profile robot with a horizontal series-roller intake, a double-jointed arm and "
    "a chain-rigged continuous tower.",
    "Experienced team, 28x28 on MK5i modules. Variable-hood flywheel shooter, active floor "
    "sweeper with indexer, winch-driven carriage climb.",
    "29 inch robot, telescoping box elevator with a 3 stage lift, pivoting compliant-wheel "
    "intake and a four-bar linkage arm.",
    "28x28 swerve robot, fixed-angle flywheel shooter, under-bumper roller intake, no climber.",
    "Just an MK4i swerve module on a Kraken X60 at L2.",
    "30x30 west-coast drivebase on six Krakens with a single flywheel backspin shooter.",
]

_CAD_LABELS = {"intake": "intake", "shooter": "shooter", "elevator": "elevator",
               "manipulator": "arm", "climber": "climber", "chassis": "chassis",
               "swerve_0": "swerve module", "drivetrain": "drivetrain",
               "electrical": "control system"}


# Mechanism types deliberately kept OUT of the CAD training families. The eval asks for them
# anyway: if the model can only build what it was shown, it memorised; if it can build these,
# it learned how a mechanism goes together. Nothing stops these appearing in `design_intent`
# — the model still has to know the names, it just never sees their geometry.
CAD_HELD_OUT = {
    "ground-to-feeder tunnel intake",
    "variable-hood flywheel shooter",
    "telescoping box",
    "four-bar linkage arm",
    "winch-driven carriage climb",
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


def _varied_cad_requests(rng: random.Random, count: int) -> list[str]:
    """Sample many genuinely different robots.

    Variety here is the whole point. A corpus built from one frame size and one mechanism
    combination teaches a model that a chassis *is* a fixed list of parts. Sampling frames,
    drives and mechanism mixes means the structure of the answer has to follow the request.
    """
    intakes = [t for t in INTAKE_TYPES if t not in CAD_HELD_OUT]
    shooters = [t for t in SHOOTER_TYPES if t not in CAD_HELD_OUT]
    elevators = [t for t in ELEVATOR_TYPES if t not in CAD_HELD_OUT]
    arms = [t for t in ARM_TYPES if t not in CAD_HELD_OUT]
    climbers = [t for t in CLIMBER_TYPES if t not in CAD_HELD_OUT]
    requests: list[str] = []
    for _ in range(count):
        width, length = rng.choice(_FRAMES)
        drive = rng.choice(["swerve", "swerve", "swerve", "MK4i swerve", "MK4n swerve",
                            "MK5i swerve", "west-coast", "tank"])
        mechs: list[str] = []
        if rng.random() < 0.85:
            mechs.append(f"a {rng.choice(intakes)}")
        if rng.random() < 0.55:
            mechs.append(f"a {rng.choice(shooters)}")
        if rng.random() < 0.55:
            mechs.append(f"a {rng.randint(1, 3)} stage {rng.choice(elevators)} elevator")
        if rng.random() < 0.40:
            mechs.append(f"a {rng.choice(arms)}")
        if rng.random() < 0.50:
            mechs.append(rng.choice(climbers))
        if not mechs:
            mechs.append("a drivebase only")
        joined = ", ".join(mechs[:-1]) + (" and " + mechs[-1] if len(mechs) > 1 else mechs[0])
        requests.append(rng.choice(_TEAM_VOICE).format(
            w=width, l=length, drive=drive, mechs=joined))
    return requests


def _typed_cad_requests(rng: random.Random) -> list[str]:
    """Cover every enumerated mechanism type at least once.

    The hand-written requests above read like real team asks but only touch a handful of the
    vocabulary. Walking the enums guarantees the model sees the geometry that belongs to each
    named type — that mapping is what stops a "ground-to-feeder tunnel intake" from being
    modelled as whatever intake the model saw most often.
    """
    requests: list[str] = []
    frames = [26, 27, 28, 28, 29, 30]
    for options, phrase in ((INTAKE_TYPES, "{} intake"), (SHOOTER_TYPES, "{}"),
                            (ELEVATOR_TYPES, "{} elevator"), (ARM_TYPES, "{}"),
                            (CLIMBER_TYPES, "{}")):
        for option in options:
            if option in CAD_HELD_OUT:
                continue
            width = rng.choice(frames)
            mechanism = phrase.format(option).replace("intake intake", "intake")
            requests.append(
                f"{width}x{width} swerve robot built around a {mechanism}. "
                f"Model it at part level.")
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
    requests = _CAD_REQUESTS + _typed_cad_requests(rng) + _varied_cad_requests(rng, 260)
    pairs: list[tuple[str, dict[str, Any]]] = []
    for request in requests:
        spec = build_robot_spec(request, use_model=False)
        for assembly in spec["cad"]["assemblies"]:
            if assembly["id"] in _CAD_LABELS and assembly["features"]:
                pairs.append((request, assembly))
    rng.shuffle(pairs)

    seen: dict[tuple[Any, ...], int] = {}
    kept: list[tuple[str, dict[str, Any]]] = []
    for request, assembly in pairs:
        signature = _structure_signature(assembly)
        if seen.get(signature, 0) >= per_structure:
            continue
        seen[signature] = seen.get(signature, 0) + 1
        kept.append((request, assembly))
        if len(kept) >= count:
            break

    for offset, (request, assembly) in enumerate(kept):
        label = _CAD_LABELS[assembly["id"]]
        user = (rng.choice(_CAD_PROMPTS).format(label=label) + "\n\nRobot:\n"
                + _fenced(request))
        answer = json.dumps(assembly, separators=(",", ":"), sort_keys=False)
        rows.append(_row(SYSTEM_CAD, user, answer, "cad_geometry", start + offset))
    return rows


def _cad_qa_examples(rng: random.Random, start: int) -> list[dict[str, Any]]:
    """Dimensioned questions whose answers are computed from the same CAD tree.

    These are the questions a mentor asks at a design review, and every number in the answer
    is derived, not remembered — which is what stops the model inventing plausible dimensions.
    """
    rows: list[dict[str, Any]] = []
    index = start
    for request in _CAD_REQUESTS + _typed_cad_requests(rng):
        spec = build_robot_spec(request, use_model=False)
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
             cad_count: int = 90) -> dict[str, Any]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    rows += [_intent_example(rng, index) for index in range(intent_count)]
    rows += _parts_examples(rng, len(rows))
    rows += _electrical_examples(rng, len(rows), electrical_count)
    rows += _technique_examples(rng, len(rows))
    rows += _diagnosis_examples(len(rows))
    rows += _cad_geometry_examples(rng, len(rows), cad_count)
    rows += _cad_qa_examples(rng, len(rows))

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
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument("--merge", nargs="*", default=[],
                        help="existing processed dataset dirs to fold in (e.g. datasets/processed/design-v2)")
    args = parser.parse_args()
    manifest = generate(Path(args.out), args.intent_count, args.electrical_count, args.seed,
                        [Path(item) for item in args.merge], cad_count=args.cad_count)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
