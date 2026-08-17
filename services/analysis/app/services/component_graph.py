"""Which parts of a robot an edit is allowed to touch, and which ones actually moved.

`build_robot_spec` is a pure function of its prompt: every edit regenerates the whole robot
from text. That is what makes the engine trustworthy — there is no incremental state to drift
— but it costs two things a user feels:

  * **Time.** An edit built the base robot three times: once inside `plan_and_resolve` to
    measure the design before the edit, once in the studio to diff against, and once for the
    result. Measured at 8.8 s for one intake edit, of which 4.2 s was the same build repeated.
  * **Trust.** Nothing checked that an edit to the intake left the climber alone. The
    placement pass is global — moving one mechanism can re-station another — so unrelated
    geometry moving after an edit was possible and invisible.

This module addresses both without giving up the pure-function property. `build_signature`
makes repeated builds of an identical design free, and the dependency graph turns an edit plan
into an explicit contract — these subsystems MUST change, these MAY change as a consequence,
everything else MUST NOT — which `drift_report` then checks against what actually happened.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Every subsystem the spec tracks, in build order.
SUBSYSTEMS: tuple[str, ...] = (
    "frame", "drivetrain", "intake", "hopper", "shooter",
    "elevator", "manipulator", "climber", "electrical",
)

# What a change to X can legitimately drag with it.
#
# Two kinds of edge are mixed here on purpose, because both are real:
#   * design coupling — the intake hands over to the hopper, the hopper feeds the shooter, a
#     climber rides the elevator. Change one end and the other has to follow.
#   * packaging coupling — every mechanism competes for stations and lanes, so `_separate` can
#     re-station a neighbour that was not itself edited. That is legitimate, but it is only
#     legitimate for NEIGHBOURS, not for the whole robot.
_DEPENDS: dict[str, tuple[str, ...]] = {
    "frame": SUBSYSTEMS,                      # the frame is the datum for everything
    "drivetrain": ("frame", "electrical"),
    # Every mechanism lists `frame`: the chassis puts a crossmember pair under each mechanism
    # at its station, so moving or resizing one genuinely rewrites chassis geometry. Declared
    # rather than discovered, or a shooter edit reports the frame as unexplained drift.
    "intake": ("hopper", "frame", "electrical"),
    "hopper": ("intake", "shooter", "frame", "electrical"),
    "shooter": ("hopper", "frame", "electrical"),
    "elevator": ("climber", "manipulator", "frame", "electrical"),
    "manipulator": ("elevator", "frame", "electrical"),
    "climber": ("elevator", "frame", "electrical"),
    "electrical": (),
}

# Derived artefacts that are rebuilt whenever anything at all changes. Listing them stops
# "the cut list changed" from being reported as unexplained drift.
DERIVED: frozenset[str] = frozenset({
    "cut_list", "editable_manifest", "mass", "rule_report", "fidelity",
    "transmission", "geometry_status", "electrical",
})


def dirty_set(plan: dict[str, Any]) -> dict[str, list[str]]:
    """The contract for one edit: what must change, what may, and what must not.

    Derived from the edit plan rather than from the diff, so it can be checked AGAINST the
    diff afterwards. An architecture-scope edit is allowed to touch everything; a parameter
    edit is not, and that is the whole point of classifying scope.
    """
    targets = [s for s in (plan.get("affected_subsystems") or []) if s in SUBSYSTEMS]
    scope = plan.get("scope") or "subsystem"
    if scope == "architecture" or not targets:
        # No named target, or an explicit architecture edit: everything is in play. Saying so
        # is honest — a false "must not change" list would just produce noise.
        return {"must_change": targets, "may_change": list(SUBSYSTEMS), "must_not_change": []}
    may: set[str] = set()
    for target in targets:
        may.update(_DEPENDS.get(target, ()))
    may.update(DERIVED & set(SUBSYSTEMS))
    may.difference_update(targets)
    must_not = [s for s in SUBSYSTEMS if s not in targets and s not in may]
    return {"must_change": targets,
            "may_change": sorted(may),
            "must_not_change": must_not}


# ── what actually moved ──────────────────────────────────────────────────────
def _fingerprint(value: Any) -> str:
    """Stable hash of one spec section, insensitive to dict ordering."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _design_fingerprints(spec: dict[str, Any]) -> dict[str, str]:
    """One hash per subsystem over its DESIGN only — parameters and geometry, minus position.

    The distinction this draws is the useful one. The placement pass is global: change the
    shooter's size and `_separate` may re-station the hopper, the elevator and the climber to
    make room. That is legitimate repackaging and reporting it as "unintended" on every edit
    would make the report noise.

    A subsystem whose PARAMETERS or part geometry changed while the edit had no business
    touching it is a different thing entirely, and that is what this isolates: assembly
    origins are stripped before hashing, so a body that merely moved does not register.
    """
    assemblies = {str(a.get("id")): a for a in (spec.get("cad") or {}).get("assemblies") or []
                  if isinstance(a, dict)}

    def stripped(assembly: dict[str, Any] | None) -> Any:
        if not assembly:
            return None
        return {k: v for k, v in assembly.items() if k not in ("origin", "sweep")}

    out: dict[str, str] = {}
    for name in SUBSYSTEMS:
        parts: list[Any] = [spec.get(name)]
        if name == "drivetrain":
            parts += [stripped(a) for aid, a in sorted(assemblies.items())
                      if aid.startswith("swerve")]
            parts.append(stripped(assemblies.get("drivetrain")))
        elif name == "frame":
            parts.append(stripped(assemblies.get("chassis")))
        else:
            parts.append(stripped(assemblies.get(name)))
        out[name] = _fingerprint(parts)
    return out


