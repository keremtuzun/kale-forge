"""Turn an edit request into an engineering objective, then into changes the engine can make.

The studio's edit path is a text pipeline: a design is `prompt + "Revision request: …"`, and
deterministic selectors in `robot_spec` read that text for things they recognise. It is precise
and it is honest — `revision_report` names any clause it could not apply — but it is only as
capable as the words it is handed, and that produced two failures users actually hit:

  * **"make the intake better" changes nothing.** No selector matches it, so the engine rebuilds
    the identical robot and the studio says the design was updated. The request was an
    engineering objective and the pipeline needed a dimension.
  * **"a little more" has no referent.** Every prompt is re-read from scratch, so a follow-up
    that refers to the previous edit is simply unrecognised.

This module sits in front of the selectors rather than replacing them. It classifies the
request (intent, scope, strength), diagnoses what would have to change to satisfy it, resolves
anything said relative to a previous edit, and rewrites it as the concrete clauses the engine
already knows how to apply. The deterministic engine keeps final authority over geometry; what
changes is that it is now told what to do in a language it understands.

Aggressiveness is a policy, not an accident. "improve the intake" is allowed to move several
parameters at once, because a 5% change to one dimension is not an improvement, it is a way of
appearing to have done something.
"""
from __future__ import annotations

import re
from typing import Any

# ── vocabulary ───────────────────────────────────────────────────────────────
# Intent decides WHAT the edit is for; strength decides how far it may go. They are separate
# because "fix the intake" and "redesign the intake" want the same subsystem touched with very
# different licence.
_INTENTS: tuple[tuple[str, str], ...] = (
    (r"\bredesign|start over|from scratch|completely re(?:build|work|design)", "REDESIGN"),
    (r"\breplace\b|swap out|rip out", "REPLACE"),
    (r"\bsimplif|reduce complexity|fewer parts|less complex", "REDUCE_COMPLEXITY"),
    (r"\blighten|lose weight|too heavy|reduce weight|lighter", "LIGHTEN"),
    (r"\bstrengthen|stiffer|stronger|flex(?:es|ing)?\b|rigid", "STRENGTHEN"),
    (r"\bfaster|more speed|speed it up|quicker|cycle time", "INCREASE_SPEED"),
    (r"\breliab|jam(?:s|ming)?\b|drop(?:s|ping)? (?:the )?(?:ball|piece|note|coral)|misses|inconsistent",
     "INCREASE_RELIABILITY"),
    (r"\boptimi[sz]e|best|tune|dial in", "OPTIMIZE"),
    (r"\bfix\b|broken|does(?:n't| not) work|failing|won'?t\b", "FIX"),
    (r"\bimprove|better|upgrade|more capable", "IMPROVE"),
    (r"\bmove\b|shift|reposition|relocate", "MOVE"),
    (r"\bwider|narrower|taller|shorter|bigger|smaller|resize|larger", "RESIZE"),
)

# How far the edit may go. Read off the user's own language, because "a bit" and "much" are the
# clearest signal anyone gives about how much change they want.
_STRENGTH: tuple[tuple[str, str], ...] = (
    (r"\bcompletely|entirely|from scratch|redesign|whole new|totally", "redesign"),
    (r"\bmuch\b|a lot|far\b|significantly|substantially|way\s+(?:more|better|bigger)|properly",
     "aggressive"),
    (r"\bslightly|a little|a bit|marginally|a touch|nudge|tweak|slight", "minimal"),
)

_SUBSYSTEMS: tuple[tuple[str, str], ...] = (
    (r"\bintake|collector|floor pickup", "intake"),
    (r"\bshooter|launcher|flywheel|hood|turret|barrel", "shooter"),
    (r"\bhopper|spindexer|indexer|magazine|storage|serialis", "hopper"),
    (r"\belevator|lift|tower|cascade|stage", "elevator"),
    (r"\barm\b|wrist|gripper|claw|manipulator|end effector", "manipulator"),
    (r"\bclimb|hang|cage|rung", "climber"),
    (r"\bdrive(?:train|base)?\b|swerve|chassis|frame|wheels?\b", "drivetrain"),
    (r"\belectrical|wiring|battery|roborio|pdh|pdp|harness", "electrical"),
)

# Scope follows from how many subsystems the objective touches and how deep it cuts.
_SCOPE_BY_STRENGTH = {"minimal": "parameter", "moderate": "subsystem",
                      "aggressive": "subsystem", "redesign": "architecture"}


def _first(patterns: tuple[tuple[str, str], ...], text: str, default: str = "") -> str:
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            return label
    return default


def _all(patterns: tuple[tuple[str, str], ...], text: str) -> list[str]:
    return [label for pattern, label in patterns if re.search(pattern, text, re.I)]


