"""Prompt → FRC robot specification.

Three layers, in order of authority:

1. **Deterministic parsing.** Explicit numbers and named hardware in the prompt always win.
   If the user says "MK4n modules on Krakens at L2+ with a 3 stage elevator", that is not a
   suggestion for the model to reinterpret.
2. **The self-hosted model.** For everything the prompt leaves open, architecture choices,
   subsystem selection, design notes, Kale asks its own inference service, constrained to a
   fixed vocabulary and clamped numeric ranges. A model answer can never push a dimension
   outside the envelope the rules and the parts catalog allow.
3. **Prompt-seeded defaults.** When the model is unavailable (stub provider, offline host),
   choices left open are resolved from a hash of the prompt, so two different requests still
   diverge instead of collapsing onto one canned robot.

Layer 3 is the reason a prompt change is always visible in the output even with no weights
loaded; layer 2 is the reason the output is *sensible* rather than merely different.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from typing import Any

from app.config import get_settings
from app.services.frc_cad import CAD_VERSION, build_cad, cut_list
from app.services.frc_parts import (
    ELECTRONICS,
    MOTORS,
    PARTS_VERSION,
    SWERVE_MODULES,
    drivetrain_summary,
    electrical_layout,
    motor_loads,
    power_budget,
    select_drive_ratio,
    select_module,
    select_motor,
)
from app.services.frc_robot_knowledge import (
    KNOWLEDGE_VERSION,
    choose_profile,
    references_for,
    techniques_for,
)

SUBSYSTEM_NAMES = ("intake", "shooter", "elevator", "arm", "climber")

INTAKE_TYPES = ("coaxial slapdown", "four-bar over-bumper", "under-bumper roller",
                "side funnel roller", "fixed over-bumper roller",
                "dual-roller over-bumper", "pivoting compliant-wheel intake",
                "ground-to-feeder tunnel intake", "horizontal series-roller intake",
                "active floor sweeper with indexer")
SHOOTER_TYPES = ("dual independent flywheel hooded shooter", "single flywheel backspin shooter",
                 "turreted dual flywheel hooded shooter", "fixed-angle flywheel shooter",
                 "variable-hood flywheel shooter",
                 "stacked multi-flywheel barrel shooter", "pivoting barrel flywheel shooter",
                 "staged accelerator-and-flywheel shooter")
ARM_TYPES = ("dead-axle shoulder arm", "telescoping dead-axle shoulder arm",
             "double-jointed arm", "four-bar linkage arm", "wristed carriage arm")
CLIMBER_TYPES = ("dual telescoping winch hooks", "single pivoting hook",
                 "winch-driven carriage climb", "deep-cage hook climb")
ELEVATOR_TYPES = ("cascade", "continuous", "telescoping box",
                  "belt-rigged cascade tower", "chain-rigged continuous tower")

_INCH = r'(?:in(?:ch(?:es)?)?|\")'


# ─────────────────────────────────────────────────────────────────────────────
# Small parsing helpers
# ─────────────────────────────────────────────────────────────────────────────
def _seed(prompt: str) -> random.Random:
    digest = hashlib.sha256(prompt.strip().lower().encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _number(text: str, pattern: str, default: float) -> float:
    matches = re.findall(pattern, text, re.I)
    if not matches:
        return default
    value = matches[-1]
    if isinstance(value, tuple):
        value = next((item for item in value if item), "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _count(text: str, noun: str, default: int) -> int:
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "single": 1, "dual": 2, "triple": 3}
    match = re.search(rf"\b(\d+|{'|'.join(words)})\s*[- ]?(?:stage|{noun})", text, re.I)
    if not match:
        return default
    token = match.group(1).lower()
    return int(words.get(token, token)) if token.isdigit() or token in words else default


def _pick_type(text: str, options: tuple[str, ...], keywords: dict[str, str], fallback: str) -> str:
    for keyword, option in keywords.items():
        if re.search(keyword, text, re.I):
            return option
    return fallback


# ─────────────────────────────────────────────────────────────────────────────
# The model pass
# ─────────────────────────────────────────────────────────────────────────────
INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subsystems": {"type": "array", "items": {"type": "string", "enum": list(SUBSYSTEM_NAMES)}},
        "drive_type": {"type": "string", "enum": ["swerve", "swerve-ready", "west-coast", "tank"]},
        "intake_type": {"type": "string", "enum": list(INTAKE_TYPES)},
        "shooter_type": {"type": "string", "enum": list(SHOOTER_TYPES)},
        "arm_type": {"type": "string", "enum": list(ARM_TYPES)},
        "climber_type": {"type": "string", "enum": list(CLIMBER_TYPES)},
        "elevator_architecture": {"type": "string", "enum": list(ELEVATOR_TYPES)},
        "elevator_stages": {"type": "integer"},
        "pneumatics": {"type": "boolean"},
        "design_notes": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subsystems"],
}

SYSTEM_DESIGN = """You are Kale Forge's self-hosted FRC design model. You turn a team's request \
into ONE structured design intent for a competition robot.
- Choose only from the enumerated values in the schema. Never invent an option.
- Respect everything the request states explicitly; only decide what it leaves open.
- Prefer architectures the team can actually build and service: rigid subassemblies, dead \
axles, serviceable plates, short wire runs.
- design_notes are short engineering justifications for YOUR choices. risks are the concrete \
ways this configuration fails at competition.
- Never claim rule compliance, structural safety, or fabrication readiness.
Respond with a single JSON object matching the schema and nothing else."""


def intent_vocabulary_block() -> str:
    """The enumerated vocabulary, written out for the model to choose from.

    Only the llama.cpp provider constrains decoding to the schema; the transformers provider
    validates afterwards, so without this the model has to *recall* exact strings it was never
    shown and lands on near-misses like "dual flywheel hooded shooter". Presenting the list
    turns the task from recall into selection, which is both more reliable and more portable:
    add a mechanism type next season and the model can use it with no retraining, because the
    skill it learned is "pick from the options given", not "remember these options".

    Training and inference both call this, so the two can never drift.
    """
    groups = (("drive_type", ("swerve", "swerve-ready", "west-coast", "tank")),
              ("intake_type", INTAKE_TYPES), ("shooter_type", SHOOTER_TYPES),
              ("arm_type", ARM_TYPES), ("climber_type", CLIMBER_TYPES),
              ("elevator_architecture", ELEVATOR_TYPES))
    lines = ["Allowed values — copy one exactly, never paraphrase or invent:"]
    lines += [f"- {field}: {' | '.join(options)}" for field, options in groups]
    lines.append(f"- subsystems: any of {' | '.join(SUBSYSTEM_NAMES)}")
    return "\n".join(lines)


def _model_intent(prompt: str, parsed: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask the self-hosted model to fill the gaps. Returns (intent, provenance)."""
    from app.services.inference_client import InferenceClient, InferenceUnavailable
    from app.services.security import fence_user_content

    settings = get_settings()
    provenance: dict[str, Any] = {"used": False, "provider": "", "model_version": "", "reason": ""}
    client = InferenceClient(settings.inference_url, settings.inference_timeout_seconds)
    stated = {key: value for key, value in parsed.items() if value is not None}
    user = (
        "Design an FRC robot for this request. Fields already fixed by the team are given as "
        "'stated', repeat them unchanged and decide only the rest.\n\n"
        + intent_vocabulary_block()
        + f"\n\nstated: {stated}\n\nTeam request:\n" + fence_user_content(prompt)
    )
    try:
        # Temperature is deliberately non-zero: two similar prompts should still explore
        # different architectures rather than collapsing onto one answer.
        result = client.generate(SYSTEM_DESIGN, user, json_schema=INTENT_SCHEMA,
                                 max_tokens=900, temperature=0.55)
    except InferenceUnavailable as exc:
        provenance["reason"] = f"inference unavailable, deterministic synthesis used ({exc})"
        return {}, provenance
    if result.provider == "stub" or not isinstance(result.json, dict):
        provenance["reason"] = (
            "no model weights loaded (stub provider), deterministic synthesis used"
            if result.provider == "stub" else "model returned no usable JSON, deterministic synthesis used"
        )
        provenance["provider"] = result.provider
        return {}, provenance
    provenance.update(used=True, provider=result.provider, model_version=result.model_version,
                      latency_ms=round(result.latency_ms), reason="model-selected architecture")
    return result.json, provenance


