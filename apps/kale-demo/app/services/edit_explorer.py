"""For an open-ended request, build several real designs and keep the one that measures best.

`edit_planner` turns "make the intake better" into concrete parameters, but it commits to one
interpretation: widen it, add a roller, push the compression up. That is a reasonable first
guess and it is still only a guess. A wider two-roller intake and a three-roller compliant
intake are different machines with different failure modes, and which one is better is a
question with a measurable answer.

So for the requests that are genuinely open — "improve", "optimise", "redesign", said without
a dimension — this builds each candidate for real and scores it. Not a heuristic ranking of
descriptions: each option goes through the same compiler, the same geometry validation and the
same season rule check as anything else, and an option that cannot be built loses to one that
can. The user does not get asked to approve each trial; they get the winner and the table.

The cost is honest and bounded: one extra full build per option, which is why exploration is
reserved for aggressive and redesign strength rather than run on every edit.
"""
from __future__ import annotations

from typing import Any, Callable

# Only these are open enough to be worth exploring. A request naming a dimension has already
# chosen; running alternatives against it would be second-guessing a decision the user made.
EXPLORABLE_INTENTS = frozenset({"IMPROVE", "OPTIMIZE", "REDESIGN", "INCREASE_RELIABILITY",
                                "INCREASE_SPEED"})
EXPLORABLE_STRENGTHS = frozenset({"aggressive", "redesign"})

# How many candidates to build. Three is the point where a second architecture and a stretch of
# the first are both represented; past that each option costs a full compile for a shrinking
# chance of winning.
MAX_OPTIONS = 3


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── the candidate architectures ──────────────────────────────────────────────
# Each option is a genuinely different machine, not a jitter of the same one. The clauses are
# phrased the way `edit_planner` learned to phrase them — "26 in intake width", never "26 in
# wide intake", which the frame selector would steal.
def _intake_options(spec: dict[str, Any]) -> list[dict[str, Any]]:
    intake = spec.get("intake") or {}
    if not intake.get("included"):
        return []
    width = _f(intake.get("width_in"), 22.0)
    frame_w = _f((spec.get("frame") or {}).get("width_in"), 27.0)
    wide = min(round(width * 1.22, 1), round(frame_w - 1.0, 1))
    return [
        {"name": "wide two-roller",
         "rationale": "capture width first: the cheapest way to stop missing a piece is to "
                      "make the mouth wider than the driver's aim error",
         "clauses": [f"{wide:g} in intake width", "over-bumper intake"]},
        {"name": "three-roller compliant",
         "rationale": "throughput first: a third roller separates acquisition from handover so "
                      "neither stage has to do both",
         "clauses": [f"{min(wide, round(width * 1.12, 1)):g} in intake width", "three rollers",
                     "compliant wheel intake"]},
        {"name": "large-roller over-bumper",
         "rationale": "reach first: a bigger roller closes the gap to the carpet and tolerates "
                      "a piece lying flat against the bumper",
         "clauses": [f"{wide:g} in intake width", "3 in rollers", "over-bumper intake"]},
    ]


def _shooter_options(spec: dict[str, Any]) -> list[dict[str, Any]]:
    shooter = spec.get("shooter") or {}
    if not shooter.get("included"):
        return []
    dia = _f(shooter.get("flywheel_diameter_in"), 5.0)
    return [
        {"name": "high-energy flywheels",
         "rationale": "a bigger wheel carries more energy through the shot, so exit velocity "
                      "sags less on a fast cycle",
         "clauses": [f"{round(min(dia * 1.25, 8.0), 1):g} in flywheels"]},
        {"name": "fast-recovery flywheels",
         "rationale": "a smaller, lighter wheel spins back up sooner, which is the limit when "
                      "shots come close together",
         "clauses": [f"{round(max(dia * 0.8, 2.5), 1):g} in flywheels"]},
        {"name": "turreted adjustable hood",
         "rationale": "aim instead of power: a turret and a moving hood let the robot shoot "
                      "from where it already is",
         "clauses": ["turret", "adjustable hood"]},
    ]


def _elevator_options(spec: dict[str, Any]) -> list[dict[str, Any]]:
    elevator = spec.get("elevator") or {}
    if not elevator.get("included"):
        return []
    stages = int(_f(elevator.get("stages"), 2))
    out = []
    if stages > 1:
        out.append({"name": "fewer stages",
                    "rationale": "every stage is a handoff and a place to rack; a shorter "
                                 "ladder is stiffer and faster if the travel still reaches",
                    "clauses": [f"{stages - 1} stage elevator"]})
    out.append({"name": "more stages",
                "rationale": "more stages keep the retracted height down, which buys room "
                             "under the height rule for everything else",
                "clauses": [f"{stages + 1} stage elevator"]})
    return out


_OPTIONS: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
    "intake": _intake_options,
    "shooter": _shooter_options,
    "elevator": _elevator_options,
}


