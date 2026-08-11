"""What is the user actually asking for, and what does it have to be made of?

Kale Forge began as a robot compiler: every prompt produced a whole FRC robot, and the
season, the drivetrain and the control system were assumed rather than asked for. That is
the right answer for "design a 2026 REBUILT robot" and the wrong answer for "design a
bearing block for a 1/2 in hex shaft", which needs no season, no drivetrain and no battery.

This module is the stage that runs before any geometry and decides which of those two
questions was asked. It answers with:

  * a DESIGN TYPE — robot, subsystem, mechanical_part, mechanical_assembly, pcb,
    electronics, enclosure or other;
  * an ENGINEERING SPEC — the structured requirement the generator downstream compiles.

Both are deterministic. The site's geometry path runs with `use_model=False` (see
`api/studio.py`), so a classifier that needed a model round-trip would not run at all where
it matters; a model hint can override the classification when one is available, but the
default has to work on its own.

Everything here is stdlib-only: this module is imported by the Vercel function, which ships
without a requirements.txt (see the site-bundle parity test).

Provenance is first class. Every value the spec carries is labelled with where it came
from, because the difference between "you told me the bore is 1.125 in" and "I assumed a
bore of 1.125 in" is the difference between a part a team can cut and a part that quietly
does not fit.
"""
from __future__ import annotations

import re
from typing import Any

INTENT_VERSION = "kale-intent-1.0"

# ── design types ─────────────────────────────────────────────────────────────
# `robot` and `subsystem` stay FRC-shaped because that is what they are; everything else is
# season-agnostic. `other` is the honest answer when nothing matches well — it routes to the
# mechanical engine with low confidence rather than guessing at a robot.
DESIGN_TYPES = ("robot", "subsystem", "mechanical_part", "mechanical_assembly",
                "pcb", "electronics", "enclosure", "other")

# What the studio's type selector offers. AUTO means "classify it for me" and is the default.
SELECTABLE_TYPES = ("auto", "robot", "subsystem", "mechanical_part", "pcb")

TYPE_LABELS = {
    "auto": "Auto", "robot": "Robot", "subsystem": "Subsystem",
    "mechanical_part": "Part", "mechanical_assembly": "Assembly", "pcb": "PCB",
    "electronics": "Electronics", "enclosure": "Enclosure", "other": "Other",
}

# ── provenance ───────────────────────────────────────────────────────────────
# Where a value came from, in descending order of how much a user should trust it.
VERIFIED = "VERIFIED"            # from the parts catalog, a real vendor number
USER_PROVIDED = "USER_PROVIDED"  # stated in the prompt
CALCULATED = "CALCULATED"        # derived in code from other known values
INFERRED = "INFERRED"            # implied by the request without being stated
ASSUMED = "ASSUMED"              # a default this compiler chose; the user should check it
UNRESOLVED = "UNRESOLVED"        # needed, not known, and not safe to invent

PROVENANCE_ORDER = (VERIFIED, USER_PROVIDED, CALCULATED, INFERRED, ASSUMED, UNRESOLVED)


def value(v: Any, source: str, *, unit: str = "", note: str = "") -> dict[str, Any]:
    """One spec value with its provenance. The unit rides along because a bare number in a
    mixed inch/millimetre design is how a 28.6 mm pocket becomes a 28.6 in one."""
    out: dict[str, Any] = {"value": v, "source": source}
    if unit:
        out["unit"] = unit
    if note:
        out["note"] = note
    return out


# ── unit parsing ─────────────────────────────────────────────────────────────
# Shared with design_gate's vocabulary deliberately: teams write both systems, often in one
# sentence ("1/2 inch hex shaft, 220 mm long").
_TO_IN = {
    "mm": 1 / 25.4, "millimeter": 1 / 25.4, "millimetre": 1 / 25.4,
    "cm": 10 / 25.4, "centimeter": 10 / 25.4, "centimetre": 10 / 25.4,
    "m": 1000 / 25.4, "meter": 1000 / 25.4, "metre": 1000 / 25.4,
    "in": 1.0, "inch": 1.0, "inches": 1.0, '"': 1.0, "”": 1.0,
    "thou": 0.001, "mil": 0.001,
}
_UNIT_RE = (r"(?:mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|met(?:er|re)s?|m"
            r"|inch(?:e?s)?|in|thou|mils?|[\"”])")

# "1/2", "1-1/8", "0.5", "1.125"
_NUMBER = r"(?:\d+\s*-\s*\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+(?:\.\d+)?)"
_DIM_RE = re.compile(rf"({_NUMBER})\s*({_UNIT_RE})(?![a-z])", re.I)


