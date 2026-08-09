"""Shared transmission geometry: tooth outlines, belt wrap loops, mesh checks.

One implementation feeds three consumers so they cannot drift apart:
the STEP worker extrudes these outlines into B-rep solids, the validator in
cad_contract measures the same radii the outlines are drawn from, and the tests
exercise the math without needing cadquery installed. The three.js viewer keeps
its own JS copy of the tooth profile; its proportions are asserted against this
module's constants in the test suite rather than shared at runtime.

All dimensions are inches. Outlines are closed 2D polylines (last point is NOT
repeated) wound counter-clockwise in the part's cross-section plane.
"""
from __future__ import annotations

import math
from typing import Any

# Tooth proportions, as fractions of pitch radius. These deliberately match the
# viewer's makeGear() so the browser preview and the STEP download show the same
# tooth. They are display-honest trapezoids, not involutes — see fidelity notes.
GEAR_ADDENDUM_F = 0.085
GEAR_DEDENDUM_F = 0.11

# Half-widths of tip / pitch / root, as fractions of the angular pitch.
_GEAR_TIP_F, _GEAR_PITCH_F, _GEAR_ROOT_F = 0.17, 0.26, 0.30
# Sprocket teeth are pointier and taller than gear teeth.
_SPKT_TIP_F, _SPKT_PITCH_F, _SPKT_ROOT_F = 0.06, 0.22, 0.30
SPKT_ADDENDUM_F, SPKT_DEDENDUM_F = 0.14, 0.12

BELT_THICKNESS_IN = 0.12     # HTD belt section half-height is thickness/2
CHAIN_THICKNESS_IN = 0.26    # #25 roller chain reads thicker than a belt
FALLBACK_WRAP_R_IN = 0.5     # endpoint did not resolve to a pulley/sprocket


def _toothed_outline(teeth: int, pitch_r: float, tip_f: float, pitch_f: float,
                     root_f: float, add_f: float, ded_f: float) -> list[tuple[float, float]]:
    ro, rp, rr = pitch_r * (1 + add_f), pitch_r, pitch_r * (1 - ded_f)
    step = math.tau / teeth
    t_tip, t_pit, t_root = step * tip_f, step * pitch_f, step * root_f
    pts: list[tuple[float, float]] = []
    for i in range(teeth):
        a = i * step
        for ang, r in ((a - t_root, rr), (a - t_pit, rp), (a - t_tip, ro), (a + t_tip, ro),
                       (a + t_pit, rp), (a + t_root, rr), (a + step * 0.5 - t_root, rr)):
            pts.append((math.cos(ang) * r, math.sin(ang) * r))
    return pts


def gear_outline(teeth: int, pd: float) -> list[tuple[float, float]]:
    """Closed spur-gear outline: trapezoidal teeth about the true pitch circle."""
    return _toothed_outline(max(int(teeth), 6), pd / 2.0,
                            _GEAR_TIP_F, _GEAR_PITCH_F, _GEAR_ROOT_F,
                            GEAR_ADDENDUM_F, GEAR_DEDENDUM_F)


def sprocket_outline(teeth: int, pd: float) -> list[tuple[float, float]]:
    """Closed sprocket outline: pointier, taller teeth than a gear's."""
    return _toothed_outline(max(int(teeth), 8), pd / 2.0,
                            _SPKT_TIP_F, _SPKT_PITCH_F, _SPKT_ROOT_F,
                            SPKT_ADDENDUM_F, SPKT_DEDENDUM_F)


def pulley_outline(teeth: int, pd: float) -> list[tuple[float, float]]:
    """Closed HTD pulley outline: the tooth *grooves* cut inward from the OD."""
    r = pd / 2.0
    teeth = max(int(teeth), 10)
    step = math.tau / teeth
    groove_half, depth = step * 0.16, min(0.05, r * 0.12)
    pts: list[tuple[float, float]] = []
    for i in range(teeth):
        a = i * step
        for ang, rr in ((a - step * 0.5 + groove_half, r), (a - groove_half, r),
                        (a - groove_half * 0.7, r - depth), (a + groove_half * 0.7, r - depth),
                        (a + groove_half, r)):
            pts.append((math.cos(ang) * rr, math.sin(ang) * rr))
    return pts


