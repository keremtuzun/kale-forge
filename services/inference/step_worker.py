"""Design → STEP, in its own process.

Reads {"prompt": ..., "season": ...} on stdin, writes an AP214 STEP assembly to the path
given as argv[1], and prints {"name": ..., "parts": N} on stdout.

Why a worker and not an endpoint function: OpenCascade is a large native library with its
own failure modes (the llama-server lesson, again), and the Design Studio's modules live in
a package that is also called `app` — a subprocess gives both problems the same answer.

The solids are the CAD tree's own dimensioned geometry: every tube is a real hollow
section; gears, sprockets and pulleys carry their teeth as a single closed outline about
the true pitch diameter (display-honest trapezoids, not involutes — one extrusion, no
per-tooth booleans); belts and chains are wrapped loops around the pitch radii the
enrichment pass resolved from their endpoint pulleys. Motors, gearboxes and electronics
are catalog outer envelopes and their part names say so. Imported into Onshape everything
arrives as separate, named, editable solid bodies — never a frozen mesh.
"""
from __future__ import annotations

import json
import math
import os
import sys

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "apps", "site")
sys.path.insert(0, SITE)

import cadquery as cq  # noqa: E402

from app.services.cad_contract import expand_mirrors, feature_fidelity  # noqa: E402
from app.services.robot_spec import build_robot_spec  # noqa: E402
from app.services.transmission_geom import (  # noqa: E402
    belt_loop, gear_outline, pulley_outline, sprocket_outline)

IN = 25.4  # inches → mm; STEP ships in mm

# the viewer's palette, so the import looks like the studio
MAT = {
    "aluminium": "b7bec4", "aluminium-dark": "8b9298", "anodised": "cbb46a",
    "steel": "c8ced4", "black": "1a1c1e", "plastic-black": "232528",
    "delrin": "e6e4dd", "nylon": "30353a", "urethane": "55595e",
    "rubber": "161719", "copper": "c47a3a", "polycarb": "bfd8cc",
    "plywood": "c9a876", "foam": "e4e0d2",
}
COMPLIANT = "2f9d5b"

BOX_DEFAULTS = {
    "gearbox": (2.0, 2.0, 1.2), "component": (3.0, 2.0, 1.0),
    "polycarb": (1.0, 0.093, 1.0), "hardstop": (0.75, 0.5, 0.75),
    "pawl": (0.25, 1.2, 0.6), "slide": (0.6, 1.2, 0.25),
    "hook": (0.6, 1.8, 3.0), "actuator": (1.6, 0.8, 0.8),
    "sensor": (0.5, 0.5, 0.5), "chain_track": (0.9, 0.7, 4.0),
}
SKIP = {"rope", "cable", "envelope"}


def _outline_solid(pts, height, bore=0.0):
    """Extrude a closed 2D outline (inches) into a solid along local Y."""
    mm = [(x * IN, y * IN) for x, y in pts]
    s = cq.Workplane("XZ").polyline(mm).close().extrude(height * IN / 2.0, both=True)
    if bore > 0:
        s = s.cut(cq.Workplane("XZ").circle(bore * IN / 2.0)
                  .extrude(height * IN / 2.0 * 1.02, both=True))
    return s