def _has_concrete_numbers(text: str) -> bool:
    """Does the request already name a dimension the selectors can act on?"""
    return bool(re.search(r"\d", text))


# ── diagnosis ────────────────────────────────────────────────────────────────
# What actually has to change on a robot to satisfy each objective, per subsystem. These are
# the physical causes a mentor would list, and they are what the amplifier turns into numbers.
_DIAGNOSIS: dict[tuple[str, str], list[str]] = {
    ("intake", "INCREASE_RELIABILITY"): [
        "roller sits too high off the carpet to reach a piece lying flat",
        "not enough compression on the gamepiece to carry it up",
        "intake is narrower than the approach error while driving",
        "single roller cannot both acquire and hand over"],
    ("intake", "IMPROVE"): [
        "capture width is the limiting factor when driving at speed",
        "compression is set for handling, not for acquisition",
        "one roller stage leaves a gap between pickup and indexer"],
    ("shooter", "INCREASE_RELIABILITY"): [
        "feeder centreline sits below the flywheel entry, so the piece arrives at a step",
        "compression at the flywheels is too low for a repeatable exit velocity",
        "hood wrap is too short to settle the piece before it leaves"],
    ("shooter", "INCREASE_SPEED"): [
        "flywheel inertia is high for the recovery time wanted",
        "single motor per wheel limits spin-up"],
    ("hopper", "INCREASE_RELIABILITY"): [
        "exit lane is wide enough to present two pieces at once",
        "no agitation, so pieces bridge over the exit"],
    ("elevator", "INCREASE_SPEED"): [
        "stage count is high for the travel required, so every cycle pays the handoffs"],
    ("climber", "INCREASE_RELIABILITY"): [
        "hook geometry does not capture unless the approach is square"],
}


def _diagnose(subsystems: list[str], intent: str) -> list[str]:
    out: list[str] = []
    for sub in subsystems:
        out += _DIAGNOSIS.get((sub, intent), [])
        if not _DIAGNOSIS.get((sub, intent)):
            out += _DIAGNOSIS.get((sub, "IMPROVE"), [])
    return out


# ── amplification: objective → clauses the engine already understands ────────
# The multipliers are the whole point of the module. A 5% change to one dimension is not an
# improvement, it is a way of appearing to have done something, so `moderate` and above move
# several parameters together and by amounts a mentor would recognise as a real change.
_SCALE = {"minimal": 0.04, "moderate": 0.12, "aggressive": 0.22, "redesign": 0.35}


def _intake_changes(spec: dict[str, Any], intent: str, strength: str) -> list[str]:
    intake = spec.get("intake") or {}
    if not intake.get("included"):
        return []
    step = _SCALE[strength]
    out: list[str] = []
    width = float(intake.get("width_in") or 0)
    frame_w = float((spec.get("frame") or {}).get("width_in") or 27.0)
    if width:
        # Widening is the single highest-value change for acquisition, bounded by the frame:
        # an intake wider than the robot is not an intake, it is an inspection failure.
        target = min(round(width * (1 + step), 1), round(frame_w - 1.0, 1))
        if target > width + 0.4:
            # "26 in intake width", never "26 in wide intake". The frame selector matches a
            # number followed immediately by wide/width, so the second phrasing silently
            # shrank the whole robot to 26 in instead of widening its intake — the edit landed
            # on the wrong parameter and the summary showed "frame width 27 → 26".
            out.append(f"{target:g} in intake width")
    if strength in ("aggressive", "redesign") and int(intake.get("roller_count") or 0) < 3:
        out.append("three rollers")
    # Compression has no selector, so emitting it would only add a clause the engine reports
    # as unapplied. Kept out of the request and stated in the diagnosis instead.
    if strength in ("aggressive", "redesign") and "over" not in str(intake.get("type") or ""):
        out.append("over-bumper intake")
    return out


def _shooter_changes(spec: dict[str, Any], intent: str, strength: str) -> list[str]:
    shooter = spec.get("shooter") or {}
    if not shooter.get("included"):
        return []
    step = _SCALE[strength]
    out: list[str] = []
    dia = float(shooter.get("flywheel_diameter_in") or 0)
    if dia and intent in ("INCREASE_SPEED", "OPTIMIZE"):
        # Smaller wheel spins up faster; bigger wheel holds energy through a shot.
        out.append(f"{round(dia * (1 - step), 1):g} in flywheels")
    elif dia and intent in ("IMPROVE", "INCREASE_RELIABILITY", "FIX"):
        out.append(f"{round(dia * (1 + step), 1):g} in flywheels")
    if strength in ("aggressive", "redesign"):
        if not shooter.get("turreted"):
            out.append("turret")
        if "adjust" not in str(shooter.get("hood") or shooter.get("type") or ""):
            out.append("adjustable hood")
    return out