def parse_number(text: str) -> float | None:
    """'1/2' -> 0.5, '1-1/8' -> 1.125, '0.5' -> 0.5. Fractions because that is how imperial
    hardware is written and quietly dropping them loses half the dimensions in a prompt."""
    text = text.strip()
    whole = re.fullmatch(r"(\d+)\s*-\s*(\d+)\s*/\s*(\d+)", text)
    if whole:
        a, b, c = (float(g) for g in whole.groups())
        return a + b / c if c else None
    frac = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if frac:
        a, b = (float(g) for g in frac.groups())
        return a / b if b else None
    try:
        return float(text)
    except ValueError:
        return None


def dimensions_in(text: str) -> list[dict[str, Any]]:
    """Every dimension in the text, converted to inches, with the span it came from.

    The span matters: the caller matches a dimension to what it dimensions by looking at the
    words around it, and without the offset "1/2 inch hex shaft, 220 mm long" cannot be told
    apart from "220 mm hex shaft, 1/2 inch long".
    """
    out: list[dict[str, Any]] = []
    for m in _DIM_RE.finditer(text):
        raw, unit = m.group(1), m.group(2).lower().rstrip(".")
        number = parse_number(raw)
        factor = _TO_IN.get(unit)
        if number is None or factor is None:
            continue
        out.append({"inches": round(number * factor, 5), "raw": m.group(0).strip(),
                    "unit": unit, "start": m.start(), "end": m.end(),
                    "metric": unit in ("mm", "cm", "m", "millimeter", "millimetre",
                                       "centimeter", "centimetre", "meter", "metre")})
    return out


def prefers_metric(text: str) -> bool:
    """Which unit system to present in. A prompt written in millimetres should come back in
    millimetres; answering a metric request in decimal inches is technically correct and
    practically useless."""
    dims = dimensions_in(text)
    if not dims:
        return False
    return sum(1 for d in dims if d["metric"]) > len(dims) / 2


# ── classification ───────────────────────────────────────────────────────────
# Scored keyword evidence rather than a decision tree, because prompts mix vocabularies:
# "design a bearing block for an FRC elevator" is a PART that mentions a SUBSYSTEM and a
# ROBOT programme, and only weighing the evidence gets that right.
#
# Each entry is (compiled pattern, points, why). `why` is surfaced to the user so a
# classification can be argued with instead of just accepted.
def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