def _belt_solid(f):
    """A belt/chain as the wrapped loop the enrichment pass resolved.

    Built in a local frame (X along the run, Z the pulley axis), rotated into
    place with two axis rotations, positioned relative to the feature's `at`
    (the main loop applies the `at` translation itself)."""
    a, b = f.get("at") or [0, 0, 0], f.get("to")
    r1, r2 = f.get("r1"), f.get("r2")
    if not b or r1 is None or r2 is None:
        return None                     # unresolved endpoint: the report already says so
    run = [b[i] - a[i] for i in range(3)]
    d = math.sqrt(sum(v * v for v in run))
    loop = belt_loop(float(r1), float(r2), d, _num(f.get("thick"), 0.12))
    if loop is None:
        return None
    run = [v / d for v in run]
    axis = f.get("axis") or [1, 0, 0]
    dot = sum(axis[i] * run[i] for i in range(3))
    axis = [axis[i] - dot * run[i] for i in range(3)]
    n = math.sqrt(sum(v * v for v in axis))
    if n < 1e-6:
        axis = [0.0, 1.0, 0.0] if abs(run[1]) < 0.9 else [0.0, 0.0, 1.0]
        dot = sum(axis[i] * run[i] for i in range(3))
        axis = [axis[i] - dot * run[i] for i in range(3)]
        n = math.sqrt(sum(v * v for v in axis))
    axis = [v / n for v in axis]

    outer, inner = loop
    width = 0.05 + _num(f.get("w"), 0.35)
    s = _outline_solid_xy(outer, width).cut(_outline_solid_xy(inner, width * 1.02))
    # local frame: X=run, Z=axis. Rotate local +Z onto the axis, then swing the
    # image of local +X onto the run about that axis.
    s = _aim(s, axis, run)
    return s


def _outline_solid_xy(pts, height):
    mm = [(x * IN, y * IN) for x, y in pts]
    return cq.Workplane("XY").polyline(mm).close().extrude(height * IN / 2.0, both=True)


def _aim(body, z_to, x_to):
    """Rotate a solid so local +Z lands on z_to and local +X lands on x_to."""
    zt = cq.Vector(*z_to)
    cross = cq.Vector(0, 0, 1).cross(zt)
    dot = max(-1.0, min(1.0, cq.Vector(0, 0, 1).dot(zt)))
    ang = math.degrees(math.acos(dot))
    if cross.Length > 1e-9:
        body = body.rotate((0, 0, 0), tuple(cross.normalized().toTuple()), ang)
        rot_axis, rot_ang = cross.normalized(), math.radians(ang)
    elif dot < 0:                                    # anti-parallel: flip about X
        body = body.rotate((0, 0, 0), (1, 0, 0), 180.0)
        rot_axis, rot_ang = cq.Vector(1, 0, 0), math.pi
    else:
        rot_axis, rot_ang = cq.Vector(0, 0, 1), 0.0
    # where did local +X go? Rodrigues, then measure the residual swing about z_to
    x0 = cq.Vector(1, 0, 0)
    k, th = rot_axis, rot_ang
    x1 = (x0.multiply(math.cos(th)) + k.cross(x0).multiply(math.sin(th))
          + k.multiply(k.dot(x0) * (1 - math.cos(th))))
    xt = cq.Vector(*x_to)
    cos2 = max(-1.0, min(1.0, x1.dot(xt)))
    ang2 = math.degrees(math.acos(cos2))
    if x1.cross(xt).dot(zt) < 0:
        ang2 = -ang2
    if abs(ang2) > 1e-6:
        body = body.rotate((0, 0, 0), tuple(zt.toTuple()), ang2)
    return body


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _cyl(dia, height, bore=0.0):
    """cylinder along local Y — the viewer's convention for every turned part"""
    s = cq.Workplane("XZ").circle(dia * IN / 2.0).extrude(height * IN / 2.0, both=True)
    if bore > 0 and bore < dia:
        s = s.cut(cq.Workplane("XZ").circle(bore * IN / 2.0)
                  .extrude(height * IN / 2.0 * 1.02, both=True))
    return s


def _box(w, h, d):
    return cq.Workplane("XY").box(w * IN, h * IN, d * IN)


