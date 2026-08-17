"""Keep working the edit until the objective is actually met, or say what stopped it.

One pass through the planner produces a technically valid robot, and a technically valid robot
is not the same thing as one that satisfies what was asked. Two failures survive a single pass:

  * **The change was too timid.** "Make the intake much better" widens it by an inch, the
    result compiles, and the loop ends having improved almost nothing. Nothing measured
    whether the objective moved.
  * **The change broke something.** A bigger shooter collides with the turret motor, and the
    only options a single pass has are to ship the collision or throw the edit away. Both are
    wrong: the right answer is to try the same intent at a size that fits.

So each attempt is measured on two separate numbers — how well it serves the objective, and
whether it can be built — and the next attempt is chosen from WHICH of them failed. Too timid
means push harder. Broke the geometry means the same direction, smaller. That is the whole
loop, and it is deterministic: no attempt depends on anything but the ones before it.
"""
from __future__ import annotations

from typing import Any, Callable

from app.services.edit_explorer import score

# Each attempt is a build. Three is enough for push-harder, back-off, and one more of
# whichever the evidence asked for; past that the returns do not pay for the seconds.
MAX_ATTEMPTS = 3

# How much of the objective metric has to move before an edit counts as having done something.
# Below this the design changed but the objective did not, which is the "5% nudge" failure the
# whole edit rework exists to stop.
MEANINGFUL_GAIN = 0.02

# Ordered ladder the iterator escalates along when an attempt is too timid.
_LADDER: tuple[str, ...] = ("minimal", "moderate", "aggressive", "redesign")


def _next_strength(current: str) -> str | None:
    try:
        index = _LADDER.index(current)
    except ValueError:
        return "aggressive"
    return _LADDER[index + 1] if index + 1 < len(_LADDER) else None


# Where a clause's number came from, so backing off knows what it is backing off TOWARD.
_FLOORS: tuple[tuple[str, str, str], ...] = (
    ("intake width", "intake", "width_in"),
    ("rollers", "intake", "roller_diameter_in"),
    ("flywheels", "shooter", "flywheel_diameter_in"),
)


def _weaker(clauses: list[str], factor: float, before: dict[str, Any]) -> list[str]:
    """The same changes, asked for less hard — but never past where the design started.

    Only numbers move: the architecture the planner chose stays chosen, because an attempt
    that collided is evidence about SIZE, not about whether a third roller was the right idea.

    The floor is the point. Scaling freely toward zero let the ladder overshoot the ORIGINAL
    dimension, so "make the intake much better" backed 26 in down past the 23 in it started
    from and reported success on a narrower intake. Backing off means returning toward the
    design that worked, not shrinking indefinitely.
    """
    import re

    out: list[str] = []
    for clause in clauses:
        floor = 0.0
        for needle, section, key in _FLOORS:
            if needle in clause.lower():
                floor = float((before.get(section) or {}).get(key) or 0.0)
                break

        def shrink(match: "re.Match[str]") -> str:
            value = float(match.group(0)) * factor
            return f"{round(max(value, floor), 1):g}"

        out.append(re.sub(r"\d+(?:\.\d+)?", shrink, clause, count=1))
    return out


def iterate(base_prompt: str, plan: dict[str, Any], *, season: str,
            build: Callable[[str], dict[str, Any]],
            before: dict[str, Any]) -> dict[str, Any] | None:
    """Try, measure, diagnose, try again. Returns the best attempt and the trail.

    Returns None when there is nothing to iterate on — no target subsystem, or an explicit
    dimension the user asked for, which is an instruction rather than an objective.
    """
    from app.services.edit_planner import _amplify  # noqa: PLC0415

    subsystems = [s for s in (plan.get("affected_subsystems") or [])]
    if not subsystems or not plan.get("changes"):
        return None                       # nothing vague enough to iterate on
    subsystem = subsystems[0]
    intent = plan.get("intent") or "IMPROVE"
    strength = plan.get("strength") or "moderate"

    baseline = score(before, subsystem, intent)
    joiner = "\n\nRevision request: "
    attempts: list[dict[str, Any]] = []
    clauses = list(plan.get("changes") or [])
    seen: set[str] = set()

    for step in range(MAX_ATTEMPTS):
        text = ", ".join(clauses)
        if not text or text in seen:
            break
        seen.add(text)
        try:
            built = build(base_prompt + joiner + text)
        except Exception:
            break
        result = score(built, subsystem, intent)
        gain = result["performance"] - baseline["performance"]
        # Relative to the baseline's own magnitude, so "meaningful" means the same thing for a
        # 20 in intake as for a 4 in flywheel.
        relative = gain / max(abs(baseline["performance"]), 1.0)
        regressed = result["validity"] < baseline["validity"]
        attempts.append({
            "attempt": step + 1, "text": text, "strength": strength,
            "performance": result["performance"], "validity": result["validity"],
            "gain": round(relative, 4), "metrics": result["metrics"],
            "verdict": ("broke geometry" if regressed
                        else "objective met" if relative >= MEANINGFUL_GAIN
                        else "too timid"),
        })

        if not regressed and relative >= MEANINGFUL_GAIN:
            break                         # done: better on the objective, no worse to build

        if regressed:
            # The direction was right and the size was not. Same architecture, smaller numbers.
            clauses = _weaker(clauses, 0.88, before)
            continue
        stronger = _next_strength(strength)
        if not stronger:
            break                         # already at the top of the ladder
        strength = stronger
        harder = _amplify(before, subsystems, intent, strength)
        if not harder:
            break
        clauses = harder

    if not attempts:
        return None

    # Choosing the winner, in the order a person would.
    #
    # First: attempts that neither break the geometry nor make the objective worse. A wider
    # intake that collides is not an improvement, and neither is a NARROWER one that fits —
    # the back-off ladder can overshoot past the design it started from, and picking that was
    # shipping a regression while reporting success.
    #
    # If nothing qualifies, the honest answer is to change nothing and say why. An empty
    # `resolved_text` means exactly that, and the caller leaves the design alone.
    buildable = [a for a in attempts if a["validity"] >= baseline["validity"]]
    improving = [a for a in buildable if a["gain"] > 0]
    if improving:
        best = max(improving, key=lambda a: a["gain"])
    elif buildable:
        best = dict(max(buildable, key=lambda a: a["gain"]))
        best["verdict"] = "no improvement available without breaking the geometry"
        best["text"] = ""
    else:
        best = dict(max(attempts, key=lambda a: a["validity"]))
        best["verdict"] = "every attempt broke the geometry"
        best["text"] = ""
    return {
        "attempts": attempts,
        "iterations": len(attempts),
        "resolved_text": best["text"],
        "outcome": best["verdict"],
        "gain": best["gain"],
        "converged": best["verdict"] == "objective met",
        "note": ("Each attempt was built and measured on two separate numbers — how well it "
                 "serves the objective and whether it can be built — and the next attempt was "
                 "chosen from which of the two failed. An edit that only compiles is not a "
                 "finished edit."),
    }
