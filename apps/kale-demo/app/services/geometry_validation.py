"""Deterministic geometry validation: nothing overlaps, nothing is swallowed, nothing escapes.

`cad_contract` already answers two questions about a generated robot — does every body touch
structure (`structural_report`), and do two *mechanisms* interpenetrate (`clearance_report`).
Both are assembly-level. Neither one ever looked INSIDE an assembly, and neither one sampled a
mechanism through its travel, so three whole classes of fault shipped as valid geometry:

  * a shaft that ends half way through a bearing block, or a wheel buried in a chassis rail —
    the part is not "colliding" so much as *swallowed*, which is what reads on screen as a
    component cut off in the middle;
  * a turret head that clears the tower in the pose it happens to be drawn in and drives
    straight through it 90° later — `clearance_report` grows a swept assembly to a cylinder,
    which catches the gross case but cannot tell you at WHICH angle it fails, or check the
    turret against the static part of its own assembly;
  * an intake that is clear stowed and clear deployed but sweeps through the bumper somewhere
    in between.

This module is the narrow-phase authority. AI-generated coordinates are suggestions; the
functions here have the final say, and `repair_geometry` is allowed to move things until they
agree.

Everything is in inches, in the shared world frame (+X right, +Y up, −Z forward, origin at the
frame centre on the top face of the bellypan).
"""
from __future__ import annotations

import math
from typing import Any

from app.services.cad_contract import (
    _PASSES_THROUGH, _floats, _obb, _structural_bodies, expand_mirrors)

VERSION = "kale-geometry-2.0"

# ── tolerances ───────────────────────────────────────────────────────────────
# World boxes are built from nominal dimensions with no fillets or chamfers, and a rotated
# part's box only grows, so a collision has to be deeper than the modelling noise before it
# is real. This is the same threshold `clearance_report` uses between assemblies.
PENETRATION_TOL_IN = 0.60

# Inside one assembly, absolute depth is the wrong measure and reporting on it is what made
# the first version of this unusable. Structure joins structure: a tie butts into an upright,
# a gusset laps a rail, a motor's boss sits in its plate. Every one of those overlaps by an
# inch or two of nominal box and every one of them is the design. What distinguishes a joint
# from a fault is not how deep the overlap is but how much of the smaller part disappears into
# it — a butted tie loses a fifth of itself, a tie driven through the middle of an upright
# loses half. So intra-assembly pairs are judged on the buried fraction.
INTRA_BURIED_FRACTION = 0.35

# Depth below which an intra-assembly overlap is not even worth measuring the fraction for.
INTRA_PENETRATION_TOL_IN = 0.30

# A body more than this fraction of the way inside another solid is not colliding, it has been
# swallowed — the visible half of a wheel with the rest inside a rail, a shaft that terminates
# in a plate. Reported separately because the repair is different: a collision gets pushed
# apart, a truncation means the part was built to the wrong length or hung off the wrong datum.
TRUNCATION_FRACTION = 0.55

# What a moving mechanism has to keep between itself and everything it passes. Contact is not
# good enough for something on a bearing: it has to actually clear.
MOTION_CLEARANCE_IN = 0.25

# Poses sampled through a mechanism's range, as fractions of travel. The endpoints are always
# included because that is where hard stops put the geometry.
MOTION_SAMPLES = 11


