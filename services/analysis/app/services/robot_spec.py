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
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.frc_cad import CAD_VERSION, build_cad, cut_list, ladder_label
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
from app.services.frc_season import (
    SEASON_VERSION,
    design_targets,
    element,
    frame_budget,
    get_season,
    optimal_launch_angle,
    required_exit_fps,
    rule_findings,
    scoring_math,
    season_digest,
    shot_solution,
    surface_speed_for_exit,
)

SUBSYSTEM_NAMES = ("intake", "hopper", "shooter", "elevator", "arm", "climber")

INTAKE_TYPES = ("coaxial slapdown", "four-bar over-bumper", "under-bumper roller",
                "side funnel roller", "fixed over-bumper roller",
                "dual-roller over-bumper", "pivoting compliant-wheel intake",
                "ground-to-feeder tunnel intake", "horizontal series-roller intake",
                "active floor sweeper with indexer")
# Bulk gamepiece handling. A hopper is the mechanism between "acquired" and "staged": it holds
# many pieces, turns them into a single ordered lane and hands exactly one to the shooter.
# The 2026 field's foam balls make it the defining subsystem of the season, but the archetypes
# are older than any one game and are named for what they do, not for the year.
HOPPER_TYPES = ("circular spindexer", "oval spindexer", "belt-floor hopper",
                "twin-lane belt hopper", "funnel-to-tower hopper",
                "serpentine tunnel indexer", "paddle-wheel agitator hopper")
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
class DesignNotBuildable(ValueError):
    """The request cannot be compiled as asked.

    Carries the gate result (state, questions, violations) so the API can answer with a
    clarification prompt or a rule citation instead of a robot nobody asked for.
    """

    def __init__(self, gate_result: dict) -> None:
        super().__init__(gate_result.get("reason", "design not buildable"))
        self.gate = gate_result


def _seed(prompt: str) -> random.Random:
    """Seeded from the ORIGINAL request only, never the revision text.

    Every choice the prompt leaves open is drawn from this stream. Seeding it from the full
    prompt meant that ANY revision — "make the flywheel 6 inches" — reshuffled every seeded
    choice on the robot: mechanism types flipped, roller sizes changed, the flywheel the
    edit asked about could come back *smaller*. An edit must change what it names and
    nothing else, so the seed is pinned to the request as it stood before any revision.
    """
    base = re.split(r"revision request:", prompt, maxsplit=1, flags=re.I)[0]
    digest = hashlib.sha256(base.strip().lower().encode()).hexdigest()
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
    segments = [text]
    if "revision request:" in text.lower():
        base, latest = re.split(r"revision request:", text, maxsplit=1, flags=re.I)
        segments = [latest, base]
    for segment in segments:
        for keyword, option in keywords.items():
            if re.search(keyword, segment, re.I):
                return option
    return fallback


# ─────────────────────────────────────────────────────────────────────────────
# The model pass
# ─────────────────────────────────────────────────────────────────────────────
INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subsystems": {"type": "array", "items": {"type": "string", "enum": list(SUBSYSTEM_NAMES)},
                       "uniqueItems": True, "maxItems": len(SUBSYSTEM_NAMES)},
        "drive_type": {"type": "string", "enum": ["swerve", "swerve-ready", "west-coast", "tank"]},
        "intake_type": {"type": "string", "enum": list(INTAKE_TYPES)},
        "hopper_type": {"type": "string", "enum": list(HOPPER_TYPES)},
        "shooter_type": {"type": "string", "enum": list(SHOOTER_TYPES)},
        "arm_type": {"type": "string", "enum": list(ARM_TYPES)},
        "climber_type": {"type": "string", "enum": list(CLIMBER_TYPES)},
        "elevator_architecture": {"type": "string", "enum": list(ELEVATOR_TYPES)},
        "elevator_stages": {"type": "integer", "minimum": 1, "maximum": 4},
        "pneumatics": {"type": "boolean"},
        "design_notes": {"type": "array", "items": {"type": "string", "maxLength": 220},
                         "maxItems": 6},
        "risks": {"type": "array", "items": {"type": "string", "maxLength": 220},
                  "maxItems": 6},
    },
    # drive_type is required, not optional. The llama.cpp provider compiles this schema into a
    # decoding grammar, and an optional field is one the grammar lets the model skip — v18
    # skipped it on every prompt of its first served run, so every design silently fell back to
    # the "swerve" default no matter what the robot was for. A field the model is meant to
    # decide has to be one the grammar makes it decide.
    "required": ["subsystems", "drive_type"],
}

SYSTEM_DESIGN = """You are Kale Forge's self-hosted FRC design model. You turn a team's request \
into ONE structured design intent for a competition robot.
- Choose only from the enumerated values in the schema. Never invent an option.
- Respect everything the request states explicitly; only decide what it leaves open.
- The season sets the constraints: the gamepiece decides how it is handled, the goal height \
decides the scoring mechanism, the obstacles decide the envelope, and the perimeter and \
extension budgets decide what fits. Design to those, not to last year's robot.
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
              ("intake_type", INTAKE_TYPES), ("hopper_type", HOPPER_TYPES),
              ("shooter_type", SHOOTER_TYPES),
              ("arm_type", ARM_TYPES), ("climber_type", CLIMBER_TYPES),
              ("elevator_architecture", ELEVATOR_TYPES))
    lines = ["Allowed values — copy one exactly, never paraphrase or invent:"]
    lines += [f"- {field}: {' | '.join(options)}" for field, options in groups]
    lines.append(f"- subsystems: any of {' | '.join(SUBSYSTEM_NAMES)}")
    return "\n".join(lines)


def intent_user_message(prompt: str, stated: dict[str, Any], season_key: str = "") -> str:
    """The exact user message for the design-intent task.

    One function so training, inference and evaluation cannot disagree about the prompt. They
    already did once: the vocabulary block was added to the corpus and to the Design Studio but
    not to the eval harness, so v10 was trained with it and scored without it. Its intent
    output looked like a collapse and was actually a prompt it had never been trained on.
    Anything that asks the model for a design intent must build the message here — and that now
    includes the season block, for exactly the same reason.
    """
    from app.services.frc_season import prompt_block
    from app.services.security import fence_user_content
    season = get_season(season_key)
    return ("Design an FRC robot for this request. Fields already fixed by the team are given "
            "as 'stated', repeat them unchanged and decide only the rest.\n\n"
            + prompt_block(season) + "\n\n"
            + intent_vocabulary_block()
            + f"\n\nstated: {stated}\n\nTeam request:\n" + fence_user_content(prompt))


def _model_intent(prompt: str, parsed: dict[str, Any],
                  season_key: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask the self-hosted model to fill the gaps. Returns (intent, provenance)."""
    from app.services.inference_client import InferenceClient, InferenceUnavailable

    settings = get_settings()
    provenance: dict[str, Any] = {"used": False, "provider": "", "model_version": "", "reason": ""}
    client = InferenceClient(settings.inference_url, settings.inference_timeout_seconds)
    stated = {key: value for key, value in parsed.items() if value is not None}
    user = intent_user_message(prompt, stated, season_key)
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


# Which assembly the model is asked to propose, most interesting first. One subsystem, not the
# robot: a full robot of geometry is ~10k tokens of generation, and the measured failure mode
# (a feature repeated until the budget dies) grows with output length.
_GEOMETRY_CANDIDATES = ("shooter", "intake", "elevator", "arm", "climber", "hopper")


def _first_json_object(text: str) -> dict[str, Any] | None:
    """The first balanced JSON object in a completion, or None."""
    import json as _json
    start = text.find("{")
    if start < 0:
        return None
    depth, in_string, escape = 0, False, False
    for index in range(start, len(text)):
        ch = text[index]
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
                    return _json.loads(text[start:index + 1])
                except _json.JSONDecodeError:
                    return None
    return None