def candidates(spec: dict[str, Any], subsystems: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in subsystems:
        builder = _OPTIONS.get(name)
        if builder:
            for option in builder(spec):
                option["subsystem"] = name
                out.append(option)
    return out[:MAX_OPTIONS]


# ── scoring ──────────────────────────────────────────────────────────────────
# Buildability dominates on purpose. A design that scores well on capture width and cannot be
# assembled is not a better intake, and letting a performance number outweigh a geometry
# failure is how a tool ends up recommending something nobody can make.
_INVALID_PENALTY = 12.0
_RULE_PENALTY = 40.0
_FLOAT_PENALTY = 25.0


def score(spec: dict[str, Any], subsystem: str, intent: str) -> dict[str, Any]:
    """Measure one candidate.

    `performance` is how well it serves the objective; `validity` is whether it can be built.
    They are reported separately as well as summed, because an iteration needs to tell "this
    improved the intake but broke the geometry" from "this did nothing" — the first wants a
    smaller step in the same direction, the second wants a bigger one, and a single total
    cannot distinguish them.
    """
    metrics: dict[str, float] = {}
    validity = 0.0

    geometry = spec.get("geometry_status") or {}
    blocking = _f(geometry.get("blocking"))
    metrics["geometry_blocking"] = blocking
    validity -= blocking * _INVALID_PENALTY
    if not ((spec.get("cad") or {}).get("integrity") or {}).get("ok", True):
        validity -= _FLOAT_PENALTY
        metrics["floating_bodies"] = 1.0
    if (spec.get("rule_report") or {}).get("export_blocked"):
        validity -= _RULE_PENALTY
        metrics["rule_blocked"] = 1.0
    total = validity

    if subsystem == "intake":
        intake = spec.get("intake") or {}
        width = _f(intake.get("width_in"))
        rollers = _f(intake.get("roller_count"))
        speed = _f(intake.get("roller_surface_speed_fps"))
        metrics.update({"capture_width_in": width, "rollers": rollers,
                        "surface_speed_fps": speed})
        # Width is the dominant term for acquisition; a second/third roller is worth real
        # points but not as much as the mouth being wide enough in the first place.
        total += width * 1.0 + rollers * 2.0 + min(speed, 25.0) * 0.2
    elif subsystem == "shooter":
        shooter = spec.get("shooter") or {}
        exit_fps = _f(shooter.get("exit_velocity_fps"))
        spinup = _f(shooter.get("spinup_time_s"), 1.0)
        metrics.update({"exit_velocity_fps": exit_fps, "spinup_time_s": spinup,
                        "turreted": 1.0 if shooter.get("turreted") else 0.0})
        if intent == "INCREASE_SPEED":
            # "Faster" on a shooter means cycles, not muzzle velocity: the wheel has to be back
            # up to speed before the next piece arrives. Recovery therefore dominates, and
            # exit velocity is kept only as a floor so the option cannot win by being feeble.
            total += exit_fps * 0.15 + (1.0 / max(spinup, 0.05)) * 30.0
        else:
            total += exit_fps * 1.0 + (1.0 / max(spinup, 0.05)) * 4.0
        if shooter.get("turreted"):
            total += 6.0
    elif subsystem == "elevator":
        elevator = spec.get("elevator") or {}
        stages = _f(elevator.get("stages"), 1)
        height = _f(elevator.get("max_height_in"))
        metrics.update({"stages": stages, "max_height_in": height})
        # Reach is the product; stages are the cost of getting it.
        total += height * 0.6 - stages * 3.0

    mass = _f((spec.get("mass_estimate") or {}).get("total_lb"))
    if mass:
        metrics["mass_lb"] = mass
        total -= mass * 0.15
    return {"total": round(total, 2), "performance": round(total - validity, 2),
            "validity": round(validity, 2), "metrics": metrics}


# ── the search ───────────────────────────────────────────────────────────────
def explore(base_prompt: str, plan: dict[str, Any], *, season: str,
            build: Callable[[str], dict[str, Any]],
            spec: dict[str, Any]) -> dict[str, Any] | None:
    """Build every candidate, score it, and return the winner with the comparison table.

    Returns None when the request is not open-ended enough to be worth the extra builds, or
    when no candidate could be built at all — in which case the caller keeps the single
    interpretation `edit_planner` already produced.
    """
    if plan.get("intent") not in EXPLORABLE_INTENTS:
        return None
    if plan.get("strength") not in EXPLORABLE_STRENGTHS:
        return None
    options = candidates(spec, plan.get("affected_subsystems") or [])
    if len(options) < 2:
        return None                       # nothing to compare against

    joiner = "\n\nRevision request: "
    results: list[dict[str, Any]] = []
    for option in options:
        text = ", ".join(option["clauses"])
        try:
            built = build(base_prompt + joiner + text)
        except Exception:
            continue                      # an option that will not compile simply loses
        assessment = score(built, option["subsystem"], plan.get("intent") or "IMPROVE")
        results.append({"name": option["name"], "rationale": option["rationale"],
                        "subsystem": option["subsystem"], "clauses": option["clauses"],
                        "text": text, "score": assessment["total"],
                        "metrics": assessment["metrics"]})
    if not results:
        return None
    results.sort(key=lambda r: -r["score"])
    winner = results[0]
    return {
        "explored": len(results),
        "chosen": winner["name"],
        "resolved_text": winner["text"],
        "why": winner["rationale"],
        "options": [{k: v for k, v in r.items() if k != "clauses"} for r in results],
        "note": ("Every option was compiled, validated and rule-checked for real; the winner "
                 "is the one that measured best, not the one that read best. Buildability "
                 "outranks performance — an option that cannot be assembled loses to one "
                 "that can."),
    }
