"""The gate a request passes through BEFORE any geometry is generated.

Three defects motivated this module, all of them the same mistake in different clothes: the
compiler treated every prompt as buildable and answered with a robot no matter what was
asked.

  * "4 meter elevator robot" produced a normal FRC robot. 4 m is 157 in; the 2026 manual caps
    total height at 30 in. Silently building a legal-height robot answers a question nobody
    asked, and quietly clipping the number is worse than refusing.
  * "no mechanisms, only a 27 inch chassis" produced ~121 parts including swerve modules and
    a control system. "No" and "only" were not represented at all.
  * "robot" produced a ~258-part robot built almost entirely out of assumptions.

So the gate answers one of four things, and geometry only runs for the first:

    accepted                 - enough is known to build
    clarification_required   - too little is known; ask a few high-value questions
    rejected                 - what was asked breaks a hard rule of the stated season
    unsupported              - understood, but outside what this compiler can build

Everything here is deterministic and stdlib-only: this module is imported by the Vercel
function, which ships without requirements.txt (see the site bundle parity test).
"""
from __future__ import annotations

import math
import re
from typing import Any

GATE_VERSION = "kale-gate-1.0"

# ── units ────────────────────────────────────────────────────────────────────
# Everything internally is inches. Metric is first-class because teams write in both, and
# the misspellings are the ones people actually type ("meteres", "elvator").
_TO_IN = {
    "mm": 0.0393701, "millimeter": 0.0393701, "millimetre": 0.0393701,
    "cm": 0.3937008, "centimeter": 0.3937008, "centimetre": 0.3937008,
    "m": 39.37008, "meter": 39.37008, "metre": 39.37008, "meteres": 39.37008,
    "in": 1.0, "inch": 1.0, "inches": 1.0, "inchs": 1.0, '"': 1.0, "”": 1.0,
    "ft": 12.0, "foot": 12.0, "feet": 12.0, "'": 12.0, "’": 12.0,
}
_UNIT_RE = (r"(?:mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|met(?:er|re)s?|meteres|m"
            r"|inch(?:e?s)?|in|feet|foot|ft|[\"”'’])")
_NUM_UNIT = re.compile(rf"(\d+(?:\.\d+)?)\s*({_UNIT_RE})(?![a-z])", re.I)

# Tolerant spellings for the nouns that carry a *height* requirement.
_TALL_NOUN = (r"elevator|elavator|elivator|elvator|lift|tower|mast|telescop\w*"
              r"|climb\w*|reach|extension|tall|height|high|hang\w*")
_WIDE_NOUN = r"frame|chassis|chasis|drivebase|drive\s*base|wide|width|long|length|perimeter"


def _to_inches(value: float, unit: str) -> float | None:
    factor = _TO_IN.get(unit.lower().rstrip("."))
    if factor is None:
        # "metres"/"meters"/"inches" plural forms not in the table
        u = unit.lower().rstrip(".")
        for key, f in _TO_IN.items():
            if u.startswith(key):
                factor = f
                break
    return None if factor is None else value * factor


def measurements(text: str) -> list[dict[str, Any]]:
    """Every `<number> <unit>` in the prompt, converted to inches, with its context.

    The context window is what lets "4 meter elevator" be understood as a height while
    "27 inch chassis" is understood as a frame dimension.
    """
    out: list[dict[str, Any]] = []
    low = text.lower()
    for match in _NUM_UNIT.finditer(low):
        inches = _to_inches(float(match.group(1)), match.group(2))
        if inches is None or not math.isfinite(inches):
            continue
        window = low[max(0, match.start() - 40):match.end() + 40]
        out.append({
            "raw": match.group(0).strip(),
            "value": float(match.group(1)),
            "unit": match.group(2),
            "inches": round(inches, 3),
            "vertical": bool(re.search(_TALL_NOUN, window)),
            "planar": bool(re.search(_WIDE_NOUN, window)),
            "at": match.start(),
        })
    return out