def _adopt_model_assembly(spec: dict[str, Any], proposal: Any,
                          subsystem: str) -> tuple[bool, str]:
    """Swap one compiled assembly for a model-proposed one — iff the whole robot still passes.

    Pure so it can be tested without a model. The proposal replaces the matching assembly in a
    COPY of the CAD tree, and the merged whole goes back through normalize + require_valid_cad:
    the model's geometry earns its place by passing the exact gate the compiler's does, coincident
    -body and duplicate-assembly checks included. Any failure leaves the spec untouched.
    """
    from copy import deepcopy

    from app.services.cad_contract import (CadContractError, normalize_cad, require_valid_cad,
                                           structural_errors)

    if not isinstance(proposal, dict) or not isinstance(proposal.get("features"), list):
        return False, "proposal is not an assembly object"
    target = subsystem if subsystem != "arm" else "manipulator"
    cad = deepcopy(spec["cad"])
    slot = next((i for i, a in enumerate(cad["assemblies"]) if a.get("id") == target), None)
    if slot is None:
        return False, f"no {target!r} assembly to replace"
    # The id is the merge key and the origin places the assembly in the robot; neither is the
    # model's to move. Everything else — the features — is what it is proposing.
    proposal = deepcopy(proposal)
    proposal["id"] = target
    proposal.setdefault("kind", cad["assemblies"][slot].get("kind", "mechanism"))
    proposal["origin"] = cad["assemblies"][slot].get("origin", [0, 0, 0])
    cad["assemblies"][slot] = proposal
    try:
        spec_cad = require_valid_cad(normalize_cad(cad))
    except CadContractError as exc:
        return False, f"contract rejected it: {exc}"
    # The same "nothing floats" audit the deterministic tree passes: a proposed mechanism
    # whose parts touch nothing, or that never reaches the chassis, is not a mechanism.
    floats = structural_errors(spec_cad)
    if floats:
        return False, "structural audit rejected it: " + "; ".join(floats[:6])
    # Model-authored coordinates are a suggestion, not geometry. A proposal that drives its
    # own parts through each other, buries one inside another or sweeps through the rest of
    # the robot is refused here and the deterministic tree is kept.
    #
    # Judged as a DELTA against the tree it is replacing, not against zero. The compiler's own
    # output still carries faults of its own in other mechanisms, and holding a proposed intake
    # responsible for a pre-existing overlap in the climber would reject every proposal ever
    # made while saying nothing about the proposal.
    from app.services.geometry_validation import (  # noqa: PLC0415
        geometry_errors, validate_geometry)
    before = set(geometry_errors(spec["cad"]))
    introduced = [e for e in geometry_errors(spec_cad) if e not in before]
    if introduced:
        return False, "geometry audit rejected it: " + "; ".join(introduced[:6])
    # The shipped report must describe the shipped tree, not the compiler's replaced one.
    from app.services.cad_contract import structural_report  # noqa: PLC0415
    spec_cad["integrity"] = structural_report(spec_cad)
    spec_cad["geometry"] = validate_geometry(spec_cad)
    spec["cad"] = spec_cad
    return True, f"model geometry adopted for {target} ({len(proposal['features'])} features)"


def _model_geometry_pass(spec: dict[str, Any], prompt: str) -> None:
    """Ask the model for one subsystem's assembly and let the contract decide.

    Provenance lands in spec["model_geometry"] either way, because a design whose geometry
    might be model-authored must say so — and a fallback must say why.
    """
    from app.services.frc_cad import SYSTEM_CAD
    from app.services.inference_client import InferenceClient, InferenceUnavailable
    from app.services.security import fence_user_content

    subsystem = next((name for name in _GEOMETRY_CANDIDATES if name in spec["subsystems"]), None)
    provenance: dict[str, Any] = {"attempted": subsystem is not None, "used": False,
                                  "subsystem": subsystem, "reason": ""}
    spec["model_geometry"] = provenance
    if subsystem is None:
        provenance["reason"] = "no mechanism to propose geometry for"
        return
    settings = get_settings()
    client = InferenceClient(settings.inference_url, settings.inference_timeout_seconds)
    user = (f"Give me the {subsystem} assembly for this robot at part level.\n\nRobot:\n"
            + fence_user_content(prompt[:1200]))
    try:
        # A permissive object schema, not the full assembly grammar: it makes the sampler emit
        # one balanced JSON object (llama.cpp compiles it to a grammar) while leaving the real
        # judging to the CAD contract afterwards. Without it the first live run produced an
        # unparseable completion; with it, parse failures can only be truncation. 2600 tokens
        # matches the evaluation budget the adapter was scored at. Temperature is low —
        # geometry is not the place to explore.
        result = client.generate(SYSTEM_CAD, user, json_schema={"type": "object"},
                                 max_tokens=2600, temperature=0.3)
    except InferenceUnavailable as exc:
        provenance["reason"] = f"inference unavailable ({exc}); compiler geometry kept"
        return
    proposal = result.json if isinstance(result.json, dict) else _first_json_object(result.text or "")
    if proposal is None:
        provenance["reason"] = "no parseable assembly in the completion; compiler geometry kept"
        return
    adopted, reason = _adopt_model_assembly(spec, proposal, subsystem)
    provenance.update(used=adopted, reason=reason,
                      model_version=result.model_version, provider=result.provider,
                      completion_tokens=result.completion_tokens)
    if not adopted:
        _record_geometry_rejection(spec, prompt, subsystem, proposal, reason,
                                   result.model_version)