def _hopper_changes(spec: dict[str, Any], intent: str, strength: str) -> list[str]:
    hopper = spec.get("hopper") or {}
    if not hopper.get("included"):
        return []
    out: list[str] = []
    if intent in ("INCREASE_RELIABILITY", "FIX", "IMPROVE"):
        if not hopper.get("agitator"):
            out.append("agitator")
        out.append("spindexer")
    return out


def _elevator_changes(spec: dict[str, Any], intent: str, strength: str) -> list[str]:
    elevator = spec.get("elevator") or {}
    if not elevator.get("included"):
        return []
    stages = int(elevator.get("stages") or 0)
    if intent == "REDUCE_COMPLEXITY" and stages > 2:
        return [f"{stages - 1} stage elevator"]
    if intent in ("IMPROVE", "INCREASE_SPEED") and stages and stages < 3:
        return [f"{stages + 1} stage elevator"]
    return []


_AMPLIFIERS = {"intake": _intake_changes, "shooter": _shooter_changes,
               "hopper": _hopper_changes, "elevator": _elevator_changes}


def _amplify(spec: dict[str, Any], subsystems: list[str], intent: str,
             strength: str) -> list[str]:
    out: list[str] = []
    for sub in subsystems:
        builder = _AMPLIFIERS.get(sub)
        if builder:
            out += builder(spec, intent, strength)
    return out


# ── conversational memory ────────────────────────────────────────────────────
# "a little more", "a bit less", "actually make it 2 inches smaller than that" — every one of
# these is meaningless without the edit before it, and the engine re-reads the whole prompt
# from scratch every time. Resolving them here is what makes an edit chain feel like one
# conversation with one engineer instead of a series of unrelated requests.
_RELATIVE = re.compile(
    r"^\s*(?:actually,?\s*)?(?:make\s+it\s+|go\s+)?"
    r"(?P<dir>a little more|a bit more|a little less|a bit less|more|less|wider|narrower|"
    r"bigger|smaller|taller|shorter)\s*(?:than that)?\s*$", re.I)
_RELATIVE_BY = re.compile(
    r"(?:make\s+it\s+|by\s+)?(?P<n>\d+(?:\.\d+)?)\s*(?:in|inch|inches|\")\s*"
    r"(?P<dir>more|less|wider|narrower|bigger|smaller|taller|shorter)"
    r"(?:\s+than\s+that)?", re.I)

_GROW = {"a little more", "a bit more", "more", "wider", "bigger", "taller"}


def _last_numeric_edit(previous: list[str]) -> tuple[str, float, str] | None:
    """The most recent edit that set a dimension: (clause, value, unit-phrase).

    Searched newest first so "a little more" refers to the last thing changed, which is what
    the word "more" means in a conversation.
    """
    for text in reversed(previous):
        match = re.search(r"(?P<n>\d+(?:\.\d+)?)\s*(?:in|inch|inches|\")\s*(?P<what>[a-z ]{3,30})",
                          text, re.I)
        if match:
            return text, float(match.group("n")), match.group("what").strip()
    return None


def _resolve_relative(text: str, previous: list[str]) -> tuple[str, str]:
    """Rewrite a relative follow-up into an absolute one. Returns (text, note)."""
    last = _last_numeric_edit(previous)
    if not last:
        return text, ""
    _, value, what = last
    plain = _RELATIVE.match(text)
    if plain:
        direction = plain.group("dir").lower()
        delta = value * (0.10 if "little" in direction or "bit" in direction else 0.20)
        new = value + delta if direction in _GROW else value - delta
        return (f"{round(new, 1):g} in {what}",
                f"read as {round(new, 1):g} in {what}, from the previous {value:g} in")
    by = _RELATIVE_BY.search(text)
    if by and not re.search(r"\d+(?:\.\d+)?\s*(?:in|inch|inches|\")\s*(?:wide|long|tall)", text, re.I):
        amount, direction = float(by.group("n")), by.group("dir").lower()
        new = value + amount if direction in _GROW else value - amount
        return (f"{round(new, 1):g} in {what}",
                f"read as {round(new, 1):g} in {what}, from the previous {value:g} in")
    return text, ""


