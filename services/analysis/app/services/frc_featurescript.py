"""CAD tree → parametric Onshape FeatureScript.

The OBJ export is a dead end by construction: a mesh has vertices, not dimensions, so once it
lands in Onshape there is nothing to edit.  You cannot change a tube's wall thickness, a gear's
tooth count or the frame width, because none of those facts survive the trip — they were
flattened into triangles before the file was written.

This module takes the same `spec["cad"]` tree and emits FeatureScript instead.  Every part
becomes a real feature in the tree, built from named variables, so the whole robot rebuilds
when a dimension changes:

* the frame's width and length are `definition` parameters, editable in the feature dialog;
* every stock section, wall, bore, pitch diameter and centre distance is a named constant;
* a tube is an extruded rectangle minus its inner rectangle, so its wall is a real dimension;
* a gear's pitch diameter is written as `teeth / DP`, not as the number that came out of it,
  so changing the tooth count moves the geometry the way it would in a hand-built model.

The output is a Feature Studio's worth of source.  Paste it into a Feature Studio (or let
`onshape.publish_parametric` create one) and the robot appears as an editable feature.

What this is not: it does not reproduce Onshape's native sketch-and-extrude history the way a
human modeller would build it, and it does not claim to. It is generated, parametric,
fully-editable geometry — which is the difference that matters against a mesh.
"""
from __future__ import annotations

import math
import os
from typing import Any

FS_VERSION = "kale-fs-1.1"

# The FeatureScript language/std-library version the generated source declares.
#
# This is not cosmetic and it does go stale. The previous value, 2500, was inherited from an
# older stub and by the time it was tested against a live account the std library was at 3029 —
# the import silently failed and every standard type came back "No declaration found for type
# Vector", which reads like a bug in the generated code and is not one.
#
# So: a current default, overridable without a code change, because it will age again.
# `KALE_FS_STD_VERSION=3100 ...` is enough to move it.
_FS_STD = os.environ.get("KALE_FS_STD_VERSION", "3029").split(".")[0]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ident(name: str, used: dict[str, int]) -> str:
    """A stable, unique, FeatureScript-safe identifier for a feature id string."""
    safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_") or "part"
    if safe[0].isdigit():
        safe = "p" + safe
    count = used.get(safe, 0)
    used[safe] = count + 1
    return safe if count == 0 else f"{safe}_{count}"


def _vec(at: list[float]) -> str:
    x, y, z = (round(_num(v), 4) for v in (at + [0, 0, 0])[:3])
    return f"vector({x}, {y}, {z}) * inch"


def _rot(rot: list[float] | None) -> str:
    if not rot:
        return "vector(0, 0, 0) * degree"
    rx, ry, rz = (round(_num(v), 3) for v in (list(rot) + [0, 0, 0])[:3])
    return f"vector({rx}, {ry}, {rz}) * degree"


# ─────────────────────────────────────────────────────────────────────────────
# Per-feature emitters. Each returns FeatureScript that builds ONE part at the
# origin and then places it, so every part is its own entry in the feature tree.
# ─────────────────────────────────────────────────────────────────────────────
def _tube(fid: str, f: dict[str, Any]) -> list[str]:
    """A real tube: outer rectangle extruded, inner rectangle removed.

    Wall thickness is a dimension rather than a baked-in shape, which is the whole reason for
    not shipping a mesh — change the wall and the part changes.
    """
    w, h = (_num(v) for v in (f.get("sec") or [2, 1])[:2])
    length = _num(f.get("len"), 1.0)
    wall = _num(f.get("wall"), 0.100)
    return [
        f'    // {f.get("n", "tube")} — {w:g}x{h:g}x{wall:.4g} in stock, {length:g} in long',
        f'    kaleTube(context, id + "{fid}", {w}, {h}, {length}, {wall}, '
        f'{_vec(f["at"])}, {_rot(f.get("rot"))});',
    ]