def subsystem_fingerprints(spec: dict[str, Any]) -> dict[str, str]:
    """One hash per subsystem, over BOTH its parameters and its geometry.

    Geometry matters as much as parameters here: the placement pass moves an assembly by
    changing its origin, which leaves every number in `spec["climber"]` identical while the
    climber is six inches from where it was. Hashing the parameters alone would call that
    unchanged.
    """
    assemblies = {str(a.get("id")): a for a in (spec.get("cad") or {}).get("assemblies") or []
                  if isinstance(a, dict)}
    out: dict[str, str] = {}
    for name in SUBSYSTEMS:
        parts: list[Any] = [spec.get(name)]
        if name == "drivetrain":
            parts += [a for aid, a in sorted(assemblies.items()) if aid.startswith("swerve")]
            parts.append(assemblies.get("drivetrain"))
        elif name == "frame":
            parts.append(assemblies.get("chassis"))
        else:
            parts.append(assemblies.get(name))
        out[name] = _fingerprint(parts)
    return out


def drift_report(before: dict[str, Any], after: dict[str, Any],
                 plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Which subsystems moved, and whether the edit was entitled to move them.

    `unintended` is the number worth watching: a subsystem that changed while the edit had no
    business touching it is either a coupling nobody declared or a bug, and both are worth
    seeing rather than discovering later in a render.
    """
    a, b = subsystem_fingerprints(before), subsystem_fingerprints(after)
    da, db = _design_fingerprints(before), _design_fingerprints(after)
    changed = sorted(name for name in SUBSYSTEMS if a.get(name) != b.get(name))
    unchanged = sorted(name for name in SUBSYSTEMS if a.get(name) == b.get(name))
    redesigned = sorted(name for name in SUBSYSTEMS if da.get(name) != db.get(name))
    restationed = [name for name in changed if name not in redesigned]
    contract = dirty_set(plan or {})
    # Only a REDESIGN of an off-limits subsystem counts as drift. Being re-stationed to make
    # room for the thing that was edited is the placement pass doing its job.
    unintended = [name for name in redesigned if name in contract["must_not_change"]]
    missing = [name for name in contract["must_change"] if name not in changed]
    return {
        "changed": changed,
        "redesigned": redesigned,
        "restationed": sorted(restationed),
        "preserved": unchanged,
        "rebuilt": len(changed),
        "reused": len(unchanged),
        "contract": contract,
        "unintended": unintended,
        "objective_untouched": missing,
        "note": ("An edit regenerates the whole robot from its prompt; this reports which "
                 "subsystems the rebuild actually altered, so an edit that quietly moved "
                 "something it was not aiming at is visible rather than silent."),
    }


# ── build reuse ──────────────────────────────────────────────────────────────
def build_signature(prompt: str, season: str, use_model: bool, design_type: str,
                    repair: bool) -> str:
    """Content address for one build. Identical inputs are the same robot, every time.

    Safe precisely because the builder is pure: the same five inputs cannot produce two
    different robots — the design seed is pinned to the request, and the repair loop is bounded
    in trials rather than in seconds so it cannot vary with machine speed.
    """
    return _fingerprint([prompt, season, bool(use_model), design_type, bool(repair)])