# ── negative / exclusive scope ───────────────────────────────────────────────
MECHANISMS = ("intake", "hopper", "shooter", "elevator", "manipulator", "climber")
_MECH_WORD = {
    "intake": r"intake|collector",
    "hopper": r"hopper|spindexer|indexer|magazine|carousel",
    "shooter": r"shoot(?:er)?|launcher|flywheel",
    "elevator": r"elevator|elavator|elivator|elvator|lift",
    "manipulator": r"arm|manipulator|wrist|claw|gripper",
    "climber": r"climb(?:er|ing)?|hang(?:er)?",
}
# "no mechanisms", "without any mechanisms", "no subsystems", "nothing else"
_NO_MECHANISMS = re.compile(
    r"\bno\s+(?:other\s+)?(?:mechanism|subsystem|manipulator|attachment)s?\b"
    r"|\bwithout\s+(?:any\s+)?(?:mechanism|subsystem)s?\b"
    r"|\bnothing\s+(?:else|more|but)\b"
    r"|\bmechanisms?\s*[-: ]?\s*none\b", re.I)
# "only a chassis", "chassis only", "just the frame"
_CHASSIS_ONLY = re.compile(
    r"\b(?:only|just)\s+(?:an?\s+|the\s+)?(?:[\w\-]+\s+){0,3}?(?:chassis|chasis|frame|drivebase|drive\s*base)\b"
    r"|\b(?:chassis|chasis|frame|drivebase|drive\s*base)[\s\-]*only\b", re.I)
_WANTS_DRIVE = re.compile(r"\bswerve|mk\d|tank|west[\s\-]?coast|wcd|drivetrain|drive\s*train"
                          r"|modules?|wheels?\b", re.I)
_WANTS_ELEC = re.compile(r"\belectrical|electronics|wiring|pdh|pdp|roborio|battery|breaker\b", re.I)


def forbidden_mechanisms(text: str) -> list[str]:
    """Mechanisms the prompt explicitly rules out. 'No X' beats a later 'X' mention."""
    low = text.lower()
    out: list[str] = []
    blanket = bool(_NO_MECHANISMS.search(low)) or bool(_CHASSIS_ONLY.search(low))
    for name in MECHANISMS:
        word = _MECH_WORD[name]
        denied = re.search(rf"\b(?:no|without|omit|exclude|remove|drop|skip)\s+"
                           rf"(?:an?\s+|the\s+|any\s+)?(?:{word})\b", low)
        asked = re.search(rf"\b(?:{word})\b", low)
        if denied or (blanket and not asked):
            out.append(name)
    return out


def _contradictions(text: str) -> list[str]:
    low = text.lower()
    problems: list[str] = []
    if re.search(r"\b(?:tank|west[\s\-]?coast|wcd)\b", low) and re.search(r"\bswerve\b", low):
        problems.append("The request asks for both a tank/west-coast drivetrain and swerve. "
                        "A robot has one drivetrain — say which.")
    # A contradiction is the SAME mechanism being both demanded and denied, e.g.
    # "no elevator, add a two-stage elevator". Classify each mention by whether a negation
    # immediately governs it — matching "with ... <mech>" across a clause boundary made
    # "with an intake but no climber" look self-contradictory when it is perfectly clear.
    _NEG_NEAR = re.compile(r"\b(?:no|without|remove|omit|exclude|drop|skip)\s+"
                           r"(?:an?\s+|the\s+|any\s+)?$")
    for name in MECHANISMS:
        denied = requested = False
        for mention in re.finditer(rf"\b(?:{_MECH_WORD[name]})\b", low):
            preceding = low[max(0, mention.start() - 24):mention.start()]
            if _NEG_NEAR.search(preceding):
                denied = True
            else:
                requested = True
        if denied and requested:
            problems.append(f"The request both excludes and asks for the {name}.")
    if _CHASSIS_ONLY.search(low):
        wanted = [n for n in MECHANISMS if re.search(rf"\b(?:{_MECH_WORD[n]})\b", low)]
        if wanted:
            problems.append("The request says chassis-only but also asks for "
                            + ", ".join(wanted) + ".")
    return problems