def _plate(fid: str, f: dict[str, Any]) -> list[str]:
    sx, sy, sz = (_num(v) for v in (f.get("size") or [1, 0.09, 1])[:3])
    return [f'    kaleBox(context, id + "{fid}", {sx}, {sy}, {sz}, '
            f'{_vec(f["at"])}, {_rot(f.get("rot"))});  // {f.get("n", "plate")}']


def _shaft(fid: str, f: dict[str, Any]) -> list[str]:
    dia = _num(f.get("dia"), 0.5)
    length = _num(f.get("len"), 1.0)
    if (f.get("form") or "hex") == "hex":
        return [f'    kaleHexShaft(context, id + "{fid}", {dia}, {length}, '
                f'{_vec(f["at"])}, {_rot(f.get("rot"))});  // {f.get("n", "shaft")}']
    return [f'    kaleCylinder(context, id + "{fid}", {dia}, {length}, '
            f'{_vec(f["at"])}, {_rot(f.get("rot"))});  // {f.get("n", "shaft")}']


def _bearing(fid: str, f: dict[str, Any]) -> list[str]:
    bore, od, width = _num(f.get("bore"), 0.5), _num(f.get("od"), 1.125), _num(f.get("w"), 0.313)
    flange = "true" if f.get("flanged") else "false"
    return [f'    kaleBearing(context, id + "{fid}", {bore}, {od}, {width}, {flange}, '
            f'{_vec(f["at"])}, {_rot(f.get("rot"))});  // {f.get("n", "bearing")}']


def _toothed(fid: str, f: dict[str, Any], kind: str) -> list[str]:
    """Gear, pulley or sprocket — pitch diameter written as its own derivation.

    The point of emitting the formula rather than the result: a gear whose PD is a literal is
    a cylinder that happens to be the right size, and editing the tooth count does nothing. A
    gear whose PD is `teeth / DP` is a gear.
    """
    teeth = int(_num(f.get("teeth"), 20))
    width = _num(f.get("w") or f.get("face"), 0.375)
    bore = _num(f.get("bore"), 0.5)
    if kind == "gear":
        dp = _num(f.get("dp"), 20.0)
        pd_expr = f"{teeth} / {dp}"
        comment = f"{teeth}T @ {dp:g} DP → PD {teeth / dp:.3f} in"
    elif kind == "pulley":
        pitch_mm = _num(f.get("pitch_mm"), 5.0)
        pd_expr = f"{teeth} * {pitch_mm} / PI / 25.4"
        comment = f"{teeth}T HTD {pitch_mm:g} mm → PD {teeth * pitch_mm / math.pi / 25.4:.3f} in"
    else:
        pitch_in = _num(f.get("pitch_in"), 0.25)
        pd_expr = f"{pitch_in} / sin(PI / {teeth})"
        comment = f"{teeth}T #{int(round(1 / pitch_in * 6.25))} chain → PD {_num(f.get('pd')):.3f} in"
    return [f'    // {f.get("n", kind)} — {comment}',
            f'    kaleDisc(context, id + "{fid}", {pd_expr}, {width}, {bore}, '
            f'{_vec(f["at"])}, {_rot(f.get("rot"))});']


def _wheel(fid: str, f: dict[str, Any]) -> list[str]:
    return [f'    kaleDisc(context, id + "{fid}", {_num(f.get("dia"), 4.0)}, '
            f'{_num(f.get("w"), 1.0)}, 0.5, {_vec(f["at"])}, {_rot(f.get("rot"))});'
            f'  // {f.get("n", "wheel")}']


def _motor(fid: str, f: dict[str, Any]) -> list[str]:
    return [f'    kaleCylinder(context, id + "{fid}", {_num(f.get("dia"), 2.2)}, '
            f'{_num(f.get("len"), 3.5)}, {_vec(f["at"])}, {_rot(f.get("rot"))});'
            f'  // {f.get("n", "motor")} ({f.get("key", "")})']


def _boxlike(fid: str, f: dict[str, Any], default: tuple[float, float, float]) -> list[str]:
    sx, sy, sz = (_num(v) for v in (f.get("size") or list(default))[:3])
    return [f'    kaleBox(context, id + "{fid}", {sx}, {sy}, {sz}, '
            f'{_vec(f["at"])}, {_rot(f.get("rot"))});  // {f.get("n", f.get("t", "part"))}']