# ── the plan ─────────────────────────────────────────────────────────────────
def plan_edit(revision_text: str, *, previous: list[str] | None = None,
              spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify, diagnose and concretise one edit request.

    `resolved_text` is what the deterministic engine is actually given. When the request was
    already specific it is returned unchanged — a user who asked for 4 in wheels gets 4 in
    wheels, not an interpretation of them.
    """
    previous = list(previous or [])
    spec = spec or {}
    text = (revision_text or "").strip()
    if not text:
        return {"objective": "", "resolved_text": "", "intent": "", "scope": "parameter",
                "strength": "minimal", "affected_subsystems": [], "diagnosis": [],
                "changes": [], "success_criteria": [], "notes": []}

    notes: list[str] = []
    resolved, note = _resolve_relative(text, previous)
    if note:
        notes.append(note)

    intent = _first(_INTENTS, resolved) or ("RESIZE" if _has_concrete_numbers(resolved) else "IMPROVE")
    strength = _first(_STRENGTH, resolved) or (
        "minimal" if _has_concrete_numbers(resolved) else "moderate")
    subsystems = _all(_SUBSYSTEMS, resolved)

    # An explicit dimension is an instruction, not a hint: do exactly that and nothing more.
    concrete = _has_concrete_numbers(resolved)
    changes: list[str] = []
    if not concrete and subsystems:
        changes = _amplify(spec, subsystems, intent, strength)
        if changes:
            resolved = f"{resolved}: " + ", ".join(changes)
            notes.append("vague request expanded to concrete parameters so the engine can act "
                         "on it; a 5% nudge to one dimension is not an improvement")

    scope = _SCOPE_BY_STRENGTH.get(strength, "subsystem")
    if len(subsystems) > 1:
        scope = "architecture" if strength == "redesign" else "multiSubsystem"
    elif concrete and len(subsystems) <= 1:
        scope = "parameter"

    return {
        "objective": text,
        "resolved_text": resolved,
        "intent": intent,
        "scope": scope,
        "strength": strength,
        "affected_subsystems": subsystems,
        "diagnosis": _diagnose(subsystems, intent),
        "changes": changes,
        "success_criteria": _criteria(subsystems, intent),
        "notes": notes,
    }


def _criteria(subsystems: list[str], intent: str) -> list[str]:
    """What "done" means for this edit, in terms something can actually be checked against."""
    out = ["no unintended interference between solids",
           "nothing truncated and nothing outside the assembly bounds",
           "every mechanism clear through its full range of motion"]
    if "intake" in subsystems:
        out.append("intake clears the bumper through its whole deploy arc")
    if "shooter" in subsystems:
        out.append("turret clears the superstructure through its permitted rotation")
    if intent == "LIGHTEN":
        out.append("mass estimate below the previous design")
    return out


# ── what actually changed ────────────────────────────────────────────────────
# "Robot updated successfully" is the message that made edits feel unpredictable: it is emitted
# whether the engine changed a dimension or silently rebuilt the identical robot. This diffs
# the two specs and reports the difference, so a no-op is visible as a no-op.
_REPORTED: tuple[tuple[str, str, str], ...] = (
    ("intake", "width_in", "intake width"),
    ("intake", "roller_count", "intake rollers"),
    ("intake", "roller_diameter_in", "intake roller diameter"),
    ("intake", "compression_in", "gamepiece compression"),
    ("intake", "type", "intake type"),
    ("shooter", "flywheel_diameter_in", "flywheel diameter"),
    ("shooter", "type", "shooter type"),
    ("shooter", "turreted", "turret"),
    ("hopper", "type", "hopper type"),
    ("hopper", "capacity_estimate", "hopper capacity"),
    ("elevator", "stages", "elevator stages"),
    ("elevator", "max_height_in", "elevator travel"),
    ("climber", "type", "climber type"),
    ("drivetrain", "type", "drivetrain"),
    ("drivetrain", "wheel_diameter_in", "wheel diameter"),
    ("frame", "width_in", "frame width"),
    ("frame", "length_in", "frame length"),
)


def edit_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The engineering diff between two designs, as lines a human can check."""
    changed: list[str] = []
    for section, key, label in _REPORTED:
        old = (before.get(section) or {}).get(key)
        new = (after.get(section) or {}).get(key)
        if old == new or new is None:
            continue
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            unit = " in" if key.endswith("_in") else ""
            changed.append(f"{label} {old:g}{unit} → {new:g}{unit}")
        else:
            changed.append(f"{label} {old} → {new}")
    before_cad, after_cad = before.get("cad") or {}, after.get("cad") or {}
    before_n, after_n = before_cad.get("feature_total"), after_cad.get("feature_total")
    if before_n and after_n and before_n != after_n:
        changed.append(f"modelled features {before_n} → {after_n}")
    geometry = (after.get("geometry_status") or {})
    return {
        "changed": changed,
        "applied": bool(changed),
        "validation": {
            "status": geometry.get("status"),
            "blocking": geometry.get("blocking"),
            "warnings": geometry.get("warnings"),
            "repairs": [r.get("change") for r in
                        ((after_cad.get("geometry_repair") or {}).get("repairs") or [])],
        },
        # A no-op is a real outcome and saying so is the point of this function.
        "note": ("No parameter the engine tracks changed. The request was understood but it "
                 "did not move the design — say so rather than reporting success."
                 if not changed else ""),
    }
