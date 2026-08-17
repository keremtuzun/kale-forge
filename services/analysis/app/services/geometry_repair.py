"""Deterministic repair passes: make the geometry valid, or say plainly that it is not.

`geometry_validation` says what is wrong. This says what to do about it, and does it. Three
rules govern every pass here:

  * **Nothing is deleted.** Removing the part that collides is how a shooter quietly loses its
    feeder. A repair moves, resizes or re-limits; if it cannot, the design is rejected and the
    caller regenerates the subsystem.
  * **Nothing is hidden.** No boolean subtraction, no shrinking a part until the overlap goes
    away, no widening a tolerance. Every pass changes a real design parameter — a mechanism's
    station, a mechanism's travel limits, a part's height off the floor — and records it.
  * **Every pass re-validates.** A repair that makes the total worse is reverted, so the loop
    cannot oscillate or trade one collision for two.

The headline case is the turret. The compiler declares a turreted shooter as free to rotate
through a full circle, then packs a hopper against it; the head clears the tower in the pose
it is drawn in and drives through the hopper 36° later. A real turret does not solve that by
moving the hopper — it solves it with hard stops and a cable spool, which is what
`_limit_rotation` writes into the design.
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from app.services.geometry_validation import (
    MOTION_SAMPLES, _assembly_pair_allowed, _label, _mating_allowed, _motion_plan, _moves_under,
    _aabb_gap, _obb_aabb, _obb_mtv, _pair_tolerance, _pose_obb, _pose_transform, _rotation_about,
    _solid_bodies, INTRA_PENETRATION_TOL_IN, MOTION_CLEARANCE_IN, PENETRATION_TOL_IN,
    validate_geometry)

# A turret that can only sweep a few degrees is not a turret, it is a fixed shooter that has
# been mislabelled. Below this the mechanism is rejected instead of being quietly crippled.
MIN_USEFUL_TURRET_DEG = 60.0

# How far a mechanism may be shifted from the station the placement pass chose. Beyond this it
# is not the same design any more, and regenerating the subsystem is the honest answer.
MAX_STATION_SHIFT_IN = 4.0

# Angular resolution the clear arc is searched at. Fine enough to keep a useful arc, coarse
# enough that the search stays cheap on a robot with a few hundred bodies.
_ARC_STEP_DEG = 6.0

# How many candidate moves the separation pass may evaluate across the whole repair. Each one
# costs a full re-validation, and without a ceiling a badly packed robot can spend most of a
# minute searching. Counted in TRIALS rather than seconds on purpose: a wall-clock budget
# would make the same prompt produce different geometry on a slower machine, and a
# deterministic engine cannot have that.
_MAX_TRIALS = 60


# Only the intake is allowed to live outside the frame perimeter — that is what an
# over-the-bumper intake IS. Everything else that ends up out there has been pushed, not placed.
_MAY_OVERHANG = frozenset({"intake"})


def _packaging_score(cad: dict[str, Any]) -> tuple[int, dict[str, float]]:
    """(assembly-level clashes, per-assembly overhang past the frame line).

    This module and `frc_cad._separate` are two optimisers pointed at the same robot, and they
    do not share a cost function. Left to itself this one bought fixes for its own issue list
    by shoving the intake forward until its motor hung off the bumper, and by pushing a
    gripper out past the frame — both of which the placement pass had already ruled out. So
    every candidate move has to be neutral-or-better on the older cost too.

    Overhang is tracked PER ASSEMBLY, not as a total. A single number lets the search trade an
    inch of gripper sticking out for an inch of hopper tucked in and call it progress; only the
    intake and the chassis are allowed out past the frame at all, so the constraint is
    per-mechanism or it is not the constraint.
    """
    from app.services.cad_contract import _structural_bodies, clearance_report  # noqa: PLC0415
    clashes = int(clearance_report(cad).get("clash_count") or 0)
    envelope = cad.get("envelope_in") or [30.0, 0.0, 30.0]
    half_w, half_l = float(envelope[0]) / 2, float(envelope[2]) / 2
    overhang: dict[str, float] = {}
    for body in _structural_bodies(cad):
        low, high = body["box"]
        past = max(-half_w - low[0], high[0] - half_w,
                   -half_l - low[2], high[2] - half_l)
        if past <= 0.05:
            continue
        aid = body["aid"]
        # The intake and the chassis live out there by definition — an over-bumper intake and
        # the bumper itself. Their exemption does not extend to a drive package below the
        # bumper's top, which is the first thing another robot hits.
        exempt = aid in _MAY_OVERHANG and not (
            body.get("t") in ("motor", "gearbox") and low[1] <= 5.0)
        if not exempt:
            overhang[aid] = round(max(overhang.get(aid, 0.0), past), 3)
    return clashes, overhang


def _packaging_ok(before: tuple[int, dict[str, float]],
                  after: tuple[int, dict[str, float]]) -> bool:
    """No more assembly-level clashes, and no assembly further out than it already was."""
    if after[0] > before[0]:
        return False
    return all(out <= before[1].get(aid, 0.0) + 1e-6 for aid, out in after[1].items())


def _shares_origin(cad: dict[str, Any], aid: str) -> bool:
    """Is this assembly's origin also another assembly's origin?

    A climber that rides the elevator is emitted at the elevator's exact origin, and the test
    suite checks that equality because it is the difference between riding the tower and
    building a second one. Shifting either of them silently dissolves the relationship, so an
    assembly in a shared frame is not one this pass may move.
    """
    seen: list[tuple] = []
    mine = None
    for assembly in cad.get("assemblies") or []:
        if not isinstance(assembly, dict):
            continue
        key = tuple(assembly.get("origin") or [])
        if str(assembly.get("id")) == aid:
            mine = key
        seen.append(key)
    return mine is not None and seen.count(mine) > 1


def _structurally_sound(cad: dict[str, Any]) -> bool:
    """Does every body still touch structure, and every assembly still reach the chassis?

    A repair moves whole mechanisms, and a mechanism shifted an inch off its station can lift
    clean off the crossmember it was standing on. Solving a collision by floating the thing
    that collided is not a solution, so every candidate move has to clear this too.
    """
    from app.services.cad_contract import structural_report  # noqa: PLC0415
    return bool(structural_report(cad)["ok"])


def _issue_total(report: dict[str, Any]) -> tuple[int, float]:
    """(count, depth) — the pair a repair pass has to reduce, in that priority order."""
    count = (report["collision_count"] + report["truncation_count"]
             + report["motion_collision_count"] + report["out_of_bounds_count"])
    depth = (sum(c["penetration_in"] for c in report["collisions"])
             + sum(m["penetration_in"] for m in report["motion_collisions"])
             + sum(b["over_in"] for b in report["out_of_bounds"]))
    return count, round(depth, 3)


# ── pass 1: limit a rotating mechanism to the arc it actually clears ─────────
def _clear_arc(cad: dict[str, Any], plan: dict[str, Any]) -> tuple[float, float] | None:
    """The widest arc around the home angle in which the mechanism hits nothing.

    Swept both ways from home a step at a time and stopped at the first angle that collides,
    which is exactly where a hard stop would go on the real machine.
    """
    bodies = _solid_bodies(cad)
    intended = _assembly_pair_allowed(cad)
    movers = [b for b in bodies if _moves_under(plan, b)]
    if not movers:
        return None
    others = [b for b in bodies
              if b not in movers
              and not (b["aid"] != plan["aid"]
                       and frozenset({plan["aid"], b["aid"]}) in intended)]
    pairs = [(o, _obb_aabb(o["obb"])) for o in others]
    lo, hi = plan["range"]
    home = lo

    # Pairs that already touch in the pose the robot is DRAWN in are the static audit's
    # business — the base of a turret overlaps the structure it is bolted to at every angle,
    # by construction. Without this exclusion the arc search decides the turret is broken at
    # 0° and gives up before it has looked at a single other angle.
    baseline: set[tuple[str, str]] = set()
    home_matrix = _rotation_about(plan["axis"], math.radians(home))
    for mover in movers:
        box = _obb_aabb(_pose_obb(mover["obb"], home_matrix, plan["pivot"]))
        for other, other_box in pairs:
            if _aabb_gap(box, other_box) > 0.0:
                baseline.add((_label(mover), _label(other)))

    def hits(angle: float) -> bool:
        matrix = _rotation_about(plan["axis"], math.radians(angle))
        for mover in movers:
            obb = _pose_obb(mover["obb"], matrix, plan["pivot"])
            box = _obb_aabb(obb)
            for other, other_box in pairs:
                if _aabb_gap(box, other_box) <= -MOTION_CLEARANCE_IN:
                    continue
                if (_label(mover), _label(other)) in baseline:
                    continue
                if _mating_allowed(mover, other):
                    continue
                same = mover["aid"] == other["aid"]
                tol = _pair_tolerance(
                    mover, other, INTRA_PENETRATION_TOL_IN if same else PENETRATION_TOL_IN)
                depth, _ = _obb_mtv(obb, other["obb"])
                if depth > tol - MOTION_CLEARANCE_IN:
                    return True
        return False

    if hits(home):
        return None                      # broken where it is drawn: not an arc problem
    span = abs(hi - lo)
    steps = max(1, int(span / _ARC_STEP_DEG))
    forward = 0.0
    for k in range(1, steps + 1):
        if hits(home + k * _ARC_STEP_DEG):
            break
        forward = k * _ARC_STEP_DEG
    backward = 0.0
    for k in range(1, steps + 1):
        if hits(home - k * _ARC_STEP_DEG):
            break
        backward = k * _ARC_STEP_DEG
    return (home - backward, home + forward)


def _limit_rotation(cad: dict[str, Any], log: list[dict[str, Any]],
                    *, accept_crippled: bool = False) -> bool:
    """Give every rotating mechanism that collides mid-travel the travel it can actually use.

    Runs LAST. Moving the hopper an inch to buy the turret 140° is a better robot than a
    12° turret with the hopper where it was, so the separation passes get first refusal and
    this only writes hard stops for what is left. `accept_crippled` is the final sweep: once
    nothing else can help, the limit is recorded anyway — with `usable: False`, which fails
    the design rather than shipping a turret that cannot turn.
    """
    changed = False
    for plan in _motion_plan(cad):
        if plan["linear"] or plan["kind"] != "turret":
            continue                      # a pivot has to reach both hard stops to work at all
        lo, hi = plan["range"]
        if abs(hi - lo) < 359.0:
            continue                      # already limited by whoever built it
        arc = _clear_arc(cad, plan)
        if arc is None:
            continue
        low, high = arc
        span = high - low
        if span >= 359.0 or span <= 0.0:
            continue                      # nothing to limit, or nothing survives limiting
        if span < MIN_USEFUL_TURRET_DEG and not accept_crippled:
            continue                      # let the separation passes try to buy a real arc first
        for assembly in cad.get("assemblies") or []:
            if str(assembly.get("id")) != plan["aid"]:
                continue
            assembly["sweep"] = {**(assembly.get("sweep") or {}),
                                 "deg": [round(low, 1), round(high, 1)]}
            assembly.setdefault("mates", []).append(
                f"turret travel limited to {span:.0f}° by hard stops at "
                f"{low:.0f}° and {high:.0f}°; cable spool sized for the arc")
            log.append({
                "pass": "limit-rotation", "assembly": plan["aid"],
                "change": f"turret travel {span:.0f}° instead of a full circle",
                "from": [round(lo, 1), round(hi, 1)], "to": [round(low, 1), round(high, 1)],
                "why": ("the head sweeps into another mechanism past these angles; a real "
                        "turret takes hard stops here rather than moving the robot around it"),
                "usable": span >= MIN_USEFUL_TURRET_DEG,
            })
            changed = True
    return changed


# ── pass 2: move a whole mechanism off what it is inside ─────────────────────
# Which assemblies may be shifted, in the order they should give way. The drivetrain and the
# chassis never move: a swerve module owns its corner, and a mechanism shoved into one has not
# been placed, it has been hidden.
_MOVABLE = ("shooter", "hopper", "intake", "elevator", "arm", "manipulator", "climber",
            "electrical")


def _shift_assembly(cad: dict[str, Any], aid: str, axis: int, distance: float) -> None:
    for assembly in cad.get("assemblies") or []:
        if str(assembly.get("id")) != aid:
            continue
        origin = list(assembly.get("origin") or [0.0, 0.0, 0.0])
        while len(origin) < 3:
            origin.append(0.0)
        origin[axis] = round(float(origin[axis]) + distance, 3)
        assembly["origin"] = origin


def _shifted_so_far(log: list[dict[str, Any]], aid: str) -> float:
    return sum(abs(e.get("distance_in", 0.0)) for e in log
               if e["pass"] == "separate" and e["assembly"] == aid)


def _separate_assemblies(cad: dict[str, Any], report: dict[str, Any],
                         log: list[dict[str, Any]], budget: list[int] | None = None) -> bool:
    """Pull the most deeply interfering mechanism out of whatever it is inside.

    The whole assembly moves, origin and all, so nothing inside it is left behind — moving one
    part out of a collision is how a shooter ends up with its feeder floating six inches away.

    Candidates are scored at FULL motion resolution. Ranking them on a cheap 3-pose sweep is
    about a second faster per robot and it silently loses repairs — the flagship case went
    from 7 issues down to 4 at full resolution and stuck at 7 on the cheap one, because a move
    that only pays off at poses the coarse sweep skips never looks like an improvement.
    """
    baseline = _issue_total(report)
    packaging = _packaging_score(cad)
    # Each candidate costs a full re-validation, so only the deepest few are worth trying —
    # they are sorted by depth, and a move that fixes the worst pair usually fixes its
    # neighbours with it.
    for issue in (report["collisions"][:4] + report["motion_collisions"][:4]):
        a_id, b_id = issue["a_assembly"], issue["b_assembly"]
        if a_id == b_id:
            continue                      # inside one mechanism, shifting the whole thing is a no-op
        for aid in (b_id, a_id):
            if aid not in _MOVABLE or _shares_origin(cad, aid):
                continue
            axis = "xyz".index(issue["fix"]["axis"])
            step = issue["fix"]["distance_in"]
            if not 0.0 < step <= MAX_STATION_SHIFT_IN:
                continue
            # Cumulative, not per-move: three "improvements" of an inch and a half each is a
            # mechanism that has wandered off its station, and the honest answer at that point
            # is to regenerate the subsystem rather than keep nudging.
            if _shifted_so_far(log, aid) + step > MAX_STATION_SHIFT_IN:
                continue
            for direction in (1.0, -1.0):
                if budget is not None:
                    if budget[0] <= 0:
                        return False              # search ceiling reached; see _MAX_TRIALS
                    budget[0] -= 1
                trial = deepcopy(cad)
                _shift_assembly(trial, aid, axis, direction * step)
                after = _issue_total(validate_geometry(trial))
                # Better on the new cost, no worse on the old one, and still bolted together.
                if (after < baseline and _packaging_ok(packaging, _packaging_score(trial))
                        and _structurally_sound(trial)):
                    _shift_assembly(cad, aid, axis, direction * step)
                    log.append({
                        "pass": "separate", "assembly": aid, "distance_in": step,
                        "change": (f"moved {step:.2f} in along "
                                   f"{'+-'[direction < 0]}{'XYZ'[axis]}"),
                        "why": f"{issue['a']} and {issue['b']} shared space",
                        "issues_before": baseline[0], "issues_after": after[0],
                    })
                    return True
    return False


# ── pass 3: nothing below the carpet ────────────────────────────────────────
def _lift_out_of_floor(cad: dict[str, Any], report: dict[str, Any],
                       log: list[dict[str, Any]]) -> bool:
    """Raise any mechanism drawn dipping through the floor back onto it."""
    worst: dict[str, float] = {}
    for issue in report["out_of_bounds"]:
        if issue["limit"] != "floor":
            continue
        aid = issue["part"].split("/")[0]
        worst[aid] = max(worst.get(aid, 0.0), issue["over_in"])
    changed = False
    for aid, lift in worst.items():
        if aid not in _MOVABLE or lift > MAX_STATION_SHIFT_IN or _shares_origin(cad, aid):
            continue
        trial = deepcopy(cad)
        _shift_assembly(trial, aid, 1, lift)
        if (not _structurally_sound(trial)
                or not _packaging_ok(_packaging_score(cad), _packaging_score(trial))):
            continue                     # raising it off the floor would lift it off its mount
        _shift_assembly(cad, aid, 1, lift)
        log.append({"pass": "lift", "assembly": aid,
                    "change": f"raised {lift:.2f} in onto the floor plane",
                    "why": "part of it was drawn below the drive wheels' contact patch"})
        changed = True
    return changed


# ── the loop ────────────────────────────────────────────────────────────────
def repair_geometry(cad: dict[str, Any], *, limits: dict[str, float] | None = None,
                    max_passes: int = 6, samples: int = MOTION_SAMPLES
                    ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate, repair, rebuild, validate again — until it is clean or nothing more helps.

    Returns the (possibly modified) tree and a status block whose `status` is the thing the
    export gate reads. `invalid` is a real outcome: a design that cannot be repaired is
    reported as broken rather than shown as finished.
    """
    cad = deepcopy(cad)
    log: list[dict[str, Any]] = []
    report = validate_geometry(cad, limits=limits, samples=samples)
    first = _issue_total(report)
    passes = 0
    budget = [_MAX_TRIALS]
    while not report["valid"] and passes < max_passes:
        passes += 1
        # Order matters and it is not "cheapest first". A rotation limit retires every motion
        # collision on that mechanism in one move, where separation trades an inch of station
        # for one or two issues at a time — put separation first and it eats the whole pass
        # budget on marginal gains and never reaches the limit that would have fixed twenty.
        # The useful-arc guard inside `_limit_rotation` is what makes this safe: it refuses to
        # cripple a turret, so a design that needs more room still falls through to separation
        # and gets its arc re-measured, wider, on the next pass.
        if not (_limit_rotation(cad, log)
                or _lift_out_of_floor(cad, report, log)
                or _separate_assemblies(cad, report, log, budget)):
            # Nothing that preserves the design helps any more. Write the hard stops the
            # geometry actually allows, even where the arc left is not worth having: a
            # recorded `usable: False` fails the design honestly, where leaving the turret
            # declared at 360° would ship a robot that tears itself apart on the field.
            if not _limit_rotation(cad, log, accept_crippled=True):
                break
        report = validate_geometry(cad, limits=limits, samples=samples)
    final = _issue_total(report)
    crippled = [entry for entry in log if entry.get("usable") is False]
    status = "valid" if report["valid"] and not crippled else "invalid"
    return cad, {
        "status": status,
        "valid": status == "valid",
        "passes": passes,
        "repairs": log,
        "issues_before": first[0],
        "issues_after": final[0],
        "unrepaired": [] if report["valid"] else _summary(report),
        "report": report,
        "note": ("Repairs move, re-limit or raise real design parameters. Nothing is deleted, "
                 "nothing is subtracted away and no tolerance is widened; a design that still "
                 "fails is reported invalid so the subsystem can be regenerated."),
    }


def _summary(report: dict[str, Any]) -> list[str]:
    out = [f"{c['a']} interferes with {c['b']} by {c['penetration_in']} in"
           for c in report["collisions"][:6]]
    out += [f"{t['part']} is {int(t['buried_fraction'] * 100)}% inside {t['inside']}"
            for t in report["truncations"][:6]]
    out += [f"{m['a']} hits {m['b']} at {m['pose']}" for m in report["motion_collisions"][:6]]
    out += [f"{b['part']} is {b['over_in']} in past the {b['limit']} limit"
            for b in report["out_of_bounds"][:6]]
    return out