def solid(f):
    t = f.get("t")
    if t in SKIP:
        return None
    if t == "tube":
        sec = f.get("sec") or [2.0, 1.0]
        w, h = _num(sec[1], 1.0), _num(sec[0], 2.0)
        length = _num(f.get("len"), 1.0)
        wall = min(_num(f.get("wall"), 0.100), min(w, h) / 2.0 - 0.01)
        outer = _box(w, h, length)
        inner = cq.Workplane("XY").box((w - 2 * wall) * IN, (h - 2 * wall) * IN,
                                       length * IN * 1.02)
        return outer.cut(inner)
    if t == "plate" or t in BOX_DEFAULTS:
        size = f.get("size") or list(BOX_DEFAULTS.get(t, (1.0, 0.09, 1.0)))
        return _box(_num(size[0], 1.0), _num(size[1], 0.09), _num(size[2], 1.0))
    if t == "fabric":
        size = f.get("size") or [27.0, 5.06, 3.31]
        length, h, d = (_num(v, 1.0) for v in size[:3])
        sk = 0.06
        if f.get("bend"):
            # The corner wrap: the same channel section swept 90° about Y, on the +X→+Z
            # quadrant the corner noodle uses, so cloth and foam turn the corner together.
            bend = _num(f.get("bend"), 1.66) * IN
            outer = (cq.Workplane("XZ").moveTo(bend, 0).rect(d * IN, h * IN)
                     .revolve(90, (0, 0, 0), (0, 1, 0)))
            inner = (cq.Workplane("XZ").moveTo(bend, 0)
                     .rect((d - 2 * sk) * IN, (h - 2 * sk) * IN)
                     .revolve(90, (0, 0, 0), (0, 1, 0)))
            return outer.cut(inner)
        outer = _box(length, h, d)
        inner = cq.Workplane("XY").box((length - 2 * sk) * IN, (h - 2 * sk) * IN, d * IN)
        return outer.cut(inner.translate((0, 0, -sk * IN)))
    if t == "rib":
        # A formed arc rib: the profile a hood skin is riveted to. Thin along X, arc in YZ.
        r = _num(f.get("r"), 3.0)
        web = min(_num(f.get("web"), 0.6), r - 0.05)
        th = _num(f.get("th"), 0.19)
        start = _num(f.get("start"), 0.0)
        sweep = max(_num(f.get("arc"), 90.0), 10.0)
        ring = (cq.Workplane("YZ").circle(r * IN).circle((r - web) * IN)
                .extrude(th * IN / 2.0, both=True))
        # Keep only the swept sector: a wedge, cut out of the full annulus.
        pts = [(0.0, 0.0)]
        steps = max(4, int(sweep // 15) + 2)
        for i in range(steps + 1):
            a = math.radians(start + sweep * i / steps)
            pts.append((math.cos(a) * r * IN * 1.4, math.sin(a) * r * IN * 1.4))
        wedge = (cq.Workplane("YZ").polyline(pts).close()
                 .extrude(th * IN, both=True))
        return ring.intersect(wedge)
    if t == "decal":
        # A vinyl numeral panel is a real applied part with a real thickness, so it exports
        # as one. The glyphs are not cut — nothing downstream would use them.
        size = f.get("size") or [6.0, 4.0, 0.02]
        return _box(_num(size[0], 6.0), _num(size[1], 4.0), max(_num(size[2], 0.02), 0.01))
    if t == "gusset":
        size = f.get("size") or [3.0, 3.0]
        a, b = _num(size[0], 3.0), _num(size[1], 3.0)
        th = _num(f.get("th"), 0.09)
        if f.get("form") == "angle":
            # Folded sheet: one flat leg plus the leg bent down at its outboard edge. Unioned
            # rather than left as two bodies, because the fold is what the part is for.
            leg = _num(f.get("leg"), min(a, b) * 0.66)
            flat = _box(a, th, b).translate((0, -th * IN / 2, 0))
            bent = _box(a, leg, th).translate((0, -leg * IN / 2, (b - th) * IN / 2))
            return flat.union(bent)
        if f.get("form") == "triangle":
            return (cq.Workplane("XZ")
                    .polyline([(-a * IN / 2, -b * IN / 2), (a * IN / 2, -b * IN / 2),
                               (-a * IN / 2, b * IN / 2)]).close()
                    .extrude(th * IN / 2.0, both=True))
        # The default plate has its unloaded corner clipped, which is what a waterjet cuts.
        clip = min(a, b) * 0.42
        return (cq.Workplane("XZ")
                .polyline([(-a * IN / 2, -b * IN / 2), (a * IN / 2, -b * IN / 2),
                           (a * IN / 2, (b / 2 - clip) * IN), ((a / 2 - clip) * IN, b * IN / 2),
                           (-a * IN / 2, b * IN / 2)]).close()
                .extrude(th * IN / 2.0, both=True))
    if t == "shaft" or t == "noodle":
        dia = _num(f.get("dia"), 0.5)
        length = _num(f.get("len"), 1.0)
        if t == "shaft" and (f.get("form") or "hex") == "hex":
            return (cq.Workplane("XZ").polygon(6, dia * IN / math.cos(math.pi / 6))
                    .extrude(length * IN / 2.0, both=True))
        return _cyl(dia, length)
    if t == "bearing":
        return _cyl(_num(f.get("od"), 1.125), _num(f.get("w"), 0.313),
                    bore=_num(f.get("bore"), 0.5))
    if t == "belt":
        return _belt_solid(f)
    if t == "noodle_corner":
        # Quarter-bend of bumper noodle: a circle revolved 90° about Y, spanning the
        # +X→+Z quadrant to match the viewer's base orientation; f.rot walks it
        # around the frame corners.
        bend, nr = _num(f.get("bend"), 3.0) * IN, _num(f.get("dia"), 2.5) / 2.0 * IN
        return (cq.Workplane("XY").moveTo(bend, 0).circle(nr)
                .revolve(90, (0, 0, 0), (0, -1, 0)))
    if t in ("gear", "sprocket", "pulley", "bevel"):
        pd = _num(f.get("pd"), 1.0)
        teeth = int(_num(f.get("teeth"), 0))
        height = max(_num(f.get("face") if t in ("gear", "bevel") else f.get("w"), 0.375), 0.05)
        bore = _num(f.get("bore"), 0.0)
        if t == "gear" and teeth:
            return _outline_solid(gear_outline(teeth, pd), height, bore)
        if t == "sprocket" and teeth:
            return _outline_solid(sprocket_outline(teeth, pd), height, bore)
        if t == "pulley" and teeth:
            body = _outline_solid(pulley_outline(teeth, pd), height, bore)
            for sy in (-1, 1):                       # flanges keep the belt on
                fl = _cyl(pd + 0.18, 0.05).translate((0, sy * height * IN / 2.0, 0))
                body = body.union(fl)
            return body
        # bevel stays a true-pitch disc: its teeth are conical and the flat outline
        # would be a lie — the fidelity report classes it `concept` for this reason
        return _cyl(pd, height, bore=bore)
    if t == "wheel":
        return _cyl(_num(f.get("dia"), 4.0), _num(f.get("w"), 1.0), bore=0.5)
    if t == "motor":
        return _cyl(_num(f.get("dia"), 2.2), _num(f.get("len"), 3.5))
    if t in ("standoff", "drum", "brake", "tensioner"):
        height = _num(f.get("len") if t == "standoff" else f.get("w"), 1.0)
        return _cyl(_num(f.get("dia"), 1.0), height)
    if t == "bolts":
        rep = f.get("rep") or {}
        n = int(_num(rep.get("n"), 1)) or 1
        step = [_num(v) for v in (rep.get("step") or [0, 0, 0])[:3]]
        dia, length = _num(f.get("dia"), 0.19), _num(f.get("len"), 0.75)
        out = None
        for i in range(n):
            k = i - (n - 1) / 2.0
            one = _cyl(dia, length).translate(
                (k * step[0] * IN, k * step[1] * IN, k * step[2] * IN))
            out = one if out is None else out.union(one)
        return out
    if t == "hood":
        # The shooter hood is a real part: a half shell over the flywheel, built the way the
        # viewer draws it (half cylinder about the shaft axis). Skipping it shipped shooters
        # with no hood in the STEP.
        r = _num(f.get("r"), 3.0)
        wdt = _num(f.get("w"), 2.6)
        shell = _cyl((r + 0.09) * 2.0, wdt, bore=r * 2.0)
        cutter = (cq.Workplane("XY")
                  .box(2 * (r + 1) * IN, 2 * (r + 1) * IN, (wdt + 1) * IN)
                  .translate((-(r + 1) * IN, 0, 0)))
        return shell.cut(cutter).rotate((0, 0, 0), (0, 0, 1), 90)
    # Unknown types arrive as visible boxes, never silent omissions — the same policy the
    # viewer and the FeatureScript compiler follow. A part that vanishes from the STEP is a
    # part somebody machines around.
    size = f.get("size")
    if isinstance(size, (list, tuple)) and len(size) == 3:
        return _box(_num(size[0], 1.0), _num(size[1], 1.0), _num(size[2], 1.0))
    dia = _num(f.get("dia"), 1.0)
    return _box(dia, _num(f.get("len"), dia), dia)


def colour(f, bumper_hex):
    n = (f.get("n") or "").lower()
    if f.get("t") == "fabric" or ("bumper" in n and not f.get("mat")):
        h = bumper_hex.lstrip("#")
    elif f.get("t") == "wheel" and f.get("kind") == "compliant":
        h = COMPLIANT
    elif f.get("t") == "motor":
        h = MAT["black"]
    elif f.get("t") == "belt":
        h = MAT["steel"] if "chain" in (f.get("kind") or "") else MAT["black"]
    elif f.get("t") in ("bearing", "shaft", "bolts"):
        h = MAT["steel"]
    else:
        h = MAT.get(f.get("mat") or "", MAT["aluminium"])
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return cq.Color(r, g, b, 1.0)


def main() -> None:
    req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    out_path = sys.argv[1]
    spec = build_robot_spec(req.get("prompt", ""), use_model=False,
                            season=req.get("season", ""))
    bumper_hex = (spec.get("bumper_color") or {}).get("hex", "#a01722")
    # same naming as the studio page: module + lead subsystem + season label
    dt = spec.get("drivetrain") or {}
    module = dt.get("module") or (str(dt.get("type", "swerve")).title() + " drivetrain")
    season_label = (spec.get("season") or {}).get("label", "")
    subs = spec.get("subsystems") or []
    lead = subs[0].title() if subs else "Drivebase"
    name = f"{module} {lead} robot" + (f" - {season_label}" if season_label else "")

    # the export frame is Z-up (x right, y back, z up); the tree is Y-up
    root = cq.Assembly(name=name[:80])
    parts = 0
    for asm in spec["cad"]["assemblies"]:
        ox, oy, oz = asm.get("origin") or [0, 0, 0]
        sub = cq.Assembly(name=asm.get("id", "assembly"))
        for f in expand_mirrors(asm.get("features") or []):
            body = solid(f)
            if body is None:
                continue
            rot = f.get("rot") or [0, 0, 0]
            # match the viewer's XYZ Euler: R = Rx · Ry · Rz
            if rot[2]:
                body = body.rotate((0, 0, 0), (0, 0, 1), rot[2])
            if rot[1]:
                body = body.rotate((0, 0, 0), (0, 1, 0), rot[1])
            if rot[0]:
                body = body.rotate((0, 0, 0), (1, 0, 0), rot[0])
            at = f.get("at") or [0, 0, 0]
            body = body.translate(((ox + at[0]) * IN, (oy + at[1]) * IN,
                                   (oz + at[2]) * IN))
            # Y-up → Z-up, about the world origin, applied per-body so the
            # assembly tree carries no compensating rotations to confuse edits
            body = body.rotate((0, 0, 0), (1, 0, 0), 90)
            part_name = str(f.get("n") or "part")
            # envelope-fidelity parts say so in their name, so the CAD tree itself
            # never overstates what was modelled
            if feature_fidelity(f.get("t", "")) == "envelope":
                part_name += " (catalog envelope)"
            sub.add(body, name=part_name[:80], color=colour(f, bumper_hex))
            parts += 1
        root.add(sub)
    root.save(out_path, exportType="STEP")
    print(json.dumps({"name": name, "parts": parts}))


if __name__ == "__main__":
    main()