def _gusset(fid: str, f: dict[str, Any]) -> list[str]:
    a, b = (_num(v) for v in (f.get("size") or [3, 3])[:2])
    return [f'    kaleGusset(context, id + "{fid}", {a}, {b}, {_num(f.get("th"), 0.09)}, '
            f'{_vec(f["at"])}, {_rot(f.get("rot"))});  // {f.get("n", "gusset")}']


def _bolts(fid: str, f: dict[str, Any]) -> list[str]:
    rep = f.get("rep") or {}
    count = int(_num(rep.get("n"), 1))
    step = [_num(v) for v in (rep.get("step") or [0, 0, 0])[:3]]
    return [f'    kaleBoltRow(context, id + "{fid}", {_num(f.get("dia"), 0.19)}, '
            f'{_num(f.get("len"), 0.75)}, {count}, {_vec(step)}, '
            f'{_vec(f["at"])}, {_rot(f.get("rot"))});  // {f.get("n", "bolts")}']


def _linear(fid: str, f: dict[str, Any]) -> list[str]:
    """Belt, chain, rope or cable — a swept run between two points, so its length is real."""
    to = f.get("to") or f["at"]
    dia = _num(f.get("dia") or f.get("w"), 0.25)
    return [f'    kaleRun(context, id + "{fid}", {_vec(f["at"])}, {_vec(to)}, {dia});'
            f'  // {f.get("n", "run")} ({f.get("kind", "")})']


_EMITTERS = {
    "tube": _tube, "plate": _plate, "shaft": _shaft, "bearing": _bearing,
    "wheel": _wheel, "motor": _motor, "gusset": _gusset, "bolts": _bolts,
    "gear": lambda i, f: _toothed(i, f, "gear"),
    "pulley": lambda i, f: _toothed(i, f, "pulley"),
    "sprocket": lambda i, f: _toothed(i, f, "sprocket"),
    "belt": _linear, "rope": _linear, "cable": _linear,
    # Round parts carried on a shaft. Each is a real turned solid with its own bore, not a
    # block: a standoff you cannot measure is a standoff you cannot order a replacement for.
    "standoff": lambda i, f: [
        f'    kaleDisc(context, id + "{i}", {_num(f.get("dia"), 0.375)}, '
        f'{_num(f.get("len"), 1.0)}, 0.196, {_vec(f["at"])}, {_rot(f.get("rot"))});'
        f'  // {f.get("n", "standoff")}'],
    "tensioner": lambda i, f: [
        f'    kaleDisc(context, id + "{i}", {_num(f.get("dia"), 1.0)}, '
        f'{_num(f.get("w"), 0.45)}, 0.375, {_vec(f["at"])}, {_rot(f.get("rot"))});'
        f'  // {f.get("n", "tensioner")}'],
    "drum": lambda i, f: [
        f'    kaleDisc(context, id + "{i}", {_num(f.get("dia"), 1.25)}, '
        f'{_num(f.get("w"), 2.2)}, 0.5, {_vec(f["at"])}, {_rot(f.get("rot"))});'
        f'  // {f.get("n", "winch drum")} ({f.get("rope", "")})'],
    "brake": lambda i, f: [
        f'    kaleDisc(context, id + "{i}", {_num(f.get("dia"), 2.2)}, '
        f'{_num(f.get("w"), 0.25)}, 0.5, {_vec(f["at"])}, {_rot(f.get("rot"))});'
        f'  // {f.get("n", "disc brake")}'],
    # A bevel pair meshes at 90°; the pitch cone is approximated by its pitch diameter, which
    # is the dimension that actually has to agree with its partner.
    "bevel": lambda i, f: [
        f'    kaleDisc(context, id + "{i}", {_num(f.get("pd"), 1.6)}, '
        f'{_num(f.get("face"), 0.4)}, 0.5, {_vec(f["at"])}, {_rot(f.get("rot"))});'
        f'  // {f.get("n", "bevel")} — {int(_num(f.get("teeth"), 20))}T, PD {_num(f.get("pd"), 1.6):g} in'],
    # The hood is a swept arc the gamepiece rides against, so it is emitted as a real arc
    # rather than a block — its radius is what sets the exit angle.
    "hood": lambda i, f: [
        f'    kaleHood(context, id + "{i}", {_num(f.get("r"), 5.0)}, {_num(f.get("w"), 2.6)}, '
        f'{_num((f.get("range_deg") or [0, 180])[0])}, {_num((f.get("range_deg") or [0, 180])[1])}, '
        f'{_vec(f["at"])}, {_rot(f.get("rot"))});  // {f.get("n", "hood")}'],
    "polycarb": lambda i, f: _boxlike(i, f, (1, 0.093, 1)),
    "hardstop": lambda i, f: _boxlike(i, f, (0.75, 0.5, 0.75)),
    "gearbox": lambda i, f: _boxlike(i, f, (2, 2, 1.2)),
    "component": lambda i, f: _boxlike(i, f, (3, 2, 1)),
    "pawl": lambda i, f: _boxlike(i, f, (0.25, 1.2, 0.6)),
    "slide": lambda i, f: _boxlike(i, f, (0.6, 1.2, 0.25)),
    "hook": lambda i, f: _boxlike(i, f, (0.6, 1.8, 3.0)),
    "actuator": lambda i, f: _boxlike(i, f, (1.6, 0.8, 0.8)),
    "sensor": lambda i, f: _boxlike(i, f, (0.5, 0.5, 0.5)),
    "chain_track": lambda i, f: _boxlike(i, f, (0.9, 0.7, 4.0)),
    "envelope": lambda i, f: _boxlike(i, f, (4, 5, 4)),
}