_SIGNALS: dict[str, list[tuple[re.Pattern[str], float, str]]] = {
    "robot": [
        (_p(r"\b(?:complete|whole|entire|full|finished)\s+(?:frc\s+)?robot\b"), 6.0, "asks for a complete robot"),
        (_p(r"\brobot\b"), 2.2, "says robot"),
        (_p(r"\b(?:drivebase|drive\s*base|chassis|drivetrain)\b"), 1.6, "names a drivebase"),
        (_p(r"\b(?:swerve|west\s*coast|tank\s*drive|mecanum)\b"), 1.5, "names a drive architecture"),
        (_p(r"\b(?:20\d\d)\s*(?:frc|game|season)?\b.*\b(?:robot|bot)\b"), 1.4, "a season and a robot"),
        (_p(r"\b(?:reefscape|rebuilt|crescendo|charged\s*up|rapid\s*react)\b"), 1.2, "names a game"),
        (_p(r"\bdefen[cs]e\s*bot\b|\beverybot\b|\bkitbot\b|\bkit\s*bot\b"), 3.0,
         "a whole-robot archetype"),
        # "bot" is what teams actually write, and it means the whole machine. Without it,
        # "an algae processor bot" and "a defensive bot" scored zero and were routed to the
        # part compiler, which cannot make either.
        (_p(r"\bbots?\b"), 2.0, "says bot"),
        # A game-piece role is a robot's job description: a coral scorer, an algae cycler and
        # a note shooter-bot are robots, not parts. These are the shortest prompts in the
        # store and the ones most likely to break.
        (_p(r"\b(?:coral|algae|cube|cone|note|fuel|ball|game\s*piece)\s+"
            r"(?:scorer|scoring|cycler|handler|processor|bot|machine)\b"), 3.0,
         "names a game-piece role, which is a robot's job"),
        (_p(r"\b(?:scorer|cycler)\b"), 1.8, "names a scoring role"),
    ],
    "subsystem": [
        (_p(r"\b(?:elevator|telescop\w+|cascade|climber|climb|hanger)\b"), 2.6, "names a lift or climb subsystem"),
        (_p(r"\b(?:intake|hopper|indexer|spindexer|feeder|serializer)\b"), 2.6, "names a gamepiece subsystem"),
        (_p(r"\b(?:shooter|flywheel|turret|launcher|hood)\b"), 2.6, "names a shooter subsystem"),
        (_p(r"\b(?:arm|wrist|manipulator|end\s*effector|gripper|claw)\b"), 2.2, "names a manipulator"),
        (_p(r"\bsubsystem\b"), 3.0, "says subsystem"),
        (_p(r"\bfor (?:an?|my|the) (?:frc\s+)?robot\b"), 1.2, "scoped to a robot"),
    ],
    "mechanical_part": [
        (_p(r"\bbearing\s*(?:block|housing|holder|carrier|mount)\b"), 6.0, "names a bearing block"),
        # A bare "bearing" is still a part. Without this, "build me a bearing" matched
        # nothing at all and fell through to `other`.
        (_p(r"\bbearings?\b"), 2.5, "names a bearing"),
        (_p(r"\b(?:spacer|standoff|shim|washer|collar)\b"), 4.5, "names a spacer-class part"),
        (_p(r"\b(?:hex\s*)?shaft\b"), 3.6, "names a shaft"),
        (_p(r"\b(?:gusset|bracket|mount(?:ing)?\s*(?:plate|tab|bracket))\b"), 4.0, "names a bracket or gusset"),
        # A qualified plate is a plate, not the thing it is a plate FOR. "Gearbox plate"
        # otherwise lost to the assembly signal in "gearbox" and came back as a gearbox.
        (_p(r"\b(?:gearbox|motor|adapter|mounting|face|side|end)\s*plates?\b"), 5.5,
         "names a plate for something, which is still a plate"),
        (_p(r"\b(?:plate|panel)\b"), 2.4, "names a plate"),
        (_p(r"\b(?:pulley|sprocket|hub|roller|wheel)\b"), 3.0, "names a rotating part"),
        (_p(r"\badapter\b"), 3.0, "names an adapter"),
        (_p(r"\b(?:a|one|single)\s+(?:custom\s+)?(?:part|component|piece)\b"), 3.0, "asks for one part"),
        (_p(r"\bretaining[- ]ring|snap[- ]ring|circlip\b"), 2.0, "names a groove feature"),
    ],
    "mechanical_assembly": [
        (_p(r"\bgearbox\b"), 4.0, "names a gearbox"),
        (_p(r"\b(?:assembly|assemblies)\b"), 3.6, "says assembly"),
        (_p(r"\b(?:two|three|dual|double|triple)[- ]stage\b"), 2.0, "names a staged mechanism"),
        (_p(r"\breduction\b|\bgear\s*train\b"), 2.0, "names a gear train"),
        (_p(r"\bmodule\b"), 1.4, "names a module"),
    ],
    "pcb": [
        (_p(r"\bpcb\b|\bprinted\s+circuit\b"), 6.0, "says PCB"),
        (_p(r"\b(?:breakout|carrier)\s*board\b|\bboard\b"), 2.4, "names a board"),
        (_p(r"\b\d\s*-?\s*layer\b"), 3.0, "specifies a layer count"),
        (_p(r"\bschematic\b|\bnetlist\b|\bkicad\b"), 3.0, "names electronics source"),
        (_p(r"\b(?:can|i2c|spi|uart|rs-?485)\b\s*(?:bus|transceiver|breakout|board|node)?"), 1.6, "names a bus"),
        (_p(r"\b(?:regulator|buck|ldo|smps)\b"), 2.4, "names a regulator"),
        (_p(r"\b\d+(?:\.\d+)?\s*v\b.*\b\d+(?:\.\d+)?\s*v\b"), 2.0, "names input and output rails"),
        (_p(r"\bpower\s+distribution\b"), 1.8, "names power distribution"),
    ],
    "electronics": [
        (_p(r"\b(?:wiring|harness|loom)\b"), 3.0, "names a harness"),
        (_p(r"\bcircuit\b(?!\s*board)"), 2.0, "names a circuit"),
    ],
    "enclosure": [
        (_p(r"\b(?:enclosure|housing\s+box|case|chassis\s+box|electronics\s+box)\b"), 4.0, "names an enclosure"),
        (_p(r"\b(?:lid|cover)\b"), 1.5, "names a cover"),
    ],
}