# ── vagueness ────────────────────────────────────────────────────────────────
_SIGNAL = (
    # drivetrain / structure
    r"swerve|tank|west[\s\-]?coast|wcd|mk\d|drivetrain|drive\s*base|chassis|chasis|frame"
    # mechanisms
    r"|intake|shoot\w*|launcher|elevator|elavator|elvator|lift|arm|manipulator|climb\w*"
    r"|hopper|spindexer|indexer|turret|wrist|claw|gripper|chute|feeder"
    # seasons
    r"|reefscape|rebuilt|crescendo|charged|rapid|offseason"
    # gamepieces and field vocabulary — "fuel cycler" is a real, specific request even
    # though it names no mechanism: it states the gamepiece and the archetype.
    r"|fuel|coral|algae|note|cone|cube|cargo|hatch|gamepiece"
    r"|cycler|cycl\w*|defen[cs]e|scorer|hang\w*|auto\w*|barge|processor|amp|speaker"
    r"|hub|tower|rung|cage|trap|stage|reef|station"
    # any number at all is a requirement
    r"|\d")


def _is_vague(text: str) -> bool:
    """A prompt with no concrete requirement in it at all.

    "robot" is the canonical case: it names no season, no drivetrain, no mechanism, no
    dimension. Building 258 parts from that is inventing a design, not compiling one.
    """
    stripped = text.strip().lower()
    if len(stripped) < 3:
        return True
    return not re.search(_SIGNAL, stripped)


CLARIFY_QUESTIONS = [
    {"id": "season", "question": "Which season or intended use is this for?",
     "examples": ["2026 REBUILT", "2025 REEFSCAPE", "off-season / practice"]},
    {"id": "scope", "question": "A full robot, or just a chassis/drivebase?",
     "examples": ["full robot", "chassis only", "drivebase only"]},
    {"id": "drivetrain", "question": "Which drivetrain?",
     "examples": ["MK4i swerve", "west-coast / tank", "no drivetrain yet"]},
    {"id": "mechanisms", "question": "Which mechanisms does it need?",
     "examples": ["intake + shooter", "elevator + arm", "none"]},
    {"id": "constraints", "question": "Any hard constraints?",
     "examples": ["27 x 27 in frame", "under 100 lb", "must fit under a 30 in bar"]},
]