def _sanitize_intent(intent: dict[str, Any]) -> dict[str, Any]:
    """Keep only enumerated values and clamped numbers. A model cannot widen the envelope."""
    clean: dict[str, Any] = {}
    subsystems = intent.get("subsystems")
    if isinstance(subsystems, list):
        chosen = [name for name in SUBSYSTEM_NAMES if name in subsystems]
        if chosen:
            clean["subsystems"] = chosen
    enums = {"drive_type": ("swerve", "swerve-ready", "west-coast", "tank"),
             "intake_type": INTAKE_TYPES, "shooter_type": SHOOTER_TYPES, "arm_type": ARM_TYPES,
             "climber_type": CLIMBER_TYPES, "elevator_architecture": ELEVATOR_TYPES}
    for field, options in enums.items():
        value = intent.get(field)
        if isinstance(value, str) and value in options:
            clean[field] = value
    if isinstance(intent.get("elevator_stages"), int):
        clean["elevator_stages"] = int(_clamp(intent["elevator_stages"], 1, 4))
    if isinstance(intent.get("pneumatics"), bool):
        clean["pneumatics"] = intent["pneumatics"]
    for field in ("design_notes", "risks"):
        items = intent.get(field)
        if isinstance(items, list):
            text = [str(item)[:220] for item in items if isinstance(item, (str, int, float))][:6]
            if text:
                clean[field] = text
    return clean


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic parsing
# ─────────────────────────────────────────────────────────────────────────────
def _parse(prompt: str) -> dict[str, Any]:
    """Everything the prompt states explicitly. `None` means 'not stated, decide later'."""
    p = prompt.lower()
    latest = p.rsplit("revision request:", 1)[-1]
    profile_key, profile = choose_profile(prompt)
    default_width, default_length = profile["frame"]

    pair = re.findall(rf"(\d+(?:\.\d+)?)\s*(?:x|×|by)\s*(\d+(?:\.\d+)?)\s*{_INCH}", p)
    if not pair:
        # Accept a bare "26x26" with no unit, teams write frame sizes that way. Two-digit
        # numbers only, so part callouts like "4x M4" or "2x1 tube" never match.
        pair = re.findall(r"\b(\d{2})\s*(?:x|×|by)\s*(\d{2})\b(?!\s*(?:layer|lb|mm))", p)
    pair_width, pair_length = pair[-1] if pair else (default_width, default_length)
    width_in = _clamp(_number(p, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:wide|width)", float(pair_width)), 20, 34)
    length_in = _clamp(_number(p, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:long|length|deep)", float(pair_length)), 20, 34)
    height_in = _clamp(_number(p, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:tall|height|high)",
                               profile["starting_height_in"]), 24, 84)

    def stated_subsystem(name: str) -> bool | None:
        word = {"climber": r"climb(?:er|ing)?", "elevator": r"elevator|lift",
                "arm": r"arm|manipulator|wrist", "shooter": r"shoot(?:er)?|launcher",
                "intake": r"intake|collector"}[name]
        if re.search(rf"\b(?:no|without|remove|delete|omit|drop)\s+(?:the\s+)?(?:{word})\b", latest):
            return False
        if re.search(rf"\b(?:{word})\b", latest):
            return True
        return None

    bare_swerve = bool(re.search(r"\b(?:without|no|remove|omit)\s+(?:the\s+)?swerve(?:s|\s*modules?)?\b", latest))
    if re.search(r"\b(?:tank|west[- ]coast|wcd|6[- ]wheel|kitbot)\b", latest):
        drive_type = "west-coast"
    elif bare_swerve:
        drive_type = "swerve-ready"
    elif re.search(r"\bswerve\b", p):
        drive_type = "swerve"
    else:
        drive_type = None

    wheel_in = _number(p, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*wheels?", 0) or None
    weight_lb = _number(p, r"(\d+(?:\.\d+)?)\s*(?:lb|lbs|pound)", 0) or None
    reach_in = _number(p, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:arm|reach|extension)", 0) or None

    pneumatics: bool | None = None
    if re.search(r"\b(?:pneumatic|cylinder|piston|solenoid|compressor)\w*\b", p):
        pneumatics = True
    if re.search(r"\b(?:no|without)\s+pneumatics?\b", latest):
        pneumatics = False

    distributor = "pdp" if re.search(r"\bpdp\b|power distribution panel", p) else (
        "pdh" if re.search(r"\bpdh\b|power distribution hub", p) else None)

    # Request scope. "Just an MK4i swerve module" or "drivebase only" must NOT inherit the
    # season profile's default mechanisms, that is how every prompt used to collapse into
    # the same full robot. A drivetrain-scoped request builds chassis + drivetrain and adds
    # only mechanisms the prompt itself states.
    _mech_words = r"intake|collector|shoot(?:er)?|launcher|elevator|lift|arm|manipulator|wrist|climb|turret"
    _drivetrain_words = r"swerve|mk\s*\d\w*|maxswerve|thrifty|drive\s*base|drive\s*train|drivetrain|chassis|modules?"
    # Scope is decided by the ORIGINAL request, not by revision text, "add an intake" to a
    # bare drivebase must extend the drivebase, not resurrect a full default robot.
    base = p.rsplit("revision request:", 1)[0] if "revision request:" in p else p
    explicit_only = bool(
        re.search(rf"\b(?:just|only)\s+(?:an?\s+|the\s+)?[\w\- ]{{0,24}}?(?:{_drivetrain_words})\b", latest)
        or re.search(rf"\b(?:{_drivetrain_words})\s*[- ]?only\b", latest))
    module_ask = bool(
        not re.search(r"\brobots?\b|\bbot\b", base)
        and re.search(rf"\b(?:{_drivetrain_words})\b", base)
        and not re.search(rf"\b(?:{_mech_words})\b", base))
    scope = "drivetrain" if (explicit_only or module_ask) else "robot"

    return {
        "scope": scope,
        "profile_key": profile_key, "profile": profile,
        "width_in": width_in, "length_in": length_in, "height_in": height_in,
        "drive_type": drive_type, "modules_included": not bare_swerve,
        "wheel_in": wheel_in, "weight_lb": weight_lb, "reach_in": reach_in,
        "pneumatics": pneumatics, "distributor_key": distributor,
        "elevator_stages": _count(latest, "stage", 0) or None,
        "subsystems_stated": {name: stated_subsystem(name) for name in SUBSYSTEM_NAMES},
        "second_can_bus": bool(re.search(r"\bcanivore\b|second can|separate can", p)),
        "prompt": prompt, "latest": latest, "lower": p,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────────
def build_robot_spec(prompt: str, *, use_model: bool = True) -> dict[str, Any]:
    parsed = _parse(prompt)
    rng = _seed(prompt)
    p, latest = parsed["lower"], parsed["latest"]
    profile = parsed["profile"]

    intent: dict[str, Any] = {}
    provenance = {"used": False, "provider": "", "model_version": "",
                  "reason": "model pass disabled for this call"}
    if use_model:
        raw, provenance = _model_intent(prompt, {
            "frame_in": [parsed["width_in"], parsed["length_in"]],
            "drive_type": parsed["drive_type"],
            "subsystems_stated": {k: v for k, v in parsed["subsystems_stated"].items() if v is not None},
        })
        intent = _sanitize_intent(raw)

    # Subsystems: explicit statement > model choice > season profile default.
    # A drivetrain-scoped request ("just an MK4i module", "drivebase only") gets NO
    # default mechanisms, only what the prompt states.
    defaults = set(profile["default_subsystems"]) if parsed["scope"] == "robot" else set()
    if parsed["scope"] != "robot":
        intent.pop("subsystems", None)  # model defaults don't apply to a component request
    model_subsystems = set(intent.get("subsystems", []))
    subsystems = []
    for name in SUBSYSTEM_NAMES:
        stated = parsed["subsystems_stated"][name]
        if stated is True:
            subsystems.append(name)
        elif stated is False:
            continue
        elif model_subsystems:
            if name in model_subsystems:
                subsystems.append(name)
        elif name in defaults:
            subsystems.append(name)

    drive_type = parsed["drive_type"] or intent.get("drive_type") or "swerve"
    modules_included = parsed["modules_included"] and drive_type != "west-coast"

    # ── Drivetrain hardware ────────────────────────────────────────────────
    drive_motor_key, drive_motor = select_motor(p, default="kraken_x60")
    if drive_type == "west-coast":
        # A tank drivebase has no modules at all, describing one would be a lie in the BOM.
        wheel_in = parsed["wheel_in"] or 6.0
        ratio = _number(p, r"(\d+(?:\.\d+)?)\s*:\s*1", 0) or rng.choice([8.45, 10.71, 12.75])
        motor_count = 6 if wheel_in >= 5 else 4
        wheel_rpm = drive_motor["free_rpm"] / ratio
        drivetrain = {
            "type": "west-coast", "modules_included": False, "module_count": 0,
            "module": f"{motor_count // 2}-wheel-per-side drop-centre gearbox",
            "module_key": "", "vendor": "team-fabricated / COTS gearbox",
            "motor": drive_motor["name"], "motor_key": drive_motor_key,
            "drive_motors": motor_count, "steer_motors": 0,
            "steer_motor": "", "steer_motor_key": drive_motor_key,
            "drive_ratio_label": "single-speed", "drive_ratio": round(ratio, 2), "steer_ratio": 0,
            "wheel_diameter_in": wheel_in,
            "free_speed_fps": round(wheel_rpm * math.pi * wheel_in / 12.0 / 60.0, 2),
            "stall_thrust_lbf": round(drive_motor["stall_nm"] * ratio * motor_count
                                      / (wheel_in * 0.0254 / 2) * 0.2248, 1),
            "center_drop_in": 0.125, "encoder": "drive encoder on the gearbox output",
            "second_can_bus": parsed["second_can_bus"],
            "selection_reason": f"West-coast drop-centre drivebase on {motor_count} "
                                f"{drive_motor['name']}s, {ratio:g}:1 single speed",
            "caveat": ("Free speed is the unloaded ceiling and stall thrust ignores traction "
                       "limits; both need a real current-limited simulation before you trust them."),
            "note": "Centre wheels are dropped ~1/8 in so the robot turns on four contact patches.",
        }
        module_key, module = select_module(p, wheel_in)  # reserved for a later swerve upgrade
        module_count = 0
    else:
        # Unstated module falls to a prompt-seeded pick (MK4i-weighted), not a constant.
        module_key, module = select_module(p, parsed["wheel_in"],
                                           default_key=rng.choice(("mk4i", "mk4i", "mk5i", "mk4n")))
        wheel_in = parsed["wheel_in"] or module["wheel_in"]
        ratio_label, ratio = select_drive_ratio(module, p)
        steer_motor_key = "neo_550" if module_key == "maxswerve" else drive_motor_key
        module_count = 4 if modules_included else 0
        drivetrain = drivetrain_summary(module_key, module, drive_motor_key, drive_motor,
                                        ratio_label, ratio, module_count or 4)
        drivetrain.update(
            type=drive_type, modules_included=modules_included, module_count=module_count,
            # Electrical load follows what is actually installed, not the reserved envelope.
            drive_motors=module_count, steer_motors=module_count,
            steer_motor=MOTORS[steer_motor_key]["name"], steer_motor_key=steer_motor_key,
            second_can_bus=parsed["second_can_bus"] or module_count >= 4,
            drop_in=module["drop_in"], plate_in=module["plate_in"],
            encoder=module["encoder"], vendor=module["vendor"],
            selection_reason=(
                f"{module['name']} selected from the prompt"
                if re.search(r"mk\s*\d|swerve module|maxswerve|thrifty|wcp", p)
                else f"{module['name']} chosen as the default for a {wheel_in:g} in wheel"),
        )
        if not modules_included:
            drivetrain["note"] = ("Corners are cut and drilled for modules that are not part of "
                                  "this build, the mounting envelope is reserved, nothing is "
                                  "installed. Module figures below are what it would do once "
                                  "they are.")

    # ── Subsystem detail ───────────────────────────────────────────────────
    # Fallback priority: explicit prompt keyword > model intent > PROMPT-SEEDED choice.
    # The last tier used to be a fixed constant, which made every default robot carry
    # identical mechanisms, the "same design every time" failure. Seeding it from the
    # prompt hash keeps results reproducible per prompt while different prompts diverge.
    # Duplicated entries weight the draw toward the most commonly built options.
    intake_type = _pick_type(p, INTAKE_TYPES, {
        r"4[- ]?bar|four[- ]?bar": "four-bar over-bumper",
        r"under[- ]?bumper": "under-bumper roller",
        r"funnel|side\s+roller": "side funnel roller",
        r"fixed\s+intake|static\s+intake": "fixed over-bumper roller",
        r"slapdown|slap[- ]?down": "coaxial slapdown",
        r"dual[- ]?roller|two[- ]?roller": "dual-roller over-bumper",
        r"tunnel|ground[- ]?to[- ]?feeder": "ground-to-feeder tunnel intake",
        r"series[- ]?roller|horizontal\s+roller": "horizontal series-roller intake",
        r"sweeper|floor\s+sweep|indexer": "active floor sweeper with indexer",
        r"compliant[- ]?wheel|pivoting\s+intake": "pivoting compliant-wheel intake",
    }, intent.get("intake_type") or rng.choice(
        ("coaxial slapdown", "coaxial slapdown", "four-bar over-bumper",
         "under-bumper roller", "fixed over-bumper roller", "dual-roller over-bumper",
         "pivoting compliant-wheel intake")))

    shooter_type = _pick_type(p, SHOOTER_TYPES, {
        r"turret": "turreted dual flywheel hooded shooter",
        r"stacked\s+flywheel|multi[- ]?flywheel|flywheel\s+stack": "stacked multi-flywheel barrel shooter",
        r"barrel|pivot\w*\s+shooter|angled\s+shooter": "pivoting barrel flywheel shooter",
        r"accelerator|staged\s+shooter|two[- ]stage\s+shoot": "staged accelerator-and-flywheel shooter",
        r"single\s+flywheel": "single flywheel backspin shooter",
        r"fixed[- ]angle|fixed\s+hood": "fixed-angle flywheel shooter",
        r"variable\s+hood|adjustable\s+hood": "variable-hood flywheel shooter",
    }, intent.get("shooter_type") or rng.choice(
        ("dual independent flywheel hooded shooter", "dual independent flywheel hooded shooter",
         "single flywheel backspin shooter", "variable-hood flywheel shooter",
         "fixed-angle flywheel shooter")))

    arm_type = _pick_type(p, ARM_TYPES, {
        r"double[- ]?jointed": "double-jointed arm",
        r"4[- ]?bar\s+arm|four[- ]?bar\s+arm": "four-bar linkage arm",
        r"telescop\w*\s+arm|extend\w*\s+arm": "telescoping dead-axle shoulder arm",
        r"wrist": "wristed carriage arm",
    }, intent.get("arm_type") or rng.choice(
        ("dead-axle shoulder arm", "dead-axle shoulder arm", "double-jointed arm",
         "four-bar linkage arm", "telescoping dead-axle shoulder arm")))

    climber_type = _pick_type(p, CLIMBER_TYPES, {
        r"deep\s*cage|deep\s*climb|climbs?\s+deep": "deep-cage hook climb",
        r"single\s+hook|pivot\w*\s+hook": "single pivoting hook",
        r"carriage\s+climb": "winch-driven carriage climb",
    }, intent.get("climber_type") or rng.choice(
        ("dual telescoping winch hooks", "dual telescoping winch hooks",
         "single pivoting hook", "winch-driven carriage climb")))

    elevator_arch = _pick_type(p, ELEVATOR_TYPES, {
        r"chain[- ]rigged|chain[- ]driven\s+(?:elevator|tower|lift)": "chain-rigged continuous tower",
        r"belt[- ]rigged|belt[- ]driven\s+(?:elevator|tower|lift)|tower": "belt-rigged cascade tower",
        r"continuous": "continuous", r"telescop\w*\s+(?:elevator|lift)": "telescoping box",
        r"cascade": "cascade",
    }, intent.get("elevator_architecture") or rng.choice(
        ("cascade", "cascade", "continuous", "telescoping box", "belt-rigged cascade tower")))
    stages = parsed["elevator_stages"] or intent.get("elevator_stages") or (3 if parsed["height_in"] > 60 else 2)

    pneumatics = parsed["pneumatics"]
    if pneumatics is None:
        pneumatics = bool(intent.get("pneumatics", False))

    # Choices the prompt leaves open are seeded from the prompt, so different requests
    # diverge instead of all producing the same robot.
    flywheel_in = _number(p, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*flywheel", 0) or rng.choice([4.0, 4.0, 5.0, 6.0])
    roller_in = rng.choice([1.625, 2.0, 2.0, 2.5])
    mech_motor_key, _ = select_motor(p, default=rng.choice(["neo", "neo_vortex", "kraken_x60"]))
    shooter_motor_key = "kraken_x60" if "kraken" in p else rng.choice(["neo_vortex", "kraken_x60", "falcon_500"])
    # Longitudinal placement varies within a band that stays serviceable and inside the frame.
    intake_bias = rng.uniform(0.06, 0.14)
    shooter_bias = rng.uniform(0.58, 0.72)
    elevator_bias = rng.uniform(0.50, 0.64)
    arm_bias = rng.uniform(0.46, 0.60)

    # Detailed, seeded intake geometry. Surface speed comes from the mechanism motor's free
    # speed through the belt reduction and the roller diameter; it must exceed the robot's
    # approach speed or the intake pushes the gamepiece away instead of pulling it in.
    _PI = 3.141592653589793
    intake_reduction = rng.choice([3.0, 4.0, 4.0, 5.0])
    intake_roller_count = 2 if any(word in intake_type for word in ("dual", "series", "tunnel")) else 1
    intake_roller_center_in = round(roller_in + rng.choice([1.5, 2.0, 2.5]), 2)
    intake_wheel = rng.choice(["30A green compliant wheels", "35A green compliant wheels",
                               "40A grey compliant wheels"])
    _intake_roller_rpm = MOTORS[mech_motor_key]["free_rpm"] / intake_reduction
    intake_surface_speed_fps = round(_intake_roller_rpm * _PI * roller_in / 12.0 / 60.0, 1)
    intake_deployed = intake_type != "fixed over-bumper roller"

    # Superstructure detail. A stacked/staged shooter runs several flywheel pairs in series
    # down a barrel so the gamepiece is accelerated progressively instead of by one impulse;
    # a belt- or chain-rigged tower carries the stages on rails with preloaded bearing blocks.
    shooter_stacked = any(w in shooter_type for w in ("stacked", "staged", "barrel"))
    shooter_stages = 3 if "stacked" in shooter_type else (2 if shooter_stacked else 1)
    shooter_barrel_in = round(rng.uniform(16.0, 26.0), 1) if shooter_stacked else 0.0
    shooter_turreted = "turret" in shooter_type
    shooter_pivot_deg = [28, 72] if "pivoting" in shooter_type else [18, 62]
    # Exit velocity is roughly half the flywheel surface speed for a compressed gamepiece
    # gripped between a wheel and a hood: the piece leaves at the mean of the wheel surface
    # and the stationary hood. Two counter-rotating wheels give it the full surface speed.
    shooter_reduction = rng.choice([1.0, 1.0, 1.5, 2.0])
    _shooter_rpm = MOTORS[shooter_motor_key]["free_rpm"] / shooter_reduction
    shooter_surface_fps = round(_shooter_rpm * 3.141592653589793 * flywheel_in / 12.0 / 60.0, 1)
    shooter_exit_fps = round(shooter_surface_fps * (1.0 if shooter_stacked else 0.5), 1)
    shooter_width_in = round(min(parsed["width_in"] - 6, profile["gamepiece"]["diameter_in"] + 6), 1)
    # A flywheel needs stored energy, not just speed: too light and the first shot drags the
    # wheel down and the second shot goes somewhere else. Mass is the design knob.
    shooter_flywheel_mass_lb = round(0.35 * shooter_stages * (flywheel_in / 4.0) ** 2, 2)
    shooter_spinup_s = round(0.6 + shooter_flywheel_mass_lb * 0.9, 2)

    elevator_rigged = "rigged" in elevator_arch or "tower" in elevator_arch
    elevator_rigging = ("HTD 5 mm belt" if "belt" in elevator_arch else
                        ("#25 chain" if "chain" in elevator_arch else "rope/belt"))
    # Cascade rigging multiplies carriage travel by the stage count; a continuous elevator
    # moves every stage together, so travel is one stage of extension per stage.
    elevator_stage_travel = round((parsed["height_in"] - 12) / max(1, int(stages)), 1)
    elevator_travel_in = round(elevator_stage_travel * int(stages), 1)
    elevator_speed_multiple = int(stages) if "cascade" in elevator_arch else 1
    elevator_overlap_in = round(max(6.0, elevator_stage_travel * 0.22), 1)
    elevator_span_in = round(rng.uniform(8.0, 11.0), 1)
    elevator_drum_in = round(rng.choice([1.44, 1.75, 2.0]), 2)

    # Arm geometry. A double-jointed arm is two driven segments; a four-bar keeps the end
    # effector's angle fixed through the sweep. Holding torque is the worst case (fully
    # extended, horizontal) — that is what the reduction must be sized on, not free speed.
    arm_reach_in = parsed["reach_in"] or round(rng.uniform(16, 26), 1)
    arm_segments = 2 if "double-jointed" in arm_type else 1
    arm_segment_lengths = ([round(arm_reach_in * 0.58, 1), round(arm_reach_in * 0.42, 1)]
                           if arm_segments == 2 else [arm_reach_in])
    arm_wristed = "wrist" in arm_type or arm_segments == 2
    arm_shoulder_deg = [-15, 115] if "four-bar" not in arm_type else [0, 95]
    arm_end_effector = ("compliant-wheel gripper" if "intake" in subsystems else "passive claw")
    arm_reduction = rng.choice([60.0, 75.0, 90.0, 100.0])
    # W = m·g·r  → N·m, with a nominal 8 lb payload+arm mass at the CoG of the extended arm.
    arm_holding_nm = round(8 * 0.4536 * 9.81 * (arm_reach_in * 0.0254 * 0.55), 1)

    arm_shoulder_height_in = round(rng.uniform(6.0, 9.5), 1)

    climber_stages = 2 if "telescoping" in climber_type else 1
    climber_drum_in = round(rng.choice([1.0, 1.25, 1.5]), 2)
    climber_hook = ("deep-cage barb hook" if "deep-cage" in climber_type else
                    ("passive bar hook" if "pivoting" in climber_type else "carriage hook"))
    # A climb is a torque problem, not a speed one. Rope tension is the robot's weight
    # divided by the number of load paths; drum torque is that tension times the drum radius.
    climber_load_lb = parsed["weight_lb"] or profile.get("target_weight_lb", 105)
    climber_paths = 2 if "dual" in climber_type else 1
    climber_tension_lbf = round(climber_load_lb / climber_paths, 1)
    climber_drum_torque_nm = round(climber_tension_lbf * 4.4482 * (climber_drum_in / 2 * 0.0254), 2)
    climber_reduction = round(_clamp(climber_drum_torque_nm / (MOTORS["kraken_x60"]["stall_nm"] * 0.4 * 2), 20, 240), 0)
    climber_travel_in = round(min(parsed["height_in"] + 24, 84) - min(parsed["height_in"], 29.5), 1)

    def block(name: str, **extra: Any) -> dict[str, Any]:
        included = name in subsystems
        return {"included": included, **extra}

    width_in, length_in, height_in = parsed["width_in"], parsed["length_in"], parsed["height_in"]
    gamepiece = profile["gamepiece"]

    spec: dict[str, Any] = {
        "schema_version": "3.1",
        # Structural decisions in the CAD tree — how many crossmembers, belt or chain, plate
        # or tube — are resolved from this, so one prompt always yields one robot while
        # different prompts differ in construction and not merely in dimensions.
        "design_seed": int(hashlib.sha256(prompt.strip().lower().encode()).hexdigest()[:8], 16),
        "profile": {"id": parsed["profile_key"], "label": profile["label"], "season": profile["season"],
                    "knowledge_version": KNOWLEDGE_VERSION, "parts_version": PARTS_VERSION},
        "model": provenance,
        "frame": {"width_in": width_in, "length_in": length_in, "rail_height_in": 1, "rail_width_in": 2,
                  "wall_in": 0.100, "material": "6061-T6 aluminium",
                  "tube": "2x1x0.100 in", "bellypan": "0.090 in pocketed plate",
                  "perimeter_in": round(2 * (width_in + length_in), 1)},
        "drivetrain": drivetrain,
        # Kept for backwards compatibility with existing consumers of the v2 spec.
        "drive": {"type": drive_type, "modules": module_count, "modules_included": modules_included,
                  "wheel_diameter_in": wheel_in,
                  "motors": module_count * 2 if drive_type == "swerve" else (6 if drive_type == "west-coast" else 0)},
        "subsystems": subsystems,
        "scope": parsed["scope"],
        "intake": block("intake", type=intake_type, width_in=round(min(width_in - 4, 24), 1),
                        roller_diameter_in=roller_in, roller_count=intake_roller_count,
                        roller_center_distance_in=intake_roller_center_in, compliant_wheel=intake_wheel,
                        compression_in=0.5, gear_reduction=f"{intake_reduction:g}:1",
                        roller_surface_speed_fps=intake_surface_speed_fps,
                        pivot_limit_deg=[-12, 95] if intake_deployed else [0, 0],
                        deploy="powered pivot with metal hard stops" if intake_deployed else "fixed over the bumper",
                        power_path="coaxial belt through the pivot" if "coaxial" in intake_type else "belt from a static gearbox",
                        indexer=("shooter" in subsystems or "elevator" in subsystems),
                        motor_key=mech_motor_key, motor=MOTORS[mech_motor_key]["name"], motor_count=2,
                        position_bias=round(intake_bias, 3),
                        rigid_subassemblies=["static gearbox", "intake arms", "powered roller",
                                             "belt reduction", "indexer handoff"]),
        "shooter": block("shooter", type=shooter_type, flywheel_diameter_in=flywheel_in,
                         gamepiece_diameter_in=gamepiece["diameter_in"], compression_in=0.5,
                         hood_angle_deg=shooter_pivot_deg, motor_key=shooter_motor_key,
                         motor=MOTORS[shooter_motor_key]["name"],
                         motor_count=2 * shooter_stages if shooter_stacked else 2,
                         flywheel_stages=shooter_stages, stacked=shooter_stacked,
                         barrel_length_in=shooter_barrel_in, turreted=shooter_turreted,
                         width_in=shooter_width_in,
                         gear_reduction=f"{shooter_reduction:g}:1",
                         flywheel_surface_speed_fps=shooter_surface_fps,
                         exit_velocity_fps=shooter_exit_fps,
                         flywheel_mass_lb=shooter_flywheel_mass_lb,
                         spinup_time_s=shooter_spinup_s,
                         wheel="grey 60A urethane flywheels on a 1/2 in hex shaft",
                         turret_range_deg=[-180, 180] if shooter_turreted else [0, 0],
                         feed_path="indexer → barrel → staged flywheel pairs" if shooter_stacked
                                   else "feeder → flywheel pair → hood",
                         position_bias=round(shooter_bias, 3),
                         rigid_subassemblies=(["indexer", "barrel"] +
                                              [f"flywheel stage {n}" for n in range(1, shooter_stages + 1)] +
                                              (["turret ring"] if shooter_turreted else ["hood"]))
                                             if shooter_stacked else
                                             ["feeder", "left flywheel", "right flywheel", "hood"]),
        "elevator": block("elevator", architecture=elevator_arch, stages=int(stages),
                          max_height_in=height_in, rail="2x1 tube", reduction="12:1",
                          rigged=elevator_rigged, rigging=elevator_rigging,
                          bearing_blocks="opposed, preloaded at every stage interface",
                          tower_uprights=2, carriage="bolted carriage on the final stage",
                          upright_span_in=elevator_span_in,
                          travel_in=elevator_travel_in,
                          stage_travel_in=elevator_stage_travel,
                          stage_overlap_in=elevator_overlap_in,
                          carriage_speed_multiple=elevator_speed_multiple,
                          drum_diameter_in=elevator_drum_in,
                          load_path="carriage → final stage → rigging → drum → gearbox",
                          motor_key="kraken_x60" if "kraken" in p else "neo_vortex",
                          motor=MOTORS["kraken_x60" if "kraken" in p else "neo_vortex"]["name"],
                          motor_count=2, position_bias=round(elevator_bias, 3),
                          rigid_subassemblies=["static frame"] + [f"stage {n}" for n in range(1, int(stages))] + ["carriage"]),
        "manipulator": block("arm", type=arm_type, gamepiece=gamepiece["name"],
                             reach_in=arm_reach_in, segments=arm_segments,
                             segment_lengths_in=arm_segment_lengths,
                             shoulder_pivot_deg=arm_shoulder_deg, wrist=arm_wristed,
                             wrist_range_deg=[-95, 95] if arm_wristed else [0, 0],
                             end_effector=arm_end_effector,
                             reduction=f"{arm_reduction:g}:1",
                             holding_torque_nm=arm_holding_nm,
                             gravity_compensation="feedforward sized on the horizontal holding case",
                             shoulder_height_in=arm_shoulder_height_in,
                             pivot="dead axle with serviceable bearing blocks",
                             shaft="MAXSpline" if "torque" in p or "double-jointed" in arm_type else "1/2 in hex",
                             motor_key=mech_motor_key, motor=MOTORS[mech_motor_key]["name"], motor_count=2,
                             position_bias=round(arm_bias, 3),
                             rigid_subassemblies=(["shoulder gearbox"] +
                                                  [f"segment {n}" for n in range(1, arm_segments + 1)] +
                                                  (["wrist", "end effector"] if arm_wristed else ["end effector"]))),
        "climber": block("climber", type=climber_type, stowed_height_in=min(height_in, 29.5),
                         extended_height_in=round(min(height_in + 24, 84), 1),
                         stages=climber_stages, tower="2x2x0.125 in upright tied into two rails",
                         winch_drum_diameter_in=climber_drum_in, rope="1/8 in Dyneema on a grooved drum",
                         travel_in=climber_travel_in, load_paths=climber_paths,
                         rope_tension_lbf=climber_tension_lbf,
                         drum_torque_nm=climber_drum_torque_nm,
                         reduction=f"{climber_reduction:g}:1",
                         lift_load_lb=climber_load_lb,
                         hook=climber_hook, engagement="easy in, hard out: free entry, bears under load",
                         bearing_blocks="rolling blocks at both ends of every stage overlap",
                         motor_key="kraken_x60", motor=MOTORS["kraken_x60"]["name"], motor_count=2,
                         ratchet="mechanical ratchet on the winch drum",
                         rigid_subassemblies=["climber tower", "winch gearbox"] +
                                             [f"stage {n}" for n in range(1, climber_stages + 1)] + ["hook"]),
        "pneumatics": {"included": bool(pneumatics),
                       "components": ["Pneumatic Hub", "compressor", "regulator", "accumulator tanks"] if pneumatics else [],
                       "note": "Pneumatics buy simple binary motion at a real mass and volume cost." if pneumatics else ""},
        "constraints": {
            "starting_perimeter_in": [width_in, length_in],
            "frame_perimeter_in": round(2 * (width_in + length_in), 1),
            "target_weight_lb": parsed["weight_lb"] or profile.get("target_weight_lb", 105),
            "bumper_gap_in": 0.25, "service_clearance_in": 0.5,
            "rule_snapshot": "Verify frame perimeter, height, extension and weight against the current manual and team updates.",
        },
        "reference_basis": references_for(subsystems),
        "techniques": techniques_for(subsystems, drive=drive_type),
        "design_notes": intent.get("design_notes", []),
        "risks": intent.get("risks", []),
        "intent": prompt[-1200:],
    }

    # ── Electrical: real channels, breakers, wire and placement ────────────
    distributor_key = parsed["distributor_key"] or "pdh"
    loads = motor_loads(spec)
    budget = power_budget(loads, distributor_key)
    spec["electrical"] = {
        "distributor_key": distributor_key,
        **budget,
        "components": [ELECTRONICS[key]["name"] for key in
                       ("main_breaker", "battery", "sb50", distributor_key, "rio", "radio", "rsl", "pigeon")],
    }
    spec["electrical"]["placements"] = electrical_layout(width_in * 25.4, length_in * 25.4, spec)
    spec["mass_estimate"] = _mass_estimate(spec)

    # ── CAD: the same robot expressed as individual dimensioned parts ──────
    # Everything above says what the robot *is*; this says what it is made of, in one
    # coordinate system, so a viewer, a BOM and a cut list all read the same geometry.
    spec["cad"] = build_cad(spec)
    spec["cut_list"] = cut_list(spec["cad"])
    spec["profile"]["cad_version"] = CAD_VERSION
    return spec


def _mass_estimate(spec: dict[str, Any]) -> dict[str, Any]:
    """First-order mass roll-up so the weight limit is a design input, not a surprise.

    Bumpers and the battery are listed but tracked separately: the competition weight limit is
    normally checked without them, so counting them against the target would report a false
    overweight on every design.  Verify what your season's manual excludes.
    """
    frame = spec["frame"]
    perimeter_ft = (2 * (frame["width_in"] + frame["length_in"])) / 12.0
    rows: list[dict[str, Any]] = [
        {"item": "frame rails + crossmembers (2x1)", "lb": round(perimeter_ft * 1.9 * 0.66, 1),
         "counted": True},
        {"item": "bellypan + gussets + fasteners", "lb": 6.5, "counted": True},
    ]
    drivetrain = spec["drivetrain"]
    if drivetrain.get("modules_included"):
        motor = MOTORS[drivetrain["motor_key"]]
        steer = MOTORS[drivetrain["steer_motor_key"]]
        count = drivetrain["module_count"] or 4
        rows.append({"item": f"{count}x {drivetrain['module']} modules", "counted": True,
                     "lb": round(SWERVE_MODULES[drivetrain["module_key"]]["mass_lb"] * count, 1)})
        rows.append({"item": f"{count * 2}x drive/steer motors", "counted": True,
                     "lb": round((motor["mass_lb"] + steer["mass_lb"]) * count, 1)})
    elif drivetrain.get("type") == "west-coast":
        motor = MOTORS[drivetrain["motor_key"]]
        count = int(drivetrain.get("drive_motors", 6))
        rows.append({"item": f"west-coast rails, gearboxes and {count} wheels", "lb": 22.0,
                     "counted": True})
        rows.append({"item": f"{count}x drive motors", "counted": True,
                     "lb": round(motor["mass_lb"] * count, 1)})
    for key, label in (("intake", "intake"), ("shooter", "shooter"), ("elevator", "elevator"),
                       ("manipulator", "arm"), ("climber", "climber")):
        block = spec[key]
        if not block.get("included"):
            continue
        motor = MOTORS.get(block.get("motor_key", "neo"), MOTORS["neo"])
        base = {"intake": 8.0, "shooter": 14.0, "elevator": 18.0, "manipulator": 12.0, "climber": 10.0}[key]
        rows.append({"item": f"{label} structure + motors", "counted": True,
                     "lb": round(base + motor["mass_lb"] * block.get("motor_count", 1), 1)})
    control_lb = sum(ELECTRONICS[key]["mass_lb"] for key in
                     ("main_breaker", "sb50", "rio", "radio", "rsl", "pigeon"))
    control_lb += ELECTRONICS[spec["electrical"]["distributor_key"]]["mass_lb"]
    rows.append({"item": "control system + harness", "lb": round(control_lb + 3.0, 1), "counted": True})
    if spec["pneumatics"]["included"]:
        rows.append({"item": "pneumatics (hub, compressor, tanks, plumbing)", "lb": 8.5, "counted": True})
    rows.append({"item": "battery (normally excluded from the limit)",
                 "lb": ELECTRONICS["battery"]["mass_lb"], "counted": False})
    rows.append({"item": "bumpers (normally excluded from the limit)", "lb": 15.0, "counted": False})

    counted = round(sum(row["lb"] for row in rows if row["counted"]), 1)
    excluded = round(sum(row["lb"] for row in rows if not row["counted"]), 1)
    target = spec["constraints"]["target_weight_lb"]
    return {
        "rows": rows, "counted_lb": counted, "excluded_lb": excluded,
        "total_lb": round(counted + excluded, 1), "target_lb": target,
        "margin_lb": round(target - counted, 1),
        "caveat": ("First-order estimate from envelope masses and typical subsystem weights, "
                   "compared against the target without bumpers or battery. It is for early "
                   "trade studies only, weigh the real robot, and check what your season's "
                   "manual actually excludes."),
    }
