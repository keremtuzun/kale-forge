"""Parametric generators for individual mechanical parts.

The robot compiler builds a robot out of subsystems out of features. This module builds ONE
PART out of features — the same features, deliberately. `frc_cad`'s vocabulary is a closed
set that the viewer, the FeatureScript emitter, the STEP worker, the BOM and the fidelity
report all switch on, so a generator that emits `plate` and `bore` inherits every one of
those consumers without touching them. That is the whole reason this module is thin.

Each generator takes a resolved `EngineeringSpec` and returns:

    assembly    one `frc_cad`-shaped assembly, ready for the same pipeline
    parameters  the named dimensions that drive it, for the FeatureScript variable block
    dimensions  what to show the user, each with its provenance
    hardware    catalog parts the design specifies rather than makes

Nothing here invents a critical dimension in silence. When a number has to be chosen, it is
recorded as an ASSUMED value on the spec, which the dossier prints and the user can edit.

Stdlib-only: imported by the Vercel function.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from app.services import hardware_catalog as hw
from app.services.design_intent import (
    ASSUMED, CALCULATED, EngineeringSpec, USER_PROVIDED, VERIFIED)
from app.services.frc_cad import (
    _asm, _at, _rot, bearing as bearing_feature, bore, motor as motor_feature,
    plate, pulley, shaft as shaft_feature, wheel)
from app.services.frc_parts import MOTORS

PRIMITIVES_VERSION = "kale-primitives-1.0"

MM = 1 / 25.4
HEX_CORNER = hw.HEX_CORNER_RATIO


def _dim(label: str, v: float, source: str, unit: str = "in", note: str = "") -> dict[str, Any]:
    """One dimension row for the dossier. The provenance travels with the number so the UI
    never has to guess whether 1.125 was measured, quoted or invented."""
    return {"label": label, "value": round(float(v), 4), "unit": unit,
            "source": source, "note": note}


# ── fits ─────────────────────────────────────────────────────────────────────
# Press and slip fits as diametral allowances. These are workshop numbers for aluminium
# housings on rolling-element bearings at FRC scale, not an ISO fit table, and they are
# reported as ASSUMED wherever they are used.
PRESS_FIT_IN = -0.0005      # interference: pocket smaller than the bearing
SLIP_FIT_IN = 0.0015        # clearance: pocket larger, retained by a shoulder or plate
FIT_NOTE = ("press fit is 0.0005 in interference, slip fit 0.0015 in clearance — workshop "
            "allowances for an aluminium housing, worth confirming against your own tooling")


def pocket_diameter(bearing_od_in: float, fit: str = "press") -> float:
    return bearing_od_in + (PRESS_FIT_IN if fit == "press" else SLIP_FIT_IN)


# ── shared helpers ───────────────────────────────────────────────────────────
def _bolt_pattern(width: float, depth: float, inset: float, dia: float, *,
                  width_var: str = "", depth_var: str = "", inset_var: str = "",
                  dia_var: str = "") -> list[dict[str, Any]]:
    """Four mounting holes, one per corner, inset from the edges.

    When the caller names the variables behind the numbers, each hole carries the expression
    that placed it, so widening the plate in Onshape moves the holes with it instead of
    leaving four literals stranded in the middle of a bigger part.
    """
    def _expr(sx: int, sz: int) -> dict[str, str]:
        if not (width_var and depth_var and inset_var):
            return {}
        sign = lambda s: "" if s > 0 else "-"  # noqa: E731
        out = {"x": f"{sign(sx)}({width_var} / 2 - {inset_var})",
               "z": f"{sign(sz)}({depth_var} / 2 - {inset_var})"}
        if dia_var:
            out["d"] = dia_var
        return out

    return [bore(dia, sx * (width / 2 - inset), sz * (depth / 2 - inset),
                 note="mounting hole", expr=_expr(sx, sz))
            for sx in (-1, 1) for sz in (-1, 1)]


def _called_out_hole(spec: EngineeringSpec, width: float, depth: float) -> list[dict[str, Any]]:
    """The hole the request asked for by name, centred, if it fits.

    A hole a user typed and did not get is a wrong part, and it is invisible in the dimension
    table, so it has to be cut rather than noted. One that does not fit is refused out loud
    instead of silently shrunk.
    """
    dia = spec.get("center_hole_in")
    if not dia:
        return []
    dia = float(dia)
    if dia >= min(width, depth) * 0.9:
        spec.unknown("center_hole_in",
                     f"the {dia:g} in hole asked for does not leave material in a "
                     f"{width:g} x {depth:g} in plate")
        return []
    return [bore(dia, 0.0, 0.0, note="hole called out in the request",
                 expr={"d": "centreHoleDiameter"})]


def _single(assembly: dict[str, Any], parameters: dict[str, float],
            dimensions: list[dict[str, Any]],
            hardware: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"assembly": assembly, "parameters": parameters, "dimensions": dimensions,
            "hardware": hardware or []}


# ── bearing block / bearing housing ──────────────────────────────────────────
def bearing_block(spec: EngineeringSpec) -> dict[str, Any]:
    """A block with a bearing pocket and four mounting holes.

    The dimension chain is the point of this generator, and it runs outward from the bearing:
    the bearing sets the pocket, the pocket plus a wall sets the block, and the block sets
    where the mounting holes can go. Nothing is a round number chosen for looking right.
    """
    od = float(spec.get("bearing_od_in") or 1.125)
    width = float(spec.get("bearing_width_in") or 0.313)
    bore_size = float(spec.get("shaft_size_in") or 0.5)
    bore_form = str(spec.get("shaft_form") or "hex")
    fit = str(spec.get("fit") or "press")
    wall = float(spec.get("wall_in") or 0.0)
    if not wall:
        # A wall thinner than a quarter of the bearing OD splits when the bearing is pressed,
        # and 0.25 in is about as thin as this is worth cutting in 6061 either way.
        wall = max(0.25, od * 0.28)
        spec.assume("wall_in", round(wall, 3), "in",
                    "0.28 x bearing OD, floored at 0.25 in — thinner walls split on the press")

    pocket = pocket_diameter(od, fit)
    block_w = float(spec.get("block_width_in") or 0.0) or round(od + 2 * wall, 3)
    block_h = float(spec.get("block_height_in") or 0.0) or round(od + 2 * wall, 3)
    thickness = float(spec.get("block_thickness_in") or 0.0)
    if not thickness:
        # Through-bored when the bearing is as deep as the plate; otherwise a shoulder is
        # left behind it so the bearing has something to seat against.
        thickness = round(width + 0.125, 3)
        spec.assume("block_thickness_in", thickness, "in",
                    "bearing width plus a 0.125 in shoulder for the bearing to seat on")

    bolt_key = str(spec.get("fastener") or "10-32")
    bolt = hw.FASTENERS.get(bolt_key, hw.FASTENERS["10-32"])
    bolt_dia = bolt["clearance_in"]
    inset = round(max(bolt_dia * 1.6, 0.30), 3)
    # Mounting holes need meat between them and the pocket, or the block is a ring with ears.
    clear_span = pocket + 2 * (inset * 2 + bolt_dia)
    # Each face is grown on its own and recorded on its own. Recording both under one key
    # relabelled a width the user had stated as something this compiler calculated, which is
    # exactly the confusion the provenance table exists to prevent.
    if clear_span > block_w:
        block_w = round(clear_span, 3)
        spec.calc("block_width_in", block_w, "in",
                  "grown so the mounting holes clear the bearing pocket")
    if clear_span > block_h:
        block_h = round(clear_span, 3)
        spec.calc("block_height_in", block_h, "in",
                  "grown so the mounting holes clear the bearing pocket")

    blind = thickness > width + 0.01
    bores = [bore(pocket, 0, 0, depth=(width if blind else 0.0), note="bearing pocket",
                  expr={"d": "pocketDiameter",
                        **({"depth": "bearingWidth"} if blind else {})})]
    # A shaft has to pass through the shoulder, so the back of a blind pocket is relieved.
    if blind:
        clearance = bore_size * (HEX_CORNER if bore_form == "hex" else 1.0) + 0.06
        bores.append(bore(round(clearance, 4), 0, 0,
                          note="shaft clearance through the shoulder",
                          expr={"d": f"shaftDiameter * "
                                     f"{HEX_CORNER if bore_form == 'hex' else 1.0:g} + 0.06"}))
    bores += _bolt_pattern(block_w, block_h, inset, bolt_dia,
                           width_var="blockWidth", depth_var="blockHeight",
                           inset_var="mountHoleInset", dia_var="mountHoleDiameter")

    spec.calc("pocket_diameter_in", round(pocket, 4), "in",
              f"bearing OD {od:g} in with a {fit} fit ({FIT_NOTE})")
    spec.calc("mount_hole_spacing_in", round(block_w - 2 * inset, 3), "in",
              "corner holes, inset far enough to clear the pocket")

    body = plate("bearing block", (block_w, thickness, block_h), _at(0, thickness / 2, 0),
                 mat="aluminium", bores=bores, note="pocket bored for the bearing; four "
                 "corner holes on the mounting face",
                 expr={"sx": "blockWidth", "sy": "blockDepth", "sz": "blockHeight"})
    features = [body]
    catalog = hw.bearing_for_shaft(bore_size, bore_form) or hw.bearing_by_od(od)
    if catalog:
        features.append(bearing_feature(
            catalog["name"], _at(0, thickness - width / 2, 0), _rot(90, 0, 0),
            bore=catalog["bore_in"], od=catalog["od_in"], width=catalog["width_in"],
            flanged=bool(catalog.get("flanged")), note="seats in the pocket"))
    hardware = [hw.as_bom_row(catalog or hw.bearing_requirement(od, bore_size, bore_form)),
                hw.as_bom_row(bolt, 4)]

    return _single(
        _asm("part", "Bearing block", "part", features,
             note="one machined block; the bearing is bought, not made",
             mates=["bearing pressed into the pocket",
                    "block bolts to its mounting face on four screws"]),
        {"bearingOD": od, "bearingWidth": width, "shaftDiameter": bore_size,
         "pocketDiameter": round(pocket, 4), "blockWidth": block_w, "blockHeight": block_h,
         "blockDepth": thickness, "mountHoleDiameter": bolt_dia,
         "mountHoleInset": inset, "mountHoleSpacing": round(block_w - 2 * inset, 3),
         "wallThickness": round(wall, 3)},
        [_dim("Bearing OD", od, spec.source_of("bearing_od_in")),
         _dim("Bearing width", width, spec.source_of("bearing_width_in")),
         _dim("Shaft size", bore_size, spec.source_of("shaft_size_in")),
         _dim("Pocket diameter", pocket, CALCULATED, note=f"{fit} fit"),
         _dim("Block width", block_w, spec.source_of("block_width_in")),
         _dim("Block height", block_h, spec.source_of("block_height_in")),
         _dim("Block thickness", thickness, spec.source_of("block_thickness_in")),
         _dim("Wall thickness", wall, spec.source_of("wall_in")),
         _dim("Mount hole", bolt_dia, VERIFIED, note=f"{bolt['name']} clearance")],
        hardware)


# ── shaft / hex shaft ────────────────────────────────────────────────────────
def shaft(spec: EngineeringSpec) -> dict[str, Any]:
    """A length of shaft, hex or round, optionally grooved for retaining rings."""
    size = float(spec.get("shaft_size_in") or 0.5)
    form = str(spec.get("shaft_form") or "hex")
    length = float(spec.get("length_in") or 0.0)
    if not length:
        length = 6.0
        spec.assume("length_in", length, "in", "no length was given; 6 in is a usable stub")
    features = [shaft_feature(f"{'hex' if form == 'hex' else 'round'} shaft", size, length,
                              _at(0, 0, 0), _rot(0, 0, 90), form=form,
                              note="stock length, cut to size")]
    dims = [_dim("Across flats" if form == "hex" else "Diameter", size,
                 spec.source_of("shaft_size_in")),
            _dim("Length", length, spec.source_of("length_in"))]
    params = {"shaftSize": size, "shaftLength": length}

    grooves = int(spec.get("groove_count") or 0)
    if grooves:
        # A retaining-ring groove is a real cut: the ring sits in it and the groove depth is
        # what holds the load, so it is dimensioned rather than drawn as a line.
        depth = round(size * 0.045, 4)
        gw = 0.042
        inset = float(spec.get("groove_inset_in") or 0.25)
        spec.assume("groove_depth_in", depth, "in",
                    "4.5% of shaft size — a common external-ring groove depth; confirm "
                    "against the ring you buy")
        spec.assume("groove_width_in", gw, "in", "typical external retaining-ring thickness")
        for i in range(grooves):
            z = -length / 2 + inset if i == 0 else length / 2 - inset
            if grooves > 2:
                z = -length / 2 + inset + i * (length - 2 * inset) / max(1, grooves - 1)
            features.append(shaft_feature(
                f"retaining ring groove {i + 1}", size - 2 * depth, gw, _at(0, 0, z),
                _rot(0, 0, 90), form="round", note="external retaining-ring groove"))
        params.update({"grooveDepth": depth, "grooveWidth": gw, "grooveInset": inset})
        dims += [_dim("Groove depth", depth, ASSUMED), _dim("Grooves", grooves, USER_PROVIDED,
                                                            unit="")]
    catalog = hw.SHAFTS.get(("hex_half" if size == 0.5 else "hex_three_eighth")
                            if form == "hex" else "round_half")
    return _single(
        _asm("part", f"{size:g} in {form} shaft", "part", features,
             note="cut from stock", mates=["rides in bearings at both ends"]),
        params, dims, [hw.as_bom_row(catalog)] if catalog else [])


# ── spacer ───────────────────────────────────────────────────────────────────
def spacer(spec: EngineeringSpec) -> dict[str, Any]:
    """A tube that sets a distance along a shaft."""
    bore_size = float(spec.get("shaft_size_in") or 0.5)
    form = str(spec.get("shaft_form") or "hex")
    length = float(spec.get("length_in") or 0.0)
    if not length:
        length = 0.5
        spec.assume("length_in", length, "in", "no length was given; 0.5 in is a common stack")
    od = float(spec.get("od_in") or 0.0)
    if not od:
        # Enough wall to be turned and not crush, without fouling a bearing's inner race.
        od = round(bore_size * (HEX_CORNER if form == "hex" else 1.0) + 0.19, 3)
        spec.assume("od_in", od, "in",
                    "shaft across-corners plus a 0.095 in wall — clears a bearing inner race")
    clear = round(bore_size * (HEX_CORNER if form == "hex" else 1.0) + 0.01, 4)
    spec.calc("bore_in", clear, "in",
              "shaft across-corners with 0.010 in clearance so it slides")
    body = plate("spacer", (od, length, od), _at(0, length / 2, 0), _rot(90, 0, 0),
                 mat="aluminium-dark",
                 bores=[bore(clear, 0, 0, form=form, note="shaft bore")],
                 note="turned or cut from tube; bore matches the shaft")
    return _single(
        _asm("part", f"{length:g} in spacer", "part", [body],
             note="sets a stack height on the shaft", mates=["slides on the shaft"]),
        {"spacerLength": length, "spacerOD": od, "shaftDiameter": bore_size, "boreDiameter": clear},
        [_dim("Length", length, spec.source_of("length_in")),
         _dim("Outside diameter", od, spec.source_of("od_in")),
         _dim("Bore", clear, CALCULATED, note=f"{form} shaft with clearance")], [])


# ── rectangular plate ────────────────────────────────────────────────────────
def rect_plate(spec: EngineeringSpec) -> dict[str, Any]:
    """A flat plate with an optional bolt pattern and lightening."""
    w = float(spec.get("width_in") or 0.0)
    d = float(spec.get("depth_in") or 0.0)
    t = float(spec.get("thickness_in") or 0.0)
    if not w:
        w = 4.0
        spec.assume("width_in", w, "in", "no width was given")
    if not d:
        d = 4.0
        spec.assume("depth_in", d, "in", "no depth was given")
    if not t:
        t = 0.190
        spec.assume("thickness_in", t, "in", "3/16 in 6061 — the common FRC plate stock")
    bolt = hw.FASTENERS.get(str(spec.get("fastener") or "10-32"), hw.FASTENERS["10-32"])
    inset = round(max(bolt["clearance_in"] * 1.6, 0.3), 3)
    bores = _bolt_pattern(w, d, inset, bolt["clearance_in"], width_var="plateWidth",
                          depth_var="plateDepth", inset_var="holeInset",
                          dia_var="mountHoleDiameter")
    bores += _called_out_hole(spec, w, d)
    body = plate("plate", (w, t, d), _at(0, t / 2, 0), mat="aluminium", bores=bores,
                 pockets=int(spec.get("lightening") or 0),
                 expr={"sx": "plateWidth", "sy": "plateThickness", "sz": "plateDepth"})
    return _single(
        _asm("part", "Plate", "part", [body], note="flat stock, cut and drilled"),
        {"plateWidth": w, "plateDepth": d, "plateThickness": t,
         "mountHoleDiameter": bolt["clearance_in"], "holeInset": inset,
         **({"centreHoleDiameter": float(spec.get("center_hole_in"))}
            if spec.get("center_hole_in") else {})},
        [_dim("Width", w, spec.source_of("width_in")),
         _dim("Depth", d, spec.source_of("depth_in")),
         _dim("Thickness", t, spec.source_of("thickness_in")),
         _dim("Hole", bolt["clearance_in"], VERIFIED, note=f"{bolt['name']} clearance")],
        [hw.as_bom_row(bolt, 4)])


# ── gusset ───────────────────────────────────────────────────────────────────
def gusset_plate(spec: EngineeringSpec) -> dict[str, Any]:
    """A gusset joining two tubes, hole-matched to their bolt rows.

    The hole pitch is the whole point: a gusset whose holes do not land on the rails' rows
    is a gusset that needs the rails re-drilled, which is the thing gussets exist to avoid.
    """
    section = spec.get("tube_section_in") or [2.0, 1.0]
    pitch = float(spec.get("hole_pitch_in") or 1.0)
    legs = int(spec.get("legs") or 2)
    t = float(spec.get("thickness_in") or 0.0)
    if not t:
        t = 0.118
        spec.assume("thickness_in", t, "in", "3 mm 5052 — the course's Gusset Generator stock")
    reach = float(spec.get("reach_in") or 0.0)
    if not reach:
        reach = round(pitch * 2 + 0.9, 3)
        spec.assume("reach_in", reach, "in", "far enough down each leg to catch two hole pitches")
    bolt = hw.FASTENERS.get(str(spec.get("fastener") or "10-32"), hw.FASTENERS["10-32"])
    width = float(section[0]) if section else 2.0

    bores: list[dict[str, Any]] = []
    for leg in range(max(2, legs)):
        for i in range(2):
            along = -reach / 2 + 0.45 + i * pitch
            bores.append(bore(bolt["clearance_in"],
                              along if leg == 0 else -reach / 2 + 0.45,
                              (-reach / 2 + 0.45 + i * pitch) if leg else 0.0,
                              note=f"leg {leg + 1} hole {i + 1}"))
    body = plate("gusset", (reach, t, reach), _at(0, t / 2, 0), mat="aluminium",
                 bores=bores[:8],
                 expr={"sx": "gussetReach", "sy": "gussetThickness", "sz": "gussetReach"},
                 note=f"holes on the rails' {pitch:g} in rows, so it bolts on without re-drilling")
    return _single(
        _asm("part", "Gusset", "part", [body], note="bent or flat sheet, hole-matched"),
        {"gussetReach": reach, "gussetThickness": t, "holePitch": pitch,
         "holeDiameter": bolt["clearance_in"], "tubeWidth": width},
        [_dim("Reach", reach, spec.source_of("reach_in")),
         _dim("Thickness", t, spec.source_of("thickness_in")),
         _dim("Hole pitch", pitch, spec.source_of("hole_pitch_in")),
         _dim("Hole", bolt["clearance_in"], VERIFIED, note=f"{bolt['name']} clearance")],
        [hw.as_bom_row(bolt, len(bores[:8]))])


# ── motor mounting plate ─────────────────────────────────────────────────────
# Motor face patterns. Bolt circle and pilot are what a plate has to match, and getting them
# wrong is the difference between a plate and a coaster. Kept here rather than in
# `frc_parts` because they are mounting geometry, not performance data.
MOTOR_FACES: dict[str, dict[str, Any]] = {
    "kraken_x60": {"pilot_in": 2.00, "bolt_circle_in": 2.00, "bolts": 4, "screw": "10-32",
                   "source": ASSUMED},
    "kraken_x44": {"pilot_in": 1.50, "bolt_circle_in": 1.65, "bolts": 4, "screw": "8-32",
                   "source": ASSUMED},
    "falcon_500": {"pilot_in": 2.00, "bolt_circle_in": 2.00, "bolts": 4, "screw": "10-32",
                   "source": ASSUMED},
    "neo": {"pilot_in": 1.850, "bolt_circle_in": 2.187, "bolts": 4, "screw": "10-32",
            "source": ASSUMED},
    "neo_vortex": {"pilot_in": 1.850, "bolt_circle_in": 2.187, "bolts": 4, "screw": "10-32",
                   "source": ASSUMED},
    "neo_550": {"pilot_in": 0.875, "bolt_circle_in": 1.250, "bolts": 4, "screw": "6-32",
                "source": ASSUMED},
    "cim": {"pilot_in": 0.795, "bolt_circle_in": 2.000, "bolts": 2, "screw": "10-32",
            "source": ASSUMED},
    "minion": {"pilot_in": 1.850, "bolt_circle_in": 2.187, "bolts": 4, "screw": "10-32",
               "source": ASSUMED},
}
FACE_NOTE = ("motor face patterns are nominal and NOT confirmed against a vendor drawing — "
             "check the bolt circle before you cut")


def _face(motor_key: str) -> dict[str, Any]:
    return MOTOR_FACES.get(motor_key, MOTOR_FACES["neo"])


def motor_plate(spec: EngineeringSpec) -> dict[str, Any]:
    """A plate carrying one or more motors on their face patterns."""
    motors: list[dict[str, Any]] = list(spec.hardware) or []
    keys = [m["key"] for m in motors if m.get("category") == "motor"] or ["neo"]
    count = int(spec.get("motor_count") or len(keys) or 1)
    keys = (keys * count)[:count]
    faces = [_face(k) for k in keys]
    spec.assume("motor_face_pattern", ", ".join(sorted(set(keys))), "", FACE_NOTE)

    t = float(spec.get("thickness_in") or 0.0)
    if not t:
        t = 0.250
        spec.assume("thickness_in", t, "in",
                    "1/4 in — a motor plate carries the gearbox reaction, so it is thicker "
                    "than a general bracket")
    spread = max(f["bolt_circle_in"] for f in faces) + 1.25
    w = float(spec.get("width_in") or 0.0) or round(spread * count + 0.5, 3)
    d = float(spec.get("depth_in") or 0.0) or round(spread + 0.5, 3)

    bores: list[dict[str, Any]] = []
    for i, (key, face) in enumerate(zip(keys, faces)):
        cx = -w / 2 + spread / 2 + 0.25 + i * spread
        screw = hw.FASTENERS.get(face["screw"], hw.FASTENERS["10-32"])
        bores.append(bore(face["pilot_in"], cx, 0.0, note=f"{key} pilot bore"))
        for b in range(int(face["bolts"])):
            a = math.pi / 4 + b * 2 * math.pi / max(1, int(face["bolts"]))
            bores.append(bore(screw["clearance_in"],
                              cx + math.cos(a) * face["bolt_circle_in"] / 2,
                              math.sin(a) * face["bolt_circle_in"] / 2,
                              note=f"{key} face screw"))
    body = plate("motor plate", (w, t, d), _at(0, t / 2, 0), mat="aluminium", bores=bores,
                 expr={"sx": "plateWidth", "sy": "plateThickness", "sz": "plateDepth"},
                 note="pilot bores locate the motors; the face screws take the torque")
    features: list[dict[str, Any]] = [body]
    for i, key in enumerate(keys):
        cx = -w / 2 + spread / 2 + 0.25 + i * spread
        features.append(motor_feature(f"{key} (reference)", key,
                                      _at(cx, t + MOTORS[key]["length_in"] / 2, 0),
                                      _rot(180, 0, 0)))
    return _single(
        _asm("part", "Motor mounting plate", "part", features,
             note="one plate; the motors are catalog reference geometry, not made",
             mates=[f"{k} bolts to its face pattern" for k in dict.fromkeys(keys)]),
        {"plateWidth": w, "plateDepth": d, "plateThickness": t,
         "pilotDiameter": faces[0]["pilot_in"], "boltCircle": faces[0]["bolt_circle_in"],
         "motorCount": count, "motorSpacing": round(spread, 3)},
        [_dim("Width", w, spec.source_of("width_in")),
         _dim("Depth", d, spec.source_of("depth_in")),
         _dim("Thickness", t, spec.source_of("thickness_in")),
         _dim("Pilot bore", faces[0]["pilot_in"], ASSUMED, note=FACE_NOTE),
         _dim("Bolt circle", faces[0]["bolt_circle_in"], ASSUMED, note=FACE_NOTE),
         _dim("Motor spacing", spread, CALCULATED, note="bolt circle plus clearance")],
        [hw.as_bom_row({"name": MOTORS[k]["name"], "category": "motor",
                        "verified": True, "source": VERIFIED}, keys.count(k))
         for k in dict.fromkeys(keys)])


def gearbox_plate(spec: EngineeringSpec) -> dict[str, Any]:
    """A motor plate that also carries the output bearing, which is what makes it a gearbox
    plate rather than a motor plate: the centre distance between the motor pinions and the
    output has to be a real number, not a gap left in the drawing."""
    out = motor_plate(spec)
    body = out["assembly"]["features"][0]
    od = float(spec.get("bearing_od_in") or 1.125)
    shaft_size = float(spec.get("shaft_size_in") or 0.5)
    pocket = pocket_diameter(od, "press")
    body.setdefault("bores", []).append(
        bore(pocket, 0.0, -float(body["size"][2]) / 2 + od / 2 + 0.35,
             note="output bearing pocket"))
    spec.calc("output_pocket_in", round(pocket, 4), "in", f"bearing OD {od:g} in, press fit")
    out["parameters"].update({"outputPocketDiameter": round(pocket, 4),
                              "outputShaftDiameter": shaft_size})
    out["dimensions"].append(_dim("Output pocket", pocket, CALCULATED, note="press fit"))
    out["assembly"]["name"] = "Gearbox plate"
    catalog = hw.bearing_for_shaft(shaft_size) or hw.bearing_by_od(od)
    if catalog:
        out["hardware"].append(hw.as_bom_row(catalog))
    return out


# ── roller ───────────────────────────────────────────────────────────────────
def roller(spec: EngineeringSpec) -> dict[str, Any]:
    """A roller: a shaft carrying compliant wheels."""
    dia = float(spec.get("roller_diameter_in") or 0.0)
    if not dia:
        dia = 4.0
        spec.assume("roller_diameter_in", dia, "in", "4 in — a common intake roller")
    width = float(spec.get("width_in") or 0.0)
    if not width:
        width = 12.0
        spec.assume("width_in", width, "in", "12 in of roller across the intake")
    shaft_size = float(spec.get("shaft_size_in") or 0.5)
    count = max(2, int(round(width / 3.0)))
    features = [shaft_feature("roller shaft", shaft_size, width + 1.5, _at(0, 0, 0),
                              _rot(0, 0, 90), form="hex")]
    for i in range(count):
        x = -width / 2 + (i + 0.5) * (width / count)
        features.append(wheel(f"compliant wheel {i + 1}", dia, width / count * 0.55,
                              _at(x, 0, 0), _rot(0, 0, 90), kind="compliant",
                              durometer=str(spec.get("durometer") or "")))
    for sx in (-1, 1):
        catalog = hw.bearing_for_shaft(shaft_size)
        if catalog:
            features.append(bearing_feature("roller bearing", _at(sx * (width / 2 + 0.4), 0, 0),
                                            _rot(0, 0, 90), bore=catalog["bore_in"],
                                            od=catalog["od_in"], width=catalog["width_in"]))
    spec.calc("wheel_count", count, "", "one wheel roughly every 3 in of roller")
    return _single(
        _asm("part", "Roller", "assembly", features,
             note="shaft plus compliant wheels", mates=["wheels keyed to the hex shaft"]),
        {"rollerDiameter": dia, "rollerWidth": width, "shaftDiameter": shaft_size,
         "wheelCount": count},
        [_dim("Roller diameter", dia, spec.source_of("roller_diameter_in")),
         _dim("Width", width, spec.source_of("width_in")),
         _dim("Shaft", shaft_size, spec.source_of("shaft_size_in")),
         _dim("Wheels", count, CALCULATED, unit="")],
        [hw.as_bom_row(hw.SHAFTS["hex_half"] if shaft_size == 0.5 else hw.SHAFTS["hex_three_eighth"])])


# ── pulley ───────────────────────────────────────────────────────────────────
def pulley_part(spec: EngineeringSpec) -> dict[str, Any]:
    """An HTD pulley. Pitch diameter follows from the tooth count and the belt pitch — it is
    never a chosen number, which is the mistake this generator exists to make impossible."""
    teeth = int(spec.get("teeth") or 0)
    if not teeth:
        teeth = 24
        spec.assume("teeth", teeth, "", "24T — a common driven size")
    belt = hw.BELTS.get(str(spec.get("belt") or "htd5"), hw.BELTS["htd5"])
    pitch = belt["pitch_in"]
    pd = teeth * pitch / math.pi
    bore_size = float(spec.get("shaft_size_in") or 0.5)
    form = str(spec.get("shaft_form") or "hex")
    face = float(spec.get("face_in") or 0.0)
    if not face:
        face = round(15 * MM + 0.08, 3)
        spec.assume("face_in", face, "in", "15 mm belt plus flange clearance")
    spec.calc("pitch_diameter_in", round(pd, 4), "in",
              f"{teeth}T on {belt['name']} pitch: PD = teeth x pitch / pi")
    body = pulley("pulley", teeth, face, _at(0, 0, 0), _rot(0, 0, 90),
                  pitch_mm=round(pitch * 25.4, 3), bore=bore_size)
    return _single(
        _asm("part", f"{teeth}T {belt['name']} pulley", "part", [body],
             note="turned and cut for the belt profile"),
        {"teeth": teeth, "beltPitch": round(pitch, 5), "pitchDiameter": round(pd, 4),
         "boreDiameter": bore_size, "faceWidth": face},
        [_dim("Teeth", teeth, spec.source_of("teeth"), unit=""),
         _dim("Pitch diameter", pd, CALCULATED, note="teeth x pitch / pi"),
         _dim("Bore", bore_size, spec.source_of("shaft_size_in"), note=form),
         _dim("Face width", face, spec.source_of("face_in"))],
        [hw.as_bom_row(belt)])


# ── simple bracket ───────────────────────────────────────────────────────────
def bracket(spec: EngineeringSpec) -> dict[str, Any]:
    """An L bracket: two legs at ninety degrees, each with a bolt pair."""
    leg_a = float(spec.get("leg_a_in") or 0.0) or 2.0
    leg_b = float(spec.get("leg_b_in") or 0.0) or 2.0
    t = float(spec.get("thickness_in") or 0.0)
    if not t:
        t = 0.125
        spec.assume("thickness_in", t, "in", "1/8 in 5052 — bends without cracking")
    width = float(spec.get("width_in") or 0.0) or 2.0
    bolt = hw.FASTENERS.get(str(spec.get("fastener") or "10-32"), hw.FASTENERS["10-32"])
    pitch = float(spec.get("hole_pitch_in") or 1.0)

    def leg_bores(length: float) -> list[dict[str, Any]]:
        return [bore(bolt["clearance_in"], sx * pitch / 2, -length / 2 + 0.5,
                     note="leg hole",
                     expr={"d": "holeDiameter",
                           "x": f"{'' if sx > 0 else '-'}(holePitch / 2)"})
                for sx in (-1, 1)]

    features = [
        plate("bracket leg A", (width, t, leg_a), _at(0, t / 2, leg_a / 2), mat="aluminium",
              bores=leg_bores(leg_a),
              expr={"sx": "bracketWidth", "sy": "bracketThickness", "sz": "legA"}),
        plate("bracket leg B", (width, leg_b, t), _at(0, leg_b / 2, t / 2), mat="aluminium",
              bores=[],
              expr={"sx": "bracketWidth", "sy": "legB", "sz": "bracketThickness"}),
    ]
    return _single(
        _asm("part", "L bracket", "part", features, note="one blank, bent 90 degrees",
             mates=["leg A bolts to the first face", "leg B bolts to the second"]),
        {"legA": leg_a, "legB": leg_b, "bracketWidth": width, "bracketThickness": t,
         "holeDiameter": bolt["clearance_in"], "holePitch": pitch},
        [_dim("Leg A", leg_a, spec.source_of("leg_a_in")),
         _dim("Leg B", leg_b, spec.source_of("leg_b_in")),
         _dim("Width", width, spec.source_of("width_in")),
         _dim("Thickness", t, spec.source_of("thickness_in")),
         _dim("Hole", bolt["clearance_in"], VERIFIED, note=f"{bolt['name']} clearance")],
        [hw.as_bom_row(bolt, 4)])


# ── registry ─────────────────────────────────────────────────────────────────
# The router picks by part type. Adding a generator is adding one entry here plus the
# function; nothing else in the pipeline has to know about it.
GENERATORS: dict[str, Callable[[EngineeringSpec], dict[str, Any]]] = {
    "bearing_block": bearing_block,
    "bearing_housing": bearing_block,
    "shaft": shaft,
    "hex_shaft": shaft,
    "spacer": spacer,
    "plate": rect_plate,
    "gusset": gusset_plate,
    "motor_plate": motor_plate,
    "gearbox_plate": gearbox_plate,
    "roller": roller,
    "pulley": pulley_part,
    "bracket": bracket,
}

SUPPORTED_PART_TYPES = tuple(sorted(set(GENERATORS)))


def build(part_type: str, spec: EngineeringSpec) -> dict[str, Any] | None:
    """Run one generator, or None when nothing here makes that part.

    None is a real answer and the caller must handle it: claiming to have built an arbitrary
    shape is the failure mode this whole module is arranged to avoid.
    """
    generator = GENERATORS.get(part_type)
    return generator(spec) if generator else None