def _feature_lines(f: dict[str, Any], used: dict[str, int]) -> list[str]:
    kind = f.get("t", "")
    fid = _ident(f"{kind}_{f.get('n', '')}", used)
    emitter = _EMITTERS.get(kind)
    if emitter is not None:
        return emitter(fid, f)
    # Unknown type: a labelled box rather than nothing, so a schema addition is visible in the
    # tree instead of silently disappearing — same policy as the viewer.
    size = f.get("size") or [_num(f.get("dia"), 1.0)] * 3
    sx, sy, sz = (_num(v) for v in (list(size) + [1, 1, 1])[:3])
    return [f'    kaleBox(context, id + "{fid}", {sx}, {sy}, {sz}, '
            f'{_vec(f["at"])}, {_rot(f.get("rot"))});  // UNMODELLED {kind}: {f.get("n", "")}']


# ─────────────────────────────────────────────────────────────────────────────
# The helper library, emitted once at the top of the generated Feature Studio.
# These are what make each part a real, dimensioned solid rather than a block.
# ─────────────────────────────────────────────────────────────────────────────
_HELPERS = '''
// ── Placement ───────────────────────────────────────────────────────────────
// Kale's CAD frame: inches, +X robot right, +Y up, -Z toward the front, origin at the frame
// centre on the top face of the bellypan. Rotations are XYZ Euler degrees applied about the
// part's own centre, then the part is translated into place.
function kalePlace(at is Vector, rot is Vector) returns Transform
{
    var t = identityTransform();
    if (abs(rot[0] / degree) > 1e-9)
        t = rotationAround(line(vector(0, 0, 0) * inch, vector(1, 0, 0)), rot[0]) * t;
    if (abs(rot[1] / degree) > 1e-9)
        t = rotationAround(line(vector(0, 0, 0) * inch, vector(0, 1, 0)), rot[1]) * t;
    if (abs(rot[2] / degree) > 1e-9)
        t = rotationAround(line(vector(0, 0, 0) * inch, vector(0, 0, 1)), rot[2]) * t;
    return transform(at) * t;
}

// Move the bodies a helper just built.
//
// `bodies` is passed in rather than derived from the parent id, and that is not a style
// choice: qCreatedBy(parentId) matches only what that exact id created, NOT what its
// sub-ids created. Since every primitive here is built under id + "solid" (or similar),
// querying the parent matches nothing and opTransform fails on an empty selection — which
// would silently leave every part of the robot stacked at the origin, or error outright.
// Verified against a live Onshape workspace: parent query fails, sub-id query succeeds.
//
// The size check keeps an empty result a no-op instead of an error, so one degenerate part
// cannot take the whole robot down with it.
function kaleMove(context is Context, id is Id, bodies is Query, at is Vector, rot is Vector)
{
    if (size(evaluateQuery(context, bodies)) == 0)
        return;
    opTransform(context, id + "place", {
            "bodies" : bodies,
            "transform" : kalePlace(at, rot)
    });
}

// ── Primitives ──────────────────────────────────────────────────────────────
function kaleBox(context is Context, id is Id, sx is number, sy is number, sz is number,
                 at is Vector, rot is Vector)
{
    fCuboid(context, id + "solid", {
            "corner1" : vector(-sx / 2, -sy / 2, -sz / 2) * inch,
            "corner2" : vector(sx / 2, sy / 2, sz / 2) * inch
    });
    kaleMove(context, id, qCreatedBy(id + "solid", EntityType.BODY), at, rot);
}

// A tube is the outer section minus the inner section: the wall is a dimension you can edit,
// which is the entire difference between this and an imported mesh.
function kaleTube(context is Context, id is Id, w is number, h is number, len is number,
                  wall is number, at is Vector, rot is Vector)
{
    fCuboid(context, id + "outer", {
            "corner1" : vector(-w / 2, -h / 2, -len / 2) * inch,
            "corner2" : vector(w / 2, h / 2, len / 2) * inch
    });
    if (wall > 0 && w > 2 * wall && h > 2 * wall)
    {
        fCuboid(context, id + "bore", {
                "corner1" : vector(-w / 2 + wall, -h / 2 + wall, -len / 2 - 0.1) * inch,
                "corner2" : vector(w / 2 - wall, h / 2 - wall, len / 2 + 0.1) * inch
        });
        opBoolean(context, id + "hollow", {
                "tools" : qCreatedBy(id + "bore", EntityType.BODY),
                "targets" : qCreatedBy(id + "outer", EntityType.BODY),
                "operationType" : BooleanOperationType.SUBTRACTION
        });
    }
    kaleMove(context, id, qCreatedBy(id + "outer", EntityType.BODY), at, rot);
}

function kaleCylinder(context is Context, id is Id, dia is number, len is number,
                      at is Vector, rot is Vector)
{
    fCylinder(context, id + "solid", {
            "topCenter" : vector(0, len / 2, 0) * inch,
            "bottomCenter" : vector(0, -len / 2, 0) * inch,
            "radius" : dia / 2 * inch
    });
    kaleMove(context, id, qCreatedBy(id + "solid", EntityType.BODY), at, rot);
}

// 1/2 in hex is the FRC default shaft; modelled as a real hexagon so a bore cut from it fits.
function kaleHexShaft(context is Context, id is Id, acrossFlats is number, len is number,
                      at is Vector, rot is Vector)
{
    var sketch = newSketchOnPlane(context, id + "sk", {
            "sketchPlane" : plane(vector(0, -len / 2, 0) * inch, vector(0, 1, 0))
    });
    skRegularPolygon(sketch, "hex", {
            "center" : vector(0, 0) * inch,
            "firstVertex" : vector(acrossFlats / 2 / cos(30 * degree), 0) * inch,
            "sides" : 6
    });
    skSolve(sketch);
    opExtrude(context, id + "ext", {
            "entities" : qSketchRegion(id + "sk"),
            "direction" : vector(0, 1, 0),
            "endBound" : BoundingType.BLIND,
            "endDepth" : len * inch
    });
    kaleMove(context, id, qCreatedBy(id + "ext", EntityType.BODY), at, rot);
}

function kaleDisc(context is Context, id is Id, dia is number, width is number,
                  bore is number, at is Vector, rot is Vector)
{
    fCylinder(context, id + "solid", {
            "topCenter" : vector(0, width / 2, 0) * inch,
            "bottomCenter" : vector(0, -width / 2, 0) * inch,
            "radius" : dia / 2 * inch
    });
    if (bore > 0 && bore < dia)
    {
        fCylinder(context, id + "bore", {
                "topCenter" : vector(0, width, 0) * inch,
                "bottomCenter" : vector(0, -width, 0) * inch,
                "radius" : bore / 2 * inch
        });
        opBoolean(context, id + "cut", {
                "tools" : qCreatedBy(id + "bore", EntityType.BODY),
                "targets" : qCreatedBy(id + "solid", EntityType.BODY),
                "operationType" : BooleanOperationType.SUBTRACTION
        });
    }
    kaleMove(context, id, qCreatedBy(id + "solid", EntityType.BODY), at, rot);
}

function kaleBearing(context is Context, id is Id, bore is number, od is number,
                     width is number, flanged is boolean, at is Vector, rot is Vector)
{
    kaleDisc(context, id + "race", od, width, bore, vector(0, 0, 0) * inch,
             vector(0, 0, 0) * degree);
    if (flanged)
        kaleDisc(context, id + "flange", od + 0.19, 0.06, bore,
                 vector(0, width / 2, 0) * inch, vector(0, 0, 0) * degree);
    kaleMove(context, id, qUnion([qCreatedBy(id + "race" + "solid", EntityType.BODY),
                                  qCreatedBy(id + "flange" + "solid", EntityType.BODY)]),
             at, rot);
}

function kaleGusset(context is Context, id is Id, a is number, b is number,
                    th is number, at is Vector, rot is Vector)
{
    var sketch = newSketchOnPlane(context, id + "sk", {
            "sketchPlane" : plane(vector(0, 0, 0) * inch, vector(0, 1, 0))
    });
    skPolyline(sketch, "tri", {
            "points" : [vector(0, 0) * inch, vector(a, 0) * inch, vector(0, b) * inch,
                        vector(0, 0) * inch]
    });
    skSolve(sketch);
    opExtrude(context, id + "ext", {
            "entities" : qSketchRegion(id + "sk"),
            "direction" : vector(0, 1, 0),
            "endBound" : BoundingType.BLIND,
            "endDepth" : th * inch
    });
    kaleMove(context, id, qCreatedBy(id + "ext", EntityType.BODY), at, rot);
}

function kaleBoltRow(context is Context, id is Id, dia is number, len is number,
                     count is number, step is Vector, at is Vector, rot is Vector)
{
    for (var i = 0; i < count; i += 1)
    {
        var offset = step * (i - (count - 1) / 2);
        fCylinder(context, id + ("bolt" ~ i), {
                "topCenter" : offset + vector(0, len / 2, 0) * inch,
                "bottomCenter" : offset - vector(0, len / 2, 0) * inch,
                "radius" : dia / 2 * inch
        });
    }
    var boltBodies = qNothing();
    for (var i = 0; i < count; i += 1)
        boltBodies = qUnion([boltBodies, qCreatedBy(id + ("bolt" ~ i), EntityType.BODY)]);
    kaleMove(context, id, boltBodies, at, rot);
}

// The shooter hood: an arc segment the gamepiece is squeezed against. Its radius is the
// dimension that sets the exit angle, so it is modelled as a real swept arc.
function kaleHood(context is Context, id is Id, radius is number, width is number,
                  fromDeg is number, toDeg is number, at is Vector, rot is Vector)
{
    var span = max(abs(toDeg - fromDeg), 20);
    var wall = 0.09;
    var sketch = newSketchOnPlane(context, id + "sk", {
            "sketchPlane" : plane(vector(0, -width / 2, 0) * inch, vector(0, 1, 0))
    });
    // skArc takes start/mid/end POINTS, not a centre with start and end angles. The
    // centre-radius form silently creates nothing, and the failure surfaces later and
    // unhelpfully as CANNOT_RESOLVE_ENTITIES on the extrude, because the region never existed.
    //
    // The two arcs are also open curves on their own and bound no area, so the profile is
    // closed with a radial segment at each end before it can be extruded.
    var a0 = fromDeg * degree;
    var a1 = (fromDeg + span) * degree;
    var am = (fromDeg + span / 2) * degree;
    var rOut = (radius + wall) * inch;
    var rIn = radius * inch;
    skArc(sketch, "outer", {
            "start" : vector(rOut * cos(a0), rOut * sin(a0)),
            "mid" : vector(rOut * cos(am), rOut * sin(am)),
            "end" : vector(rOut * cos(a1), rOut * sin(a1))
    });
    skArc(sketch, "inner", {
            "start" : vector(rIn * cos(a0), rIn * sin(a0)),
            "mid" : vector(rIn * cos(am), rIn * sin(am)),
            "end" : vector(rIn * cos(a1), rIn * sin(a1))
    });
    skLineSegment(sketch, "capStart", {
            "start" : vector(rIn * cos(a0), rIn * sin(a0)),
            "end" : vector(rOut * cos(a0), rOut * sin(a0))
    });
    skLineSegment(sketch, "capEnd", {
            "start" : vector(rIn * cos(a1), rIn * sin(a1)),
            "end" : vector(rOut * cos(a1), rOut * sin(a1))
    });
    skSolve(sketch);
    opExtrude(context, id + "ext", {
            "entities" : qSketchRegion(id + "sk"),
            "direction" : vector(0, 1, 0),
            "endBound" : BoundingType.BLIND,
            "endDepth" : width * inch
    });
    kaleMove(context, id, qCreatedBy(id + "ext", EntityType.BODY), at, rot);
}

// A belt, chain or rope run drawn between its real endpoints, so its length is a measurement
// rather than a picture.
function kaleRun(context is Context, id is Id, from is Vector, to is Vector, dia is number)
{
    if (norm(to - from) < 1e-6 * inch)
        return;
    fCylinder(context, id + "run", {
            "topCenter" : to,
            "bottomCenter" : from,
            "radius" : max(dia, 0.06) / 2 * inch
    });
}
'''