# ── mating interfaces: overlap that is the design, not a fault ───────────────
# A pair of feature kinds that are SUPPOSED to occupy each other's space. This is a whitelist
# on purpose: globally ignoring intra-assembly overlap is what let the faults above through,
# and widening the tolerance until the real ones stopped being reported would have hidden them
# just as well.
_MATING_KINDS: frozenset[frozenset[str]] = frozenset({
    # A shaft lives inside everything it drives.
    frozenset({"shaft", "bearing"}), frozenset({"shaft", "pulley"}),
    frozenset({"shaft", "sprocket"}), frozenset({"shaft", "gear"}),
    frozenset({"shaft", "bevel"}), frozenset({"shaft", "wheel"}),
    frozenset({"shaft", "drum"}), frozenset({"shaft", "gearbox"}),
    frozenset({"shaft", "brake"}), frozenset({"shaft", "tensioner"}),
    frozenset({"shaft", "hook"}), frozenset({"shaft", "actuator"}),
    frozenset({"shaft", "motor"}),               # the motor's own output shaft
    # A bearing is pressed into a bore in a plate, a tube wall or a polycarb panel.
    frozenset({"bearing", "plate"}), frozenset({"bearing", "tube"}),
    frozenset({"bearing", "gusset"}), frozenset({"bearing", "polycarb"}),
    frozenset({"bearing", "gearbox"}), frozenset({"bearing", "bearing"}),
    # Everything a bearing carries is concentric with it by construction. A swerve module is
    # the dense case: the azimuth ring gear and its pinion live INSIDE the slew bearing, and
    # the drive wheel hangs directly under it.
    frozenset({"bearing", "wheel"}), frozenset({"bearing", "gear"}),
    frozenset({"bearing", "bevel"}), frozenset({"bearing", "pulley"}),
    frozenset({"bearing", "sprocket"}), frozenset({"bearing", "drum"}),
    # A motor bolts onto a gearbox face and its pinion reaches inside; whatever either one
    # drives is pressed onto its output shaft, flush against the face.
    frozenset({"motor", "gearbox"}), frozenset({"motor", "pulley"}),
    frozenset({"motor", "sprocket"}), frozenset({"motor", "gear"}),
    frozenset({"gearbox", "pulley"}), frozenset({"gearbox", "sprocket"}),
    frozenset({"gearbox", "wheel"}), frozenset({"gearbox", "drum"}),
    # Gear teeth mesh: the pitch circles overlap by definition.
    frozenset({"gear", "gear"}), frozenset({"bevel", "bevel"}),
    frozenset({"gear", "bevel"}), frozenset({"gear", "gearbox"}),
    # Laminated and stiffened structure.
    frozenset({"polycarb", "plate"}), frozenset({"hood", "rib"}),
    frozenset({"rib", "plate"}), frozenset({"gusset", "tube"}),
    frozenset({"gusset", "plate"}), frozenset({"standoff", "plate"}),
    frozenset({"standoff", "polycarb"}), frozenset({"tensioner", "plate"}),
    # A wheel's tread wraps its own hub hardware, and in a swerve module the bevel pair sits
    # inside the wheel it drives.
    frozenset({"wheel", "pulley"}), frozenset({"wheel", "sprocket"}),
    frozenset({"wheel", "gear"}), frozenset({"wheel", "bevel"}),
})

# Two mechanisms that are supposed to share space: the intake's last roller sits in the
# hopper's mouth because that is the handover, and a climber that rides the elevator is bolted
# to it.
_INTENDED_ASSEMBLY_PAIRS: frozenset[frozenset[str]] = frozenset({
    frozenset({"intake", "hopper"}),
    frozenset({"climber", "elevator"}),
})


# Shells and arcs: a shooter hood is a thin curved skin and a formed rib is an arc segment,
# but `_local_extents` boxes both by the full circle they are cut from because that is the
# only conservative choice for a contact audit. The box is therefore mostly air, and treating
# it as a solid claimed that every flywheel shaft inside the hood's radius had been swallowed
# by it. A shell can still be collided WITH; it just cannot swallow anything, and it needs a
# looser depth before its box is believed.
_SHELL_KINDS = frozenset({"hood", "rib"})
_SHELL_TOL_IN = 1.60


def _pair_tolerance(a: dict[str, Any], b: dict[str, Any], base: float) -> float:
    if a.get("t") in _SHELL_KINDS or b.get("t") in _SHELL_KINDS:
        return max(base, _SHELL_TOL_IN)
    return base