def belt_loop(r1: float, r2: float, dist: float, thickness: float,
              segments: int = 18) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    """Outer and inner boundary of a belt/chain loop wrapping two circles.

    Circle 1 sits at the origin, circle 2 at (dist, 0); r1/r2 are the wrap
    (pitch) radii. Returns None when the geometry cannot wrap: coincident
    centres, or one pulley swallowing the other (no external tangent).
    """
    if dist <= 1e-6 or dist <= abs(r1 - r2):
        return None
    half = thickness / 2.0

    def boundary(rr1: float, rr2: float) -> list[tuple[float, float]]:
        rr1, rr2 = max(rr1, 0.02), max(rr2, 0.02)
        phi = math.acos(max(-1.0, min(1.0, (rr1 - rr2) / dist)))
        pts: list[tuple[float, float]] = []
        # far side of circle 1: from +phi round the back to -phi (through pi)
        for k in range(segments + 1):
            a = phi + (math.tau - 2 * phi) * k / segments
            pts.append((math.cos(a) * rr1, math.sin(a) * rr1))
        # near side of circle 2: from -phi through 0 to +phi
        for k in range(segments + 1):
            a = -phi + (2 * phi) * k / segments
            pts.append((dist + math.cos(a) * rr2, math.sin(a) * rr2))
        return pts

    return boundary(r1 + half, r2 + half), boundary(r1 - half, r2 - half)


def _rot_axis(rot: list[float] | None) -> tuple[float, float, float]:
    """Unit axis of a feature's local Y after its `rot` (degrees, applied XYZ).

    Only the axis *line* matters to mesh checks, so sign conventions are moot;
    generator rotations are axis-aligned quarter turns in practice.
    """
    if not rot:
        return (0.0, 1.0, 0.0)
    rx, ry, rz = (math.radians(v) for v in rot)
    x, y, z = 0.0, 1.0, 0.0
    y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
    x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
    x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    return (x / n, y / n, z / n)