# Phrases that mean "one of these, on its own" and should pull a part request away from the
# subsystem or robot that it is FOR. "a bearing block for an elevator" is a bearing block.
_MECHANISM_NOUN = _p(r"\b(?:elevator|intake|shooter|climber|turret|arm|wrist|hopper|indexer"
                     r"|manipulator|gripper|claw|flywheel|hood|feeder|hanger)\b")

_SCOPE_NARROWERS = (
    _p(r"\bfor\s+(?:an?|the|my)\s+"),
    _p(r"\bthat\s+(?:mounts|bolts|attaches|fits)\b"),
    _p(r"\bto\s+(?:mount|hold|carry|support)\b"),
)


def classify(prompt: str, requested: str = "auto") -> dict[str, Any]:
    """Decide what kind of artifact this request is for.

    `requested` is the studio's type selector. Anything but "auto" is the user telling us
    directly, and is honoured — with the evidence still reported, so a mismatch between what
    was picked and what was written is visible rather than silent.
    """
    text = " " + (prompt or "").strip().lower() + " "
    scores: dict[str, float] = {t: 0.0 for t in DESIGN_TYPES}
    reasons: dict[str, list[str]] = {t: [] for t in DESIGN_TYPES}

    for design_type, signals in _SIGNALS.items():
        for pattern, points, why in signals:
            if pattern.search(text):
                scores[design_type] += points
                reasons[design_type].append(why)

    # A request scoped to something else ("...for an FRC robot") names that thing without
    # asking for it. Discount the container so the contents win.
    if any(p.search(text) for p in _SCOPE_NARROWERS):
        for container in ("robot", "subsystem"):
            if scores[container]:
                scores[container] *= 0.45
                reasons[container].append("scoped as a component of this, not the thing asked for")

    # Several mechanisms in one sentence is a robot, not a subsystem. "a robot with an
    # elevator, intake, shooter and climber" lists four subsystems and asks for none of them:
    # the subsystem signals stacked and outscored the word "robot" that governs them.
    mechanisms = len({m.group(0) for m in _MECHANISM_NOUN.finditer(text)})
    if mechanisms >= 2 and scores["robot"] > 0:
        scores["robot"] += 2.0 * (mechanisms - 1)
        reasons["robot"].append(f"names {mechanisms} subsystems, which is a robot")

    # A part named alongside an assembly word is usually the assembly ("gearbox plate" is a
    # plate; "two-Kraken gearbox" is an assembly). The plate signal is weaker, so this only
    # matters when the assembly word is doing the work.
    if scores["mechanical_assembly"] >= 4.0 and scores["mechanical_part"] < 6.0:
        scores["mechanical_part"] *= 0.7

    ranked = sorted(DESIGN_TYPES, key=lambda t: (-scores[t], DESIGN_TYPES.index(t)))
    best, runner = ranked[0], ranked[1]
    top = scores[best]

    if requested and requested != "auto" and requested in DESIGN_TYPES:
        chosen, how = requested, "chosen by the user"
        confidence = 1.0
    elif top <= 0.0:
        # Nothing matched. `other` routes to the mechanical engine at low confidence, which
        # is a far better failure than assuming a robot and building 400 parts.
        chosen, how, confidence = "other", "nothing in the request named a kind of artifact", 0.0
    else:
        chosen, how = best, "; ".join(dict.fromkeys(reasons[best]))
        margin = top - scores[runner]
        confidence = max(0.0, min(1.0, 0.45 + 0.35 * min(margin / 3.0, 1.0)
                                  + 0.2 * min(top / 8.0, 1.0)))

    return {
        "version": INTENT_VERSION,
        "design_type": chosen,
        "requested": requested or "auto",
        "confidence": round(confidence, 2),
        "why": how,
        "scores": {t: round(scores[t], 2) for t in DESIGN_TYPES if scores[t]},
        "alternatives": [t for t in ranked[1:3] if scores[t] > 0],
        "overridden": bool(requested and requested != "auto" and requested != best
                           and scores.get(best, 0) > 0),
    }


# ── season relevance ─────────────────────────────────────────────────────────
_SEASON_WORDS = _p(r"\b(?:20\d\d|reefscape|rebuilt|crescendo|charged\s*up|rapid\s*react"
                   r"|season|game\s*manual|field|match)\b")
# Subsystems whose geometry genuinely depends on the game piece and the field.
_GAME_DEPENDENT = _p(r"\b(?:intake|hopper|indexer|spindexer|shooter|flywheel|launcher"
                     r"|climb\w*|hang\w*|end\s*effector|gripper)\b")