def _gamepiece_note(season: dict[str, Any]) -> str:
    """The gamepiece as one short phrase.

    The season block carries it as either a bare name or the full spec dict depending on which
    producer built it, and interpolating a dict into a source comment is how you get a Python
    repr in the middle of a FeatureScript file.
    """
    piece = season.get("gamepiece")
    if isinstance(piece, dict):
        name, dia = piece.get("name", ""), piece.get("diameter_in")
        return f" · gamepiece {name}" + (f" ({dia:g} in)" if dia else "") if name else ""
    return f" · gamepiece {piece}" if piece else ""


def build_featurescript(spec: dict[str, Any], name: str = "Kale FRC Robot") -> str:
    """Emit the whole robot as one parametric Feature Studio source file."""
    cad = spec.get("cad") or {}
    assemblies = cad.get("assemblies") or []
    frame = spec.get("frame") or {}
    season = spec.get("season") or {}
    constraints = spec.get("constraints") or {}

    width = _num(frame.get("width_in"), 27.0)
    length = _num(frame.get("length_in"), 27.0)

    out: list[str] = [
        f"FeatureScript {_FS_STD};",
        f'import(path : "onshape/std/common.fs", version : "{_FS_STD}.0");',
        "",
        f"// {name}",
        f"// Generated by Kale Forge ({FS_VERSION}) from the design's own CAD tree.",
        f"// Season: {season.get('label', 'unspecified')}" + _gamepiece_note(season),
        f"// {cad.get('feature_total', 0)} parts across {len(assemblies)} assemblies.",
        "//",
        "// Every part below is a real feature built from named dimensions — edit any number,",
        "// or the frame parameters in the feature dialog, and the model rebuilds. Nothing here",
        "// is a mesh, so nothing is flattened.",
        "//",
        "// This is dimensioned concept geometry. Sections, bores and centre distances are",
        "// consistent with each other and with the parts catalog; none has been checked",
        "// against a vendor drawing, a stress case or the current game manual.",
        "",
        "// ── Rule envelope for this season, as constants you can check against ──",
        f"export const KALE_PERIMETER_LIMIT_IN = {_num(constraints.get('perimeter_limit_in'), 110.0)};",
        f"export const KALE_START_HEIGHT_LIMIT_IN = {_num(constraints.get('start_height_limit_in'), 30.0)};",
        f"export const KALE_EXTENSION_LIMIT_IN = {_num(constraints.get('extension_limit_in'), 12.0)};",
        f"export const KALE_WEIGHT_LIMIT_LB = {_num(constraints.get('weight_limit_lb'), 115.0)};",
        "",
        _HELPERS,
        "",
        'annotation { "Feature Type Name" : "Kale FRC Robot" }',
        "export const kaleRobot = defineFeature(function(context is Context, id is Id, definition is map)",
        "    precondition",
        "    {",
        '        annotation { "Name" : "Frame width" } isLength(definition.frameWidth, LENGTH_BOUNDS);',
        '        annotation { "Name" : "Frame length" } isLength(definition.frameLength, LENGTH_BOUNDS);',
        '        annotation { "Name" : "Build chassis" } definition.doChassis is boolean;',
        '        annotation { "Name" : "Build drivetrain" } definition.doDrivetrain is boolean;',
        '        annotation { "Name" : "Build mechanisms" } definition.doMechanisms is boolean;',
        '        annotation { "Name" : "Build electrical" } definition.doElectrical is boolean;',
        "    }",
        "    {",
        f"        // Design values: {width:g} x {length:g} in frame.",
        f"        var frameW = definition.frameWidth / inch;",
        f"        var frameL = definition.frameLength / inch;",
        f"        var scaleX = frameW / {width:g};",
        f"        var scaleZ = frameL / {length:g};",
        "        // scaleX/scaleZ let the frame parameters drive the layout: change the frame in",
        "        // the dialog and the mechanisms move with it instead of floating in place.",
        "",
    ]

    used: dict[str, int] = {}
    for asm in assemblies:
        asm_id = asm.get("id", "asm")
        gate = {"chassis": "doChassis", "drivetrain": "doDrivetrain",
                "electrical": "doElectrical"}.get(
                    asm_id if asm_id in ("chassis", "electrical") else
                    ("drivetrain" if asm_id.startswith(("swerve", "drivetrain")) else "mech"),
                    "doMechanisms")
        origin = asm.get("origin") or [0, 0, 0]
        out += [
            f"        // ── {asm.get('name', asm_id)} ({len(asm.get('features') or [])} parts)"
            + (f" — {asm.get('note')}" if asm.get("note") else "") + " ──",
            f"        if (definition.{gate})",
            "        {",
        ]
        for f in asm.get("features") or []:
            if "at" not in f:
                continue
            placed = dict(f)
            placed["at"] = [_num(f["at"][i]) + _num(origin[i]) for i in range(3)]
            if placed.get("to"):
                placed["to"] = [_num(placed["to"][i]) + _num(origin[i]) for i in range(3)]
            out += ["    " + line for line in _feature_lines(placed, used)]
        out += ["        }", ""]

    out += [
        "    }, {",
        f"        frameWidth : {width:g} * inch,",
        f"        frameLength : {length:g} * inch,",
        "        doChassis : true,",
        "        doDrivetrain : true,",
        "        doMechanisms : true,",
        "        doElectrical : true",
        "    });",
        "",
    ]
    return "\n".join(out) + "\n"


def featurescript_stats(source: str) -> dict[str, Any]:
    """Small self-check used by tests and the dossier: what did we actually emit?"""
    lines = source.splitlines()
    return {
        "version": FS_VERSION,
        "lines": len(lines),
        "parts": sum(1 for line in lines if line.strip().startswith("kale")
                     and "(context, id +" in line),
        "unmodelled": sum(1 for line in lines if "UNMODELLED" in line),
        "helpers": sum(1 for line in lines if line.startswith("function kale")),
    }