# ── the gate ─────────────────────────────────────────────────────────────────
def gate(prompt: str, season_limits: dict[str, Any] | None = None,
         season_label: str = "") -> dict[str, Any]:
    """Decide whether this request can be compiled, and with what scope.

    `season_limits` is the season's own rule block (perimeter_in / max_height_in / weight_lb
    and their rule ids), so the refusal can quote the rule that was broken instead of a
    hardcoded number.
    """
    text = (prompt or "").strip()
    limits = season_limits or {}
    result: dict[str, Any] = {
        "version": GATE_VERSION,
        "state": "accepted",
        "questions": [],
        "violations": [],
        "contradictions": [],
        "forbidden_mechanisms": [],
        "scope": "robot",
        "include_drivetrain": True,
        "include_electrical": True,
        "measurements": measurements(text),
        "assumptions": [],
    }

    if not text:
        result["state"] = "clarification_required"
        result["questions"] = CLARIFY_QUESTIONS
        result["reason"] = "The prompt is empty."
        return result

    # 1. Impossible against the season's own limits — refuse, never clip.
    max_h = limits.get("max_height_in")
    max_rule = limits.get("max_height_rule", "the height rule")
    for m in result["measurements"]:
        if max_h and m["vertical"] and m["inches"] > float(max_h) + 1e-9:
            over = m["inches"] - float(max_h)
            result["violations"].append({
                "code": "DESIGN_RULE_VIOLATION",
                "rule": max_rule,
                "requested_in": m["inches"],
                "limit_in": float(max_h),
                "over_by_in": round(over, 2),
                "message": (f"{max_rule} caps total height at {float(max_h):g} in"
                            + (f" for {season_label}" if season_label else "")
                            + f", and the request asks for {m['raw']} "
                              f"({m['inches']:.1f} in) — over by {over:.1f} in. "
                              "Lower the requirement, or say which season this is for."),
            })
    # NOTE: frame perimeter is deliberately NOT rejected here. An over-perimeter frame has a
    # well-defined legal answer — shrink it to the season budget — and `frame_budget` already
    # does that and reports it in `constraints.frame_note`, which is visible to the user and
    # covered by its own tests. Height has no such answer: a 4 m elevator cannot be made into
    # a legal 4 m elevator, so that one is refused above.
    if result["violations"]:
        result["state"] = "rejected"
        result["reason"] = result["violations"][0]["message"]
        return result

    # 2. Contradictions — ask rather than silently pick a side.
    #
    # Only within the LATEST segment. A revision is *supposed* to disagree with the original
    # ("...robot with an elevator" + "Revision request: remove the elevator"); reading the
    # concatenated prompt as one statement made every removal look self-contradictory and
    # refused edits that had always worked.
    _low_all = text.lower()
    _latest = (text[_low_all.rindex("revision request:") + len("revision request:"):]
               if "revision request:" in _low_all else text)
    result["contradictions"] = _contradictions(_latest)
    if result["contradictions"]:
        result["state"] = "clarification_required"
        result["reason"] = result["contradictions"][0]
        result["questions"] = [{"id": "contradiction", "question": c, "examples": []}
                               for c in result["contradictions"]]
        return result

    # 3. Too vague to compile.
    if _is_vague(text):
        result["state"] = "clarification_required"
        result["reason"] = ("This needs a few details before it can be built — otherwise the "
                            "result is invented rather than designed.")
        result["questions"] = CLARIFY_QUESTIONS
        return result

    # 4. Scope. "only a 27 inch chassis" means chassis: no mechanisms, and no drivetrain or
    #    control system unless the prompt actually asks for them.
    low = text.lower()
    # Deliberately NOT per-mechanism: `robot_spec` resolves "no elevator" against revision
    # order. The gate only speaks to blanket scope statements, which that parser cannot see.
    result["forbidden_mechanisms"] = []
    if _CHASSIS_ONLY.search(low):
        result["scope"] = "chassis"
        result["include_drivetrain"] = bool(_WANTS_DRIVE.search(low))
        result["include_electrical"] = bool(_WANTS_ELEC.search(low))
        result["forbidden_mechanisms"] = list(MECHANISMS)
        if not result["include_drivetrain"]:
            result["assumptions"].append(
                "Read as a bare chassis: no drivetrain, no control system, no mechanisms. "
                "Say 'chassis with swerve' if the drivetrain should be included.")
    elif _NO_MECHANISMS.search(low):
        # "nothing else" closes the list — it does NOT delete what the same sentence asked
        # for. "defence bot with an intake, nothing else" keeps the intake and adds no more.
        result["forbidden_mechanisms"] = forbidden_mechanisms(text)
        result["assumptions"].append(
            "Read as a closed list: only the mechanisms named here are built.")

    return result


def design_title(prompt: str, gate_result: dict[str, Any], fallback: str) -> str:
    """A name that reflects what was actually asked for.

    "4 meter elevator robot" was being titled "SDS MK4i Intake robot" — a name assembled from
    parts the request never mentioned. Prefer the request's own words.
    """
    text = (prompt or "").strip()
    if not text:
        return fallback
    if gate_result.get("scope") == "chassis":
        dims = [m for m in gate_result.get("measurements", []) if m.get("planar")]
        size = f"{dims[0]['value']:g} in " if dims else ""
        return f"{size}chassis".strip().capitalize()
    words = re.sub(r"\s+", " ", text)[:60].strip(" .,")
    return words[:1].upper() + words[1:] if words else fallback