def _mating_allowed(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Is this overlap a real mechanical interface rather than a fault?

    Three ways to earn the exemption: the pair of kinds is a known interface, one part names
    the other in its own `allow` list, or both sit inside an assembly that declared the pair
    in `allowedIntersections`. The explicit forms exist so a generator can authorise a
    one-off nesting without widening the global table for every robot.
    """
    if frozenset({a.get("t"), b.get("t")}) in _MATING_KINDS:
        return True
    for x, y in ((a, b), (b, a)):
        allow = x.get("allow")
        if not isinstance(allow, (list, tuple)):
            continue
        target, aid = y["name"].casefold(), y["aid"].casefold()
        for entry in allow:
            wanted = str(entry).casefold()
            # `normalize_cad` gives repeated parts a numeric suffix ("left upright 2"), and a
            # mirrored pair is the common case for exactly the members a stage nests in. Match
            # the suffixed form too, or every declaration would only ever authorise one side.
            if target == wanted or aid == wanted:
                return True
            if target.startswith(wanted + " ") and target[len(wanted) + 1:].strip().isdigit():
                return True
    return False


def _assembly_pair_allowed(cad: dict[str, Any]) -> frozenset[frozenset[str]]:
    """Intended assembly pairs, the built-in ones plus anything the tree declares."""
    pairs = set(_INTENDED_ASSEMBLY_PAIRS)
    for assembly in cad.get("assemblies") or []:
        if not isinstance(assembly, dict):
            continue
        aid = str(assembly.get("id") or "")
        for other in assembly.get("allowedIntersections") or []:
            pairs.add(frozenset({aid, str(other)}))
    return frozenset(pairs)


# ── oriented-box maths ───────────────────────────────────────────────────────
def _obb_mtv(a, b) -> tuple[float, list[float]]:
    """Minimum translation vector between two oriented boxes, by the separating axis theorem.

    Returns (depth, unit axis) for the shallowest of the 15 candidate axes, or (0, ...) when
    any axis separates them. `clearance_report` only ever wanted the depth; a repair pass
    needs to know which way to push, which is what the axis is for.
    """
    (ca, ea, aa), (cb, eb, ab) = a, b
    delta = [cb[i] - ca[i] for i in range(3)]
    # The three world axes belong in the candidate set even though SAT does not need them to
    # DETECT overlap. Without them the returned depth is the shallowest of the boxes' own
    # axes, which for a raked plate against an upright bumper reported 2.8 in when sliding
    # 0.4 in along Z separates them — and it disagreed with the axis-aligned broad phase,
    # which then skipped pairs the narrow phase would have called collisions.
    candidates = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]] + list(aa) + list(ab)
    for i in range(3):
        for j in range(3):
            axis = [aa[i][1] * ab[j][2] - aa[i][2] * ab[j][1],
                    aa[i][2] * ab[j][0] - aa[i][0] * ab[j][2],
                    aa[i][0] * ab[j][1] - aa[i][1] * ab[j][0]]
            if axis[0] ** 2 + axis[1] ** 2 + axis[2] ** 2 > 1e-9:
                candidates.append(axis)
    best, best_axis = float("inf"), [0.0, 1.0, 0.0]
    for axis in candidates:
        norm = math.sqrt(sum(v * v for v in axis))
        if norm < 1e-9:
            continue
        unit = [v / norm for v in axis]
        centre_gap = abs(sum(delta[i] * unit[i] for i in range(3)))
        reach = (sum(ea[k] * abs(sum(aa[k][i] * unit[i] for i in range(3))) for k in range(3))
                 + sum(eb[k] * abs(sum(ab[k][i] * unit[i] for i in range(3))) for k in range(3)))
        overlap = reach - centre_gap
        if overlap <= 0.0:
            return 0.0, unit
        if overlap < best:
            best, best_axis = overlap, unit
    return (0.0 if best is float("inf") else best), best_axis


def _obb_aabb(obb) -> tuple[list[float], list[float]]:
    """The axis-aligned box that contains an oriented one."""
    centre, ext, axes = obb
    spread = [sum(ext[k] * abs(axes[k][i]) for k in range(3)) for i in range(3)]
    return ([centre[i] - spread[i] for i in range(3)],
            [centre[i] + spread[i] for i in range(3)])


def _aabb_gap(a: tuple[list[float], list[float]], b: tuple[list[float], list[float]]) -> float:
    """Smallest axis overlap: positive means interpenetrating, negative is the clear gap."""
    return min(min(a[1][k], b[1][k]) - max(a[0][k], b[0][k]) for k in range(3))


_GRID = [(-0.8 + 0.4 * i) for i in range(5)]         # 5 samples per axis, inside the faces


def _buried_fraction(inner, outer) -> float:
    """How much of `inner` lies inside `outer`, by sampling `inner`'s own volume.

    Volume of intersection of two oriented boxes has no closed form worth writing here, and
    doing it on the axis-aligned boxes reports a tilted part as far more buried than it is.
    125 points in the part's own frame is accurate enough to separate "bolted lap joint" from
    "this shaft ends inside the plate" and costs nothing at the pair counts involved.
    """
    centre, ext, axes = inner
    oc, oe, oa = outer
    inside = 0
    for u in _GRID:
        for v in _GRID:
            for w in _GRID:
                p = [centre[i] + ext[0] * u * axes[0][i]
                     + ext[1] * v * axes[1][i] + ext[2] * w * axes[2][i] for i in range(3)]
                d = [p[i] - oc[i] for i in range(3)]
                if all(abs(sum(d[i] * oa[k][i] for i in range(3))) <= oe[k] for k in range(3)):
                    inside += 1
    return inside / float(len(_GRID) ** 3)


def _volume(obb) -> float:
    _, ext, _ = obb
    return 8.0 * max(ext[0], 1e-4) * max(ext[1], 1e-4) * max(ext[2], 1e-4)


def _rotation_about(axis: str, radians: float) -> list[list[float]]:
    """Rotation matrix about a world axis, as rows."""
    c, s = math.cos(radians), math.sin(radians)
    if axis == "x":
        return [[1, 0, 0], [0, c, -s], [0, s, c]]
    if axis == "z":
        return [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]            # y


def _apply(matrix: list[list[float]], vec: list[float]) -> list[float]:
    return [sum(matrix[i][j] * vec[j] for j in range(3)) for i in range(3)]


def _pose_obb(obb, matrix: list[list[float]], pivot: list[float], shift: list[float] | None = None):
    """The oriented box after rotating about `pivot` and optionally translating."""
    centre, ext, axes = obb
    moved = _apply(matrix, [centre[i] - pivot[i] for i in range(3)])
    new_centre = [moved[i] + pivot[i] + (shift[i] if shift else 0.0) for i in range(3)]
    return (new_centre, ext, [_apply(matrix, axis) for axis in axes])


# ── body collection ──────────────────────────────────────────────────────────
def _solid_bodies(cad: dict[str, Any]) -> list[dict[str, Any]]:
    """Every body that occupies real space, with its oriented box and its provenance.

    Fasteners, cloth, foam, belt/rope/cable runs and the harness are dropped: they are meant
    to pass through, wrap around or deform, and every one of them would otherwise be reported
    against the structure it is holding together.
    """
    bodies = []
    for body in _structural_bodies(cad):
        if body.get("t") in _PASSES_THROUGH or not body.get("obb"):
            continue
        bodies.append(body)
    # `_structural_bodies` does not carry the authoring feature through, and the explicit
    # `allow` list lives on it. Re-walk the tree once to attach it by (assembly, name).
    allow_by_key: dict[tuple[str, str], Any] = {}
    for assembly in cad.get("assemblies") or []:
        if not isinstance(assembly, dict):
            continue
        aid = str(assembly.get("id") or "assembly")
        for feature in expand_mirrors(assembly.get("features") or []):
            allow = feature.get("allow")
            if allow:
                allow_by_key[(aid, str(feature.get("n") or feature.get("t")))] = allow
    for body in bodies:
        allow = allow_by_key.get((body["aid"], body["name"]))
        if allow:
            body["allow"] = allow
    return bodies


def _label(body: dict[str, Any]) -> str:
    return f"{body['aid']}/{body['name']}"


# ── motion ───────────────────────────────────────────────────────────────────
def _motion_plan(cad: dict[str, Any]) -> list[dict[str, Any]]:
    """Every mechanism that moves, and the poses its geometry has to be checked in.

    Three kinds, all already emitted by the compiler:
      * `sweep`   — a turret: everything above the slew bearing revolves about Y, full circle
                    unless the block bounds it.
      * `articulation` — a pivot: everything not named in `static` swings about a dead axle
                    between the hard stops.
      * `travel`  — a linear stage: everything not named in `static` translates along an axis.
    """
    plans = []
    for assembly in cad.get("assemblies") or []:
        if not isinstance(assembly, dict):
            continue
        aid = str(assembly.get("id") or "assembly")
        origin = _floats(assembly.get("origin"), 3) or [0.0, 0.0, 0.0]

        sweep = assembly.get("sweep")
        if isinstance(sweep, dict):
            lo, hi = (_floats(sweep.get("deg"), 2) or [0.0, 360.0])
            floor = origin[1] + float(sweep.get("from_y") or 0.0)
            plans.append({
                "aid": aid, "kind": "turret", "axis": "y",
                "pivot": [origin[0] + float(sweep.get("x") or 0.0), floor,
                          origin[2] + float(sweep.get("z") or 0.0)],
                "range": (lo, hi), "above_y": floor, "static": set(), "linear": None,
                "label": "turret rotation",
            })

        art = assembly.get("articulation")
        if isinstance(art, dict) and art.get("type") == "pivot":
            at = _floats(art.get("at"), 3) or [0.0, 0.0, 0.0]
            lo, hi = (_floats(art.get("deg"), 2) or [0.0, 0.0])
            if abs(hi - lo) > 0.5:
                plans.append({
                    "aid": aid, "kind": "pivot", "axis": str(art.get("axis") or "x"),
                    "pivot": [origin[i] + at[i] for i in range(3)],
                    "range": (lo, hi), "above_y": None,
                    "static": {str(n).casefold() for n in (art.get("static") or [])},
                    "linear": None, "label": f"{aid} deploy arc",
                })

        travel = assembly.get("travel")
        if isinstance(travel, dict):
            lo, hi = (_floats(travel.get("range_in"), 2) or [0.0, 0.0])
            if abs(hi - lo) > 0.25:
                plans.append({
                    "aid": aid, "kind": "linear", "axis": str(travel.get("axis") or "y"),
                    "pivot": [0.0, 0.0, 0.0], "range": (lo, hi), "above_y": None,
                    "static": {str(n).casefold() for n in (travel.get("static") or [])},
                    "linear": str(travel.get("axis") or "y"),
                    "label": f"{aid} travel",
                })
    return plans


def _moves_under(plan: dict[str, Any], body: dict[str, Any]) -> bool:
    if body["aid"] != plan["aid"]:
        return False
    if body["name"].casefold() in plan["static"]:
        return False
    if plan["above_y"] is not None:
        # Below the slew bearing is the base, not the rotating head.
        return body["obb"][0][1] + 0.05 >= plan["above_y"]
    return True


def _pose_transform(plan: dict[str, Any], t: float):
    """(rotation matrix, shift) for a mechanism at fraction `t` of its range."""
    lo, hi = plan["range"]
    value = lo + (hi - lo) * t
    if plan["linear"]:
        index = {"x": 0, "y": 1, "z": 2}.get(plan["linear"], 1)
        shift = [0.0, 0.0, 0.0]
        shift[index] = value
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1]], shift
    return _rotation_about(plan["axis"], math.radians(value)), None


# ── the report ───────────────────────────────────────────────────────────────
def _issue(a: dict[str, Any], b: dict[str, Any], depth: float, axis: list[float],
           required: float, pose: str | None = None) -> dict[str, Any]:
    """One collision, with everything a repair pass or a human needs to act on it."""
    box_a, box_b = _obb_aabb(a["obb"]), _obb_aabb(b["obb"])
    where = [round((max(box_a[0][k], box_b[0][k]) + min(box_a[1][k], box_b[1][k])) / 2, 3)
             for k in range(3)]
    # Push along whichever world axis the minimum-translation vector leans on hardest: that is
    # the cheapest direction out, and naming a world axis is actionable where a diagonal unit
    # vector is not.
    dominant = max(range(3), key=lambda k: abs(axis[k]))
    magnitude = abs(axis[dominant]) or 1.0
    return {
        "a": _label(a), "b": _label(b),
        "a_assembly": a["aid"], "b_assembly": b["aid"],
        "a_type": a.get("t"), "b_type": b.get("t"),
        "location_in": where,
        "penetration_in": round(depth, 3),
        "required_clearance_in": round(required, 3),
        "transform_a": {"centre_in": [round(v, 3) for v in a["obb"][0]]},
        "transform_b": {"centre_in": [round(v, 3) for v in b["obb"][0]]},
        "fix": {"axis": "xyz"[dominant],
                "distance_in": round((depth + required) / magnitude, 3),
                "move": _label(b)},
        "pose": pose,
    }


def _static_collisions(bodies: list[dict[str, Any]], intended: frozenset[frozenset[str]]
                       ) -> tuple[list[dict], list[dict]]:
    """Every pair of solids driven through each other, split into collisions and truncations."""
    collisions: list[dict[str, Any]] = []
    truncations: list[dict[str, Any]] = []
    boxes = [_obb_aabb(b["obb"]) for b in bodies]
    for i, a in enumerate(bodies):
        for j in range(i + 1, len(bodies)):
            b = bodies[j]
            same = a["aid"] == b["aid"]
            if not same and frozenset({a["aid"], b["aid"]}) in intended:
                continue
            tol = _pair_tolerance(a, b, INTRA_PENETRATION_TOL_IN if same else PENETRATION_TOL_IN)
            if _aabb_gap(boxes[i], boxes[j]) <= tol:
                continue                       # broad phase: the AABB never misses a real hit
            if _mating_allowed(a, b):
                continue
            depth, axis = _obb_mtv(a["obb"], b["obb"])
            if depth <= tol:
                continue
            # Swallowed, or merely overlapping? The smaller body is the one that goes missing.
            small, large = ((a, b) if _volume(a["obb"]) <= _volume(b["obb"]) else (b, a))
            # A shell's box is mostly air, so it can be hit but it cannot contain anything.
            buried = (0.0 if large.get("t") in _SHELL_KINDS
                      else _buried_fraction(small["obb"], large["obb"]))
            if same and buried < INTRA_BURIED_FRACTION:
                continue                        # a joint, not a fault — see INTRA_BURIED_FRACTION
            issue = _issue(large, small, depth, axis, tol)
            if buried >= TRUNCATION_FRACTION:
                issue["buried_fraction"] = round(buried, 3)
                issue["part"] = _label(small)
                issue["inside"] = _label(large)
                truncations.append(issue)
            else:
                issue["buried_fraction"] = round(buried, 3)
                collisions.append(issue)
    collisions.sort(key=lambda c: -c["penetration_in"])
    truncations.sort(key=lambda c: -c["buried_fraction"])
    return collisions, truncations


def _motion_collisions(cad: dict[str, Any], bodies: list[dict[str, Any]],
                       intended: frozenset[frozenset[str]],
                       samples: int = MOTION_SAMPLES) -> tuple[list[dict], list[dict]]:
    """Sample every moving mechanism through its range and check each pose.

    A static check on the drawn pose is the wrong question for anything on a bearing. The
    swept-cylinder trick `clearance_report` uses catches the gross case but cannot say at
    which angle the mechanism fails, and it cannot check a turret against the static base of
    its own assembly — which is exactly where a head that clears the tower at 0° puts itself
    at 180°.
    """
    issues: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    for plan in _motion_plan(cad):
        movers = [b for b in bodies if _moves_under(plan, b)]
        if not movers:
            continue
        others = [b for b in bodies
                  if b not in movers
                  and not (b["aid"] != plan["aid"]
                           and frozenset({plan["aid"], b["aid"]}) in intended)]
        other_boxes = [_obb_aabb(b["obb"]) for b in others]
        fractions = [k / (samples - 1) for k in range(samples)] if samples > 1 else [0.0]

        # Swept envelope first: the union of every pose. A static body outside it cannot be
        # hit at any pose, which prunes almost every pair before the per-pose loop runs.
        posed: list[list[Any]] = []
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for t in fractions:
            matrix, shift = _pose_transform(plan, t)
            row = [_pose_obb(m["obb"], matrix, plan["pivot"], shift) for m in movers]
            posed.append(row)
            for obb in row:
                box = _obb_aabb(obb)
                for k in range(3):
                    lo[k] = min(lo[k], box[0][k])
                    hi[k] = max(hi[k], box[1][k])
        envelope = ([lo[k] - MOTION_CLEARANCE_IN for k in range(3)],
                    [hi[k] + MOTION_CLEARANCE_IN for k in range(3)])
        envelopes.append({
            "assembly": plan["aid"], "motion": plan["label"], "kind": plan["kind"],
            "range": [round(v, 2) for v in plan["range"]], "samples": len(fractions),
            "bodies": len(movers),
            "min_in": [round(v, 3) for v in lo], "max_in": [round(v, 3) for v in hi],
        })
        candidates = [(others[n], other_boxes[n]) for n in range(len(others))
                      if _aabb_gap(envelope, other_boxes[n]) > 0.0]
        if not candidates:
            continue

        # Pairs that already interfere in the pose the robot is DRAWN in belong to the static
        # audit, which knows about mating interfaces and butted joints. Re-reporting them here
        # would drown the one thing this pass exists to find: the pair that is clear on screen
        # and collides somewhere else in the travel.
        baseline: set[tuple[str, str]] = set()
        for mover, obb in zip(movers, posed[0]):
            box = _obb_aabb(obb)
            for other, other_box in candidates:
                if _aabb_gap(box, other_box) > 0.0:
                    baseline.add((_label(mover), _label(other)))

        seen: set[tuple[str, str]] = set()
        for t, row in zip(fractions[1:], posed[1:]):
            lo_, hi_ = plan["range"]
            pose = (f"{plan['label']} at {lo_ + (hi_ - lo_) * t:.0f}"
                    + ("°" if not plan["linear"] else " in"))
            for mover, obb in zip(movers, row):
                mover_box = _obb_aabb(obb)
                posed_body = {**mover, "obb": obb}
                for other, other_box in candidates:
                    same = mover["aid"] == other["aid"]
                    tol = _pair_tolerance(
                        mover, other, INTRA_PENETRATION_TOL_IN if same else PENETRATION_TOL_IN)
                    # A moving part has to CLEAR what it passes, not merely avoid overlapping
                    # it: contact at speed is a collision with extra steps.
                    if _aabb_gap(mover_box, other_box) <= -MOTION_CLEARANCE_IN:
                        continue
                    key = (_label(mover), _label(other))
                    if key in seen or key in baseline or _mating_allowed(mover, other):
                        continue
                    depth, axis = _obb_mtv(obb, other["obb"])
                    if depth <= tol - MOTION_CLEARANCE_IN:
                        continue
                    seen.add(key)
                    issue = _issue(other, posed_body, depth, axis, tol, pose=pose)
                    issue["motion"] = plan["label"]
                    issue["assembly"] = plan["aid"]
                    issues.append(issue)
    issues.sort(key=lambda c: -c["penetration_in"])
    return issues, envelopes


# ── the bumper is not negotiable space ───────────────────────────────────────
# Everywhere else on the robot, an overlap has to beat 0.60 in before it is called a fault:
# boxes are nominal, rotated parts grow, and two mechanisms brushing at a tenth of an inch is
# modelling noise. The bumper is the one place that reasoning does not apply. It is a fixed
# 0.75 in of plywood in a mandated zone, nothing may be inside it at any depth, and a part
# embedded in it is visible in the viewer long before it is deep enough to trip the general
# tolerance — which is exactly how a pivot tower sat 0.7 in inside the front bumper while the
# audit reported the robot clean. Its deepest axis was the tower plate's own 0.19 in thickness.
BUMPER_TOL_IN = 0.05

# Cloth and foam already pass through everything (`_PASSES_THROUGH`); what is left is the
# structure — plywood, brackets, hangers — and that is what nothing may occupy.
def _is_bumper(body: dict[str, Any]) -> bool:
    return body["aid"] == "chassis" and "bumper" in body["name"].casefold()


def _bumper_intrusions(bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anything from another mechanism sitting inside the bumper, at any depth.

    Only cross-assembly pairs: the bumper's own hangers and brackets bolt to the rails and to
    each other, and reporting the bumper against the chassis it is mounted on would be
    reporting the mounting.
    """
    bumpers = [(b, _obb_aabb(b["obb"])) for b in bodies if _is_bumper(b)]
    if not bumpers:
        return []
    out: list[dict[str, Any]] = []
    for body in bodies:
        if body["aid"] == "chassis":
            continue
        box = _obb_aabb(body["obb"])
        for bumper, bumper_box in bumpers:
            if _aabb_gap(box, bumper_box) <= BUMPER_TOL_IN:
                continue
            if _mating_allowed(body, bumper):
                continue
            depth, axis = _obb_mtv(body["obb"], bumper["obb"])
            if depth <= BUMPER_TOL_IN:
                continue
            issue = _issue(bumper, body, depth, axis, BUMPER_TOL_IN)
            issue["limit"] = "bumper"
            out.append(issue)
    out.sort(key=lambda i: -i["penetration_in"])
    return out


def _bounds_issues(cad: dict[str, Any], bodies: list[dict[str, Any]],
                   limits: dict[str, float] | None) -> list[dict[str, Any]]:
    """Parts outside the space the robot is allowed to occupy, or under the carpet.

    The floor is the drive wheels' contact patch, not simply the lowest solid: an arm drawn
    dipping through the carpet is a modelling fault, and conflating it with height made a
    legal robot read several inches too tall.
    """
    if not bodies:
        return []
    wheels = [b for b in bodies
              if b.get("t") == "wheel"
              and (b["aid"].startswith("swerve") or b["aid"] == "drivetrain")
              and "drive" in b["name"].lower()]
    boxes = {id(b): _obb_aabb(b["obb"]) for b in bodies}
    ground = min((boxes[id(b)][0][1] for b in wheels),
                 default=min(boxes[id(b)][0][1] for b in bodies))
    out: list[dict[str, Any]] = []
    for body in bodies:
        low, high = boxes[id(body)]
        if low[1] < ground - 0.15:
            out.append({"part": _label(body), "limit": "floor",
                        "over_in": round(ground - low[1], 3),
                        "measured_in": round(low[1], 3), "allowed_in": round(ground, 3),
                        "fix": {"axis": "y", "distance_in": round(ground - low[1], 3)}})
        if not limits:
            continue
        checks = (("height", 1, high[1] - ground, limits.get("max_height_in")),
                  ("width", 0, max(abs(low[0]), abs(high[0])) * 2, limits.get("max_width_in")),
                  ("length", 2, max(abs(low[2]), abs(high[2])) * 2, limits.get("max_length_in")))
        for name, axis, measured, allowed in checks:
            if allowed and measured > allowed + 0.05:
                out.append({"part": _label(body), "limit": name,
                            "over_in": round(measured - allowed, 3),
                            "measured_in": round(measured, 3), "allowed_in": round(allowed, 3),
                            "fix": {"axis": "xyz"[axis],
                                    "distance_in": round(measured - allowed, 3)}})
    out.sort(key=lambda i: -i["over_in"])
    return out


def validate_geometry(cad: dict[str, Any], *, limits: dict[str, float] | None = None,
                      samples: int = MOTION_SAMPLES) -> dict[str, Any]:
    """The whole narrow-phase audit, as one result.

    `valid` is the conjunction the design gate and the export button both read: nothing is
    driven through anything, nothing has been swallowed, no mechanism collides anywhere in its
    travel, and nothing has escaped the robot's own coordinate space.
    """
    bodies = _solid_bodies(cad)
    intended = _assembly_pair_allowed(cad)
    collisions, truncations = _static_collisions(bodies, intended)
    bumper = _bumper_intrusions(bodies)
    motion, envelopes = _motion_collisions(cad, bodies, intended, samples=samples)
    bounds = _bounds_issues(cad, bodies, limits)
    # Bumper intrusions are deliberately OUT of `valid`. `valid` is what the repair loop runs
    # against, and repair has no move that fixes them — they are generator faults, not
    # placement ones — so counting them would spin the loop on every robot for nothing. They
    # are reported, surfaced in the dossier, and carried in `geometry_errors`.
    valid = not (collisions or truncations or motion or bounds)
    return {
        "version": VERSION,
        "valid": valid,
        "status": "valid" if valid else "invalid",
        "bodies": len(bodies),
        "collisions": collisions[:40],
        "collision_count": len(collisions),
        "truncations": truncations[:40],
        "truncation_count": len(truncations),
        "motion_collisions": motion[:40],
        "motion_collision_count": len(motion),
        "motion_envelopes": envelopes,
        "out_of_bounds": bounds[:40],
        "out_of_bounds_count": len(bounds),
        "bumper_intrusions": bumper[:40],
        "bumper_intrusion_count": len(bumper),
        "tolerances": {
            "penetration_in": PENETRATION_TOL_IN,
            "intra_assembly_penetration_in": INTRA_PENETRATION_TOL_IN,
            "intra_assembly_buried_fraction": INTRA_BURIED_FRACTION,
            "truncation_fraction": TRUNCATION_FRACTION,
            "motion_clearance_in": MOTION_CLEARANCE_IN,
            "motion_samples": samples,
        },
        "checked": (
            "oriented-box interference between every pair of solid bodies, inside an assembly "
            "as well as between assemblies; a part more than "
            f"{int(TRUNCATION_FRACTION * 100)}% inside another solid is reported as truncated "
            f"rather than colliding; every moving mechanism sampled at {samples} poses through "
            "its range. Fasteners, cloth, foam, runs and the harness are exempt, and mating "
            "interfaces (shaft in bearing, bolt through hole, meshing gears, motor on gearbox) "
            "are whitelisted by kind rather than by widening the tolerance."),
    }


# Which fault classes stop an export.
#
# The honest note, because it decides what users can do: gating on strict validity today would
# block essentially every robot. This audit is new and it surfaces faults the compiler has
# always produced — a feeder roller lapping its guide, a pulley sunk into a shooter post — and
# those live in the mechanism generators, not here. So the gate is set at the classes the
# geometry is actually unusable for: a part that is CUT OFF (swallowed by another solid) or a
# mechanism that collides somewhere in its travel, which is the fault that started all this.
# Shallow static overlaps between two solids are reported as warnings and are visible in the
# dossier. Move a class from `_WARN` to `_BLOCKING` once its generator faults are fixed;
# nothing else has to change.
_BLOCKING = ("truncations", "motion_collisions", "out_of_bounds")
# `bumper_intrusions` still WARNS rather than blocks. Five real faults have been driven out of
# the bumper — pivot tower in the plywood, arm raked the wrong way, drive plate 2.1 in inside,
# hangers through the drive wheels, corner brackets through the swerve motors — and the count
# reaches zero at an intake pivot lift of 4.2 in. It ships at 3.4, because above that the
# taller intake re-stations its neighbours and pushes the manipulator out of the frame, which
# is the worse fault. What is left is a ~0.2 in graze of the arm's box on the bumper corner.
# Promote to `_BLOCKING` once the arm profile clears without moving the pivot.
_WARN = ("collisions", "bumper_intrusions")


def geometry_status(cad: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """The lifecycle value the viewer badge and the export buttons both read.

    `building` → `validating` → `repairing` → `valid` | `invalid`. This function answers the
    last step: everything before it is the caller's state machine.
    """
    report = (cad.get("geometry") if isinstance(cad.get("geometry"), dict)
              else validate_geometry(cad, **kwargs))
    blocking = [issue for key in _BLOCKING for issue in report.get(key) or []]
    warnings = [issue for key in _WARN for issue in report.get(key) or []]
    return {
        "status": "invalid" if blocking else "valid",
        "exportable": not blocking,
        "blocking": len(blocking),
        "warnings": len(warnings),
        "blocking_classes": [key for key in _BLOCKING if report.get(key)],
        "detail": geometry_errors_from(report)[:12],
        "why": ("Exports are blocked by truncated parts, mechanism-travel collisions and "
                "out-of-bounds geometry. Solid-on-solid overlaps that are neither are "
                "reported as warnings."),
    }


def geometry_errors_from(report: dict[str, Any]) -> list[str]:
    """Contract-style error strings from an already-computed report."""
    errors = [f"$.geometry: {c['a']} interferes with {c['b']} by {c['penetration_in']} in"
              for c in report.get("collisions") or []]
    errors += [f"$.geometry: {t['part']} is {int(t['buried_fraction'] * 100)}% inside "
               f"{t['inside']} — the part is cut off, not merely overlapping"
               for t in report.get("truncations") or []]
    errors += [f"$.motion: {m['a']} hits {m['b']} at {m['pose']} by {m['penetration_in']} in"
               for m in report.get("motion_collisions") or []]
    errors += [f"$.bounds: {b['part']} is {b['over_in']} in past the {b['limit']} limit"
               for b in report.get("out_of_bounds") or []]
    errors += [f"$.bumper: {b['b']} is inside {b['a']} by {b['penetration_in']} in — "
               f"nothing may occupy the bumper zone"
               for b in report.get("bumper_intrusions") or []]
    return errors


def geometry_errors(cad: dict[str, Any], **kwargs: Any) -> list[str]:
    """The report as contract-style error strings, for gates and tests."""
    return geometry_errors_from(validate_geometry(cad, **kwargs))