def _record_geometry_rejection(spec: dict[str, Any], prompt: str, subsystem: str,
                               proposal: dict[str, Any], reason: str,
                               model_version: str) -> None:
    """Bank a rejected proposal as a chosen/rejected training candidate.

    Every rejection the contract makes is a free, exactly-labelled preference pair: the
    model's assembly with the specific defect named, against the compiler's assembly for the
    same request. `build_preference_pairs.py` consumes this file — the architecture doc names
    preference training, not more broad SFT, as the continuation strategy, and this is where
    its data accumulates without anyone doing anything.

    Best-effort by design: the file lives outside the serverless bundle and the write is
    guarded, so a read-only filesystem (Vercel) or a race loses one candidate, never a design.
    """
    import json as _json

    target = subsystem if subsystem != "arm" else "manipulator"
    chosen = next((a for a in spec["cad"]["assemblies"] if a.get("id") == target), None)
    if chosen is None:
        return
    lowered = reason.lower()
    if "coincident" in lowered or "duplicate" in lowered:
        category = "duplicate_parts"
    elif "pitch" in lowered or "disagrees with" in lowered:
        category = "pitch_mismatch"
    elif "illegal feature" in lowered:
        category = "illegal_feature"
    else:
        category = "bad_dimension"
    row = {"prompt": f"Give me the {subsystem} assembly for this robot at part level.\n\n"
                     f"Robot:\n{prompt[:1200]}",
           "rejected": proposal, "chosen": chosen, "category": category, "split": "train",
           "reason": reason[:400], "model_version": model_version}
    try:
        out = Path(__file__).resolve().parents[4] / "datasets" / "raw" / \
            "geometry-preference-candidates.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(_json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _sanitize_intent(intent: dict[str, Any]) -> dict[str, Any]:
    """Keep only enumerated values and clamped numbers. A model cannot widen the envelope."""
    clean: dict[str, Any] = {}
    subsystems = intent.get("subsystems")
    if isinstance(subsystems, list):
        chosen = [name for name in SUBSYSTEM_NAMES if name in subsystems]
        if chosen:
            clean["subsystems"] = chosen
    enums = {"drive_type": ("swerve", "swerve-ready", "west-coast", "tank"),
             "intake_type": INTAKE_TYPES, "hopper_type": HOPPER_TYPES,
             "shooter_type": SHOOTER_TYPES, "arm_type": ARM_TYPES,
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
def _parse(prompt: str, requested_season: str = "") -> dict[str, Any]:
    """Everything the prompt states explicitly. `None` means 'not stated, decide later'."""
    p = prompt.lower()
    latest = p.rsplit("revision request:", 1)[-1]
    # The request as it stood before the revision — the whole prompt when there is no revision.
    #
    # A revision states only what *changes*. Reading a sticky fact out of `latest` alone means
    # that anything the revision does not happen to mention is treated as never having been
    # asked for: "use 6 inch wheels" silently deleted the elevator and reverted the robot to
    # the season defaults. Sticky facts read `latest` first so the revision still wins, then
    # fall back here. `_pick_type` has always done this; the fields below had not.
    original = p.rsplit("revision request:", 1)[0]
    profile_key, profile, season_reason = choose_profile(prompt, requested_season)
    season = get_season(profile_key)
    default_width, default_length = profile["frame"]

    pair = re.findall(rf"(\d+(?:\.\d+)?)\s*(?:x|×|by)\s*(\d+(?:\.\d+)?)\s*{_INCH}", p)
    if not pair:
        # Accept a bare "26x26" with no unit, teams write frame sizes that way. Two-digit
        # numbers only, so part callouts like "4x M4" or "2x1 tube" never match.
        pair = re.findall(r"\b(\d{2})\s*(?:x|×|by)\s*(\d{2})\b(?!\s*(?:layer|lb|mm))", p)
    pair_width, pair_length = pair[-1] if pair else (default_width, default_length)
    width_in = _clamp(_number(latest, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:wide|width)", 0)
                      or _number(p, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:wide|width)", float(pair_width)), 20, 34)
    length_in = _clamp(_number(latest, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:long|length|deep)", 0)
                       or _number(p, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:long|length|deep)", float(pair_length)), 20, 34)

    # The season's perimeter budget is a rule, not a preference, so it outranks even an
    # explicitly stated frame: a 28 × 28 robot is a legal 2025 robot and an illegal 2026 one,
    # and quietly building the illegal one is worse than resizing it and saying so.
    budget = frame_budget(season, width_in, length_in)
    width_in, length_in = budget["width_in"], budget["length_in"]

    height_in = _clamp(_number(p, rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:tall|height|high)",
                               profile["starting_height_in"]), 24, 84)

    def stated_subsystem(name: str) -> bool | None:
        word = {"climber": r"climb(?:er|ing)?", "elevator": r"elevator|lift",
                "arm": r"arm|manipulator|wrist", "shooter": r"shoot(?:er)?|launcher",
                "intake": r"intake|collector",
                "hopper": r"hopper|spindexer|indexer|magazine|carousel|serializer"}[name]
        # The revision speaks last, but only about what it mentions; anything it is silent on
        # is still governed by the original request.
        for segment in (latest, original):
            if re.search(rf"\b(?:no|without|remove|delete|omit|drop)\s+(?:the\s+)?(?:{word})\b", segment):
                return False
            if re.search(rf"\b(?:{word})\b", segment):
                return True
        return None

    def _drive_from(segment: str) -> str | None:
        """The drivetrain one segment asks for, most specific first."""
        if re.search(r"\b(?:tank|west[- ]coast|wcd|6[- ]wheel|kitbot)\b", segment):
            return "west-coast"
        if re.search(r"\b(?:without|no|remove|omit)\s+(?:the\s+)?swerve(?:s|\s*modules?)?\b", segment):
            return "swerve-ready"
        if re.search(r"\bswerve\b", segment):
            return "swerve"
        return None

    # Sticky, for the same reason as the subsystems: a west-coast robot must not turn into a
    # swerve robot because the edit was about wheel size.
    drive_type = _drive_from(latest) or _drive_from(original)
    bare_swerve = drive_type == "swerve-ready"

    # Both word orders, and the revision before the original request.
    #
    # This only ever matched "4 inch wheels", so "make this wheel 4 inches" — which is how
    # people actually phrase an edit — changed nothing at all while the studio reported
    # success. Every other dimension goes through `_dim`, which has tried both directions for
    # a long time; this one selector was written by hand and never got the second pattern.
    #
    # The reverse pattern is deliberately tighter than `_dim`'s generic `[^.\d]{0,18}?` gap.
    # That gap would read "wheels and a 27 in frame" as a 27 in wheel — fine for flywheels,
    # where no frame dimension follows, and quietly catastrophic here.
    _wheel_forward = rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*wheels?"
    _wheel_reverse = rf"wheels?\s*(?:are|at|to|of|=)?\s*(\d+(?:\.\d+)?)\s*{_INCH}"
    wheel_in = None
    for _segment in (latest, p):
        wheel_in = (_number(_segment, _wheel_forward, 0)
                    or _number(_segment, _wheel_reverse, 0) or None)
        if wheel_in:
            break
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
    _mech_words = (r"intake|collector|shoot(?:er)?|launcher|elevator|lift|arm|manipulator|wrist"
                   r"|climb|turret|hopper|spindexer|indexer")
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

    # A closing exclusion — "defence bot, nothing else" — is a statement about the whole robot
    # rather than about one mechanism, so `stated_subsystem` never sees it: that function only
    # matches "no <mechanism>". Without this the sentence reads as saying nothing at all, the
    # season defaults and the model's suggestions both apply, and a team that asked for a bare
    # defence bot is handed an intake, a shooter and a climber. It is scoped to the latest
    # revision text so "add an intake" after the fact still works.
    exclusive = bool(re.search(
        r"\bnothing\s+(?:else|more)\b|\bno\s+other\s+(?:mechanism|subsystem|manipulator)s?\b"
        r"|\bnothing\s+but\b|\band\s+that(?:'|’)?s\s+(?:it|all)\b", latest))

    return {
        "scope": scope, "exclusive": exclusive,
        "profile_key": profile_key, "profile": profile,
        "season": season, "season_reason": season_reason, "frame_budget": budget,
        "width_in": width_in, "length_in": length_in, "height_in": height_in,
        "drive_type": drive_type, "modules_included": not bare_swerve,
        "wheel_in": wheel_in, "weight_lb": weight_lb, "reach_in": reach_in,
        "pneumatics": pneumatics, "distributor_key": distributor,
        # Sticky: a "3 stage" elevator stays three stages through an edit about something else.
        "elevator_stages": _count(latest, "stage", 0) or _count(original, "stage", 0) or None,
        "subsystems_stated": {name: stated_subsystem(name) for name in SUBSYSTEM_NAMES},
        "second_can_bus": bool(re.search(r"\bcanivore\b|second can|separate can", p)),
        "team_number": _team_number(p),
        "bumper_color": _bumper_color(p),
        "prompt": prompt, "latest": latest, "lower": p,
    }


DEFAULT_TEAM_NUMBER = 8159

# A team number has to be *asked for*, never inferred from a loose integer: a prompt is full
# of numbers that are not it — 28x28 frames, 6.75:1 ratios, 40 A breakers, 2026 seasons.
# Only an explicit "team 1234" / "frc 1234" / "#1234" counts.
_TEAM_PATTERNS = (
    r"\bteam\s*(?:number\s*|no\.?\s*|#\s*)?(\d{1,5})\b",
    r"\bfrc\s*#?\s*(\d{1,5})\b",
    r"#(\d{3,5})\b",
    r"\bfor\s+(\d{3,5})\b",
)


def _team_number(text: str) -> int:
    """The team number on the bumpers. Stated in the prompt, or the house default."""
    for pattern in _TEAM_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            number = int(match.group(1))
            if 1 <= number <= 99999:
                return number
    return DEFAULT_TEAM_NUMBER


# Alliance colours first, then the rest of what teams actually paint bumpers.
BUMPER_COLORS: dict[str, str] = {
    "red": "#a01722", "blue": "#1f4fd8", "black": "#1c1f22", "white": "#e8e8e6",
    "green": "#2f9d5b", "orange": "#e8721f", "yellow": "#e8c31f", "purple": "#7a3fb8",
    "pink": "#d94f8a", "teal": "#1f9d9d", "gold": "#c9a227", "silver": "#b9c0c6",
    "grey": "#7b838a", "gray": "#7b838a", "navy": "#16306b", "maroon": "#7b1f2b",
    "crimson": "#a51c30", "cyan": "#22b8d6", "lime": "#7fc41f", "brown": "#6b4a2f",
    "magenta": "#c0219b",
}
DEFAULT_BUMPER_COLOR = "red"

_COLOR_WORDS = "|".join(sorted(BUMPER_COLORS, key=len, reverse=True))
_BUMPER_PATTERNS = (
    rf"\b({_COLOR_WORDS})\s+bumpers?\b",
    rf"\bbumpers?\s+(?:in|are|is|colou?red|painted)?\s*({_COLOR_WORDS})\b",
    rf"\bbumpers?\s*[:=]\s*({_COLOR_WORDS})\b",
)


def _bumper_color(text: str) -> dict[str, str]:
    """Bumper colour, stated in the prompt or red by default.

    Matched only next to the word "bumper". A prompt says "green compliant wheels" and
    "blue Loctite" without meaning either as a bumper colour, so a loose colour word is
    never enough.
    """
    match = re.search(rf"\bbumpers?\s*(?:in|are|is|colou?red|painted)?\s*#([0-9a-fA-F]{{6}})\b", text)
    if not match:
        match = re.search(rf"#([0-9a-fA-F]{{6}})\s+bumpers?\b", text)
    if match:
        return {"name": f"#{match.group(1).lower()}", "hex": f"#{match.group(1).lower()}"}
    for pattern in _BUMPER_PATTERNS:
        found = re.search(pattern, text, re.I)
        if found:
            name = found.group(1).lower()
            return {"name": name, "hex": BUMPER_COLORS[name]}
    return {"name": DEFAULT_BUMPER_COLOR, "hex": BUMPER_COLORS[DEFAULT_BUMPER_COLOR]}


# ─────────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────────
# Every directive the deterministic engine can actually apply, one recognizer per row.
# The revision report tests each clause of an edit against this table so an edit the engine
# does not understand is REPORTED as not applied instead of silently rebuilding the same
# robot — the difference between an imprecise tool and a precise one that says no.
_EDIT_RECOGNIZERS: tuple[tuple[str, str], ...] = (
    (r"\d+(?:\.\d+)?\s*(?:in|inch|inches|\")\s*(?:wide|width|long|length|deep|tall|height|high)", "frame / height dimension"),
    (r"\b\d{2}\s*(?:x|×|by)\s*\d{2}\b", "frame size"),
    (r"\b(?:no|without|remove|delete|omit|drop)\s+(?:the\s+)?(?:turret|climb|elevator|lift|arm|shooter|launcher|intake|hopper|spindexer|indexer|swerve|pneumatics?)", "removal"),
    (r"\b(?:add|with)\s+(?:an?\s+|the\s+)?(?:turret|climber|elevator|lift|arm|shooter|launcher|intake|hopper|spindexer|indexer)", "addition"),
    (r"\bbumpers?\b", "bumper colour"),
    (r"\b(?:\d+|one|two|three|four|single|dual|triple)\s*[- ]?stage", "stage count"),
    (r"(?<!\w)(?:l[1-4]\+?|x[1-3]|t[1-3]|r[1-3]|\d{2}t)(?!\w)|\d+(?:\.\d+)?\s*:\s*1", "drive ratio"),
    (r"\bkraken|\bneo\b|\bvortex\b|\b550\b|\bfalcon\b|\bminion\b|\bcims?\b", "motor"),
    (r"\bmk\s*\d\w*|maxswerve|thrifty|\bwcp\b|swerve|west[- ]coast|\btank\b|kitbot", "drivetrain"),
    (r"\d+(?:\.\d+)?\s*(?:in|inch|inches|\")\s*wheels?", "wheel size"),
    (r"flywheels?[^.\d]{0,18}?\d+(?:\.\d+)?\s*(?:in|inch|inches|\")|\d+(?:\.\d+)?\s*(?:in|inch|inches|\")\s*(?:diameter\s+)?flywheels?", "flywheel size"),
    (r"rollers?[^.\d]{0,18}?\d+(?:\.\d+)?\s*(?:in|inch|inches|\")|\d+(?:\.\d+)?\s*(?:in|inch|inches|\")\s*(?:diameter\s+)?rollers?", "roller size"),
    (r"\b(?:\d+|one|two|three|four|single|dual|triple)\s*[- ]?rollers?", "roller count"),
    (r"intakes?[^.\d]{0,18}?\d+(?:\.\d+)?\s*(?:in|inch|inches|\")|\d+(?:\.\d+)?\s*(?:in|inch|inches|\")\s*(?:wide\s+)?intakes?", "intake width"),
    (r"stows?\s+inside|inside\s+the\s+frame|within\s+the\s+frame|under[- ]?bumper|over[- ]?bumper|slapdown|4[- ]?bar|four[- ]?bar|funnel|sweeper|tunnel|compliant[- ]?wheel", "intake type"),
    (r"spindexer|carousel|belt\s+floor|belt[- ]floor|twin[- ]lane|serpentine|paddle|agitator", "hopper type"),
    (r"turret|stacked|barrel|accelerator|single\s+flywheel|fixed[- ]angle|fixed\s+hood|variable\s+hood|adjustable\s+hood|hooded", "shooter type"),
    (r"double[- ]?jointed|wrist|telescop|four[- ]?bar\s+arm|\barm\b|reach", "arm"),
    (r"deep\s*cage|hook|carriage\s+climb|climb", "climber"),
    (r"cascade|continuous|elevator|lift|tower", "elevator"),
    (r"\bteam\s*(?:number\s*|no\.?\s*|#\s*)?\d{1,5}\b|\bfrc\s*#?\s*\d{1,5}\b|#\d{3,5}\b", "team number"),
    (r"\d+(?:\.\d+)?\s*(?:lb|lbs|pound)", "weight target"),
    (r"pneumatic|cylinder|piston|solenoid|compressor", "pneumatics"),
    (r"\bpdp\b|\bpdh\b|power distribution", "power distributor"),
    (r"canivore|second can|separate can", "CAN bus"),
    (r"nothing\s+(?:else|more)|no\s+other\s+mechanism|nothing\s+but", "scope"),
)


def revision_report(revision_text: str) -> dict[str, Any]:
    """Which clauses of an edit the deterministic engine recognised, clause by clause."""
    clauses = [c.strip() for c in re.split(r"[,;\n]| and | then ", revision_text, flags=re.I)
               if c.strip()]
    rows = []
    for clause in clauses:
        matched = [label for pattern, label in _EDIT_RECOGNIZERS
                   if re.search(pattern, clause, re.I)]
        rows.append({"clause": clause, "recognized": bool(matched),
                     "as": sorted(set(matched))})
    return {"request": revision_text.strip(),
            "clauses": rows,
            "unrecognized": [r["clause"] for r in rows if not r["recognized"]]}


def plan_and_resolve(prompt: str, *, season: str = "") -> tuple[str, dict[str, Any]]:
    """Rewrite the last revision into clauses the selectors can act on, and say how.

    Built here rather than in the studio because the amplifier needs the design as it stood
    BEFORE this edit to turn "wider" into a number — and the design before the edit is exactly
    the prompt minus its last revision. The pre-edit build skips repair: it is a measurement,
    thrown away, and paying the repair loop twice per edit doubles the cost of every edit for
    no benefit.
    """
    from app.services.edit_planner import plan_edit  # noqa: PLC0415

    if "revision request:" not in prompt.lower():
        return prompt, {}
    parts = re.split(r"revision request:", prompt, flags=re.I)
    latest = parts[-1].strip()
    previous = [seg.strip() for seg in parts[1:-1]]
    joiner = "\n\nRevision request: "
    base_prompt = parts[0] + "".join(joiner + seg for seg in previous)
    try:
        before = build_robot_spec(base_prompt, use_model=False, season=season, _repair=False)
    except Exception:                     # a broken base must not block the edit
        before = {}
    plan = plan_edit(latest, previous=previous, spec=before)

    # For a genuinely open request, one interpretation is a guess. Build the alternatives and
    # keep whichever measures best — the winner replaces the planner's single reading.
    from app.services.edit_explorer import explore  # noqa: PLC0415

    try:
        study = explore(base_prompt, plan, season=season, spec=before,
                        # Trial builds skip the repair loop: it is 2-4 s of the 5 s build, it
                        # is applied equally to whichever option wins, and comparing designs
                        # as GENERATED is the fairer comparison anyway. The winner is rebuilt
                        # in full by the caller.
                        build=lambda text: build_robot_spec(text, use_model=False,
                                                            season=season, _repair=False))
    except Exception:                     # exploration is an optimisation, never a gate
        study = None
    if study:
        plan = {**plan, "exploration": study, "resolved_text": study["resolved_text"]}
        plan["notes"] = list(plan.get("notes") or []) + [
            f"explored {study['explored']} alternatives and built each one; "
            f"chose \"{study['chosen']}\" — {study['why']}"]

    # Then work the chosen direction until the objective actually moves. Exploration picks
    # WHICH architecture; iteration decides how far to push it, and backs off instead of
    # giving up when a size breaks the geometry.
    from app.services.edit_iterator import iterate  # noqa: PLC0415

    try:
        loop = iterate(base_prompt, plan, season=season, before=before,
                       build=lambda text: build_robot_spec(text, use_model=False,
                                                           season=season, _repair=False))
    except Exception:                     # iteration is an optimisation, never a gate
        loop = None
    if loop and not loop["resolved_text"]:
        # The loop tried and could not improve the design without breaking it. Leave the robot
        # alone and say so — a reported failure is worth more than a silent regression.
        plan = {**plan, "iteration": loop, "resolved_text": latest}
        plan["notes"] = list(plan.get("notes") or []) + [
            f"{loop['iterations']} attempt(s) built and measured; {loop['outcome']} — "
            f"the design was left unchanged"]
        return prompt, plan
    if loop:
        plan = {**plan, "iteration": loop, "resolved_text": loop["resolved_text"]}
        plan["notes"] = list(plan.get("notes") or []) + [
            f"{loop['iterations']} attempt(s), each built and measured; "
            f"finished on \"{loop['outcome']}\""]

    resolved = plan.get("resolved_text") or latest
    if resolved == latest:
        return prompt, plan
    return base_prompt + joiner + resolved, plan


# Recently built designs, keyed by their inputs. An EDIT builds the base robot up to three
# times — once to measure the design before the edit, once to diff against, once for the
# result — and two of those are byte-identical repeats. Caching them took an intake edit from
# 8.8 s to about half that.
#
# Sound because the builder is pure: the seed is pinned to the request, and the repair loop is
# bounded in trials rather than seconds, so the same inputs cannot yield two different robots.
# Copies go in and out so a caller mutating its spec cannot corrupt the entry.
_BUILD_CACHE: dict[str, dict[str, Any]] = {}
_BUILD_CACHE_MAX = 24


def clear_build_cache() -> None:
    _BUILD_CACHE.clear()


def build_robot_spec(prompt: str, *, use_model: bool = True, season: str = "",
                     _repair: bool = True) -> dict[str, Any]:
    """Synthesize one robot for one season.

    ``season`` is the team's explicit choice ("2026-rebuilt", "2025-reefscape"). Left empty,
    the season is inferred from the prompt and falls back to the current one — see
    `frc_season.resolve_season`, which also records which of those three happened.
    """
    from copy import deepcopy as _deepcopy  # noqa: PLC0415

    from app.services.component_graph import build_signature  # noqa: PLC0415

    _key = build_signature(prompt, season, use_model, "", _repair)
    _hit = _BUILD_CACHE.get(_key)
    if _hit is not None:
        return _deepcopy(_hit)
    parsed = _parse(prompt, season)
    season_data = parsed["season"]

    # The gate runs BEFORE any geometry. A request that breaks a hard season limit, or that
    # is too vague to compile, must not come back as a robot — see design_gate for the three
    # defects that motivated this.
    from app.services.design_gate import gate as _gate  # noqa: PLC0415

    gate_result = _gate(prompt, season_data.get("rules"), season_data.get("label", ""))
    if gate_result["state"] != "accepted":
        raise DesignNotBuildable(gate_result)

    # The gate's scope decision is authoritative over the looser prose parse: "no mechanisms,
    # only a 27 inch chassis" has to mean a chassis, not a chassis plus four swerve modules
    # and a control system.
    for _name in gate_result["forbidden_mechanisms"]:
        parsed["subsystems_stated"][_name] = False
    if gate_result["scope"] == "chassis":
        parsed["scope"] = "chassis"
        parsed["exclusive"] = True

    rng = _seed(prompt)
    p, latest = parsed["lower"], parsed["latest"]
    original_text = p.rsplit("revision request:", 1)[0]
    profile = parsed["profile"]

    intent: dict[str, Any] = {}
    provenance = {"used": False, "provider": "", "model_version": "",
                  "reason": "model pass disabled for this call"}
    if use_model:
        raw, provenance = _model_intent(prompt, {
            "season": season_data["key"],
            "frame_in": [parsed["width_in"], parsed["length_in"]],
            "drive_type": parsed["drive_type"],
            "subsystems_stated": {k: v for k, v in parsed["subsystems_stated"].items() if v is not None},
        }, parsed["profile_key"])
        intent = _sanitize_intent(raw)

    # Subsystems: explicit statement > model choice > season profile default.
    # A drivetrain-scoped request ("just an MK4i module", "drivebase only") gets NO
    # default mechanisms, only what the prompt states.
    defaults = set(profile["default_subsystems"]) if parsed["scope"] == "robot" else set()
    if parsed["scope"] != "robot":
        intent.pop("subsystems", None)  # model defaults don't apply to a component request
    model_subsystems = set(intent.get("subsystems", []))
    if parsed["exclusive"]:
        # "nothing else" closes the list: the team has told us the robot is complete, so neither
        # the season defaults nor the model may add to it.
        defaults = set()
        model_subsystems = set()
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
    # The revision speaks last about hardware too: "use kraken x44 motors" must actually
    # swap the motor, and "change the ratio to L2" must not lose to an L3 mentioned by the
    # original request ("climber to L3"). Each selector runs on the revision text first and
    # only falls back to the whole prompt when the revision says nothing about it.
    from app.services.frc_parts import _MOTOR_ALIASES, _match  # noqa: PLC0415
    _motor_in_latest = _match(latest, _MOTOR_ALIASES)
    drive_motor_key, drive_motor = select_motor(latest if _motor_in_latest else p,
                                                default="kraken_x60")
    if drive_type == "west-coast":
        # A tank drivebase has no modules at all, describing one would be a lie in the BOM.
        wheel_in = parsed["wheel_in"] or 6.0
        ratio = _number(p, r"(\d+(?:\.\d+)?)\s*:\s*1", 0) or rng.choice([8.45, 10.71, 12.75])
        # R502 caps PROPULSION motors, and on a tank drive every drive motor is one. Six
        # across two gearboxes was the old six-CIM drivebase and it has been illegal since
        # the limit came down to four; the rule report has been saying so on every
        # west-coast design while the geometry went on building six. Two per side.
        # (Swerve is untouched: its steering motors are explicitly not propulsion, so four
        # drive plus four steer is eight motors and four against this limit.)
        limit = int((season_data.get("rules") or {}).get("propulsion_motors", 4))
        motor_count = min(6 if wheel_in >= 5 else 4, limit)
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
        _ratio_in_latest = re.search(r"(?<!\w)(l[1-4]\+?|x[1-3]|t[1-3]|r[1-3]|\d{2}t)(?!\w)"
                                     r"|\d+(?:\.\d+)?\s*:\s*1", latest, re.I)
        ratio_label, ratio = select_drive_ratio(module, latest if _ratio_in_latest else p)
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
        r"stows?\s+inside|inside\s+the\s+frame|within\s+the\s+frame": "under-bumper roller",
    }, intent.get("intake_type") or rng.choice(
        ("coaxial slapdown", "coaxial slapdown", "four-bar over-bumper",
         "under-bumper roller", "fixed over-bumper roller", "dual-roller over-bumper",
         "pivoting compliant-wheel intake")))

    hopper_type = _pick_type(p, HOPPER_TYPES, {
        r"oval\s+spindexer": "oval spindexer",
        r"spindexer|carousel|washing\s*machine|rotating\s+floor": "circular spindexer",
        r"twin[- ]lane|two[- ]lane|double\s+lane": "twin-lane belt hopper",
        r"belt\s+floor|belt\s+hopper|floor\s+belt": "belt-floor hopper",
        r"funnel|tower\s+feed": "funnel-to-tower hopper",
        r"serpentine|tunnel\s+index|snake": "serpentine tunnel indexer",
        r"paddle|agitator|stir": "paddle-wheel agitator hopper",
    }, intent.get("hopper_type") or rng.choice(
        ("circular spindexer", "circular spindexer", "oval spindexer",
         "belt-floor hopper", "funnel-to-tower hopper", "twin-lane belt hopper")))

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

    if re.search(r"\b(?:no|without|remove|delete|omit|drop)\s+(?:the\s+)?turret\b", latest):
        shooter_type = "dual independent flywheel hooded shooter"

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
    # Stated dimensions win over the seed, and both word orders count: "6 inch flywheel"
    # and "flywheel 6 inches". The revision text is read first so an edit lands.
    def _dim(noun: str, fallback: float) -> float:
        forward = rf"(\d+(?:\.\d+)?)\s*{_INCH}\s*(?:diameter\s+)?{noun}"
        backward = rf"{noun}[^.\d]{{0,18}}?(\d+(?:\.\d+)?)\s*{_INCH}"
        for segment in (latest, p):
            value = _number(segment, forward, 0) or _number(segment, backward, 0)
            if value:
                return value
        return fallback

    flywheel_in = _clamp(_dim(r"flywheels?", rng.choice([4.0, 4.0, 5.0, 6.0])), 2.0, 8.0)
    roller_in = _clamp(_dim(r"rollers?", rng.choice([1.625, 2.0, 2.0, 2.5])), 0.875, 4.0)
    mech_motor_key, _ = select_motor(p, default=rng.choice(["neo", "neo_vortex", "kraken_x60"]))
    shooter_motor_key = "kraken_x60" if "kraken" in p else rng.choice(["neo_vortex", "kraken_x60", "falcon_500"])
    # Longitudinal placement varies within a band that stays serviceable and inside the frame.
    intake_bias = rng.uniform(0.06, 0.14)
    shooter_bias = rng.uniform(0.58, 0.72)
    # The tower goes at the BACK. It is the tallest and heaviest thing on the robot: against
    # the back rail its feet bolt to structure at both ends, its mass sits behind the drive
    # centre, and the whole front of the frame is left for the intake and the gamepiece path.
    # A band centred on 0.57 put it in the middle with the hopper in front and the shooter
    # behind — the one place a tower should never be, and the reason the two kept fighting
    # for the same volume. This is the value that matters: `_STATION_DEFAULTS` is only the
    # fallback for a block that does not carry its own bias, and every generated one does.
    elevator_bias = rng.uniform(0.82, 0.92)
    arm_bias = rng.uniform(0.46, 0.60)

    # Detailed, seeded intake geometry. Surface speed comes from the mechanism motor's free
    # speed through the belt reduction and the roller diameter; it must exceed the robot's
    # approach speed or the intake pushes the gamepiece away instead of pulling it in.
    _PI = 3.141592653589793
    intake_reduction = rng.choice([3.0, 4.0, 4.0, 5.0])
    # A stated roller count ("three roller intake") outranks the type's implied pair.
    intake_roller_count = (_count(latest, "roller", 0) or _count(original_text, "roller", 0)
                           or (2 if any(word in intake_type
                                        for word in ("dual", "series", "tunnel")) else 1))
    # A stated intake width ("24 inch intake", "intake 24 inches wide") outranks the frame
    # heuristic; it is still capped so the intake fits between the frame rails downstream.
    intake_width_stated = _dim(r"(?:wide\s+)?intakes?(?:\s+width)?", 0.0)
    intake_roller_center_in = round(roller_in + rng.choice([1.5, 2.0, 2.5]), 2)
    intake_wheel = rng.choice(["30A green compliant wheels", "35A green compliant wheels",
                               "40A grey compliant wheels"])
    _intake_roller_rpm = MOTORS[mech_motor_key]["free_rpm"] / intake_reduction
    intake_surface_speed_fps = round(_intake_roller_rpm * _PI * roller_in / 12.0 / 60.0, 1)
    intake_deployed = intake_type != "fixed over-bumper roller"

    # ── Hopper / indexer ───────────────────────────────────────────────────
    # Sizing is volumetric and honest about it: how many gamepieces fit is floor area divided
    # by the area one piece occupies, derated hard because spheres do not tile and the real
    # limit is what the exit lane can un-jam, not what the box can hold.
    gp_dia = season_data["gamepiece"]["diameter_in"]
    hopper_floor_in = round(min(parsed["width_in"] - 4.0, 24.0), 1)
    hopper_depth_in = round(min(parsed["length_in"] * 0.55, 18.0), 1)
    hopper_wall_in = round(gp_dia * 1.9, 1)           # roughly two pieces deep, with a funnel lip
    hopper_lanes = 2 if "twin" in hopper_type else 1
    hopper_exit_in = round(gp_dia + 0.75, 2)          # exit lane: one piece wide, plus clearance
    hopper_floor_clearance_in = round(gp_dia * 0.085, 2)   # driven wheel proud of the floor plate
    # Capacity by volume, not by footprint: usable height is short of the wall because nobody
    # fills to the brim and driving spills the top layer, and randomly poured spheres pack at
    # about 60% — not the 74% of a stacked lattice they will never form in a hopper.
    _fill_in = hopper_wall_in * 0.8
    _piece_volume = math.pi / 6 * gp_dia ** 3
    hopper_capacity = int(hopper_floor_in * hopper_depth_in * _fill_in * 0.60
                          / _piece_volume) if gp_dia else 0
    hopper_wheel_in = round(rng.choice([3.0, 4.0, 4.0]), 1)
    hopper_wheel_count = max(3, int(round(hopper_floor_in / 6.0)) + 1)
    hopper_reduction = rng.choice([9.0, 12.0, 12.0, 15.0])
    hopper_motor_key = "neo_550" if hopper_floor_in < 16 else rng.choice(["neo", "neo_550"])
    _hopper_rpm = MOTORS[hopper_motor_key]["free_rpm"] / hopper_reduction
    # Throughput: one piece leaves per exit-lane length swept past the gate.
    hopper_feed_per_s = round(max(0.5, (_hopper_rpm / 60.0) * 3.141592653589793
                                 * hopper_wheel_in / max(gp_dia, 1.0) * 0.45), 1)
    hopper_agitator = "paddle" in hopper_type or "spindexer" in hopper_type

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

    # ── The shot the season actually asks for ──────────────────────────────
    # This is the difference between "a shooter" and "a shooter for this game": the goal
    # height and a stated working distance give a required exit velocity, the required exit
    # velocity gives a required surface speed, and the surface speed the design has says
    # whether it makes the shot. All of it is derived here, none of it is looked up.
    goal = element(season_data, "HUB") or element(season_data, "REEF") or {}
    rim_in = float(goal.get("opening_front_edge_in")
                   or (goal.get("levels_in") or {}).get("L4") or 0.0)
    shooter_release_in = round(min(parsed["height_in"], season_data["rules"]["start_height_in"]) * 0.72, 1)
    shot: dict[str, Any] = {}
    if rim_in:
        design_range = float(goal.get("design_range_ft", 15.0))
        long_range = float(goal.get("long_range_ft", 25.0))
        angle = optimal_launch_angle(shooter_release_in, rim_in, design_range)
        needed = required_exit_fps(angle, shooter_release_in, rim_in, design_range)
        needed_long = required_exit_fps(
            optimal_launch_angle(shooter_release_in, rim_in, long_range),
            shooter_release_in, rim_in, long_range)
        achieved = shot_solution(shooter_exit_fps, angle, shooter_release_in, rim_in)
        shot = {
            "target": goal.get("name", "goal"), "target_height_in": rim_in,
            "release_height_in": shooter_release_in,
            "design_range_ft": design_range, "long_range_ft": long_range,
            "optimal_angle_deg": angle,
            "required_exit_fps": needed,
            "required_exit_fps_long": needed_long,
            "required_surface_speed_fps": surface_speed_for_exit(
                needed, counter_rotating=shooter_stacked),
            "achieved_exit_fps": shooter_exit_fps,
            "achieved_range_ft": achieved["range_ft"],
            "apex_in": achieved["apex_in"],
            "entry_angle_deg": achieved["entry_angle_deg"],
            "makes_design_range": bool(achieved["reaches"] and achieved["range_ft"] >= design_range),
            "makes_long_range": bool(achieved["reaches"] and achieved["range_ft"] >= long_range),
            "caveat": achieved["note"],
        }
        # Centre the hood on the cheapest shot rather than on a habit; the span covers the
        # near-and-far pair the same flywheel speed has to serve.
        shooter_pivot_deg = [max(10, round(angle - 16)), min(85, round(angle + 14))]

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

    # How high the mechanism itself may stand. In a season with an in-match vertical cap
    # (2026's R107) nothing may ever exceed it, so a climber cannot telescope a mast up to the
    # rung — the robot has to hoist its own body instead, and the useful number becomes how far
    # it must RISE rather than how far it can reach.
    _height_cap = season_data["rules"].get("max_height_in") or 84.0
    # A mechanism's own height is NOT the robot's height. It stands on the rail top plane
    # (2.0 in above the bellypan) and the wheels hold the bellypan ~1.125 in off the carpet,
    # so both of those eat into the legal envelope before the tower starts. Ignoring them is
    # what produced a 32.1 in robot in a 30 in season — measured, not theorised.
    _MOUNT_Y_IN, _GROUND_CLEARANCE_IN = 2.0, 1.125
    _mech_budget = max(6.0, _height_cap - _MOUNT_Y_IN - _GROUND_CLEARANCE_IN - 1.25)
    climber_stowed_in = round(min(parsed["height_in"],
                                  season_data["rules"]["start_height_in"], _mech_budget), 1)
    climber_extended_in = round(min(parsed["height_in"] + 24, _mech_budget), 1)
    climber_travel_in = round(max(climber_extended_in - climber_stowed_in, 0.0), 1)

    _tower = element(season_data, "TOWER")
    climber_rise: dict[str, Any] = {}
    if _tower and _tower.get("level_criteria"):
        _rungs = _tower["rungs_in"]
        _bumper_top = season_data["rules"]["bumper_zone_in"][1]
        climber_rise = {
            "mode": "hoist the robot, not reach the rung",
            "rung_od_in": _tower.get("rung_od_in", 1.66),
            "rung_spacing_in": _tower.get("rung_spacing_in", 18.0),
            "l2_rise_in": round(_rungs["L2"] - _bumper_top, 1),
            "l3_rise_in": round(_rungs["L3"] - _bumper_top, 1),
            "criteria": _tower["level_criteria"],
            "note": ("Levels score on where the robot's bumper covers end up, not on what it "
                     "touches, and the height cap forbids extending to the rung. The mechanism "
                     "grips a rung and lifts the whole robot; re-gripping one rung higher "
                     "(18 in) is what earns the next level."),
        }

    def block(name: str, **extra: Any) -> dict[str, Any]:
        included = name in subsystems
        return {"included": included, **extra}

    width_in, length_in, height_in = parsed["width_in"], parsed["length_in"], parsed["height_in"]
    gamepiece = profile["gamepiece"]

    spec: dict[str, Any] = {
        "schema_version": "3.2",
        # Both go on the bumpers, and both stay out of the CAD tree on purpose: a number and
        # a colour are finish, not fabricated parts, and adding them to the geometry would
        # change every chassis assembly the model was trained on.
        "team_number": parsed["team_number"],
        "bumper_color": parsed["bumper_color"],
        # Structural decisions in the CAD tree — how many crossmembers, belt or chain, plate
        # or tube — are resolved from this, so one prompt always yields one robot while
        # different prompts differ in construction and not merely in dimensions.
        # Pinned to the original request (see _seed): a revision may not reshuffle the build.
        "design_seed": int(hashlib.sha256(
            re.split(r"revision request:", prompt, maxsplit=1, flags=re.I)[0]
            .strip().lower().encode()).hexdigest()[:8], 16),
        "profile": {"id": parsed["profile_key"], "label": profile["label"], "season": profile["season"],
                    "knowledge_version": KNOWLEDGE_VERSION, "parts_version": PARTS_VERSION},
        "model": provenance,
        # What the request actually scoped. `build_cad` and the FeatureScript defaults both
        # read these, so a chassis-only request cannot pick up a drivetrain or a control
        # system on the way through.
        "scope": gate_result["scope"],
        "include_drivetrain": gate_result["include_drivetrain"],
        "include_electrical": gate_result["include_electrical"],
        "gate": {"state": gate_result["state"], "assumptions": gate_result["assumptions"],
                 "forbidden_mechanisms": gate_result["forbidden_mechanisms"],
                 "version": gate_result["version"]},
        "frame": {"width_in": width_in, "length_in": length_in, "height_in": height_in,
                  "rail_height_in": 1, "rail_width_in": 2,
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
        # A stated width wins; an over-bumper intake may run wider than the frame interior
        # (full-width "touch it, own it" intakes are real), an in-frame one cannot.
        "intake": block("intake", type=intake_type,
                        width_in=round(min(intake_width_stated, width_in + 4) if intake_width_stated
                                       else min(width_in - 4, 24), 1),
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
        "hopper": block("hopper", type=hopper_type,
                        floor_width_in=hopper_floor_in, floor_depth_in=hopper_depth_in,
                        wall_height_in=hopper_wall_in, lanes=hopper_lanes,
                        exit_lane_width_in=hopper_exit_in,
                        gamepiece_diameter_in=gp_dia,
                        capacity_estimate=hopper_capacity,
                        wheel_diameter_in=hopper_wheel_in, wheel_count=hopper_wheel_count,
                        wheel_proud_in=hopper_floor_clearance_in,
                        gear_reduction=f"{hopper_reduction:g}:1",
                        feed_rate_per_s=hopper_feed_per_s,
                        agitator=hopper_agitator,
                        floor="0.093 in polycarbonate floor plate over a plate frame",
                        sensor="beam-break at the exit gate, one staged piece at a time",
                        access="thumbscrew top panel so a jam clears without removing the shooter",
                        motor_key=hopper_motor_key, motor=MOTORS[hopper_motor_key]["name"],
                        motor_count=2 if hopper_lanes > 1 else 1,
                        position_bias=round(rng.uniform(0.34, 0.50), 3),
                        rigid_subassemblies=["hopper floor", "outer wall", "driven wheel shaft",
                                             "exit gate", "feeder handoff"],
                        caveat=("Capacity is volumetric: floor area × 80% of the wall height × "
                                "60% random-pour packing, divided by one gamepiece. The real "
                                "limit is usually what the exit lane clears without jamming, "
                                "not what the box holds. Feed rate is a geometric ceiling — "
                                "measure it with a full hopper while driving the obstacles.")),
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
                         release_height_in=shooter_release_in,
                         shot=shot or None,
                         feed_path=("hopper → feeder tower → " if "hopper" in subsystems else "")
                                   + ("indexer → barrel → staged flywheel pairs" if shooter_stacked
                                      else "feeder → flywheel pair → hood"),
                         position_bias=round(shooter_bias, 3),
                         rigid_subassemblies=(["indexer", "barrel"] +
                                              [f"flywheel stage {n}" for n in range(1, shooter_stages + 1)] +
                                              (["turret ring"] if shooter_turreted else ["hood"]))
                                             if shooter_stacked else
                                             ["feeder", "left flywheel", "right flywheel", "hood"]),
        "elevator": block("elevator", architecture=elevator_arch, stages=int(stages),
                          max_height_in=min(height_in, _mech_budget), rail=ladder_label(int(stages) - 1),
                          reduction="12:1",
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
        "climber": block("climber", type=climber_type, stowed_height_in=climber_stowed_in,
                         extended_height_in=climber_extended_in,
                         height_cap_in=_height_cap, rise=climber_rise or None,
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
            "perimeter_limit_in": season_data["rules"]["perimeter_in"],
            "start_height_limit_in": season_data["rules"]["start_height_in"],
            "extension_limit_in": season_data["rules"]["extension_in"],
            "propulsion_motor_limit": season_data["rules"]["propulsion_motors"],
            "weight_limit_lb": season_data["rules"]["weight_lb"],
            "target_weight_lb": parsed["weight_lb"] or profile.get("target_weight_lb", 105),
            "bumper_gap_in": 0.25, "service_clearance_in": 0.5,
            "bumper_zone_in": season_data["rules"]["bumper_zone_in"],
            "frame_note": parsed["frame_budget"]["note"],
            "rule_snapshot": (f"Limits shown are the published {season_data['label']} figures. "
                              "Rules move by team update — verify perimeter, height, extension, "
                              "motor count and weight against the current manual before "
                              "inspection."),
        },
        # What this robot is for, expressed as the season's own numbers. Everything here is
        # derived from the field and the rules rather than described, so the same request in a
        # different season produces different targets instead of the same robot relabelled.
        "season": {
            **season_digest(season_data),
            # The raw published limits travel WITH the design, so the rule report measures
            # geometry against this season's numbers instead of a constant compiled in
            # somewhere else. `season_rule_report` reads this block.
            "limits": dict(season_data["rules"]),
            "rule_source": {
                "manual": f"{season_data.get('year', '')} FRC Game Manual".strip(),
                "season_key": season_data["key"],
                "enforced_automatically": ["perimeter_in", "max_height_in", "extension_in"],
                "human_review_required": ["weight_lb", "bumper_zone_in", "propulsion_motors"],
            },
            "selected_by": parsed["season_reason"],
            "summary": season_data["summary"],
            "gamepiece": season_data["gamepiece"],
            "match": season_data["match"],
            "scoring": season_data["scoring"],
            "ranking": season_data["ranking"],
            "field_elements": season_data["field"]["elements"],
            "design_targets": design_targets(season_data, robot_height_in=height_in,
                                             release_height_in=shooter_release_in),
            "scoring_math": scoring_math(season_data),
            "archetypes": season_data["archetypes"],
            "verify": season_data["verify"],
        },
        "reference_basis": references_for(subsystems, turreted=shooter_turreted),
        "techniques": techniques_for(subsystems, drive=drive_type, turreted=shooter_turreted),
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
    # Run the season's construction rules over the finished spec. This is the last thing that
    # happens because it checks the assembled result, not the intent — and it is reported, not
    # enforced: a design that trips a check is shown with the check, so the team decides.
    spec["rule_check"] = rule_findings(spec, season_data)

    # ── CAD: the same robot expressed as individual dimensioned parts ──────
    # Everything above says what the robot *is*; this says what it is made of, in one
    # coordinate system, so a viewer, a BOM and a cut list all read the same geometry.
    from app.services.cad_contract import (compact_design_spec, editable_manifest, normalize_cad,
                                           require_valid_cad, require_valid_parametric_design)
    from app.services.geometry_validation import geometry_status
    # The bumper envelope is what the robot is actually measured at, so it is published beside
    # the frame rather than left for each consumer to re-derive from a plate size.
    from app.services.frc_cad import bumper_envelope  # noqa: PLC0415
    spec["bumper"] = bumper_envelope(width_in, length_in)
    spec["cad"] = require_valid_cad(normalize_cad(build_cad(spec)))
    # Model-proposed geometry, gated by the same contract as everything else. The model may
    # PROPOSE one subsystem's assembly; require_valid_cad decides whether it ships, and a
    # rejection falls back to the compiler's geometry with the reason recorded on the spec.
    if use_model and get_settings().model_geometry:
        _model_geometry_pass(spec, prompt)
    spec["parametric_design"] = require_valid_parametric_design(compact_design_spec(spec))
    # The design's own title, set here so every caller (not just the web wrapper) gets a name
    # that matches the request. A chassis-only design must not be titled after a mechanism it
    # was explicitly denied.
    _season_label = (spec.get("season") or {}).get("label", "")
    if spec.get("scope") == "chassis":
        spec["name"] = (f"{width_in:g} × {length_in:g} in chassis"
                        + (f" | {_season_label}" if _season_label else ""))
    else:
        _dt = spec.get("drivetrain") or {}
        _module = _dt.get("module") or (str(_dt.get("type", "swerve")).title() + " drivetrain")
        _lead = (spec.get("subsystems") or ["drivebase"])[0].title()
        spec["name"] = (f"{_module} {_lead} robot"
                        + (f" | {_season_label}" if _season_label else ""))

    # Measured against the solids that were actually generated, not against the constants the
    # FeatureScript declares. `export_blocked` is what the API refuses exports on.
    from app.services.cad_contract import (  # noqa: PLC0415
        fidelity_report, season_rule_report, transmission_report)
    spec["rule_report"] = season_rule_report(spec)
    spec["fidelity"] = fidelity_report(spec.get("cad") or {})
    spec["transmission"] = transmission_report(spec.get("cad") or {})
    # Generate → validate → repair → rebuild → validate again. The repair passes move real
    # design parameters (a mechanism's station, a turret's travel limits) and re-validate
    # after each one; a pass that makes the total worse, or that would leave a mechanism
    # floating off its mount, is reverted. A design that is already clean is untouched.
    from app.services.geometry_repair import repair_geometry  # noqa: PLC0415
    repaired, repair = (repair_geometry(spec["cad"]) if _repair
                        else (spec["cad"], {"issues_before": 0, "issues_after": 0,
                                            "passes": 0, "repairs": [], "status": "skipped"}))
    if repair["issues_after"] < repair["issues_before"]:
        spec["cad"] = require_valid_cad(normalize_cad(repaired))
        spec["cad"]["geometry"] = repair["report"]
    spec["cad"]["geometry_repair"] = {k: v for k, v in repair.items() if k != "report"}
    spec["geometry_status"] = geometry_status(spec.get("cad") or {})
    spec["editable_manifest"] = editable_manifest(spec["cad"])
    spec["cut_list"] = cut_list(spec["cad"])
    spec["profile"]["cad_version"] = CAD_VERSION
    if "revision request:" in p:
        # Say exactly which clauses of the edit landed. An edit the engine cannot apply is
        # reported, never silently absorbed into an identical rebuild.
        raw_revision = re.split(r"revision request:", prompt, flags=re.I)[-1]
        spec["revision"] = revision_report(raw_revision)
    if len(_BUILD_CACHE) >= _BUILD_CACHE_MAX:
        _BUILD_CACHE.pop(next(iter(_BUILD_CACHE)), None)
    _BUILD_CACHE[_key] = _deepcopy(spec)
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
    for key, label in (("intake", "intake"), ("hopper", "hopper"), ("shooter", "shooter"),
                       ("elevator", "elevator"), ("manipulator", "arm"), ("climber", "climber")):
        block = spec[key]
        if not block.get("included"):
            continue
        motor = MOTORS.get(block.get("motor_key", "neo"), MOTORS["neo"])
        base = {"intake": 8.0, "hopper": 9.0, "shooter": 14.0, "elevator": 18.0,
                "manipulator": 12.0, "climber": 10.0}[key]
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