def _sub(a: list[float], b: list[float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(a, a))


def _pitch_r(f: dict[str, Any]) -> float:
    pd = f.get("pd")
    if pd:
        return float(pd) / 2.0
    if f.get("teeth") and f.get("dp"):
        return float(f["teeth"]) / float(f["dp"]) / 2.0
    if f.get("dia"):                        # drums and sheaves wrap at their surface
        return float(f["dia"]) / 2.0
    return FALLBACK_WRAP_R_IN


# What a belt, chain or rope can wrap: toothed transmission parts, plus winch
# drums and sheave-style tensioners (a rope's endpoints are a drum and a sheave).
_WRAP_KINDS = ("pulley", "sprocket", "gear", "drum", "tensioner")


def resolve_wrap(features: list[dict[str, Any]], belt: dict[str, Any],
                 tol: float = 0.35) -> tuple[float | None, float | None,
                                             tuple[float, float, float] | None]:
    """Wrap radii and axis for a belt feature.

    The radii are the pitch radii of whatever the two endpoints land on; the
    axis is the resolved pulley's rotation axis, which is the normal of the
    plane the loop lies in. An endpoint that lands on nothing returns None for
    its radius — that is itself a modelling finding, reported by
    transmission_issues()."""
    found: list[dict[str, Any] | None] = []
    for end in (belt.get("at"), belt.get("to")):
        best, best_d = None, tol
        for f in features:
            if f.get("t") not in _WRAP_KINDS or f is belt:
                continue
            d = _norm(_sub(list(end), list(f["at"])))
            if d < best_d:
                best, best_d = f, d
        found.append(best)
    axis = None
    for f in found:
        if f is not None:
            axis = _rot_axis(f.get("rot"))
            break
    return (_pitch_r(found[0]) if found[0] else None,
            _pitch_r(found[1]) if found[1] else None, axis)


def resolve_wrap_radii(features: list[dict[str, Any]], belt: dict[str, Any],
                       tol: float = 0.35) -> tuple[float | None, float | None]:
    r1, r2, _ = resolve_wrap(features, belt, tol)
    return r1, r2


def transmission_issues(assemblies: list[dict[str, Any]],
                        mesh_tol: float = 0.06) -> list[dict[str, Any]]:
    """Measure the transmissions actually generated and report what fails.

    Two checks, both against the same radii the parts are drawn from:
    - gear pairs on parallel, coplanar axes that sit close enough to claim a
      mesh must sit at exactly the sum of their pitch radii (gap => not
      meshing, interference => teeth through teeth);
    - each belt/chain endpoint must land on a pulley/sprocket centre.
    """
    issues: list[dict[str, Any]] = []
    for asm in assemblies:
        feats = asm.get("features") or []
        gears = [f for f in feats if f.get("t") == "gear"]
        for i, ga in enumerate(gears):
            for gb in gears[i + 1:]:
                axis_a, axis_b = _rot_axis(ga.get("rot")), _rot_axis(gb.get("rot"))
                if abs(_dot(axis_a, axis_b)) < 0.985:          # not parallel: bevel territory
                    continue
                sep = _sub(gb["at"], ga["at"])
                along = _dot(sep, axis_a)
                if abs(along) > 0.35:                          # different planes: stacked stages
                    continue
                radial = math.sqrt(max(_dot(sep, sep) - along * along, 0.0))
                want = _pitch_r(ga) + _pitch_r(gb)
                if radial > want * 1.30 or radial < 1e-6:      # not claiming to mesh
                    continue
                err = radial - want
                if abs(err) > mesh_tol:
                    issues.append({
                        "type": "gear_mesh", "assembly": asm.get("id"),
                        "parts": [ga.get("n"), gb.get("n")],
                        "centre_distance_in": round(radial, 3),
                        "required_in": round(want, 3),
                        "detail": (f"{ga.get('n')} and {gb.get('n')} sit {radial:.3f} in apart "
                                   f"but their pitch radii sum to {want:.3f} in — "
                                   + ("a gap that large means they do not mesh."
                                      if err > 0 else "that much interference drives tooth through tooth.")),
                    })
        # Pulley/sprocket bodies share one belt plane but may never occupy the same radial
        # space. This catches the visually convincing but impossible case where two toothed
        # solids render through each other even though every belt endpoint is valid.
        wheels = [f for f in feats if f.get("t") in ("pulley", "sprocket")]
        for i, wa in enumerate(wheels):
            for wb in wheels[i + 1:]:
                axis_a, axis_b = _rot_axis(wa.get("rot")), _rot_axis(wb.get("rot"))
                if abs(_dot(axis_a, axis_b)) < 0.985:
                    continue
                sep = _sub(wb["at"], wa["at"])
                along = _dot(sep, axis_a)
                if abs(along) > (float(wa.get("w", 0.4)) + float(wb.get("w", 0.4))) / 2:
                    continue
                radial = math.sqrt(max(_dot(sep, sep) - along * along, 0.0))
                required = _pitch_r(wa) + _pitch_r(wb)
                if radial > 1e-6 and radial < required - mesh_tol:
                    issues.append({
                        "type": "pulley_interference", "assembly": asm.get("id"),
                        "parts": [wa.get("n"), wb.get("n")],
                        "centre_distance_in": round(radial, 3),
                        "required_in": round(required, 3),
                        "detail": (f"{wa.get('n')} and {wb.get('n')} overlap in the same "
                                   f"transmission plane: {radial:.3f} in between centres, "
                                   f"but at least {required:.3f} in is required."),
                    })
        for f in feats:
            if f.get("t") != "belt":
                continue
            r1, r2 = resolve_wrap_radii(feats, f)
            for end_name, r in (("start", r1), ("end", r2)):
                if r is None:
                    issues.append({
                        "type": "belt_endpoint", "assembly": asm.get("id"),
                        "parts": [f.get("n")],
                        "detail": (f"the {end_name} of {f.get('n')} does not land on any "
                                   "pulley or sprocket centre, so the loop has nothing to wrap."),
                    })
    return issues