def season_is_relevant(prompt: str, design_type: str) -> dict[str, Any]:
    """Should the studio ask for, or apply, an FRC game season?

    A complete robot is measured against a season's rules, so yes. A subsystem that handles
    the game piece or the climb depends on the field, so yes. A bearing housing does not,
    and asking a user to pick REEFSCAPE before they can have one is the single clearest sign
    that this tool thought it only made robots.
    """
    text = " " + (prompt or "").lower() + " "
    if design_type == "robot":
        return {"relevant": True, "why": "a complete robot is inspected against a season's rules"}
    explicit = bool(_SEASON_WORDS.search(text))
    if explicit:
        return {"relevant": True, "why": "the request names a season or the field"}
    if design_type == "subsystem" and _GAME_DEPENDENT.search(text):
        return {"relevant": True,
                "why": "this subsystem handles the game piece, so its sizing depends on the game"}
    return {"relevant": False,
            "why": "nothing about this artifact depends on a particular FRC game"}


# ── engineering specification ────────────────────────────────────────────────
class EngineeringSpec:
    """The structured requirement a generator compiles, with provenance on every value.

    Four buckets, kept apart on purpose so the UI can show them apart:

        requirements  what the user actually said
        inferred      what their words imply without saying
        assumptions   what this compiler chose because nothing said otherwise
        calculated    what code derived from the above

    `unresolved` is the fifth state and the important one: a value that is needed, is not
    known, and must not be invented. A critical unresolved value stops the build and asks;
    a non-critical one ships as a labelled assumption the user can edit.
    """

    def __init__(self, design_type: str, prompt: str) -> None:
        self.design_type = design_type
        self.prompt = prompt
        self.part_type: str = ""
        self.units: str = "mm" if prefers_metric(prompt) else "inch"
        self.requirements: dict[str, dict[str, Any]] = {}
        self.inferred: dict[str, dict[str, Any]] = {}
        self.assumptions: dict[str, dict[str, Any]] = {}
        self.calculated: dict[str, dict[str, Any]] = {}
        self.unresolved: list[dict[str, Any]] = []
        self.interfaces: list[dict[str, Any]] = []
        self.hardware: list[dict[str, Any]] = []
        self.notes: list[str] = []

    # Each setter records into its own bucket so provenance cannot be lost by accident.
    def require(self, key: str, v: Any, unit: str = "", note: str = "") -> None:
        self.requirements[key] = value(v, USER_PROVIDED, unit=unit, note=note)

    def infer(self, key: str, v: Any, unit: str = "", note: str = "") -> None:
        self.inferred[key] = value(v, INFERRED, unit=unit, note=note)

    def assume(self, key: str, v: Any, unit: str = "", note: str = "") -> None:
        self.assumptions[key] = value(v, ASSUMED, unit=unit, note=note)

    def calc(self, key: str, v: Any, unit: str = "", note: str = "") -> None:
        self.calculated[key] = value(v, CALCULATED, unit=unit, note=note)

    def verified(self, key: str, v: Any, unit: str = "", note: str = "") -> None:
        self.requirements[key] = value(v, VERIFIED, unit=unit, note=note)

    def unknown(self, key: str, why: str, *, critical: bool = False) -> None:
        self.unresolved.append({"key": key, "why": why, "critical": bool(critical),
                                "source": UNRESOLVED})

    def get(self, key: str, default: Any = None) -> Any:
        """The resolved value of a key, whichever bucket holds it. Ordered by trust, so a
        stated dimension always beats an assumed one for the same key."""
        for bucket in (self.requirements, self.calculated, self.inferred, self.assumptions):
            if key in bucket:
                return bucket[key]["value"]
        return default

    def source_of(self, key: str) -> str:
        for bucket in (self.requirements, self.calculated, self.inferred, self.assumptions):
            if key in bucket:
                return str(bucket[key]["source"])
        return UNRESOLVED

    @property
    def critical_gaps(self) -> list[dict[str, Any]]:
        return [u for u in self.unresolved if u["critical"]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": INTENT_VERSION,
            "designType": self.design_type,
            "partType": self.part_type,
            "units": self.units,
            "requirements": self.requirements,
            "inferred": self.inferred,
            "assumptions": self.assumptions,
            "calculated": self.calculated,
            "unresolved": self.unresolved,
            "interfaces": self.interfaces,
            "hardware": self.hardware,
            "notes": self.notes,
        }
