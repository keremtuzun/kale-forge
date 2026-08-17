"""Robot spec → dimensioned CAD feature tree.

The Design Studio's spec says *what* a robot is: a three-stage cascade elevator, a
dual-roller intake, an MK4i drivetrain.  This module says what that is *made of* — the
individual tubes, plates, shafts, bearings, pulleys, gears, belts, wheels and fasteners,
each with a real section, a real length and a real position in one coordinate system.

That is the difference between a diagram and CAD.  A viewer, a BOM, an Onshape export or a
cut list can all be driven off the same tree, and every consumer sees the same robot.

Coordinate system (inches, right-handed, Y up):

    +X  robot right
    +Y  up, with Y = 0 at the top face of the bellypan
    -Z  toward the front of the robot (the intake end); +Z is the back

Every feature carries ``at`` (its centre) and ``rot`` (XYZ Euler degrees).  A tube's length
runs along its local Z before rotation, so a rail across the front of the robot is a tube
rotated 90° about Y.  Nothing here is a guess about a specific vendor part: sections come
from the STRUCTURE catalog, motor envelopes from MOTORS, module plates from SWERVE_MODULES.

Nothing in this module claims fabrication readiness.  It is dimensioned concept geometry —
the numbers are consistent with each other, not verified against a manufacturing drawing.
"""
from __future__ import annotations

import math
import random
from typing import Any

from app.services.electronics_detail import device_bodies, harness_cables
from app.services.frc_parts import ELECTRONICS, MOTORS, STRUCTURE, SWERVE_MODULES

CAD_VERSION = "kale-cad-2.2"

# Materials the renderer and the BOM both understand.  Keeping this closed means a feature
# can never arrive with a finish nobody knows how to draw or price.
MATERIALS = ("aluminium", "aluminium-dark", "steel", "polycarb", "urethane", "delrin",
             "nylon", "rubber", "anodised", "copper", "plastic-black", "plywood", "foam")

# Standard stock the mechanisms are built from, so a cut list is a real cut list.
TUBE_2X1 = STRUCTURE["tube_2x1"]["section_in"]
TUBE_1X1 = STRUCTURE["tube_1x1"]["section_in"]
TUBE_2X2 = STRUCTURE["tube_2x2"]["section_in"]

# Telescoping ladders. Each entry is (section, wall), outermost first; a stage only ever
# nests in the member before it, so the ladder — not arithmetic — decides how many moving
# stages a tower can have. Running out of ladder is a design answer, not a licence to invent
# a section no supplier stocks.
LADDER_HEAVY = [(TUBE_2X2, 0.125),
                (STRUCTURE["tube_1_5x1_5"]["section_in"], 0.0625),
                (STRUCTURE["tube_1x1_thin"]["section_in"], 0.0625)]
LADDER_LIGHT = [(TUBE_2X1, 0.100),
                (STRUCTURE["tube_1_5x0_5"]["section_in"], 0.0625)]

def ladder_for(moving_stages: int) -> list[tuple[tuple[float, float], float]]:
    """The nesting ladder deep enough for this many moving stages."""
    ladder = LADDER_HEAVY if moving_stages >= 2 else LADDER_LIGHT
    if moving_stages + 1 > len(ladder):
        # No ladder goes deeper than three. Cap the geometry rather than invent a section.
        return ladder
    return ladder[:moving_stages + 1]


def ladder_label(moving_stages: int) -> str:
    """The ladder written out for a spec sheet, e.g. '2x2 → 1.5x1.5 → 1x1 nesting ladder'.

    The spec block and the CAD tree have to name the same stock. This used to be a hardcoded
    "2x1 tube", which stopped being true for a three-stage tower the moment stages started
    coming off the heavy ladder — and both strings are trained on.
    """
    rungs = ladder_for(moving_stages)
    text = " → ".join(f"{sec[0]:g}x{sec[1]:g}" for sec, _ in rungs)
    return f"{text} nesting ladder" if len(rungs) > 1 else f"{text} tube"


def cad_section_vocabulary() -> str:
    """The stock sections a member is allowed to be, written out for the model.

    Generated from the catalog rather than typed by hand: an earlier revision listed three
    sections in the prompt while the targets used six, which trains a model to disbelieve its
    own instructions. Training and evaluation both call this.
    """
    keys = ("tube_2x1", "tube_1x1", "tube_2x2", "tube_1_5x1_5", "tube_1x1_thin", "tube_1_5x0_5")
    return ", ".join(
        f"{STRUCTURE[k]['section_in'][0]:g}x{STRUCTURE[k]['section_in'][1]:g}x"
        f"{STRUCTURE[k]['wall_in']:.4g}" for k in keys)


SYSTEM_CAD = (
    "You are Kale Forge Design Model working at CAD level. Given a robot request and one "
    "subsystem, emit that subsystem's assembly as dimensioned parts in Kale's CAD schema.\n"
    "- Units are inches. The origin is the frame centre on the top face of the bellypan; "
    "+X is robot right, +Y is up, -Z is toward the front of the robot.\n"
    "- Every feature has t (type), n (name) and at ([x, y, z] centre). Rotations are XYZ "
    "Euler degrees in rot. A tube's length runs along its own Z before rotation.\n"
    f"- Structural members are catalog stock sections only: {cad_section_vocabulary()}.\n"
    "- Telescoping stages nest: each moving stage is the next section down that list, never "
    "an intermediate size invented to make the arithmetic work.\n"
    "- Bearings, pulleys, gears and shafts carry real bores, tooth counts and pitches; a "
    "pitch diameter must follow from the tooth count, not from the space available.\n"
    "- A left/right or dual pair is ONE feature with mirror: 'x'|'y'|'z' — the reflected twin "
    "is derived for you. Never emit the second copy yourself, and never put a mirrored "
    "feature on the mirror plane.\n"
    "- Emit one JSON object with id, name, kind, origin, features and mates. Nothing else.\n"
    "This is dimensioned concept geometry. Never claim it is ready to machine."
)

_HEX_BORE = 0.5          # 1/2 in hex, the default FRC shaft
_BEARING_OD = 1.125      # standard 1/2 in bore flanged bearing
_BOLT_DIA = 0.190        # 10-32
_HTD_PITCH_MM = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Feature constructors.  Each returns one dict; the keys are deliberately short
# because a full robot is several hundred features and this travels over the wire.
# ─────────────────────────────────────────────────────────────────────────────
def _meshed(base: list[float], r_sum: float, dx: float, dz: float) -> list[float]:
    """Centre for a gear meshing a partner at `base`: exactly the sum of the two
    pitch radii away, along (dx, dz) in the gear plane. Gear centres are
    computed, not eyeballed — a gap or an overlap here is teeth that don't
    carry torque, and the transmission validator measures exactly this."""
    n = math.sqrt(dx * dx + dz * dz) or 1.0
    return [base[0] + r_sum * dx / n, base[1], base[2] + r_sum * dz / n]


def _at(x: float, y: float, z: float) -> list[float]:
    return [round(x, 3), round(y, 3), round(z, 3)]


def _rot(rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> list[float] | None:
    if rx == 0 and ry == 0 and rz == 0:
        return None
    return [round(rx, 2), round(ry, 2), round(rz, 2)]


def _feat(_t: str, _n: str, _at_: list[float], **kw: Any) -> dict[str, Any]:
    """Underscored positional names so a feature is free to carry a `kind`/`name` field."""
    feature: dict[str, Any] = {"t": _t, "n": _n, "at": _at_}
    for key, value in kw.items():
        if value is None:
            continue
        feature[key] = value
    return feature


def tube(name: str, section: tuple[float, float], length: float, at: list[float],
         rot: list[float] | None = None, *, wall: float = 0.100,
         mat: str = "aluminium", bolts: float | None = 2.0,
         pockets: bool = False, note: str = "",
         allow: list[str] | None = None) -> dict[str, Any]:
    """A length of rectangular tube.  ``bolts`` is the hole pitch down the long face.

    ``allow`` names the parts this one is MEANT to sit inside — a telescoping stage nests in
    its parent by design, and the geometry audit would otherwise report a stage 100% buried
    in the tower as a truncated component. Declaring it here, on the part that nests, is the
    honest form: it authorises one interface rather than exempting every tube on the robot.
    """
    return _feat("tube", name, at, sec=[section[0], section[1]], len=round(length, 3),
                 wall=wall, rot=rot, mat=mat, bolt_pitch=bolts, pockets=pockets or None,
                 note=note or None, allow=allow or None)


def bore(dia: float, x: float = 0.0, z: float = 0.0, *, depth: float = 0.0,
         form: str = "round", note: str = "",
         expr: dict[str, str] | None = None) -> dict[str, Any]:
    """One hole through or into a plate, in the plate's own XZ.

    `depth` 0 means through. `form` "hex" cuts across the flats, which is what a spacer or a
    hub on a hex shaft actually has. These are REAL material removal in every consumer —
    the viewer extrudes the profile with holes, the STEP worker cuts solids, FeatureScript
    sketches and removes — because a bearing block whose pocket is only a number in the
    dossier is not a bearing block.

    `expr` maps any of d/x/z/depth to the FeatureScript expression that produced the number,
    so the export carries the relationship ("half the block, less the inset") and not just
    the answer. Geometry is unaffected; only the exported source reads differently.
    """
    out: dict[str, Any] = {"d": round(dia, 4), "x": round(x, 4), "z": round(z, 4)}
    if depth:
        out["depth"] = round(depth, 4)
    if form != "round":
        out["form"] = form
    if note:
        out["note"] = note
    if expr:
        out["expr"] = dict(expr)
    return out


def plate(name: str, size: tuple[float, float, float], at: list[float],
          rot: list[float] | None = None, *, mat: str = "aluminium",
          pockets: int = 0, bores: list[dict[str, Any]] | None = None,
          fillet: float = 0.0, note: str = "",
          expr: dict[str, str] | None = None) -> dict[str, Any]:
    """A flat plate.  ``size`` is (width, thickness, depth) in its own frame.

    ``bores`` are holes cut in it — bearing pockets, mounting holes, shaft clearance. They
    are what turns this primitive from a block into a part.

    ``expr`` names the driving variable behind sx/sy/sz for the FeatureScript export.
    """
    feature = _feat("plate", name, at, size=[round(v, 3) for v in size], rot=rot, mat=mat,
                    pockets=pockets or None, note=note or None)
    if bores:
        feature["bores"] = list(bores)
    if fillet:
        feature["fillet"] = round(fillet, 4)
    if expr:
        feature["expr"] = dict(expr)
    return feature


def shaft(name: str, dia: float, length: float, at: list[float],
          rot: list[float] | None = None, *, form: str = "hex",
          mat: str = "steel", note: str = "") -> dict[str, Any]:
    return _feat("shaft", name, at, dia=dia, len=round(length, 3), rot=rot, form=form,
                 mat=mat, note=note or None)


def bearing(name: str, at: list[float], rot: list[float] | None = None, *,
            bore: float = _HEX_BORE, od: float = _BEARING_OD, width: float = 0.313,
            flanged: bool = True, note: str = "") -> dict[str, Any]:
    return _feat("bearing", name, at, bore=bore, od=od, w=width, rot=rot, flanged=flanged,
                 note=note or None)


def pulley(name: str, teeth: int, width: float, at: list[float],
           rot: list[float] | None = None, *, pitch_mm: float = _HTD_PITCH_MM,
           bore: float = _HEX_BORE) -> dict[str, Any]:
    """HTD timing pulley.  Pitch diameter = teeth × pitch / π, in inches."""
    pd = teeth * pitch_mm / math.pi / 25.4
    return _feat("pulley", name, at, teeth=teeth, pitch_mm=pitch_mm, pd=round(pd, 3),
                 w=width, bore=bore, rot=rot)


def sprocket(name: str, teeth: int, at: list[float], rot: list[float] | None = None, *,
             pitch_in: float = 0.25, width: float = 0.25) -> dict[str, Any]:
    pd = pitch_in / math.sin(math.pi / teeth)
    return _feat("sprocket", name, at, teeth=teeth, pitch_in=pitch_in, pd=round(pd, 3),
                 w=width, rot=rot)


def gear(name: str, teeth: int, at: list[float], rot: list[float] | None = None, *,
         dp: float = 20.0, face: float = 0.375, bore: float = _HEX_BORE,
         mat: str = "steel", lightened: int = 0) -> dict[str, Any]:
    """Spur gear.  Pitch diameter = teeth / diametral pitch."""
    return _feat("gear", name, at, teeth=teeth, dp=dp, pd=round(teeth / dp, 3), face=face,
                 bore=bore, rot=rot, mat=mat, pockets=lightened or None)


def belt(name: str, frm: list[float], to: list[float], width: float, *,
         kind: str = "HTD 5 mm", teeth: int | None = None) -> dict[str, Any]:
    return _feat("belt", name, frm, to=to, w=width, kind=kind, teeth=teeth)


def wheel(name: str, dia: float, width: float, at: list[float],
          rot: list[float] | None = None, *, kind: str = "tread",
          durometer: str = "", note: str = "") -> dict[str, Any]:
    return _feat("wheel", name, at, dia=dia, w=width, kind=kind, rot=rot,
                 duro=durometer or None, note=note or None)


def motor(name: str, key: str, at: list[float], rot: list[float] | None = None,
          *, scale: float = 1.0) -> dict[str, Any]:
    spec = MOTORS.get(key, MOTORS["neo"])
    return _feat("motor", name, at, key=key, dia=spec.get("diameter_in", 2.2),
                 len=spec.get("length_in", 3.5), rot=rot, scale=None if scale == 1.0 else scale)


def gearbox(name: str, size: tuple[float, float, float], at: list[float],
            rot: list[float] | None = None, *, ratio: str = "",
            stages: int = 2) -> dict[str, Any]:
    return _feat("gearbox", name, at, size=[round(v, 3) for v in size], rot=rot,
                 ratio=ratio or None, stages=stages)


def gusset(name: str, size: tuple[float, float], at: list[float],
           rot: list[float] | None = None, *, thickness: float = 0.090,
           form: str = "flat", leg: float = 0.0, holes: float = 0.0,
           mat: str = "aluminium", note: str = "") -> dict[str, Any]:
    """A bolt-on joining plate.

    ``form`` is how the blank leaves the brake press, and it is not cosmetic — it decides how
    many tube faces the joint actually catches:

    * ``flat``     one face, the plate every corner gets;
    * ``angle``    bent 90°, catching two faces off one part (``leg`` is the bent leg's
                   height; it defaults to two thirds of the plate);
    * ``triangle`` a flat plate with the unloaded corner cut away, which is what a shear
                   gusset in a truss actually looks like.

    Bent parts are 5052, not 6061: the reference teams bend sheet in 5052 because 6061 cracks
    at the bend radius a bracket this size needs. ``holes`` is the hole pitch the blank is
    punched on, so a gusset lands on the rail's existing rows instead of asking for new ones.
    """
    feature = _feat("gusset", name, at, size=[size[0], size[1]], th=thickness, rot=rot,
                    mat=mat, note=note or None)
    if form != "flat":
        feature["form"] = form
        if form == "angle":
            feature["leg"] = round(leg or min(size) * 0.66, 3)
            feature["alloy"] = "5052"
    if holes:
        feature["pitch"] = round(holes, 3)
    return feature


def polycarb(name: str, size: tuple[float, float, float], at: list[float],
             rot: list[float] | None = None, *, note: str = "") -> dict[str, Any]:
    return _feat("polycarb", name, at, size=[round(v, 3) for v in size], rot=rot,
                 note=note or None)


def rope(name: str, frm: list[float], to: list[float], dia: float = 0.125,
         *, material: str = "Dyneema") -> dict[str, Any]:
    return _feat("rope", name, frm, to=to, dia=dia, mat=material)


def hardstop(name: str, at: list[float], rot: list[float] | None = None,
             *, size: tuple[float, float, float] = (0.75, 0.5, 0.75)) -> dict[str, Any]:
    return _feat("hardstop", name, at, size=[round(v, 3) for v in size], rot=rot)


def fastener_row(name: str, at: list[float], count: int, step: list[float],
                 *, dia: float = _BOLT_DIA, rot: list[float] | None = None,
                 length: float = 0.75) -> dict[str, Any]:
    """One linear pattern of bolts, expanded by the consumer rather than stored N times."""
    return _feat("bolts", name, at, dia=dia, len=length, rot=rot,
                 rep={"n": count, "step": [round(v, 3) for v in step]})


def _foot_bolts(name: str, x: float, z: float, *, count: int = 2, spread_x: float = 1.4,
                length: float = 1.3) -> dict[str, Any]:
    """Vertical bolts dropping through a foot plate into the member below it.

    Under rot(90,0,0) the row's local X step stays lateral, so the bolts spread across the
    plate while pointing down through the crossmember — the joint every mate string claims.
    """
    return fastener_row(name, _at(x, 0.05, z), count, [spread_x, 0, 0],
                        rot=_rot(90, 0, 0), length=length)


# One Z per mechanism, shared by the chassis and the mechanism itself. The crossmember and
# the feet that bolt to it must come from the same number, or the mate is a story: the old
# code kept two sets of default biases and two clamps, and a 1x1 crossmember could sit a
# station away from the feet it was supposed to carry.
# The elevator's 0.88 is a real decision, not a nudge. A tower is the tallest and heaviest
# thing on the robot, and it belongs against the BACK rail: its feet bolt to structure at
# both ends, its mass sits behind the drive centre, and the whole front of the frame is left
# for the intake and the gamepiece path. That is where the published swerve CADs put it.
# At 0.58 it stood in the middle of the robot with the hopper in front of it and the shooter
# behind, which is the one place a tower should never be. The bias clamps to 0.38 of the
# frame length, which lands a 7 in deep tower's back face on the rail.
_STATION_DEFAULTS = (("hopper", 0.42), ("shooter", 0.65), ("elevator", 0.88),
                     ("manipulator", 0.54), ("climber", 0.85))


def _package_pos(spec: dict[str, Any], axis: str, origin: float, want: float, reach: float,
                 side: float = 0.0, limit: float | None = None) -> float:
    """Local coordinate for a motor/gearbox package so its far end stays inside the frame.

    Every mechanism here mounts its drive package at "the edge of the thing it drives, plus a
    constant" — and nothing checked that the edge was still on the robot. A Kraken hanging
    two inches past the frame perimeter at bumper height is not a packaging quirk: it is
    outside the bumper, so it is the first thing another robot hits, and it fails inspection
    besides.

    ``reach`` is how far the package extends from its mount and ``side`` which way (by
    default, whichever way ``want`` already leans). ``limit`` overrides the frame line when
    something closer in is what the package really has to clear — a drivetrain corner, say.
    The result only ever moves the package INBOARD, so a mechanism that already fitted is
    left exactly where it was.
    """
    span = spec["frame"]["width_in" if axis == "x" else "length_in"]
    limit = span / 2 - 0.35 if limit is None else limit
    side = side or (1.0 if want >= 0 else -1.0)
    if side > 0:
        return min(want, limit - reach - origin)
    return max(want, reach - limit - origin)


def _package_x(spec: dict[str, Any], origin_x: float, want_x: float, reach: float,
               side: float = 0.0, limit: float | None = None) -> float:
    return _package_pos(spec, "x", origin_x, want_x, reach, side, limit)


def _module_inset(spec: dict[str, Any]) -> float:
    """How far a swerve module's centre sits in from the frame corner.

    One definition, read by the drivetrain that places the modules, by the chassis that has
    to keep crossmembers out of their wheels, and by the electrical mast that has to not
    stand in one. Three copies of this number is how the mast ended up inside a wheel.
    """
    dt = spec.get("drivetrain") or {}
    if dt.get("type") == "west-coast" or not int(dt.get("module_count") or 0):
        return 0.0
    plate_in = dt.get("plate_in") or (4.10, 4.10)
    return plate_in[0] / 2 + 0.55


def _module_bands(spec: dict[str, Any], pad: float = 0.0) -> list[tuple[float, float]]:
    """The Z bands a crossmember must not land in, because the drivetrain is already there.

    On a swerve robot that is the two corners' wheels. On a west-coast it is the pair of
    drop-centre gearboxes, which sit inboard of the drive rails on the robot's centreline —
    a crossmember at z = 0 goes straight through both of them.
    """
    dt = spec.get("drivetrain") or {}
    half_l = spec["frame"]["length_in"] / 2
    inset = _module_inset(spec)
    if not inset:
        if dt.get("type") == "west-coast":
            reach = 1.0 + pad + 0.3           # gearbox half-depth in Z
            return [(-reach, reach)]
        return []
    reach = dt.get("wheel_diameter_in", 4.0) / 2 + pad + 0.2
    centre = half_l - inset
    return [(-centre - reach, -centre + reach), (centre - reach, centre + reach)]


def _stations(spec: dict[str, Any]) -> dict[str, float]:
    """Resolved station Z (robot frame) for every included mechanism.

    Applies the hopper's clear-the-tower shift, the chassis clamp and the shared-crossmember
    snap in ONE place, so a mechanism's origin and its crossmember can never disagree. Two
    mechanisms within 2.5 in share a crossmember — and both are moved onto it.
    """
    frame = spec["frame"]
    ln = frame["length_in"]
    defaults = dict(_STATION_DEFAULTS)
    zs: dict[str, float] = {}
    for key, default_bias in _STATION_DEFAULTS:
        blk = spec.get(key) or {}
        if not blk.get("included"):
            continue
        bias = blk.get("position_bias", default_bias)
        if key == "hopper":
            # Two mechanisms may not occupy the same air: when a tower (elevator or arm
            # post) shares the robot with the hopper, the hopper pulls forward until its
            # back wall clears the tower plane — polycarb through a tube is a render, not
            # a robot.
            depth = blk.get("floor_depth_in", 14.0)
            for other in ("elevator", "manipulator"):
                ob = spec.get(other) or {}
                if ob.get("included"):
                    other_bias = ob.get("position_bias", defaults[other])
                    if bias * ln + depth / 2 > other_bias * ln - 1.6:
                        bias = max(0.12, (other_bias * ln - 1.6 - depth / 2) / ln)
        z = max(-0.38, min(0.38, bias - 0.5)) * ln
        for prior in zs.values():
            if abs(z - prior) <= 2.5:
                z = prior
                break
        zs[key] = round(z, 3)
    return zs


# ─────────────────────────────────────────────────────────────────────────────
# Structural decisions
# ─────────────────────────────────────────────────────────────────────────────
class Choices:
    """The decisions that change a robot's *part list*, not just its dimensions.

    Two teams handed the same requirement do not build the same parts. One drives a roller
    off a belt, another off chain; one carries a mechanism on plate, another on tube; a long
    frame needs a crossmember a short one does not. Those are the decisions here.

    Every choice is resolved from the design seed, so a prompt always produces the same robot,
    while different prompts produce genuinely different *constructions*. That matters beyond
    variety for its own sake: a corpus generated from a single fixed part list teaches a model
    to reproduce that list, not to understand how a mechanism goes together.

    Where a choice is really driven by engineering — crossmember count by unsupported span,
    ladder depth by stage count — it follows the engineering, and the seed only breaks ties.
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        seed = spec.get("design_seed")
        self.rng = random.Random(seed if seed is not None else 0)
        frame = spec["frame"]
        self.width, self.length = frame["width_in"], frame["length_in"]

        pick = self.rng.choice
        # Chassis. Unsupported span drives the crossmember count, and a tall superstructure
        # adds one to carry its load into the rails rather than into the bellypan.
        loaded = sum(1 for key in ("elevator", "climber", "manipulator")
                     if (spec.get(key) or {}).get("included"))
        self.crossmembers = (1 if self.length < 25 else (2 if self.length < 31 else 3))
        if loaded >= 2:
            self.crossmembers += 1
        self.crossmember_section = pick([TUBE_1X1, TUBE_1X1, TUBE_2X1])
        self.gusset_faces = pick([1, 1, 2])          # top only, or top and inboard face
        self.bellypan_style = pick(["pocketed", "pocketed", "lattice", "two-piece"])
        self.rail_pockets = pick([True, True, False])

        # Power transmission. Belt is quiet and needs tension; chain takes shock and needs a
        # tensioner; a gear pair is compact and loud. All three are built in FRC every year.
        # Belt or chain, not gears. A gear train only carries torque tooth to tooth, so
        # gear-driving an over-bumper arm means the whole power package lives out at the
        # roller bar — in front of the frame, at bumper height, which is the one place on a
        # robot a motor is guaranteed to get hit. The branch that did it also walked the
        # train the wrong way down the arm and put the gearbox 0.7 in off the carpet, seven
        # inches in front of the bumper. Teams belt or chain these for the same reason.
        self.intake_drive = pick(["belt", "belt", "belt", "chain"])
        self.intake_supports = pick(["plate", "plate", "tube"])
        self.intake_hardstops = pick([True, True, False])
        # Hopper construction. The floor takes the wear and the walls take the impact, so teams
        # build them out of different things — a polycarb floor slides better and a plate floor
        # stays flat, and the walls are bent polycarb or folded sheet depending on the shop.
        self.hopper_floor = pick(["polycarb", "polycarb", "plate"])
        self.hopper_walls = pick(["polycarb", "polycarb", "plate"])
        self.hopper_drive = pick(["belt", "belt", "chain"])
        self.hopper_agitator = pick([True, False])
        self.shooter_feeder = pick(["roller", "belt", "kicker"])
        self.shooter_hood_drive = pick(["servo", "rack", "fixed"])
        self.elevator_rigging = pick(["belt", "belt", "chain", "rope"])
        self.elevator_bearings = pick(["roller", "roller", "slide"])
        self.elevator_braces = pick([1, 2])
        self.arm_shoulder_drive = pick(["gear", "chain", "belt"])
        self.climber_hooks = 1
        self.climber_hold = pick(["ratchet", "ratchet", "brake+ratchet"])
        self.module_mount = pick(["through-rail", "corner-plate"])

    def ladder(self, moving_stages: int) -> list[tuple[tuple[float, float], float]]:
        """The nesting ladder deep enough for this many moving stages."""
        return ladder_for(moving_stages)


# ─────────────────────────────────────────────────────────────────────────────
# Assemblies
# ─────────────────────────────────────────────────────────────────────────────
def _asm(asm_id: str, name: str, kind: str, features: list[dict[str, Any]], *,
         origin: list[float] | None = None, note: str = "",
         mates: list[str] | None = None,
         articulation: dict[str, Any] | None = None) -> dict[str, Any]:
    asm: dict[str, Any] = {"id": asm_id, "name": name, "kind": kind,
                           "origin": origin or [0.0, 0.0, 0.0],
                           "features": [f for f in features if f],
                           "mates": mates or [], "note": note}
    if articulation:
        # How the mechanism actually moves: a limited arc about a real axle, for the viewer
        # and any downstream consumer. Never a licence to spin a part 360°.
        asm["articulation"] = articulation
    return asm


# ── Bumpers ──────────────────────────────────────────────────────────────────
# Measured off the reference chassis (Onshape, "kerem şase bumper": 700 x 600 mm frame in
# 50 x 25 mm tube): the bumper wrap stands 5.00 in tall and 3.31 in proud of the frame on
# every side, underside flush with the bottom of the frame rail.  3.31 in is a construction,
# not an envelope: 0.75 in plywood backing + a Ø2.5 in pool noodle + the fabric wrap
# (0.75 + 2.50 + 2 × 0.03 = 3.31).  Two noodles stack to make the 5.00 in face: 2 × 2.50.
# The bumper is modelled the way the reference builds it — per side: one plywood backing,
# two stacked noodles, one fabric wrap, mounting brackets into the rail — never one slab.
#
# Two authorities have to agree here.  HOW it goes together comes from the team's Onshape
# course (one continuous ring swept round a rounded-corner path).  WHAT SIZE each piece is
# comes from the game manual's bumper rules, because a bumper built the right way to the
# wrong dimensions is a bumper that fails inspection:
#
#   R402-A  padding at least 2.25 in deep and 4.5 in tall
#   R402-B  backing at least 4.5 in tall, 3/4 in nominal plywood
#   R402-C  cloth over every outward, upward and downward padding face, nothing exposed
#   R404    no hard bumper part more than 1.25 in outboard of the frame perimeter, and
#           padding standing at least 2.0 in proud of any hard part
#   R405    padding and backing entirely filling the BUMPER ZONE, 2.5 – 5.75 in off the floor
#   R406    uncompressed padding at least 2.25 in around every corner
#   R410    a rigid fastening system — removable by two people in under five minutes
#   R412    white numerals at least 3.75 in tall with at least a 0.5 in stroke, in three or
#           more places about 90° apart
BUMPER_HEIGHT_IN = 5.00
BUMPER_THICKNESS_IN = 3.31
BUMPER_PLYWOOD_IN = 0.75
BUMPER_NOODLE_DIA_IN = 2.50
BUMPER_FABRIC_IN = 0.03
BUMPER_NOODLE_IN = round(BUMPER_THICKNESS_IN - BUMPER_PLYWOOD_IN, 3)

# The carpet, in robot coordinates.  The drivetrain sets every wheel so the frame rides 2 in
# off the floor, which puts the carpet at Y = -2.0 and the rail underside (Y = 0) 2 in up.
# Every bumper-zone dimension in the manual is measured from the carpet, so the geometry and
# the rule check have to read that ride height from ONE constant or they will disagree about
# whether a legal bumper is legal.
RIDE_HEIGHT_IN = 2.00
BUMPER_ZONE_IN = (2.50, 5.75)

# R412.  4.0 in of digit height and a 5/8 in stroke clear the 3.75 / 0.5 in minimums with
# enough margin to survive a cloth wrap, and four digits still fit inside a 27 in side.
TEAM_NUMBER_HEIGHT_IN = 4.00
TEAM_NUMBER_STROKE_IN = 0.625
TEAM_NUMBER_DIGIT_W_IN = 2.55        # condensed bold, the weight teams actually cut vinyl in

# Bumper mounting hardware.  The reference teams all land on the same stack: a bent 5052
# bracket that hooks the rail, one 1/4-20 through the plywood into it, and a flange nut.
# Two people, four nuts, well under five minutes — which is what R410 is really asking.
BUMPER_BOLT_DIA_IN = 0.25
BUMPER_SCREW_DIA_IN = 0.164          # #8 wood screw into the plywood edge


def noodle(name: str, dia: float, length: float, at: list[float],
           rot: list[float] | None = None) -> dict[str, Any]:
    """A pool noodle: closed-cell foam cylinder, the energy absorber of every FRC bumper."""
    return _feat("noodle", name, at, dia=dia, len=round(length, 3), rot=rot, mat="foam")


def noodle_corner(name: str, dia: float, bend: float, at: list[float],
                  rot: list[float] | None = None) -> dict[str, Any]:
    """A quarter-bend of pool noodle joining two straight runs at a frame corner.

    The team's course models the bumper as ONE ring swept around a rounded-corner
    path — the noodles are continuous around the corner, never butted square. The
    bend radius is the noodle centreline's distance from the frame corner."""
    return _feat("noodle_corner", name, at, dia=dia, bend=round(bend, 3), rot=rot, mat="foam")


def fabric(name: str, size: tuple[float, float, float], at: list[float],
           rot: list[float] | None = None, *, bend: float = 0.0,
           note: str = "") -> dict[str, Any]:
    """A bumper fabric wrap: a U-channel of cloth (outer face + top + bottom returns) whose
    ``size`` is (length along the segment, height, wrapped depth). Carries the alliance
    colour in the viewer and the team number rides on it.

    ``bend`` turns the straight channel into a quarter-bend of the same channel, swept at
    that centreline radius, which is what closes a corner. R402-C wants cloth over every
    outward-facing padding surface: four straight wraps leave eight corner noodle bends bare,
    and bare foam at the corner is the single most common bumper re-inspection."""
    feature = _feat("fabric", name, at, size=[round(v, 3) for v in size], rot=rot,
                    note=note or None)
    if bend:
        feature["bend"] = round(bend, 3)
    return feature


def decal(name: str, text: str, at: list[float], *, height: float,
          stroke: float, rot: list[float] | None = None,
          note: str = "") -> dict[str, Any]:
    """Applied lettering — in practice, the team number on the bumper cloth.

    This is a part, not a caption. R412 gives the numerals a minimum height and a minimum
    stroke width and an inspector measures both with a ruler, so they belong in the feature
    tree where the rule check, the STEP export and the viewer all read the same two numbers.
    Drawn only in the renderer, as they were, they were 1.95 in tall — a bit over half the
    legal height — and nothing in the pipeline was in a position to notice."""
    width = round(max(1, len(str(text))) * TEAM_NUMBER_DIGIT_W_IN, 3)
    return _feat("decal", name, at, text=str(text),
                 size=[width, round(height, 3), 0.02], stroke=round(stroke, 3),
                 rot=rot, note=note or None)


def bumper_envelope(width_in: float, length_in: float) -> dict[str, float]:
    """The bumper's outer envelope, which is what the robot is actually measured at.

    Kept separate from the features so the viewer, the rule check and the packaging maths all
    read one definition instead of each re-deriving it from a plate size.
    """
    t = BUMPER_THICKNESS_IN
    floor_bottom = RIDE_HEIGHT_IN                       # rail underside, off the carpet
    floor_top = round(RIDE_HEIGHT_IN + BUMPER_HEIGHT_IN, 3)
    hard_out = round(BUMPER_FABRIC_IN + BUMPER_PLYWOOD_IN, 3)   # plywood outer face
    return {"thickness_in": t, "height_in": BUMPER_HEIGHT_IN,
            "plywood_in": BUMPER_PLYWOOD_IN, "noodle_in": BUMPER_NOODLE_IN,
            "noodle_dia_in": BUMPER_NOODLE_DIA_IN, "noodles_per_segment": 2,
            "fabric_in": BUMPER_FABRIC_IN,
            "construction": (f"{BUMPER_PLYWOOD_IN:g} in plywood + 2 stacked Ø"
                             f"{BUMPER_NOODLE_DIA_IN:g} in noodles + fabric wrap"),
            "outer_width_in": round(width_in + 2 * t, 3),
            "outer_length_in": round(length_in + 2 * t, 3),
            "bottom_y_in": 0.0, "top_y_in": BUMPER_HEIGHT_IN,
            # Everything below is measured from the carpet, because that is where the manual
            # measures it from. Quoting a bumper height without saying how high off the floor
            # it sits says nothing about whether it fills the zone.
            "ride_height_in": RIDE_HEIGHT_IN,
            "floor_to_bottom_in": floor_bottom, "floor_to_top_in": floor_top,
            "zone_in": list(BUMPER_ZONE_IN),
            "zone_filled": floor_bottom <= BUMPER_ZONE_IN[0] + 1e-9
                           and floor_top >= BUMPER_ZONE_IN[1] - 1e-9,
            "hard_part_out_in": hard_out,
            "padding_proud_of_hard_in": round(t - hard_out, 3),
            "corner_padding_in": round(BUMPER_FABRIC_IN + BUMPER_PLYWOOD_IN
                                       + BUMPER_NOODLE_DIA_IN, 3),
            "number_height_in": TEAM_NUMBER_HEIGHT_IN,
            "number_stroke_in": TEAM_NUMBER_STROKE_IN,
            "number_colour": "white", "number_locations": 4}


def _bumper(w: float, ln: float, team_number: int | str = 0,
            corner_low: bool = True) -> list[dict[str, Any]]:
    """The bumper ring, built the way the reference builds it and sized the way R402–R412
    measures it.

    Per segment, inside out: a plywood backing standing the full 5.00 in face, two Ø2.5 in
    pool noodles stacked on the plywood's outer face, and the fabric wrap closing over the
    outer face and the top and bottom returns.  The front and back segments run the full
    outer width and the side segments are captured between them.  The corners are continuous
    — a quarter-bend of noodle at each level and a quarter-bend of cloth over them — because
    the course sweeps the whole ring round a rounded-corner path, and because bare foam at a
    corner is a re-inspection.  Every segment is hung on an angle bracket and pinned by a
    through-bolt, and carries the team number at its legal size.
    """
    t = BUMPER_THICKNESS_IN
    ply = BUMPER_PLYWOOD_IN
    fab = BUMPER_FABRIC_IN
    nd = BUMPER_NOODLE_DIA_IN
    nr = nd / 2
    h = BUMPER_HEIGHT_IN
    half_w, half_l = w / 2, ln / 2
    mid_y = h / 2
    # Stack, working outward from the frame face: fabric return, plywood, noodles, fabric.
    ply_off = fab + ply / 2                 # plywood centreline off the frame face
    noodle_off = fab + ply + nr             # noodle centreline off the frame face
    wrap_off = t / 2                        # fabric wrap centre: the whole 3.31 stack
    ply_note = f"{ply:g} in plywood backing, {h:g} in tall, underside flush with the frame"
    features: list[dict[str, Any]] = []

    # Front and back segments run the full outer width; sides are captured between them.
    for sz, tag in ((-1, "front"), (1, "back")):
        seg = w + 2 * t
        z_face = sz * half_l
        features.append(plate(f"{tag} bumper plywood", (w + 2 * (fab + ply), h, ply),
                              _at(0, mid_y, z_face + sz * ply_off), _rot(0, 0, 0),
                              mat="plywood", note=ply_note))
        for level, y in (("lower", nr), ("upper", h - nr)):
            # Runs exactly rail-to-rail: the corner quarter-bends pick up from the
            # tangent points, so the ring is continuous the way the course sweeps it.
            features.append(noodle(f"{tag} bumper {level} noodle", nd, w,
                                   _at(0, y, z_face + sz * noodle_off), _rot(0, 0, 90)))
        features.append(fabric(f"{tag} bumper fabric", (seg, h + 2 * fab, t),
                               _at(0, mid_y, z_face + sz * wrap_off),
                               _rot(0, 0 if sz > 0 else 180, 0),
                               note="cordura wrap; carries the team number"))
    for sx, tag in ((-1, "left"), (1, "right")):
        seg = ln + 2 * (fab + ply)
        x_face = sx * half_w
        features.append(plate(f"{tag} bumper plywood", (ply, h, ln),
                              _at(x_face + sx * ply_off, mid_y, 0), mat="plywood",
                              note=ply_note))
        for level, y in (("lower", nr), ("upper", h - nr)):
            features.append(noodle(f"{tag} bumper {level} noodle", nd, ln,
                                   _at(x_face + sx * noodle_off, y, 0), _rot(90, 0, 0)))
        features.append(fabric(f"{tag} bumper fabric", (seg, h + 2 * fab, t),
                               _at(x_face + sx * wrap_off, mid_y, 0),
                               _rot(0, sx * 90, 0),
                               note="cordura wrap; carries the team number"))

    # Corners: the course sweeps the bumper as ONE continuous ring around a
    # rounded-corner path, so each corner gets a quarter-bend of noodle at both
    # levels, tangent to the straight runs on either side. The bend radius is the
    # noodle centreline offset — the arc's tangent points land exactly where the
    # straight noodles end.
    # Quadrants: the base arc spans +X→+Z; rotating about Y by -90° per step walks
    # it around the frame (R_y(-90) maps +X→+Z→-X→-Z).
    corner_ry = {(1, 1): 0, (-1, 1): 270, (-1, -1): 180, (1, -1): 90}
    for sx in (-1, 1):
        for sz in (-1, 1):
            for level, y in (("lower", nr), ("upper", h - nr)):
                features.append(noodle_corner(
                    f"bumper corner {level} noodle", nd, noodle_off,
                    _at(sx * half_w, y, sz * half_l),
                    _rot(0, corner_ry[(sx, sz)], 0)))

    # Corner cloth. R402-C wants cloth over every outward-facing padding surface, and four
    # straight wraps leave the eight corner bends bare. One quarter-bend of the same U-channel
    # per corner closes the ring — swept at the wrap's own centreline radius so it lands on
    # the straight wraps' ends instead of near them.
    for sx in (-1, 1):
        for sz in (-1, 1):
            features.append(fabric("bumper corner fabric", (0.0, h + 2 * fab, t),
                                   _at(sx * half_w, mid_y, sz * half_l),
                                   _rot(0, corner_ry[(sx, sz)], 0), bend=wrap_off,
                                   note="quarter-bend of the wrap; no bare foam at the corner"))

    # R412: white numerals, at least 3.75 in tall with a 0.5 in stroke, in at least three
    # places about 90° apart. Four faces is the practical answer — it is what teams cut and
    # it means no approach angle to the robot has an unreadable side. The decal rides on the
    # cloth, just proud of it, so it moves with the bumper's real thickness.
    if team_number:
        face = wrap_off + t / 2 + 0.02
        for tag, at, rot in (
            ("front", _at(0, mid_y, -(half_l + face)), _rot(0, 180, 0)),
            ("back", _at(0, mid_y, half_l + face), None),
            ("left", _at(-(half_w + face), mid_y, 0), _rot(0, -90, 0)),
            ("right", _at(half_w + face, mid_y, 0), _rot(0, 90, 0)),
        ):
            features.append(decal(f"{tag} team number", str(team_number), at,
                                  height=TEAM_NUMBER_HEIGHT_IN,
                                  stroke=TEAM_NUMBER_STROKE_IN, rot=rot,
                                  note="white vinyl on the cloth; R412 minimum is "
                                       "3.75 in tall on a 0.5 in stroke"))

    # Attachment (R410). A rigid fastening system, not the single anonymous plate this used
    # to draw: per mount, a bent 5052 bracket hooking over the rail top and down its outboard
    # web, one 1/4-20 through the plywood into the bracket, a flange nut behind it, and two
    # #8 screws pinning the bracket to the board so the board cannot slide off the bolt.
    # 5052 rather than 6061 because these get bent, and 6061 cracks at the bend radius a
    # bracket this small needs — the reference teams all bend sheet in 5052 for that reason.
    for sx in (-1, 1):
        for sz in (-1, 1):
            # The height depends on what owns the corner, because something always does.
            #
            # A swerve module puts its motors there, standing from y 2.2 to 5.9, so the old
            # fixed h-0.6 (y 4.4) ran straight through them on every swerve robot. Dropping the
            # bracket to the rail fixes that — and breaks west-coast, where the corner belongs
            # to a drive wheel whose tread reaches y 2.9. Neither height is free on both.
            #
            # So it is chosen: low under the swerve motors, high over the west-coast tread. The
            # bracket only has to tie the two plywood ends into one ring, and it does that at
            # any height on the board.
            features.append(gusset("bumper corner bracket", (2.5, 2.5),
                                   _at(sx * (half_w - 0.4), h * 0.28 if corner_low else h - 0.6,
                                       sz * (half_l - 0.4)),
                                   thickness=0.125, form="angle",
                                   note="ties the two plywood ends into one ring at the corner"))
    # A bolt runs along its own local Z, and a turned part along its own local Y, so the two
    # halves of one joint need two different rotations to end up on one axis. Getting that
    # wrong is invisible in a render and obvious on a robot.
    for sx, sz, tag in ((0, -1, "front"), (0, 1, "back"), (-1, 0, "left"), (1, 0, "right")):
        front_back = sx == 0
        perim = half_l if front_back else half_w      # the frame plane this segment hangs on
        sign = sz if front_back else sx
        rot_y = 0 if front_back else 90
        bolt_rot = None if front_back else _rot(0, 90, 0)
        nut_rot = _rot(90, 0, 0) if front_back else _rot(0, 0, 90)

        def place(along: float, out: float) -> list[float]:
            """(position along the segment, distance outboard of the frame plane) → a point."""
            return (_at(along, 0.0, sign * (perim + out)) if front_back
                    else _at(sign * (perim + out), 0.0, along))

        for side in (-1, 1):
            label = "a" if side < 0 else "b"
            # Between the modules, not on top of them. At 3.0 in from the corner a hanger sits
            # exactly where the drive wheel is (x 9.95-11.45 on a 27 in frame), so it was drawn
            # through the wheel on all four rails. The hangers only have to carry the board's
            # weight along the rail; further inboard does that and leaves the corners clear.
            along = side * ((half_w if front_back else half_l) - 6.5)

            # The hanger carries the bumper's weight: an angle bolted flat to the rail top
            # with its short leg turned down against the board's inner face, so the board is
            # held up by aluminium and the bolt only has to keep it from swinging out.
            # Level with the frame plane, not 0.6 in inside it. A west-coast drivetrain runs
            # wheels the length of both side rails and their tread stands above the rail top,
            # so a hanger set inboard was drawn through a wheel on every side hanger. At the
            # plane it still lands on the rail — nothing floats — and it clears the tread.
            hanger = place(along, -0.1)
            hanger[1] = 2.045
            features.append(gusset(f"{tag} bumper hanger {label}", (2.0, 1.6), hanger,
                                   _rot(0, rot_y, 0), thickness=0.090, form="angle",
                                   leg=1.0, holes=1.0,
                                   note="bent 5052 angle on the rail top; the board rests on it"))

            # One 1/4-20 through the board and through BOTH walls of the rail. A closed tube
            # has no inside to reach, which is why the fastener is a through-bolt and a flange
            # nut rather than a screw into the rail — the joint R410 is really asking for.
            bolt = place(along, -0.4)
            bolt[1] = 1.20
            features.append(fastener_row(f"{tag} bumper bolt {label}", bolt, 1, [0, 0, 0],
                                         dia=BUMPER_BOLT_DIA_IN, rot=bolt_rot, length=2.6))
            nut = place(along, -1.15)
            nut[1] = 1.20
            features.append(_feat("standoff", f"{tag} bumper flange nut {label}", nut,
                                  dia=0.55, len=0.30, rot=nut_rot, mat="steel",
                                  note="1/4-20 flange nut, flush inside the rail's inner wall"))

            # Two #8s pin the hanger's turned-down leg to the plywood, so the board cannot
            # slide along the bolt and take the noodles off the corner with it.
            screws = place(along, 0.15)
            screws[1] = 2.05
            features.append(fastener_row(f"{tag} bumper wood screws {label}", screws, 2,
                                         [1.2, 0.0, 0.0], dia=BUMPER_SCREW_DIA_IN,
                                         rot=bolt_rot, length=0.9))
    return features


def _chassis(spec: dict[str, Any], c: Choices, stations: dict[str, float]) -> dict[str, Any]:
    """Frame rails, crossmembers, gussets and the bellypan.

    The rails are mitre-less: two full-length side rails with the front and back rails
    captured between them, which is how a tube frame is actually cut and gusseted. How many
    crossmembers it needs, how the corners are gusseted and how the pan is lightened are
    decisions, not constants — see `Choices`.
    """
    frame = spec["frame"]
    w, ln = frame["width_in"], frame["length_in"]
    sec = TUBE_2X1
    rail_h = sec[0]                       # 2 in tall, 1 in wide — the strong axis vertical
    half_w, half_l = w / 2, ln / 2
    inset = sec[1] / 2                    # rail centreline sits half a wall in from the edge
    y = rail_h / 2

    features: list[dict[str, Any]] = []
    # Side rails run the full length; front/back rails are cut to fit between them.
    # Rails follow the team's course build (Onshape Tube Converter on the master
    # sketch): 50×25×2 mm box tube (2×1 in, 0.079 in wall) punched with a Ø5 mm
    # hole row at 25 mm (~1 in) pitch, mid-face — so gussets and blocks bolt
    # anywhere along the rail without new holes.
    rail_note = "Tube 50x25x2 mm (2x1 in): O5 mm holes at 25 mm pitch, mid-face"
    for sx in (-1, 1):
        features.append(tube(f"side rail {'left' if sx < 0 else 'right'}", sec, ln,
                             _at(sx * (half_w - inset), y, 0), pockets=c.rail_pockets,
                             wall=0.079, bolts=1.0, note=rail_note))
    cross_len = w - sec[1] * 2
    for sz, tag in ((-1, "front"), (1, "back")):
        features.append(tube(f"{tag} rail", sec, cross_len,
                             _at(0, y, sz * (half_l - inset)), _rot(0, 90, 0),
                             wall=0.079, bolts=1.0, pockets=c.rail_pockets,
                             note=rail_note))
    # Interior crossmembers tie the side rails together and carry superstructure loads into
    # them. They are placed AT the mechanism stations — a tower bolted "to a crossmember"
    # must have one directly underneath it, or the mates are a story and the mechanism
    # floats. The stations come from `_stations`, the same resolver every mechanism's origin
    # uses, so the member and the feet cannot drift apart. Any spare members spread over the
    # remaining span.
    placed: list[tuple[float, str]] = []
    for key, z in stations.items():
        if all(abs(z - z0) > 2.5 for z0, _ in placed):
            placed.append((z, key))
    spread = max(0, c.crossmembers - len(placed))
    for i in range(spread):
        z = (-0.3 + (i + 1) * (0.6 / (spread + 1))) * ln
        if all(abs(z - z0) > 2.5 for z0, _ in placed):
            placed.append((z, ""))
    # The crossmember's TOP face is flush with the rail top, whatever section the pick lands
    # on: feet bolt to the top plane, and a 1x1 centred on the rail axis used to leave a
    # half-inch of air under every foot it was meant to carry.
    cm_sec = c.crossmember_section
    cm_y = rail_h - cm_sec[0] / 2
    # A west-coast drivetrain hangs its wheels off rails INBOARD of the frame rails, so a
    # crossmember cut to the full inside width runs straight through both drive rails and
    # both columns of wheels. It ties the drive rails instead — which is what it is actually
    # for, and what the front and back rails (still full width) are not.
    cm_len = cross_len
    if (spec.get("drivetrain") or {}).get("type") == "west-coast":
        cm_len = max(4.0, w - 2 * (2.6 + TUBE_2X1[1] / 2))
    # A crossmember spans rail to rail, and a swerve wheel lives inboard of the rails at each
    # corner — so a member placed at a corner's Z runs straight through two wheels. Real
    # chassis put their crossmembers BETWEEN the modules for exactly this reason. Nudge any
    # member whose Z lands in a module band to the nearest clear side of it; the mechanism
    # feet above it move with it, because both read the same station dict.
    keepout = _module_bands(spec, cm_sec[0] / 2)
    if keepout:
        nudged: list[tuple[float, str]] = []
        for z, station in placed:
            for lo, hi in keepout:
                if lo < z < hi:
                    z = lo if (z - lo) < (hi - z) else hi
            nudged.append((round(z, 3), station))
        placed = nudged
        for key, _ in _STATION_DEFAULTS:
            for z, station in placed:
                if station == key:
                    stations[key] = z
    for z, station in sorted(placed):
        features.append(tube(f"crossmember{' at ' + station if station else ''}",
                             cm_sec, cm_len,
                             _at(0, cm_y, z), _rot(0, 90, 0), bolts=2.5,
                             note=f"under the {station} feet; top face flush with the rails"
                                  if station else "top face flush with the rails"))
    # Corner gussets: on the top face always, on the inboard web too when the corner is
    # carrying a module.
    for sx in (-1, 1):
        for sz in (-1, 1):
            # 2.95 x 2.80 in is the bracket measured off the reference chassis: it reaches
            # far enough down each rail to catch two hole pitches on both legs. Thickness
            # and material follow the course's Gusset Generator settings: 3 mm 6061 with
            # holes matching the rails' O5 mm rows, so it bolts on without new holes.
            # Bent, not flat. A flat plate on the rail top catches one face of each tube and
            # takes the corner's moment in bolt shear alone; folding the same blank down the
            # outboard web catches two faces per tube and puts the fold in the load path.
            # It is one extra operation on the brake and it is why the reference chassis
            # gussets look like angle rather than triangles.
            features.append(gusset("corner gusset", (2.95, 2.80),
                                   _at(sx * (half_w - 2.0), rail_h, sz * (half_l - 2.0)),
                                   thickness=0.118, form="angle", leg=1.5, holes=1.0,
                                   note="3 mm 5052, folded; holes on the rail's 25 mm rows"))
            if c.gusset_faces > 1:
                features.append(gusset("corner web gusset", (2.5, 1.6),
                                       _at(sx * (half_w - sec[1] - 0.1), rail_h / 2,
                                           sz * (half_l - 2.0)), _rot(0, 0, 90),
                                       form="triangle", holes=1.0))
    if c.bellypan_style == "two-piece":
        # Two pans split on the centreline come off independently, so the electronics side
        # can be dropped without disturbing the drivetrain side.
        for sz in (-1, 1):
            features.append(plate(f"bellypan {'front' if sz < 0 else 'rear'} half",
                                  (w - 0.5, 0.090, ln / 2 - 0.4), _at(0, -0.045, sz * ln / 4),
                                  pockets=8, note="0.090 in 6061, splits on the centreline"))
    else:
        features.append(plate("bellypan", (w - 0.5, 0.090, ln - 0.5), _at(0, -0.045, 0),
                              pockets=26 if c.bellypan_style == "lattice" else 18,
                              note=("0.090 in 6061, lattice-lightened"
                                    if c.bellypan_style == "lattice"
                                    else "0.090 in pocketed 6061, bolts to the rail underside")))
    # The pan is BOLTED, and until now nothing said so — a 0.090 sheet drawn flush under the
    # rails with no fastener anywhere is exactly the kind of body that renders fine and comes
    # off on the first hit. Four rows of 10-32s, on the rails' own hole pitch, plus a rivnut
    # standoff at each corner so the pan can be dropped without losing the hardware.
    # Under rot(90,0,0) a bolt points down its own local Z and the row's local Y is what
    # runs fore-and-aft, so the step goes on Y. Stepping local Z here would have stacked the
    # whole row vertically through the pan.
    pan_bolt_rows = max(3, int((ln - 4) // 5))
    for sx in (-1, 1):
        features.append(fastener_row(f"bellypan bolts {'left' if sx < 0 else 'right'}",
                                     _at(sx * (half_w - inset), 0.10, 0.0),
                                     pan_bolt_rows, [0, 2.5, 0], rot=_rot(90, 0, 0),
                                     length=0.55))
    for sx in (-1, 1):
        for sz in (-1, 1):
            features.append(_feat("standoff", "bellypan corner standoff",
                                  _at(sx * (half_w - 1.4), -0.045, sz * (half_l - 1.4)),
                                  dia=0.375, len=0.30, mat="aluminium-dark",
                                  note="rivnut boss; the pan drops without losing hardware"))
    # Swerve puts motors at the corners and west-coast puts a wheel there, so the corner
    # bracket has to go under one and over the other.
    features += _bumper(w, ln, spec.get("team_number") or 0,
                        corner_low=(spec.get("drivetrain") or {}).get("type") != "west-coast")
    return _asm("chassis", f"Chassis {w:g} × {ln:g} in", "structure", features,
                note="Welded-free bolted tube frame; every joint is a gusset and four 10-32s.",
                mates=["bellypan fixed to rails", "rails fixed to each other at the corners"])


def _swerve_module(dt: dict[str, Any], index: int, at: list[float],
                   detailed: bool, c: Choices, corner: tuple[int, int],
                   rail_off: float) -> dict[str, Any]:
    """One swerve module, built from its real plate size, drop and wheel.

    ``detailed`` opens up the gear train — pinion, spur, bevel pair and the azimuth ring — so
    the assembly reads as a mechanism instead of a black box. Every corner gets it: the
    module is a catalog part and four of them on a robot are identical, so modelling one and
    boxing the rest was a saving that bought nothing and cost the ratio check.
    ``corner`` is the frame corner this module lives in and ``rail_off`` the distance from
    the module centre to each rail's centreline, so the mounting hardware lands ON the rails
    instead of near them: the top plate rests on the rail top faces and the bolt rows drop
    through it into both rails.
    """
    plate_in = dt.get("plate_in") or (4.10, 4.10)
    p = plate_in[0]
    drop = dt.get("drop_in", 4.60)
    wheel_d = dt.get("wheel_diameter_in", 4.0)
    # The top plate rests ON the rail top face (rail top = 2.0): underside at 2.0, not
    # hovering 0.3 in over it the way a fixed 2.4 centre height had it.
    top_y = 2.095
    sx_c, sz_c = corner
    mkey = dt.get("motor_key", "kraken_x60")
    skey = dt.get("steer_motor_key", mkey)
    features: list[dict[str, Any]] = [
        plate("module top plate", (p, 0.190, p), _at(0, top_y, 0), pockets=4,
              note="rests on the rail top faces at the corner"),
        plate("module bottom plate", (p, 0.190, p), _at(0, 0.20, 0), pockets=4),
    ]
    for sx in (-1, 1):
        for sz in (-1, 1):
            features.append(_feat("standoff", "module standoff",
                                  _at(sx * (p / 2 - 0.5), (top_y + 0.2) / 2, sz * (p / 2 - 0.5)),
                                  dia=0.375, len=top_y - 0.2))
    features.append(motor("drive motor", mkey, _at(-1.0, top_y + 0.095 + MOTORS[mkey]["length_in"] / 2, -1.0), _rot(180, 0, 0)))
    features.append(motor("steer motor", skey, _at(1.0, top_y + 0.095 + MOTORS.get(skey, MOTORS[mkey])["length_in"] / 2, 1.0), _rot(180, 0, 0)))
    # The wheel patch sets the robot's ride height: ~1.1 in of ground clearance under the
    # frame, the published figure for this module class. Deriving the centre from `drop`
    # put the patch 4 in below the bellypan and the whole robot on stilts.
    ground_clearance = 1.125
    # The wheel sits INBOARD of its own plate, and which way "inboard" is depends on the
    # corner. A fixed +0.2 offset moved the front-left wheel away from the rails and the
    # other three straight into them; combined with a plate inset chosen so the top plate
    # laps the rail tops, that put a slice of tread inside the frame tube on three corners.
    # The plate has to reach the rails and the wheel has to clear them, so the wheel's
    # position under the plate is the parameter that gives.
    rail_wall = TUBE_2X1[1]                       # the rail the wheel must clear, 1 in wide
    clear = max(0.20, rail_wall + wheel_d / 2 + 0.15 - rail_off - 0.5)
    features.append(wheel("drive wheel", wheel_d, 1.5,
                          _at(-sx_c * 0.2, wheel_d / 2 - ground_clearance, -sz_c * clear),
                          _rot(0, 0, 90), kind="tread", durometer="billet with grip tread",
                          note="inboard of the rails; the contact patch is what sets ride height"))
    features.append(bearing("azimuth main bearing", _at(0, 0.9, 0), bore=2.5, od=3.5, width=0.4))
    # Attachment, in both mount styles: a vertical bolt row on each rail centreline, dropping
    # through the plate into the tube. The corner-plate style adds the spreader plate over
    # both rails; the through-rail style keeps its centre row through the rail web as well.
    features.append(fastener_row("side rail bolt row", _at(sx_c * rail_off, 2.15, 0),
                                 2, [0, 2.2, 0], rot=_rot(90, 0, 0), length=1.4))
    features.append(fastener_row("end rail bolt row", _at(0, 2.15, sz_c * rail_off),
                                 2, [2.2, 0, 0], rot=_rot(90, 0, 0), length=1.4))
    if c.module_mount == "corner-plate":
        # A corner plate spreads the module's load into both rails instead of relying on the
        # bolts through one rail wall.
        # Sized to the corner it sits in: p + 1.4 overhung the frame by a sixteenth on a
        # tight module, and a spreader plate poking out past the bumper spreads nothing.
        spread = min(p + 1.4, 2 * (rail_off + 0.5) - 0.4)
        features.append(plate("module corner plate", (spread, 0.190, spread),
                              _at(0, top_y + 0.19, 0), pockets=2,
                              note="spreads the module load across both rails"))
    else:
        # Centred on the module. Offset to one side it was mirrored nowhere, so on the two
        # left-hand corners the row marched an inch and a half out past the frame.
        features.append(fastener_row("module through-bolts", _at(0, top_y + 0.1, 0),
                                     4, [p / 3, 0, 0]))
    if detailed:
        # Pitch radius = teeth / dp / 2. Meshing centres are placed at exactly the
        # sum of the pitch radii; the old hand-placed centres left the idler and
        # azimuth pinion floating half an inch from the gears they claimed to drive.
        spur_at = _at(-0.1, top_y - 0.30, -0.1)
        ring_at = _at(0, 0.9, 0)
        r_pinion, r_spur, r_idler = 12 / 20 / 2, 40 / 20 / 2, 22 / 20 / 2
        r_ring, r_azimuth = 60 / 20 / 2, 14 / 20 / 2
        features += [
            gear("drive pinion", 12, _meshed(spur_at, r_pinion + r_spur, -1, -1), dp=20, face=0.35, bore=0.312),
            gear("drive spur", 40, spur_at, dp=20, face=0.35, lightened=6, mat="anodised"),
            gear("idler", 22, _meshed(spur_at, r_spur + r_idler, 0.9, 0.5), dp=20, face=0.35, lightened=5, mat="anodised"),
            _feat("bevel", "bevel pair", _at(0.8, 0.55, 0.4), teeth=20, pd=1.6, face=0.4),
            gear("azimuth ring gear", 60, ring_at, dp=20, face=0.4, bore=p / 2 - 0.55, mat="anodised"),
            gear("azimuth pinion", 14, _meshed(ring_at, r_ring + r_azimuth, 1, 1), dp=20, face=0.4, bore=0.312),
            shaft("drive output shaft", _HEX_BORE, 3.2, _at(0.2, top_y / 2 - 0.4, 0.2)),
        ]
    return _asm(f"swerve_{index}", f"Swerve module {index + 1}", "drivetrain", features,
                origin=at, note=dt.get("selection_reason", ""),
                mates=["top plate rests on both rail top faces; a bolt row drops into each rail",
                       "azimuth revolute about Y"])


def _drivetrain(spec: dict[str, Any], c: Choices) -> list[dict[str, Any]]:
    dt = spec.get("drivetrain") or {}
    frame = spec["frame"]
    w, ln = frame["width_in"], frame["length_in"]
    if dt.get("type") == "west-coast":
        wheel_d = dt.get("wheel_diameter_in", 6.0)
        count = int(dt.get("drive_motors", 6))
        per_side = max(2, count // 2)
        features: list[dict[str, Any]] = []
        rail_x = w / 2 - 2.6              # drive rail centreline
        frame_rail_x = w / 2 - 0.5        # chassis side rail centreline
        tie_w = (frame_rail_x - rail_x) + 1.0
        for sx in (-1, 1):
            features.append(tube("drive rail", TUBE_2X1, ln - 4, _at(sx * rail_x, 1.0, 0),
                                 note="wheel rail, inboard of the frame rail"))
            # Three tie plates per side lap the drive rail and the frame rail across their
            # shared top plane, each with a bolt dropping into each tube — the drive rail
            # used to be a free-floating tube with nothing joining it to the frame.
            for ti, tz in enumerate((-(ln - 10) / 2, 0.0, (ln - 10) / 2)):
                features.append(plate(f"drive rail tie plate {ti + 1}", (tie_w, 0.190, 2.5),
                                      _at(sx * (rail_x + frame_rail_x) / 2, 2.095, tz),
                                      note="laps both rails; carries the drive rail load into the frame"))
                features.append(fastener_row(f"tie plate bolts {ti + 1}",
                                             _at(sx * (rail_x + frame_rail_x) / 2, 2.15, tz),
                                             2, [frame_rail_x - rail_x, 0, 0],
                                             rot=_rot(90, 0, 0), length=1.4))
            # Axles sit so the frame rides ~2 in off the carpet; the centre pair drops 1/8.
            axle_y = wheel_d / 2 - 2.0
            axle_x = (rail_x + frame_rail_x) / 2
            prev: list[float] | None = None
            axle_zs: list[tuple[float, float]] = []      # (z, drop) of every sprocket on this side
            for i in range(per_side):
                z = (i - (per_side - 1) / 2) * ((ln - 8) / max(1, per_side - 1))
                centre = abs(i - (per_side - 1) / 2) < 0.25
                drop = 0.125 if centre else 0.0
                # Long enough to carry both bearings, short enough to stay on the robot:
                # tie_w + 1.4 ran the hex out through the frame rail and into the bumper.
                axle_len = min(tie_w + 1.4, 2 * (w / 2 - 0.3 - axle_x))
                features.append(shaft(f"axle {i + 1}", _HEX_BORE, axle_len,
                                      _at(sx * axle_x, axle_y - drop, z), _rot(0, 0, 90),
                                      form="hex"))
                features.append(wheel("drive wheel", wheel_d, 1.4,
                                      _at(sx * (w / 2 - 1.6), axle_y - drop, z), _rot(0, 0, 90),
                                      kind="tread"))
                features.append(sprocket("#25 sprocket", 22 if not centre else 24,
                                         _at(sx * rail_x, axle_y - drop, z), _rot(0, 0, 90)))
                features.append(bearing("drive rail bearing", _at(sx * (w / 2 - 2.4), axle_y - drop, z),
                                        _rot(0, 0, 90)))
                features.append(bearing("frame rail bearing", _at(sx * frame_rail_x, axle_y - drop, z),
                                        _rot(0, 0, 90)))
                if prev is not None:
                    features.append(belt(f"axle {i}→{i + 1} chain", prev,
                                         _at(sx * rail_x, axle_y - drop, z), 0.3,
                                         kind="#25 chain"))
                prev = _at(sx * rail_x, axle_y - drop, z)
                axle_zs.append((z, drop))
            # The gearbox bolts to the drive rail's inboard web on its own plate; the motors
            # stack on the gearbox face instead of marching into empty air above it.
            features.append(plate("gearbox mount plate", (0.190, 3.0, 3.4),
                                  _at(sx * (rail_x - 0.45), 2.2, 0),
                                  note="bolts to the drive rail web; the gearbox hangs on this"))
            features.append(fastener_row("gearbox plate bolts", _at(sx * (rail_x - 0.35), 2.2, 0),
                                         2, [0, 0, 2.4], rot=_rot(0, 90, 0), length=1.2))
            features.append(gearbox("drop-centre gearbox", (3.2, 4.0, 2.0),
                                    _at(sx * (rail_x - 1.6), 3.0, 0),
                                    ratio=f"{dt.get('drive_ratio', 8.45):g}:1", stages=2))
            features.append(sprocket("gearbox output sprocket", 12, _at(sx * rail_x, 3.0, 0),
                                     _rot(0, 0, 90)))
            # The gearbox drives whichever axle is nearest it, and with four propulsion
            # motors that is a two-wheel-per-side base with NO centre axle at all. Aiming at
            # a dropped centre wheel that only exists on a six-wheel base left the chain's
            # end wrapping nothing — which is exactly what the transmission audit is for.
            drive_z, drive_drop = min(axle_zs, key=lambda a: abs(a[0])) if axle_zs else (0.0, 0.0)
            features.append(belt("gearbox→axle chain", _at(sx * rail_x, 3.0, 0),
                                 _at(sx * rail_x, axle_y - drive_drop, drive_z), 0.3,
                                 kind="#25 chain"))
            motor_posts = [(2.6, -1.8), (4.4, -1.8), (3.5, 1.8)][:max(1, count // 2)]
            for m, (my, mz) in enumerate(motor_posts):
                features.append(motor("drive motor", dt.get("motor_key", "kraken_x60"),
                                      _at(sx * (rail_x - 1.6), my, mz), _rot(0, 0, 90)))
        return [_asm("drivetrain", "West-coast drivetrain", "drivetrain", features,
                     note=dt.get("selection_reason", ""),
                     mates=["drive rails tie to the frame rails on three bolted plates a side",
                            "axles ride bearings in both rails"]
                           + (["centre wheels dropped 1/8 in so the robot turns on four patches"]
                              if per_side > 2 else
                              ["four wheels, all on one plane — a two-per-side base has no "
                               "centre to drop"]))]

    plate_in = dt.get("plate_in") or (4.10, 4.10)
    # Close enough to the corner that the module top plate laps onto the rail top faces —
    # the old 1.2 in margin held every module 0.2 in shy of the rails it claimed to bolt to.
    # Shared with the chassis and the electrical layout, both of which have to stay out of
    # the wheels this places.
    inset = _module_inset(spec) or (plate_in[0] / 2 + 0.55)
    count = int(dt.get("module_count") or 0)
    if count == 0:
        env = [_feat("envelope", "reserved module envelope",
                     _at(sx * (w / 2 - inset), 2.5, sz * (ln / 2 - inset)),
                     size=[plate_in[0], 5.0, plate_in[1]])
               for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        return [_asm("drivetrain", "Reserved swerve envelope", "drivetrain", env,
                     note="Corners cut and drilled; nothing installed.")]
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)][:count]
    rail_off = round(inset - 0.5, 3)
    # All four, gear train and all. A robot does not carry one real module and three blocks,
    # and every published swerve CAD shows four identical assemblies — a corner drawn as a
    # box is the difference between CAD you can check a ratio against and a picture of a
    # robot. The train is 21 bodies a corner; there is no reason to ration it.
    return [_swerve_module(dt, i, _at(sx * (w / 2 - inset), 0, sz * (ln / 2 - inset)),
                           True, c, (sx, sz), rail_off)
            for i, (sx, sz) in enumerate(corners)]


def _intake(spec: dict[str, Any], c: Choices) -> dict[str, Any] | None:
    """Over-bumper pivoting roller intake, mounted to the frame it serves.

    Built the way the reference robot builds it: two pivot towers bolted to the front rail,
    a dead axle across them, and two arms swinging *toward the front* over the bumper.  The
    green rollers ride at the arm ends; deploying swings them forward and down in front of
    the bumper face, stowing swings them back up over the bumper — a limited arc between
    hard stops, never a full rotation.  The CAD models the deployed pose; the viewer
    animates the arc from the articulation block.
    """
    ik = spec.get("intake") or {}
    if not ik.get("included"):
        return None
    frame = spec["frame"]
    w, ln = frame["width_in"], frame["length_in"]
    width = min(ik.get("width_in", 20), w + 4)   # spec decides; over-bumper may exceed the frame
    roller_d = ik.get("roller_diameter_in", 2.0)
    count = int(ik.get("roller_count", 1))
    centre = ik.get("roller_center_distance_in", roller_d + 2.0)
    mkey = ik.get("motor_key", "neo")
    duro = ik.get("compliant_wheel", "")
    drive = c.intake_drive
    bumper_face = BUMPER_THICKNESS_IN + BUMPER_FABRIC_IN     # off the frame face, local z
    rail_top = 2.0

    # The pivot sits above the bumper top on two towers; the arms reach over the bumper.
    # High enough that the arm plate clears the bumper it reaches over.
    #
    # 1.6 put the pivot at y 6.6, and a 2.4 in deep arm raked ~46 deg from there still cut the
    # bumper's top corner — measured at 1.0 in of overlap. The relationship is monotonic once
    # the arm is raked the right way (it was not, until the rake sign was fixed: at +1.6 the
    # intrusion was identical at every pivot height, which is what proved the fault was
    # orientation rather than height). Swept: 1.6 -> 1.01 in, 2.8 -> 0.46, 3.4 -> 0.22,
    # 4.2 -> clear on every drivetrain and season, with R107 height still passing.
    #
    # But 3.4 is what ships. Above it the taller intake changes its own footprint enough that
    # the placement pass re-stations its neighbours and the manipulator ends up 0.39 in past
    # the frame line — `test_nothing_but_the_intake_hangs_outside_the_frame` fails at 3.8 and
    # 4.2 and passes at 3.4. Trading a 0.2 in graze on the bumper for a gripper hanging out of
    # the frame is the wrong way round, so the last fifth of an inch is reported, not taken.
    pivot_y = BUMPER_HEIGHT_IN + 3.4
    pivot_z = 0.6                                            # just inside the front rail
    # Deployed, the first roller floats a half inch off the carpet, clear of the bumper.
    roll1_y = roller_d / 2 + 0.5
    roll1_z = -(bumper_face + roller_d / 2 + 0.7)
    features: list[dict[str, Any]] = []

    # Pivot towers: plates standing on the front rail, gusseted and bolted to it. They are
    # the only static parts of the mechanism — everything else swings on the dead axle.
    # The tower feet are clamped to the rail's real span: a wide over-bumper intake may
    # overhang the frame, but a tower standing outboard of the rail stands on nothing.
    tower_x = min(width / 2 + 0.45, w / 2 - 1.35)
    # The tower plate stands ON the front rail, so it may not reach in front of it.
    #
    # It used to be centred on the pivot, and the pivot sits forward of the rail on an
    # over-bumper intake — which put 0.7 in of the plate's 2.6 in depth out past the rail face
    # and straight into the bumper plywood. Visible in the viewer as the intake disappearing
    # into the bumper, and missed by the interference audit because the deepest axis of the
    # overlap is the plate's own 0.19 in thickness, well under the 0.60 in tolerance meant for
    # mechanism-on-mechanism packaging.
    #
    # The plate slides back until its front face clears the rail; the pivot axis, the bearing
    # and the dead axle do not move, so the intake's kinematics are untouched.
    _TOWER_DEPTH = 2.6
    tower_z = max(pivot_z, _TOWER_DEPTH / 2 + 0.08)
    for sx, side in ((-1, "left"), (1, "right")):
        tx = sx * tower_x
        features.append(plate(f"pivot tower {side}", (0.190, pivot_y - rail_top + 1.0, _TOWER_DEPTH),
                              _at(tx, (pivot_y + rail_top) / 2 - 0.3, tower_z), pockets=2,
                              note="stands on the front rail; carries the pivot bearing"))
        features.append(gusset(f"tower foot gusset {side}", (2.2, 1.8),
                               _at(tx, rail_top + 0.1, tower_z + 0.9), _rot(90, 0, 0)))
        features.append(fastener_row(f"tower foot bolts {side}", _at(tx, rail_top + 0.1, 0.5),
                                     2, [0, 0.9, 0], rot=_rot(90, 0, 0), length=1.3))
        features.append(bearing(f"pivot bearing {side}", _at(tx, pivot_y, pivot_z),
                                _rot(0, 0, 90), bore=0.625, od=1.375, width=0.5))
        if c.intake_hardstops:
            features.append(hardstop(f"stowed hard stop {side}",
                                     _at(tx, pivot_y + 1.1, pivot_z + 0.9)))
            features.append(hardstop(f"deployed hard stop {side}",
                                     _at(tx, pivot_y - 1.2, pivot_z - 1.3)))
    features.append(shaft("pivot dead axle", 0.625, width + 2.2,
                          _at(0, pivot_y, pivot_z), _rot(0, 0, 90), form="round"))

    # Arms: pivot down to the roller carriage, over the bumper. Everything from here down
    # swings, so it is all positioned along the arm line.
    arm_dy = pivot_y - roll1_y
    arm_dz = pivot_z - roll1_z
    arm_len = math.hypot(arm_dy, arm_dz)
    arm_angle = math.degrees(math.atan2(arm_dz, arm_dy))     # from vertical, toward the front
    arm_mid = _at(0, (pivot_y + roll1_y) / 2, (pivot_z + roll1_z) / 2)
    for sx, side in ((-1, "left"), (1, "right")):
        ax = sx * width / 2
        # `+arm_angle`, not `-`. Rotating the plate about X by `a` sends its length axis to
        # (0, cos a, sin a), and the pivot-to-roller direction is (0, arm_dy, arm_dz) — both
        # positive, since the pivot is above and BEHIND the roller. Negating the angle leaned
        # the arm up-and-forward instead of up-and-back, so the plate never lay along the line
        # it is supposed to join and instead ran down through the front bumper. The rollers
        # were always placed correctly off `arm_dy/arm_dz`, which is why only the arm was wrong
        # and why it looked plausible until the bumper rule measured it.
        features.append(plate(f"intake arm {side}", (0.190, arm_len + 1.6, 2.4),
                              _at(ax, arm_mid[1], arm_mid[2]), _rot(arm_angle, 0, 0),
                              pockets=3, note=f"{arm_len:.1f} in between pivot and roller"))

    # Rollers climb back up the arm toward the frame, so a gamepiece walks over the bumper.
    #
    # The gear train sits on the roller shafts, and the shafts run the full width, so the
    # train does NOT have to be at the intake's outboard edge — which matters, because an
    # over-bumper intake is allowed to be wider than the frame and the drive package then
    # ends up hanging outside the bumper. It goes as far out as it can while keeping the
    # motor on the robot, and inboard of that when it cannot.
    # The drive package sits behind the pivot, which on a swerve robot is the front module's
    # Z band — so what it has to clear is not the frame line but the module's own motors,
    # which stand up off the module plate into exactly this height. It lives in the gap
    # between the two front modules.
    drive_reach = 2.05 + MOTORS.get(mkey, MOTORS["neo"])["length_in"] / 2 + 0.3
    _inset = _module_inset(spec)
    drive_limit = (w / 2 - _inset - 2.4) if _inset else (w / 2 - 0.35)
    drive_x = _package_x(spec, 0.0, width / 2 + 0.55, drive_reach, limit=drive_limit)

    motor_r = MOTORS.get(mkey, MOTORS["neo"])["diameter_in"] / 2

    def transmit(name: str, teeth: int, at: list[float]) -> dict[str, Any]:
        if drive == "chain":
            return sprocket(f"{name} sprocket", teeth, at, _rot(0, 0, 90))
        return pulley(f"{name} pulley", teeth, 0.45, at, _rot(0, 0, 90))

    roller_ats: list[list[float]] = []
    unit_y, unit_z = arm_dy / arm_len, arm_dz / arm_len
    for r in range(count):
        along = r * centre
        y = roll1_y + unit_y * along
        z = roll1_z + unit_z * along
        roller_ats.append(_at(0, y, z))
        features.append(shaft(f"roller {r + 1} shaft", _HEX_BORE, width + 1.0,
                              _at(0, y, z), _rot(0, 0, 90)))
        for sx, side in ((-1, "left"), (1, "right")):
            features.append(bearing(f"roller {r + 1} bearing {side}",
                                    _at(sx * width / 2, y, z), _rot(0, 0, 90)))
        wheels = max(3, int(round(width / 4)))
        for i in range(wheels):
            x = -width / 2 + (i + 0.5) * (width / wheels)
            features.append(wheel(f"compliant wheel {r + 1}.{i + 1}", roller_d,
                                  width / wheels * 0.55, _at(x, y, z), _rot(0, 0, 90),
                                  kind="compliant", durometer=duro))
        if drive != "gear":
            features.append(transmit(f"roller {r + 1}", 18, _at(drive_x, y, z)))

    # Power hangs on the TOWER, not on the arm.
    #
    # It used to ride the arm, and the spec has always described the power path as "belt from
    # a static gearbox" — the geometry and the declared design disagreed, and the geometry was
    # wrong. A drive package bolted to the arm swings with it: sitting 1.9 in below the pivot
    # and behind it, the motor and gearbox sweep up and forward through the front bumper
    # somewhere around 60° of deploy. The motion audit reported that on every over-bumper
    # intake, and no amount of moving the package around the arm fixes it, because the arm is
    # what is moving.
    #
    # Mounted to the tower it does not move at all, and the reduction reaches the arm through
    # the dead axle: gearbox → idler on the axle (both static), then axle → first roller,
    # which is an arm-local run whose length cannot change as the arm rotates about that same
    # axle. That is how a real over-bumper intake is built and why the axle is dead.
    drive_plate_x = drive_x + 0.32
    # Where the power package sits. Computed BEFORE the plate, because the plate exists to
    # carry it and therefore has to be under it — it used to be pinned at `pivot_z - 1.0`,
    # which is 2.4 in away from its own gearbox and, worse, is squarely inside the front
    # bumper. A plate that carries nothing and lives in the bumper is two faults in one line.
    pack_z = _package_pos(spec, "z", -ln / 2, pivot_z + 1.4, motor_r + 0.4, side=-1.0)
    # The standoffs span from the arm plate to the drive plate, and on a wide over-bumper
    # intake the drive plate is now INBOARD of the arm rather than outboard of it. The span
    # is therefore a distance, not a signed offset — as a signed one it went negative and the
    # contract rejected the whole design for a negative dimension.
    features.append(plate("intake drive plate", (0.190, 3.6, 3.4),
                          _at(drive_plate_x, pivot_y - 1.9, pack_z), pockets=1,
                          note="bolts to the pivot tower on standoffs; carries the gearbox "
                               "and motor clear of the arm's swing"))
    for so, sy in ((1, 1.2), (2, -1.2)):
        features.append(_feat("standoff", f"drive plate standoff {so}",
                              _at((tower_x + drive_plate_x) / 2, pivot_y - 1.9 + sy, pack_z),
                              dia=0.375, len=max(0.5, round(abs(drive_plate_x - tower_x), 3)),
                              rot=_rot(0, 0, 90)))
    # The power package goes BEHIND the pivot, inside the frame. It used to sit 1.4 in in
    # front of it, which on an over-bumper intake is inside the front bumper — the one place
    # on a robot where a motor is guaranteed to be hit. Belt and chain are what make that
    # possible: they carry torque down the arm, so the mass stays back at the pivot.
    gb_at = _at(drive_x + 0.85, pivot_y - 1.9, pack_z)
    features.append(gearbox("intake gearbox", (2.0, 2.2, 1.2), gb_at,
                            ratio=ik.get("gear_reduction", "4:1"), stages=2))
    features.append(motor("intake motor", mkey,
                          _at(drive_x + 2.05, pivot_y - 1.9, pack_z), _rot(0, 0, 90)))
    features.append(transmit("gearbox output", 36, _at(drive_x, pivot_y - 1.9, pack_z)))
    kind = "#25 chain" if drive == "chain" else "HTD 5 mm 15 mm belt"
    # The idler rides the dead axle at the pivot. Everything upstream of it is bolted to the
    # tower and never moves; everything downstream turns with the arm about this same centre,
    # so the run to the first roller is a fixed length at every deploy angle.
    # On its own plane, outboard of the roller pulleys. Sharing their plane put it through
    # roller 3's pulley on a three-roller intake — a fault introduced by adding the idler,
    # caught by the truncation audit. A two-stage reduction runs on two planes anyway.
    idler_x = drive_x + 0.55
    features.append(transmit("pivot idler", 24, _at(idler_x, pivot_y, pivot_z)))
    features.append(belt("reduction run", _at(drive_x, pivot_y - 1.9, pack_z),
                         _at(idler_x, pivot_y, pivot_z), 0.35, kind=kind))
    features.append(belt("arm run", _at(idler_x, pivot_y, pivot_z),
                         _at(drive_x, roller_ats[0][1], roller_ats[0][2]), 0.35, kind=kind))
    for r in range(count - 1):
        features.append(belt(f"roller {r + 1}→{r + 2} run",
                             _at(drive_x, roller_ats[r][1], roller_ats[r][2]),
                             _at(drive_x, roller_ats[r + 1][1], roller_ats[r + 1][2]),
                             0.35, kind=kind))
    features.append(_feat("tensioner", f"{drive} tensioner",
                          _at(drive_x + 0.7, (roller_ats[0][1] + pivot_y - 1.9) / 2,
                              (roller_ats[0][2] + pack_z) / 2), dia=0.9, w=0.4,
                          rot=_rot(0, 0, 90)))
    if ik.get("indexer"):
        # The guide runs BETWEEN the front modules, so it is as wide as the gap between them
        # and no wider. Sizing it off the intake width let it grow past the frame and through
        # both front modules' steer motors — the intake is allowed over the bumper, but not
        # through the drivetrain.
        inset = _module_inset(spec)
        span = (w - 2 * (inset + 2.3)) if inset else (w - 1.0)
        features.append(polycarb("indexer guide",
                                 (max(4.0, min(width - 1.0, span)), 0.093, centre + 2.0),
                                 _at(0, pivot_y - 0.8, pivot_z + 1.6), _rot(-18, 0, 0),
                                 note="spans the gap between the front modules"))

    # The limited deploy arc, for the viewer and any downstream consumer: rotation about the
    # dead axle from the deployed pose (as modelled, 0°) up and back to the stow.  The green
    # rollers swing toward the front of the robot and back — never 360°.
    stow_deg = 78.0
    return _asm("intake", "Intake", "mechanism", features,
                origin=_at(0, 0, -ln / 2),
                note=ik.get("type", ""),
                articulation={"type": "pivot", "axis": "x",
                              "at": [0, round(pivot_y, 3), round(pivot_z, 3)],
                              "deg": [0.0, stow_deg], "home": 0.0,
                              # The drive package is tower-mounted, so it does not swing with
                              # the arm — listing it here is what stops the motion audit
                              # sweeping a motor through the front bumper.
                              "static": ["pivot tower left", "pivot tower right",
                                         "tower foot gusset left", "tower foot gusset right",
                                         "tower foot bolts left", "tower foot bolts right",
                                         "pivot bearing left", "pivot bearing right",
                                         "stowed hard stop left", "stowed hard stop right",
                                         "deployed hard stop left", "deployed hard stop right",
                                         "pivot dead axle", "intake drive plate",
                                         "drive plate standoff 1", "drive plate standoff 2",
                                         "intake gearbox", "intake motor",
                                         # `transmit` names by drive type, so both spellings
                                         # are listed — matching is exact, and the belt build
                                         # of this robot would otherwise leave the output
                                         # pulley swinging on its own through the bumper.
                                         "gearbox output pulley", "gearbox output sprocket",
                                         "pivot idler pulley", "pivot idler sprocket"],
                              "note": "deploys toward the front over the bumper; "
                                      "limited arc between the hard stops"},
                mates=["pivot towers bolt to the front rail, two 10-32s each",
                       "arms revolute about the dead axle, limited by the hard stops",
                       "roller shafts revolute in the arm plates",
                       "gearbox and motor on the standoff-mounted drive plate"])


def _hopper(spec: dict[str, Any], lane_x: float, c: Choices,
            station_z: float) -> dict[str, Any] | None:
    """Bulk gamepiece storage and serialisation: floor, walls, driven wheels, exit lane.

    A hopper is geometrically simple and mechanically fussy, and the geometry is where the
    fussiness lives.  Three dimensions decide whether it works: how far the driven wheels
    stand proud of the floor (too flush and pieces slip, too high and they climb over), how
    wide the exit lane is (one piece plus clearance, never two), and how tall the wall is
    relative to a piece (which sets both capacity and how much spills over the bump).
    """
    hp = spec.get("hopper") or {}
    if not hp.get("included"):
        return None
    frame = spec["frame"]
    w, ln = frame["width_in"], frame["length_in"]
    # The hopper is the biggest single volume on the robot, and it has to fit INSIDE the
    # frame: its floor, its walls, its support rails and its motors all live below bumper
    # height, where the frame line is five inches of plywood and foam. Taking the spec's
    # floor width at face value produced a 29 in hopper on a 27 in frame, whose funnels
    # reached through both bumpers and into the front swerve modules — and, because it was
    # the widest thing on the robot, left the placement pass nowhere to put anything else.
    #
    # How much room there is depends on the DRIVETRAIN. A swerve robot gives up the frame
    # rails; a west-coast gives up the rails, the drive rails inboard of them and the
    # drop-centre gearboxes inboard of those, which is most of a foot off the width. Sized
    # against the rails alone, the hopper's corner posts, support rails and index wheels all
    # ended up inside the drive gearboxes.
    rail_w = TUBE_2X1[1]
    if (spec.get("drivetrain") or {}).get("type") == "west-coast":
        # drive rail centreline, gearbox offset, gearbox half-width, clearance
        side_reserved = 2.6 + 1.6 + 1.6 + 0.4
    else:
        # A swerve module's wheel and motors own its corner, and the hopper sits forward in
        # the front modules' band, so it has to stop short of the WHEEL — not of the module
        # centre, and not just of the rails. 1.2 cleared the centre and left the corner posts
        # a quarter inch inside the tread.
        side_reserved = max(rail_w + 1.0, _module_inset(spec) + 1.6)
    max_width = w - 2 * side_reserved
    max_depth = ln - 2 * (rail_w + 0.4)
    width = min(hp.get("floor_width_in", 20.0), max_width)
    depth = min(hp.get("floor_depth_in", 14.0), max_depth)
    # The station (including the clear-the-tower shift) comes from `_stations`, the same
    # resolver the chassis used to place this station's crossmember.
    wall_h = hp.get("wall_height_in", 10.0)
    wheel_d = hp.get("wheel_diameter_in", 4.0)
    proud = hp.get("wheel_proud_in", 0.5)
    exit_w = hp.get("exit_lane_width_in", 6.5)
    lanes = int(hp.get("lanes", 1))
    count = max(3, int(hp.get("wheel_count", 4)))
    mkey = hp.get("motor_key", "neo_550")
    kind = hp.get("type", "")
    rotating = "spindexer" in kind
    features: list[dict[str, Any]] = []

    # Floor. The driven wheels sit under it and stand `proud` above it, so the floor plane is
    # the datum for the whole mechanism and everything else is dimensioned from it.
    floor_y = 0.0
    if c.hopper_floor == "polycarb":
        features.append(polycarb("hopper floor", (width, 0.093, depth), _at(0, floor_y, 0)))
    else:
        features.append(plate("hopper floor", (width, 0.090, depth), _at(0, floor_y, 0), pockets=6,
                              note="0.090 in 6061, slots for the driven wheels"))

    # Walls. Three sides plus a funnel that narrows onto the exit lane; the fourth side is the
    # exit itself. Bolting them to a plate frame rather than to the floor keeps the floor flat.
    def wall(name: str, size: tuple[float, float, float], at: list[float],
             rot: list[float] | None = None) -> dict[str, Any]:
        if c.hopper_walls == "polycarb":
            return polycarb(name, size, at, rot)
        return plate(name, size, at, rot, mat="aluminium", pockets=2)

    features.append(wall("hopper back wall", (width, wall_h, 0.093),
                         _at(0, floor_y + wall_h / 2, depth / 2)))
    for sx in (-1, 1):
        features.append(wall(f"hopper {'left' if sx < 0 else 'right'} wall",
                             (0.093, wall_h, depth), _at(sx * width / 2, floor_y + wall_h / 2, 0)))
    # Funnel: two angled panels that turn the whole floor width into one exit lane. This is the
    # part that decides whether the hopper serialises or bridges.
    funnel_span = max((width - exit_w * lanes) / 2 - 0.5, 1.0)
    for sx in (-1, 1):
        # The funnel panel stands ON the floor — a guide a gamepiece can slip under is a
        # jam, and a panel that starts mid-air is a floater.
        features.append(wall(f"hopper {'left' if sx < 0 else 'right'} funnel",
                             (funnel_span, wall_h * 0.75, 0.093),
                             _at(sx * (exit_w * lanes / 2 + funnel_span / 2),
                                 floor_y + wall_h * 0.375, -depth / 2 + 1.4),
                             _rot(0, sx * 28, 0)))

    # Driven wheels on one shaft across the floor, standing `proud` above it. On a spindexer
    # the floor itself rotates and the wheels index the pieces off it; on a belt-floor hopper
    # the same wheels drive a belt underneath.
    shaft_y = floor_y - wheel_d / 2 + proud
    # The shaft reaches past the wall to the drive elements at drive_x — a driven pulley
    # hanging beyond the end of its own shaft is not a drivetrain.
    features.append(shaft("hopper drive shaft", _HEX_BORE, width + 2.4,
                          _at(0, shaft_y, -depth * 0.18), _rot(0, 0, 90)))
    for sx in (-1, 1):
        features.append(bearing("hopper shaft bearing", _at(sx * (width / 2 + 0.4), shaft_y, -depth * 0.18),
                                _rot(0, 0, 90)))
    for i in range(count):
        x = -width / 2 + (i + 0.5) * (width / count)
        features.append(wheel(f"index wheel {i + 1}", wheel_d, 0.9, _at(x, shaft_y, -depth * 0.18),
                              _rot(0, 0, 90), kind="compliant", durometer="35A green compliant"))
    if rotating:
        # The rotating floor: one large disc on a centre bearing, driven at its rim.
        disc_d = min(width, depth) * (1.35 if "oval" in kind else 1.0)
        features.append(wheel("spindexer floor disc", disc_d, 0.19, _at(0, floor_y + 0.14, 0),
                              kind="spindexer", durometer="0.19 in polycarb disc on a rim drive"))
        features.append(bearing("spindexer centre bearing", _at(0, floor_y - 0.3, 0),
                                bore=1.125, od=2.0, width=0.4))
        # The rim gear is AT the rim: tooth count follows from the disc diameter at 20 DP,
        # and the pinion sits at the sum of the pitch radii so the pair actually meshes.
        rim_teeth = int(round((disc_d - 0.8) * 20))
        features.append(gear("spindexer rim gear", rim_teeth, _at(0, floor_y - 0.55, 0), dp=20, face=0.35,
                             bore=disc_d - 1.6, mat="anodised"))
        features.append(gear("spindexer pinion", 16, _at((disc_d - 0.8) / 2 + 0.4, floor_y - 0.55, 0),
                             dp=20, face=0.35, bore=0.375))
    if c.hopper_agitator or hp.get("agitator"):
        features.append(shaft("agitator shaft", 0.375, width * 0.8,
                              _at(0, floor_y + wall_h * 0.62, depth * 0.12), _rot(0, 0, 90),
                              form="round"))
        # The paddles index around the SHAFT, and the shaft runs across the hopper along X.
        # Rotating them about Z instead swung a 12 in paddle up into the Y plane, so each one
        # boxed as a 12 x 10 in slab standing above the hopper walls and fouled every
        # mechanism behind it. About X, the paddle stays across the hopper and its 2.2 in
        # blade is what sweeps — which is also the only version that would clear the floor.
        for i in range(3):
            features.append(polycarb(f"agitator paddle {i + 1}", (width * 0.6, 0.093, 2.2),
                                     _at(0, floor_y + wall_h * 0.62, depth * 0.12),
                                     _rot(60 * i, 0, 0)))

    # Exit lane and gate. One piece wide, with a sensor that tells the code exactly one is
    # staged — a timer here is how you end up feeding two and jamming the shooter.
    for lane in range(lanes):
        offset = (lane - (lanes - 1) / 2) * (exit_w + 1.0)
        features.append(polycarb(f"exit lane {lane + 1} guide", (exit_w, wall_h * 0.6, 3.0),
                                 _at(offset, floor_y + wall_h * 0.3, -depth / 2 - 1.5)))
        # The roller runs INSIDE the guide channel — that is what makes it a lane rather than
        # two loose parts — so the nesting is declared instead of being reported as a roller
        # swallowed by a polycarb panel.
        exit_roller = wheel(f"exit lane {lane + 1} roller", 2.0, exit_w * 0.8,
                            _at(offset, floor_y + 1.0, -depth / 2 - 2.4), _rot(0, 0, 90),
                            kind="compliant", durometer="40A")
        exit_roller["allow"] = [f"exit lane {lane + 1} guide"]
        features.append(exit_roller)
        features.append(_feat("sensor", f"lane {lane + 1} beam-break",
                              _at(offset, floor_y + 1.6, -depth / 2 - 2.0),
                              size=[0.5, 0.5, 0.5], kind="beam-break",
                              note="one staged piece, sensed not timed"))
        features.append(hardstop(f"lane {lane + 1} gate stop",
                                 _at(offset + exit_w / 2, floor_y + 0.8, -depth / 2 - 2.4)))

    # Power: gearbox outboard of the wall on its own plate, one run to the index shaft.
    # The plate stands off the hopper wall, the gearbox bolts to the plate, and the motors
    # stack against the gearbox face — the whole package used to hang in free air.
    # The transmission plane, and everything outboard of it, measured against the DRIVETRAIN
    # rather than the frame line: the hopper sits in the front modules' band on a swerve and
    # alongside the drop-centre gearboxes on a west-coast, and both reach a long way inboard
    # of the rail. The driven pulley, the gearbox output and the motor all share this plane —
    # clamping the gearbox alone left its output pulley behind on the old axis, driving a
    # belt that ran diagonally to nothing.
    _inset_h = _module_inset(spec)
    if (spec.get("drivetrain") or {}).get("type") == "west-coast":
        hop_limit = w / 2 - (2.6 + 1.6 + 1.6) - 0.3
    else:
        hop_limit = w / 2 - (_inset_h + 2.4 if _inset_h else 0.35)
    drive_x = _package_x(spec, lane_x, width / 2 + 0.9,
                         0.45 + 1.65 + MOTORS.get(mkey, MOTORS["neo_550"])["length_in"] / 2 + 0.3,
                         limit=hop_limit)
    features.append(plate("hopper drive plate", (0.190, 4.0, 4.5),
                          _at(width / 2 + 0.35, shaft_y + 1.0, -depth * 0.05),
                          note="stands off the wall; carries the gearbox and motors"))
    for so, sy in ((1, 1.3), (2, -1.3)):
        features.append(_feat("standoff", f"hopper drive standoff {so}",
                              _at(width / 2 + 0.18, shaft_y + 1.0 + sy, -depth * 0.05),
                              dia=0.375, len=0.36, rot=_rot(0, 0, 90)))
    # The drive package hangs off the hopper's side, so how far out it reaches has to be
    # measured against the frame and not against the hopper. A second motor goes BESIDE the
    # first along the robot, not further outboard: that is how a two-motor gearbox is built,
    # and marching them outward is what put the second one through the bumper.
    gb_x = drive_x + 0.45
    features.append(gearbox("hopper gearbox", (2.0, 2.2, 1.2), _at(gb_x, shaft_y + 1.6, depth * 0.1),
                            ratio=hp.get("gear_reduction", "12:1"), stages=2))
    for i in range(int(hp.get("motor_count", 1))):
        features.append(motor("hopper motor", mkey,
                              _at(gb_x + 1.65, shaft_y + 1.6, depth * 0.1 + i * 2.2),
                              _rot(0, 0, 90)))
    if c.hopper_drive == "chain":
        features.append(sprocket("hopper driven sprocket", 24, _at(drive_x, shaft_y, -depth * 0.18), _rot(0, 0, 90)))
        features.append(sprocket("hopper drive sprocket", 12, _at(drive_x, shaft_y + 1.6, depth * 0.1), _rot(0, 0, 90)))
        features.append(belt("hopper drive run", _at(drive_x, shaft_y + 1.6, depth * 0.1),
                             _at(drive_x, shaft_y, -depth * 0.18), 0.35, kind="#25 chain"))
    else:
        features.append(pulley("hopper driven pulley", 30, 0.45, _at(drive_x, shaft_y, -depth * 0.18), _rot(0, 0, 90)))
        features.append(pulley("hopper drive pulley", 15, 0.45, _at(drive_x, shaft_y + 1.6, depth * 0.1), _rot(0, 0, 90)))
        features.append(belt("hopper drive run", _at(drive_x, shaft_y + 1.6, depth * 0.1),
                             _at(drive_x, shaft_y, -depth * 0.18), 0.45, kind="HTD 5 mm 15 mm belt"))
    # The tensioner arm bolts to the drive plate — riding the middle of the belt run with
    # nothing behind it is exactly the floating-part defect the audit exists to catch.
    features.append(_feat("tensioner", f"{c.hopper_drive} tensioner",
                          _at(width / 2 + 0.45, shaft_y + 0.8, -depth * 0.04), dia=0.9, w=0.4,
                          rot=_rot(0, 0, 90)))
    # Structure under it all: two rails the floor and walls bolt to, and a corner post at
    # each wall corner running down to the chassis — a glass box with nothing visibly
    # holding it up reads as floating, because it would be.
    # The origin lifts the floor far enough that the driven wheels under it clear the
    # bellypan; the corner posts then have to span that whole lift down to the pan.
    origin_y = 2.4 + wheel_d / 2 + proud - 0.9
    for sx in (-1, 1):
        features.append(tube("hopper support rail", TUBE_1X1, depth + 1.0,
                             _at(sx * (width / 2 - 0.6), floor_y - 0.8, 0), bolts=2.5))
    post_n = 0
    for sx in (-1, 1):
        for sz in (-1, 1):
            post_n += 1
            features.append(tube("hopper corner post", TUBE_1X1, round(origin_y, 3),
                                 _at(sx * (width / 2 - 0.5), floor_y - origin_y / 2,
                                     sz * (depth / 2 - 0.5)),
                                 _rot(90, 0, 0)))
            # Each post lands on the bellypan and bolts through it — the visible answer to
            # "what holds this box up".
            features.append(fastener_row(f"post foot bolt {post_n}",
                                         _at(sx * (width / 2 - 0.5), floor_y - origin_y + 0.05,
                                             sz * (depth / 2 - 0.5)),
                                         1, [0, 0, 0], rot=_rot(90, 0, 0), length=0.8))
    features.append(plate("hopper access panel", (width + 0.2, 0.090, depth * 0.4),
                          _at(0, floor_y + wall_h + 0.045, depth * 0.25), pockets=3,
                          note="rests on both wall tops, thumbscrewed — a jam clears "
                               "without removing the shooter"))

    return _asm("hopper", "Hopper / indexer", "mechanism", features,
                origin=_at(lane_x, origin_y, station_z),
                note=kind,
                mates=["corner posts bolt through the bellypan, one bolt each",
                       "support rails carry the floor between the posts",
                       "index shaft revolute in the wall bearings",
                       "exit gate feeds the shooter one piece at a time"]
                      + (["floor disc revolute about Y on the centre bearing"] if rotating else []))


def _turret_pedestal(ring_bore: float, height: float) -> list[dict[str, Any]]:
    """Four posts and a deck that carry the slew bearing above whatever is beside it.

    A turret packed next to a spindexer does not graze the hopper, it sweeps straight through
    it: the pivot sits at y≈3.4 and the hopper's walls reach y≈14.8, so every angle past a few
    degrees drives the head into a wall. No rotation limit can fix that — the arc that clears
    is zero wide — and moving the hopper is not available on a 27 in frame.

    Teams solve it the way this does: the turret goes on a tower and sweeps OVER the storage.
    The deck is what the turret base plate bolts to, and the posts land on the crossmember
    pair at this station, so the whole stack still chains back to the frame.
    """
    if height <= 0.1:
        return []
    side = ring_bore + 3.2
    inset = 1.0
    out: list[dict[str, Any]] = []
    for sx in (-1, 1):
        for sz in (-1, 1):
            x, z = sx * (side / 2 - inset), sz * (side / 2 - inset)
            out.append(tube(f"turret tower post {'LR'[sx > 0]}{'FB'[sz > 0]}", TUBE_1X1, height,
                            _at(x, height / 2, z), _rot(90, 0, 0), bolts=3.0,
                            note="lands on the crossmember pair; carries the turret deck"))
            out.append(gusset(f"turret tower foot {'LR'[sx > 0]}{'FB'[sz > 0]}", (1.8, 1.8),
                              _at(x, 0.9, z + sz * 0.55), _rot(90, 0, 0), form="triangle"))
    # Cross-bracing: four posts and a deck is a parallelogram until the diagonals go in, and a
    # turret is exactly the load case that racks it.
    for sz in (-1, 1):
        out.append(tube(f"turret tower brace {'FB'[sz > 0]}", TUBE_1X1, side - 2 * inset,
                        _at(0, height * 0.55, sz * (side / 2 - inset)), _rot(0, 90, 0),
                        bolts=None, mat="aluminium-dark"))
    out.append(plate("turret deck", (side, 0.250, side), _at(0, height + 0.125, 0), pockets=6,
                     note="the turret base plate bolts to this"))
    return out


def _turret_stack(ring_bore: float, lift: float = 0.0) -> list[dict[str, Any]]:
    """A real turret, bottom-up: base plate on the crossmembers, slew bearing on the base,
    ring gear bolted to the rotating platform, the platform disc everything above rides on,
    and the pinion + motor that drive it. Heights stack so every part rests on the one below
    it — nothing floats:

        0.000  base plate underside (on the crossmembers)
        0.250  base plate top  →  slew bearing seat
        0.750  bearing top     →  ring gear
        1.200  ring gear top   →  platform disc
        1.610  platform top    →  the shooter head's y0
    """
    r = ring_bore / 2
    side = ring_bore + 3.2
    # The pinion meshes the ring gear at the sum of their pitch radii — the ring is 120T at
    # 20 DP (PD 6.0) and the pinion 14T (PD 0.7), so the centre distance is 3.35 in. The old
    # offset hung the pinion in air past the ring's rim, a gear train that could not turn.
    mesh_x = 6.0 / 2 + 0.7 / 2
    stack: list[dict[str, Any]] = [
        plate("turret base plate", (side, 0.250, side), _at(0, 0.125, 0), pockets=8,
              note="bolts to the crossmember pair at this station; the slew bearing bolts to this"),
        fastener_row("turret base bolts a", _at(0, 0.16, -(r + 0.8)), 2,
                     [ring_bore + 1.6, 0, 0], rot=_rot(90, 0, 0), length=1.2),
        fastener_row("turret base bolts b", _at(0, 0.16, r + 0.8), 2,
                     [ring_bore + 1.6, 0, 0], rot=_rot(90, 0, 0), length=1.2),
        bearing("turret slew bearing", _at(0, 0.50, 0),
                bore=ring_bore, od=ring_bore + 1.6, width=0.50),
        gear("turret ring gear", 120, _at(0, 0.975, 0), dp=20, face=0.45,
             bore=ring_bore - 0.4, mat="anodised"),
        wheel("turret platform", ring_bore + 2.4, 0.32, _at(0, 1.45, 0), kind="smooth",
              durometer="0.25 in 6061 disc — everything above this rotates"),
        # The drive package meshes on the ring's +X side, where the shooter posts also stand,
        # so the motor and the right-hand post share about an inch of space.
        #
        # Moving it to the BACK of the ring is the obvious fix and it does clear the post — a
        # pinion meshes at any clock position, only the centre distance matters. It was tried
        # and reverted: it changes the shooter's footprint in Z, the placement pass re-stations
        # the head off that footprint, and the swept turret then lands 2.1 in inside the
        # elevator uprights on a packed robot. Trading a 1 in intra-assembly overlap for a
        # 2 in swept collision with another mechanism is the wrong way round.
        #
        # Fixing this properly means teaching the placement pass that the shooter's footprint
        # changed, not moving the motor and hoping. Left as it is, and reported.
        gear("turret pinion", 14, _at(mesh_x, 0.975, 0), dp=20, face=0.45, bore=0.375),
        motor("turret motor", "neo_550", _at(mesh_x, 2.2, 0), _rot(180, 0, 0)),
    ]
    for sz in (-1, 1):
        stack.append(hardstop("turret rotation stop",
                              _at(-(r + 0.6), 1.45, sz * (r + 0.4))))
    stack.append(_feat("chain_track", "turret energy chain", _at(0, 0.45, r + 0.9),
                       size=[0.9, 0.7, ring_bore * 1.5], kind="energy chain",
                       note="constant-force spring keeps it tensioned through the sweep"))
    # The whole stack rides the pedestal deck when there is one. Shifting the built features
    # keeps the height table in the docstring true relative to the deck, which is the datum
    # every part above it is dimensioned from.
    if lift > 0.001:
        for feature in stack:
            at = feature["at"]
            feature["at"] = [at[0], round(at[1] + lift, 3), at[2]]
    return stack


def _shooter(spec: dict[str, Any], lane_x: float, c: Choices,
             station_z: float, turret_lift: float = 0.0) -> dict[str, Any] | None:
    """Flywheel shooter — staged barrel or classic hooded pair — on a real pivot."""
    sh = spec.get("shooter") or {}
    if not sh.get("included"):
        return None
    frame = spec["frame"]
    ln = frame["length_in"]
    # A turret on a pedestal puts its slew bearing on the deck, so every datum above the
    # bearing moves up with it: the deck sits at `turret_lift`, is 0.250 thick, and the stack
    # is dimensioned from its top face.
    turret_lift = max(0.0, float(turret_lift or 0.0))
    stack_lift = round(turret_lift + 0.250, 3) if turret_lift > 0.1 else 0.0
    stages = max(1, min(int(sh.get("flywheel_stages", 1)), 3))
    fw_d = sh.get("flywheel_diameter_in", 4.0)
    stacked = bool(sh.get("stacked"))
    if stacked:
        fw_d = min(fw_d, 4.0)
    fw = fw_d / 2
    barrel = sh.get("barrel_length_in", 0.0)
    mkey = sh.get("motor_key", "kraken_x60")
    width = sh.get("width_in", 10.0)
    # A raked barrel hangs its breech end well below its pivot, and how far below depends on
    # how long the barrel is: a 20 in barrel at 36° reaches nearly six inches down, plus the
    # guide under it. Pinned at 6.0 the breech ended up at y = 0.6 — under the frame rails,
    # through three crossmembers and into the hopper it is fed by. The pivot rises with the
    # barrel so the whole thing clears the rail top it stands on.
    _RAKE = 36.0
    pivot_y = 6.0
    if stacked and barrel > 4:
        pivot_y = max(pivot_y, 0.45 + (barrel / 2) * math.sin(math.radians(_RAKE))
                      + fw * 0.73)
    features: list[dict[str, Any]] = []

    if stacked and barrel > 4:
        # Two side rails carry every flywheel shaft; a polycarb floor guides the gamepiece.
        # 1x1 is the smallest square stock the catalog carries, so the rail is orderable; it
        # is centred to keep the original inner face at fw + 0.5, clear of the flywheel.
        for sx in (-1, 1):
            features.append(tube("barrel side rail", TUBE_1X1, barrel,
                                 _at(sx * (fw + 1.0), pivot_y, 0), _rot(-_RAKE, 0, 0), bolts=2.0))
        # The guide is the barrel's floor, so it is offset PERPENDICULAR to the barrel, not
        # straight down. Straight down left it hanging out of the raked tube it belongs to.
        _c, _s = math.cos(math.radians(_RAKE)), math.sin(math.radians(_RAKE))
        features.append(polycarb("barrel guide", (fw * 1.8, 0.093, barrel),
                                 _at(0, pivot_y - fw * 0.9 * _c, fw * 0.9 * _s),
                                 _rot(-_RAKE, 0, 0)))
        # Every stage motor bolts to one raked plate riding the right barrel rail — motors
        # beside a barrel with nothing holding them are ballast, not a drivetrain.
        features.append(plate("barrel drive plate", (0.190, 3.2, barrel * 0.72),
                              _at(fw + 1.55, pivot_y, -0.2), _rot(-_RAKE, 0, 0),
                              note="bolts to the right barrel rail; every stage motor mounts to this"))
        for s in range(stages):
            z = -barrel / 2 + (s + 0.7) * (barrel / (stages + 0.4))
            for sy in (-1, 1):
                features.append(shaft(f"stage {s + 1} shaft", _HEX_BORE, width * 0.4,
                                      _at(0, pivot_y + sy * (fw + 0.25), z), _rot(0, 0, 90)))
                for sx in (-1, 1):
                    features.append(wheel(f"stage {s + 1} flywheel", fw_d, 0.7,
                                          _at(sx * 0.9, pivot_y + sy * (fw + 0.25), z), _rot(0, 0, 90),
                                          kind="urethane", durometer="grey 60A"))
                    features.append(bearing("flywheel bearing",
                                            _at(sx * (fw + 0.55), pivot_y + sy * (fw + 0.25), z), _rot(0, 0, 90)))
            features.append(motor(f"stage {s + 1} motor", mkey,
                                  _at(fw + 2.6, pivot_y + (-1 if s % 2 else 1) * (fw + 0.4), z), _rot(0, 0, 90)))
        post_base = (1.61 + stack_lift) if sh.get("turreted") else 0.0
        for sx in (-1, 1):
            features.append(bearing("pivot trunnion", _at(sx * (fw + 1.5), pivot_y, 0), _rot(0, 0, 90),
                                    bore=0.625, od=1.5, width=0.5))
            features.append(tube("shooter post", TUBE_2X1, pivot_y - post_base,
                                 _at(sx * (fw + 1.5), (pivot_y + post_base) / 2, 0),
                                 _rot(90, 0, 0), bolts=2.0))
        if sh.get("turreted"):
            features += _turret_pedestal(5.4, turret_lift)
            features += _turret_stack(5.4, lift=stack_lift)
    else:
        # On a turret the whole head sits on the rotating platform, so every head part is
        # lifted by the platform's top face. A head drawn at fixed heights over a turret is
        # how the old model ended up with posts starting mid-air.
        turret = bool(sh.get("turreted"))
        y0 = (1.61 + stack_lift) if turret else 0.0
        # The head's centre height follows the FLYWHEEL, because the lower wheel hangs a full
        # radius below the shaft and the shaft hangs (fw + 0.25) below the centre. Pinned at
        # 4.5 it worked for a 4 in wheel and only just; a 5 in wheel put the lower flywheel's
        # bottom edge at y = 1.25, three quarters of an inch inside the crossmember the
        # shooter is bolted to. Everything else on the head is dimensioned off this.
        head_y = max(4.5, 2 * fw + 0.6)
        rise = head_y - 4.5
        # The side plates ARE the mechanism's structure: flywheel bearings, feeder shaft,
        # hood and hood drive all mount to them, and they stand on the posts. Shafts run
        # plate-to-plate — a bearing with no plate and a pulley past the end of its shaft
        # were the two ways this head used to come apart.
        side_x = fw + 1.05
        m_len = MOTORS.get(mkey, MOTORS["kraken_x60"])["length_in"]
        for sx in (-1, 1):
            features.append(plate(f"shooter side plate {'left' if sx < 0 else 'right'}",
                                  (0.190, 6.6 + rise, 5.6), _at(sx * side_x, y0 + 3.5 + rise / 2, 0.5),
                                  pockets=3,
                                  note="carries the flywheel bearings, the feeder and the hood"))
        # The drive package sits OUTBOARD of the post that carries the side plate, not on top
        # of it. A 2x1 post at x = side_x spans ±0.5 in, so a pulley at side_x + 0.3 is inside
        # it — 100% inside it, which is why the flywheel pulley was reported as a truncated
        # part rather than a colliding one on every turreted robot. The shaft grows to carry
        # the pulley out past the post, which is what it has to do on the real machine too.
        # The pulley stays where it always was and the POST moves inboard instead.
        #
        # Moving the drive package outboard also clears the post, and it was tried: every tenth
        # of an inch it gains is a tenth on the radius of the cylinder the whole head sweeps,
        # and on a packed robot that drove the swept turret straight into the elevator
        # uprights. Sliding the post in costs nothing — it still underlaps the side plate it
        # carries — and leaves the swept radius exactly as it was.
        drive_x = round(side_x + 0.3, 3)
        post_x = round(side_x - 0.35, 3)
        for sy in (-1, 1):
            fy = y0 + head_y + sy * (fw + 0.25)
            features.append(shaft("flywheel shaft", _HEX_BORE, (drive_x + 0.35) * 2,
                                  _at(0, fy, 0), _rot(0, 0, 90)))
            for sx in (-1, 1):
                features.append(wheel("flywheel", fw_d, 0.8, _at(sx * 1.0, fy, 0),
                                      _rot(0, 0, 90), kind="urethane", durometer="grey 60A"))
                features.append(bearing("flywheel bearing", _at(sx * side_x, fy, 0),
                                        _rot(0, 0, 90)))
            features.append(pulley("flywheel pulley", 24, 0.45, _at(drive_x, fy, 0),
                                   _rot(0, 0, 90)))
            # The motor keeps its original station. It is the longest thing on the head and
            # therefore what sets the radius of the cylinder the turret sweeps — pushing it
            # out with the pulley drove that cylinder 0.84 in into the elevator uprights.
            # It sits behind the post at z = -1.4, not on it, so it does not need the offset
            # the pulley does.
            features.append(motor("flywheel motor", mkey,
                                  _at(side_x + 0.05 + m_len / 2, fy, -1.4), _rot(0, 0, 90)))
            features.append(pulley("flywheel motor pulley", 12, 0.45,
                                   _at(drive_x, fy, -1.4), _rot(0, 0, 90)))
            features.append(belt("flywheel drive belt", _at(drive_x, fy, -1.4),
                                 _at(drive_x, fy, 0), 0.45))
        # The hood is not a half pipe. It is a formed skin on two cut ribs, wrapping the ball
        # from where the feeder hands it over to where it leaves — about 130°, not the 180°
        # this used to draw, which put a metre of aluminium behind the flywheels doing
        # nothing and made the whole head read as a cylinder someone had dropped on it.
        hood_name = "fixed hood" if c.shooter_hood_drive == "fixed" else "adjustable hood"
        hood_r = fw + 1.0
        hood_y = y0 + head_y
        features.append(_feat("hood", hood_name, _at(0, hood_y, 0),
                              r=hood_r, w=round(side_x * 2 - 0.1, 3), arc=130, start=25,
                              range_deg=[0, 0] if c.shooter_hood_drive == "fixed"
                                        else sh.get("hood_angle_deg", [18, 62])))
        # The ribs are the part that actually holds the wrap's shape, and they are what a
        # team cuts first. Two of them, one at each end of the skin, on the same arc.
        for sx in (-1, 1):
            features.append(_feat("rib", f"hood rib {'left' if sx < 0 else 'right'}",
                                  _at(sx * (side_x - 0.35), hood_y, 0),
                                  r=round(hood_r + 0.19, 3), arc=130, start=25, web=0.6,
                                  th=0.190, rot=_rot(0, 0, 0), mat="aluminium",
                                  note="formed rib; the skin rivets to this"))
        features.append(polycarb("hood skin backer", (side_x * 2 - 0.8, 0.093, 2.2),
                                 _at(0, hood_y + hood_r + 0.35, -0.9), _rot(-18, 0, 0),
                                 note="closes the top of the wrap between the ribs"))
        if c.shooter_hood_drive == "servo":
            features.append(_feat("actuator", "hood servo", _at(side_x + 0.4, y0 + head_y + 1.1, 1.0),
                                  size=[1.6, 0.8, 0.8], kind="linear servo"))
        elif c.shooter_hood_drive == "rack":
            features.append(gear("hood sector gear", 60, _at(side_x + 0.25, y0 + head_y, 0), _rot(0, 0, 90),
                                 dp=20, face=0.3, mat="anodised"))
            features.append(gear("hood pinion", 12, _at(side_x + 0.25, y0 + head_y + 1.8, 0), _rot(0, 0, 90),
                                 dp=20, face=0.3, bore=0.375))
        # Two side plates joined by nothing but the shafts that are supposed to spin in them
        # is a mechanism that racks the moment it is loaded. Standoffs are how the pair
        # becomes one frame — three of them, clear of both flywheel circles and the ball path.
        for i, (sy, sz) in enumerate(((5.9, -1.9), (1.0, -1.9), (1.0, 2.2))):
            features.append(_feat("standoff", f"shooter plate standoff {i + 1}",
                                  _at(0, y0 + sy, sz), dia=0.500, len=round(side_x * 2 - 0.19, 3),
                                  rot=_rot(0, 0, 90), mat="aluminium-dark",
                                  note="ties the two side plates into one frame"))
        for sx in (-1, 1):
            features.append(tube("shooter post", TUBE_2X1, 4.4 + rise,
                                 _at(sx * post_x, y0 + (4.4 + rise) / 2, 0),
                                 _rot(90, 0, 0), bolts=2.0,
                                 note="stands under the side plate it carries, inboard of the "
                                      "flywheel drive pulley"))
            features.append(gusset(f"shooter post gusset {'left' if sx < 0 else 'right'}",
                                   (2.2, 2.4), _at(sx * (side_x - 0.15), y0 + 4.1, 0.9),
                                   _rot(0, 0, 90), thickness=0.090, form="triangle",
                                   note="post to side plate; takes the recoil couple"))
        # How the gamepiece is presented to the flywheels is its own small mechanism.
        if c.shooter_feeder == "kicker":
            features.append(shaft("kicker shaft", _HEX_BORE, (side_x + 0.4) * 2,
                                  _at(0, y0 + 3.0, 1.8), _rot(0, 0, 90)))
            features.append(wheel("kicker wheel", 3.0, 1.2, _at(0, y0 + 3.0, 1.8), _rot(0, 0, 90),
                                  kind="compliant", durometer="40A"))
            features.append(motor("kicker motor", "neo_550", _at(width * 0.4, y0 + 3.0, 1.8), _rot(0, 0, 90)))
        elif c.shooter_feeder == "belt":
            features.append(shaft("feeder pulley shaft", _HEX_BORE, (side_x + 0.4) * 2,
                                  _at(0, y0 + 3.0, 2.4), _rot(0, 0, 90)))
            features.append(pulley("feeder pulley", 18, 0.5, _at(0, y0 + 3.0, 2.4), _rot(0, 0, 90)))
            # The belt's far end wraps a second pulley on its own shaft — a loop needs
            # two wrap surfaces, and this end used to terminate in empty air.
            features.append(shaft("feeder idler shaft", _HEX_BORE, (side_x + 0.4) * 2,
                                  _at(0, y0 + 3.6, 1.2), _rot(0, 0, 90)))
            features.append(pulley("feeder idler pulley", 18, 0.5, _at(0, y0 + 3.6, 1.2), _rot(0, 0, 90)))
            features.append(belt("feeder belt", _at(0, y0 + 3.0, 2.4), _at(0, y0 + 3.6, 1.2), 0.5))
            features.append(motor("feeder motor", "neo_550", _at(width * 0.4, y0 + 3.0, 2.4), _rot(0, 0, 90)))
        else:
            features.append(shaft("feeder roller shaft", _HEX_BORE, (side_x + 0.4) * 2,
                                  _at(0, y0 + 3.1, 1.9), _rot(0, 0, 90)))
            features.append(wheel("feeder roller", 2.0, width * 0.5, _at(0, y0 + 3.1, 1.9),
                                  _rot(0, 0, 90), kind="compliant", durometer="35A"))
            features.append(motor("feeder motor", "neo_550", _at(width * 0.4, y0 + 3.1, 1.9), _rot(0, 0, 90)))
        features.append(polycarb("feeder guide", (width * 0.7, 0.093, 3.0), _at(0, y0 + 3.2, 1.6), _rot(-24, 0, 0)))
        if turret:
            ring = max(width * 0.45, 5.0)
            features += _turret_pedestal(ring, turret_lift)
            features += _turret_stack(ring, lift=stack_lift)
    # Feet: every post lands on a plate bolted through the crossmember the chassis placed at
    # this station. The origin is the rail-top plane, so local y = 0 IS structure.
    post_xs = [sx * (fw + 1.5) for sx in (-1, 1)] if (stacked and barrel > 4) \
        else [sx * (fw + 1.05) for sx in (-1, 1)]
    if not sh.get("turreted"):
        for px in post_xs:
            side = "left" if px < 0 else "right"
            features.append(plate(f"shooter foot {side}",
                                  (3.0, 0.190, 3.2), _at(px, 0.095, 0), pockets=2,
                                  note="bolts through the crossmember at this station"))
            features.append(_foot_bolts(f"shooter foot bolts {side}", px, 0))
    asm = _asm("shooter", "Shooter", "mechanism", features,
               origin=_at(lane_x, 2.0, station_z),
               note=sh.get("type", ""),
               mates=(["turret base plate bolts to the crossmember pair at this station",
                       "turret revolute about Y on the slew bearing"] if sh.get("turreted")
                      else ["feet bolt to the crossmember at this station, two 10-32s each"])
                     + ["barrel revolute on the trunnions",
                        "flywheel shafts ride bearings in both side plates"])
    if sh.get("turreted"):
        # Everything above the slew bearing turns, so everything above it needs clearance all
        # the way round — not just where it happens to be pointing when the model is drawn.
        # The clearance audit reads this and checks the swept cylinder instead of the pose.
        # Only what is above the slew bearing turns, and the bearing rides the deck — so the
        # cut plane moves up with the pedestal. Left at 1.45 it would call the tower posts
        # rotating parts and sweep the whole pedestal round the robot.
        asm["sweep"] = {"x": 0.0, "z": 0.0, "from_y": round(1.45 + stack_lift, 3)}
    return asm


def _elevator(spec: dict[str, Any], lane_x: float, c: Choices,
              station_z: float) -> dict[str, Any] | None:
    """Rigged tower: two uprights, staged inner rails on bearing blocks, a carriage and
    the belt or chain run with a tensioner."""
    el = spec.get("elevator") or {}
    if not el.get("included"):
        return None
    frame = spec["frame"]
    ln = frame["length_in"]
    h = min(el.get("max_height_in", 48), 62)
    stages = max(1, min(int(el.get("stages", 2)), 4))
    span = el.get("upright_span_in", 9.0)
    moving = stages - 1                     # the static tower is not a moving stage
    ladder = c.ladder(moving)
    outer_sec, outer_wall = ladder[0]
    features: list[dict[str, Any]] = []
    for sx in (-1, 1):
        side = "left" if sx < 0 else "right"
        features.append(tube(f"{side} upright", outer_sec, h,
                             _at(sx * span / 2, h / 2, 0), _rot(90, 0, 0), wall=outer_wall,
                             pockets=True, note="static tower, strong axis fore-aft"))
        features.append(fastener_row("upright bolt column", _at(sx * span / 2, h / 2, 1.0),
                                     max(2, int((h - 5) // 3)), [0, 0, 3.0], rot=_rot(90, 0, 0)))
        # A tower carrying an overturning moment stands on a bolted foot plate, not on two
        # gussets and hope.
        features.append(plate(f"tower foot {side}", (3.4, 0.190, 3.6),
                              _at(sx * span / 2, 0.095, 0.2), pockets=2,
                              note="bolts through the crossmember at this station"))
        features.append(_foot_bolts(f"tower foot bolts {side}", sx * span / 2, 0.2))
        features.append(gusset("tower foot gusset", (2.5, 2.0),
                               _at(sx * span / 2, 0.10, 1.05), _rot(90, 0, 0)))
    features.append(tube("top tie", TUBE_1X1, span + 1, _at(0, h, 0), _rot(0, 90, 0)))
    features.append(tube("bottom tie", TUBE_1X1, span + 1, _at(0, 1.2, 0), _rot(0, 90, 0)))
    for i in range(c.elevator_braces):
        features.append(tube("tower brace", TUBE_1X1, span + 1,
                             _at(0, h * (0.35 + 0.3 * i), 0), _rot(0, 90, 0), bolts=2.5))
    # Each moving stage is the next section down the ladder, so every member is orderable.
    # If the ladder runs out before the stage count does, the tower stops there.
    #
    # The tower is drawn STOWED — every stage down, carriage at the bottom. That is the state
    # the robot is inspected in, the state it starts a match in, and the only state whose
    # height the rules care about. Drawing it half-extended, as this did, made the bearing
    # layout impossible to read and quietly put the carriage in mid-air.
    stage_count = min(moving, len(ladder) - 1)
    innermost_x = span / 2
    innermost_top = h
    innermost_sec = outer_sec
    for s in range(1, stage_count + 1):
        sec, wall = ladder[s]
        # Stagger, not shortening. A cascade stage is only a couple of inches shorter than
        # the one it nests in — that stagger IS the overlap, and it is what keeps the
        # parent's upper roller bearing on the stage at every extension including stowed.
        # Six inches a stage put every stage's top well below its parent's top roller, so
        # the upper blocks were bolted to a tube with nothing under them, and the tower lost
        # most of its travel for nothing.
        stagger = 2.0
        sh_len = max(6.0, h - 1.2 - stagger * s)
        inset = (outer_sec[1] - sec[1]) / 2 + (s - 1) * 0.4
        stage_y = sh_len / 2 + 1.2               # stowed: every stage bottomed out at 1.2
        stage_top = stage_y + sh_len / 2
        innermost_x = span / 2 - inset
        innermost_top = stage_top
        innermost_sec = sec
        for sx in (-1, 1):
            x = sx * (span / 2 - inset)
            features.append(tube(f"stage {s + 1} rail", sec, sh_len,
                                 _at(x, stage_y, 0),
                                 _rot(90, 0, 0), wall=wall, mat="aluminium-dark",
                                 note=f"nests inside {outer_sec[0]:g}x{outer_sec[1]:g}"
                                      if s == 1 else f"nests inside {ladder[s - 1][0][0]:g}x{ladder[s - 1][0][1]:g}",
                                 allow=["left upright", "right upright"]
                                       + [f"stage {k} rail" for k in range(1, s + 1)]))
            # Four rollers per stage, and WHICH member each one is bolted to is the whole
            # constraint: the upper pair rides on the parent's top, the lower pair on this
            # stage's own bottom. Two blocks bolted to the same tube — which is what putting
            # both at the inner stage's own ends amounts to — constrain nothing.
            # Four rollers per stage, and WHICH member each one is bolted to is the whole
            # constraint: the upper pair bolts to the parent, the lower pair to this stage.
            # Both sit inside the overlap, so both bear on two tubes — two blocks on the
            # same tube, which is what putting them at one member's own ends amounts to,
            # constrain nothing.
            for tag, y, host in (("upper", stage_top - 1.0, "parent"),
                                 ("lower", stage_y - sh_len / 2 + 1.0, "stage")):
                name = f"stage {s + 1} {tag} roller"
                if c.elevator_bearings == "slide":
                    features.append(_feat("slide", f"{name} pad",
                                          _at(x, y, 0.9), size=[0.6, 1.2, 0.25], mat="delrin",
                                          note=f"bolted to the {host}"))
                else:
                    features.append(bearing(name, bore=0.375, od=0.875, width=0.44,
                                            at=_at(x, y, 0.9), rot=_rot(90, 0, 0),
                                            note=f"bolted to the {host}"))
            # A stage that can leave its parent is a stage that lands on the floor. The stop
            # is a real block inside the overlap, so it bears on both tubes.
            features.append(hardstop(f"stage {s + 1} up stop", _at(x, stage_top - 2.2, -0.9),
                                     size=(0.9, 0.6, 0.5)))

    # The carriage rides the INNERMOST stage, drawn stowed at the bottom of it. It is a face
    # plate standing in front of the tower with a back bracket behind each rail, tied
    # together by standoffs, and eight rollers pinching the two rails front and back.
    #
    # The roller AXIS is the thing that has to be right. A roller running on a rail's front
    # face turns about an axis across the robot, not up it — these were discs lying flat
    # beside the tube, 0.15 in clear of the only surface they were supposed to run on, which
    # is a carriage that would have fallen off its own elevator.
    carriage_y = 3.2
    cy = carriage_y + 1.4
    rz = innermost_sec[0] / 2                 # the rail's half-depth, the face rollers run on
    roll_r = 0.875 / 2
    face_z = rz + 1.05
    features.append(plate("carriage plate", (innermost_x * 2 + 2.0, 4.2, 0.250),
                          _at(0, cy, -face_z), pockets=4,
                          note="stands in front of the tower; the manipulator bolts to this"))
    for sx in (-1, 1):
        side = "left" if sx < 0 else "right"
        features.append(plate(f"carriage back bracket {side}", (2.2, 4.2, 0.250),
                              _at(sx * innermost_x, cy, face_z),
                              note="closes the carriage round the rail"))
        for sy in (-1, 1):
            features.append(_feat("standoff", f"carriage tie standoff {side}",
                                  _at(sx * innermost_x, cy + sy * 1.6, 0),
                                  dia=0.375, len=round(2 * face_z - 0.25, 3),
                                  rot=_rot(90, 0, 0), mat="aluminium-dark",
                                  note="clamps the front plate to the back bracket"))
            for sz in (-1, 1):
                features.append(bearing("carriage roller",
                                        _at(sx * innermost_x, cy + sy * 1.4,
                                            sz * (rz + roll_r)),
                                        _rot(0, 0, 90), bore=0.375, od=0.875, width=0.5,
                                        note=f"runs on the innermost rail's "
                                             f"{'front' if sz < 0 else 'back'} face"))
    # Rigging. Belt, chain and rope are all built, and each is a genuinely different machine:
    # a belt or chain run needs a driven element and an idler on every side it drives, while
    # a rope elevator is a WINCH — one drum, one pull, and a return that comes from gravity
    # and the stage stack, which is why the teams that run rope run it on a single side.
    # That distinction was being flattened into "two runs, whatever the medium", which drew a
    # rope elevator with two drums and no drum shaft joining them.
    rig = c.elevator_rigging
    rig_kind = {"chain": "#25 chain", "belt": "HTD 5 mm belt", "rope": "1/8 in Dyneema"}[rig]
    drive_y, top_y = 2.2, h - 0.9
    if rig == "rope":
        # The reference build: two motors on 12T pulleys into a 36T on the drum shaft — 1:3,
        # taken before the drum so the belt sees the motor torque and not the load. The rope
        # dead-ends on a standoff-and-washer stack riveted through the drum wall, because a
        # rope wrapped round a smooth tube pays out under load.
        drum_x = -(span / 2 - 1.5)
        drum_at = _at(drum_x, drive_y, 0.7)
        features.append(_feat("drum", "rigging drum", drum_at, dia=1.5, w=2.4,
                              rot=_rot(0, 0, 90), rope="1/8 in Dyneema", mat="aluminium",
                              note="aluminium tube; the rope ties off inside it"))
        features.append(_feat("standoff", "rope tie-off standoff",
                              _at(drum_x, drive_y + 0.55, 0.7), dia=0.375, len=0.60,
                              rot=_rot(90, 0, 0), mat="aluminium-dark",
                              note="riveted through the drum wall with a large washer; the "
                                   "rope's dead end"))
        features.append(_feat("tensioner", "rope sheave", _at(drum_x, top_y, 0.7),
                              dia=1.2, w=0.5, rot=_rot(0, 0, 90)))
        features.append(rope("carriage rigging", drum_at, _at(drum_x, top_y, 0.7)))
        features.append(rope("carriage return", _at(drum_x, top_y, 0.7),
                             _at(drum_x + 0.6, carriage_y + 1.3, 0.7)))
        features.append(plate("rigging guard", (1.4, innermost_top * 0.9, 0.093),
                              _at(drum_x - 0.85, innermost_top * 0.45 + 1.0, 0.7),
                              _rot(0, 90, 0), mat="polycarb",
                              note="keeps the rope off the stage rails on the way up"))
    else:
        # One drive shaft spans the base, so BOTH sides carry a driven element, and both
        # sides get a top idler — each run starts and ends exactly on a wrap centre.
        for sx in (-1, 1):
            side = "left" if sx < 0 else "right"
            drive_at = _at(sx * (span / 2 - 1.5), drive_y, 0.7)
            idler_at = _at(sx * (span / 2 - 1.5), top_y, 0.7)
            features.append(belt(f"{side} rigging run", drive_at, idler_at, 0.36, kind=rig_kind))
            if rig == "chain":
                features.append(sprocket(f"rigging drive sprocket {side}", 18, drive_at, _rot(0, 0, 90)))
                features.append(sprocket(f"rigging idler {side}", 18, idler_at, _rot(0, 0, 90)))
            else:
                features.append(pulley(f"rigging drive pulley {side}", 24, 0.45, drive_at, _rot(0, 0, 90)))
                features.append(pulley(f"rigging idler {side}", 24, 0.45, idler_at, _rot(0, 0, 90)))
    # On the upright's inboard web, where its bracket bolts — not free-floating mid-run.
    # The web plane follows the ladder's outer section, so this holds for 2x2 and 2x1 towers.
    features.append(_feat("tensioner", f"{rig} tensioner",
                          _at(span / 2 - outer_sec[1] / 2, 3.4, 0.7),
                          dia=1.0, w=0.45, rot=_rot(0, 0, 90)))
    # The winch package hangs on a shelf off the upright, with the gearbox output on the
    # rigging drive shaft's own axis — drawn floating beside the tower it reads as wrong,
    # because it would be.
    mkey_pre = el.get("motor_key", "neo_vortex")
    shelf_x = _package_x(spec, lane_x, -span / 2 - 1.15,
                         MOTORS.get(mkey_pre, MOTORS["neo_vortex"])["diameter_in"] / 2 + 1.5,
                         side=-1.0)
    features.append(plate("gearbox mount shelf", (2.9, 0.190, 3.4), _at(shelf_x, 0.60, 0.7),
                          pockets=2, note="bolts to the upright web; gearbox and motors hang on this"))
    features.append(gearbox("elevator gearbox", (2.6, 3.0, 1.6), _at(shelf_x, drive_y, 0.7),
                            ratio=el.get("reduction", "12:1"), stages=2))
    mkey = el.get("motor_key", "neo_vortex")
    motor_count = int(el.get("motor_count", 2))
    # The pair STRADDLES the gearbox rather than marching away from it. Marching, the second
    # motor ended up two feet forward of the tower — which on a tower at the back rail is
    # inside the turret's swept circle. Symmetric about the output is also how a two-motor
    # gearbox is actually built.
    # Centre distance has to clear both pitch radii, or the belt drive is one pulley inside
    # another. A 36T and a 12T HTD 5 mm pair is 1.13 + 0.38 in of radius, and the motors used
    # to sit 0.85 in from the driven pulley — so on every rope-rigged elevator the nearest
    # winch motor pulley was reported 100% inside the driven pulley it was supposed to drive.
    # The package stays symmetric about its own centre; the centre just moves far enough away.
    # Straddle the output rather than stack behind it. Stacking every motor on one side keeps
    # the centre distance honest but walks the package back along -Z until it reaches the
    # carriage rollers — which is exactly what happened on the first attempt at this. Motors
    # alternate sides instead, so the nearest is always at the minimum centre distance and the
    # package never grows further from the drum than it has to.
    # Two constraints, and the motor has to satisfy both:
    #   * centre distance from the 36T driven pulley at z=0.7 must clear both pitch radii
    #     (1.13 + 0.38 in) or the belt drive is one pulley inside another;
    #   * the carriage rollers ride at z = ±1.19 and a 2.2 in motor body reaches 1.1 in either
    #     way, so anything nearer than ~2.9 in from centre lands in a roller.
    # NOTE — attempted and reverted, 17 Aug 2026. The nearest winch motor pulley sits 0.85 in
    # from the 36T driven pulley whose pitch radius alone is 1.13 in, so it is reported 100%
    # inside it: a belt drive with a centre distance smaller than its pulleys. The fix is one
    # line here, and every version of it costs more than it saves on a packed robot:
    #
    #   * stacking both motors back  → the far one lands in the carriage rollers at z ±1.19;
    #   * straddling the drum at ±3  → the +Z one enters the turret's swept circle;
    #   * shifting only 1.05 in back → 0.14 in brush on a carriage roller;
    #   * taking that 0.14 in in X   → motors reach into the turret's swept circle;
    #   * adding a motor plate so nothing floats → the plate itself is in that circle.
    #
    # Z is crowded by the carriage rollers and X by the turret, so the real fix is to give the
    # winch package its own lane — which is a change to how the tower is laid out, not to where
    # two motors sit. Left as it is and reported, rather than traded for a worse collision.
    def _motor_z(i: int) -> float:
        return 0.7 - 1.9 - (i - (motor_count - 1) / 2) * 2.1

    for i in range(motor_count):
        features.append(motor("elevator motor", mkey,
                              _at(shelf_x, drive_y, _motor_z(i)), _rot(0, 0, 90)))
    features.append(shaft("rigging drive shaft", _HEX_BORE, span + 3.2, _at(-1.0, drive_y, 0.7),
                          _rot(0, 0, 90)))
    if rig == "rope":
        # The reduction is a real pair of pulleys on a real belt, not a ratio in a caption.
        # 12T on each motor into one 36T on the drum shaft is the 1:3 the spec claims.
        features.append(pulley("winch driven pulley", 36, 0.45, _at(shelf_x + 1.5, drive_y, 0.7),
                               _rot(0, 0, 90)))
        for i in range(motor_count):
            mz = _motor_z(i)
            features.append(pulley("winch motor pulley", 12, 0.45, _at(shelf_x + 1.5, drive_y, mz),
                                   _rot(0, 0, 90)))
            features.append(belt("winch reduction belt", _at(shelf_x + 1.5, drive_y, mz),
                                 _at(shelf_x + 1.5, drive_y, 0.7), 0.45, kind="HTD 5 mm belt"))
    asm = _asm("elevator", "Elevator", "mechanism", features,
               origin=_at(lane_x, 2.0, station_z),
               note=f"{el.get('architecture', '')} · {stages} stage",
               mates=["tower feet bolt through the crossmember at this station",
                      "each stage slides in the one outboard of it",
                      "carriage slides on the final stage"])
    # What another mechanism needs in order to mount ON this tower instead of erecting its
    # own. `build_cad` strips it before the tree ships; it is a build-time fact, not geometry.
    asm["host"] = {"lane_x": lane_x, "station_z": station_z, "height_in": h,
                   "span_in": span, "upright_x": span / 2,
                   "carriage_y": carriage_y, "carriage_z": -face_z}
    return asm


def _arm(spec: dict[str, Any], lane_x: float, c: Choices,
         station_z: float) -> dict[str, Any] | None:
    """Dead-axle shoulder, one or two driven segments, an optional wrist and the end effector."""
    am = spec.get("manipulator") or {}
    if not am.get("included"):
        return None
    frame = spec["frame"]
    ln = frame["length_in"]
    lens = am.get("segment_lengths_in") or [am.get("reach_in", 20.0)]
    shoulder_y = am.get("shoulder_height_in", 7.0)
    mkey = am.get("motor_key", "neo")
    features: list[dict[str, Any]] = []
    for sx in (-1, 1):
        features.append(tube("shoulder post", TUBE_2X1, shoulder_y, _at(sx * 2.2, shoulder_y / 2, 0),
                             _rot(90, 0, 0), pockets=True))
        features.append(bearing("shoulder bearing block", _at(sx * 2.2, shoulder_y, 0), _rot(0, 0, 90),
                                bore=0.625, od=1.5, width=0.5))
    features.append(shaft("shoulder dead axle", 0.625, 5.0, _at(0, shoulder_y, 0), _rot(0, 0, 90), form="round"))
    features.append(gearbox("shoulder gearbox", (2.6, 2.6, 1.4), _at(-3.4, shoulder_y, 0),
                            ratio=am.get("reduction", "80:1"), stages=3))
    features.append(fastener_row("shoulder gearbox bolts", _at(-2.6, shoulder_y, 0),
                                 2, [0, 1.6, 0], rot=_rot(0, 90, 0), length=1.2))
    features.append(motor("shoulder motor", mkey, _at(-5.4, shoulder_y, 0), _rot(0, 0, 90)))
    # The last stage into the shoulder is its own decision: a gear pair is stiff and has no
    # slack to manage; chain and belt let the gearbox sit off the pivot where there is room.
    if c.arm_shoulder_drive == "gear":
        features.append(gear("shoulder output gear", 84, _at(2.2, shoulder_y, 0), dp=20, face=0.5,
                             bore=0.625, mat="anodised", lightened=6))
        features.append(gear("shoulder pinion", 14, _at(2.2, shoulder_y - 2.45, 0), dp=20,
                             face=0.5, bore=0.375))
    elif c.arm_shoulder_drive == "chain":
        features.append(sprocket("shoulder driven sprocket", 60, _at(2.2, shoulder_y, 0),
                                 _rot(0, 0, 90), width=0.35))
        features.append(sprocket("shoulder drive sprocket", 12, _at(2.2, shoulder_y - 4.0, 0),
                                 _rot(0, 0, 90), width=0.35))
        features.append(belt("shoulder chain run", _at(2.2, shoulder_y - 4.0, 0),
                             _at(2.2, shoulder_y, 0), 0.35, kind="#25 chain"))
        features.append(_feat("tensioner", "chain tensioner", _at(2.85, shoulder_y - 2.0, 0),
                              dia=0.9, w=0.35, rot=_rot(0, 0, 90)))
    else:
        features.append(pulley("shoulder driven pulley", 72, 0.6, _at(2.2, shoulder_y, 0),
                               _rot(0, 0, 90)))
        features.append(pulley("shoulder drive pulley", 15, 0.6, _at(2.2, shoulder_y - 4.0, 0),
                               _rot(0, 0, 90)))
        features.append(belt("shoulder belt run", _at(2.2, shoulder_y - 4.0, 0),
                             _at(2.2, shoulder_y, 0), 0.6, kind="HTD 5 mm 25 mm belt"))
    # The shoulder hard stop is emitted once the stow angle is known, below — it has to be
    # under the beam the arm actually rests on, and a fixed position was only ever under the
    # one pose the arm used to take.

    # Segments hinge off each other; each is a real 2x1 beam with a bolt column and pockets.
    #
    # The stowed pose has to keep the end effector ABOVE the carpet. The old fixed -32 deg
    # start swung the first segment down hard enough to bury the gripper ~4.6 in below the
    # wheel contact plane on a long-reach arm — geometry that cannot exist. Pick the first
    # angle from the natural stow that keeps the whole chain clear of the floor.
    _floor_local = -(2.0 + 1.125) + 0.5      # ground plane in this assembly's frame, + margin
    # ...and the STOWED arm has to be inside the frame perimeter, which is the other half of
    # the same question. Choosing the angle against the carpet alone let the chain reach
    # forward out of the frame and into whatever was parked there — on a hopper robot, six
    # bodies of arm inside the hopper. A robot starts a match folded up inside its own
    # perimeter, so that is what "stowed" has to mean here.
    _front_local = -ln / 2 - station_z + 1.0
    _back_local = ln / 2 - station_z - 1.0
    # ...and under the height limit, which is the constraint folding it up runs into. A
    # stowed arm has three walls, a floor and a ceiling, and picking the pose against any
    # one of them alone just moves the violation somewhere else: against the floor it
    # reached out of the frame, and against the frame it stood up through R107.
    _limits = (spec.get("season") or {}).get("limits") or (spec.get("season") or {}).get("rules") or {}
    _ceiling_local = float(_limits.get("max_height_in") or 30.0) - 2.0 - 1.0

    def _chain(start_deg: float) -> tuple[float, float, float, float]:
        """(lowest y, highest y, furthest forward z, furthest back z) of the stowed chain."""
        y, z, ang = shoulder_y, 0.0, start_deg
        low, high, front, back = shoulder_y, shoulder_y, 0.0, 0.0
        for seg in lens:
            y += math.sin(math.radians(ang)) * seg
            z -= math.cos(math.radians(ang)) * seg
            low, high = min(low, y), max(high, y)
            front, back = min(front, z), max(back, z)
            ang += 52.0
        return low - 1.4, high + 1.4, front - 1.0, back + 1.0   # the jaw hangs past the node

    _CANDIDATES = (-32.0, -24.0, -16.0, -8.0, 0.0, 8.0, 16.0, 24.0, 32.0, 44.0, 56.0, 68.0, 80.0)

    def _fits_vertically(a: float) -> bool:
        low, high, _f, _b = _chain(a)
        return low >= _floor_local and high <= _ceiling_local

    def _frame_miss(a: float) -> float:
        _l, _h, front, back = _chain(a)
        return max(0.0, _front_local - front) + max(0.0, back - _back_local)

    # Floor and ceiling are hard: below one the jaw is through the carpet, above the other
    # the robot fails R107 before it has moved. Reaching outside the frame is the one to
    # trade away when a long arm on a small frame cannot satisfy everything — so choose among
    # the poses that fit vertically, and take the least excursion of those.
    _vertical = [a for a in _CANDIDATES if _fits_vertically(a)]
    if _vertical:
        angle = min(_vertical, key=lambda a: (_frame_miss(a), abs(a)))
    else:
        angle = max(_CANDIDATES, key=lambda a: _chain(a)[0])
    # The stop lands under the first beam, a short way out from the pivot: it is what the arm
    # comes to rest ON, so it follows the pose rather than sitting at a fixed point that only
    # matched one of them.
    _stop_rad = math.radians(angle)
    features.append(hardstop("shoulder hard stop",
                             _at(0.0,
                                 shoulder_y + math.sin(_stop_rad) * 1.6 - 0.85,
                                 -math.cos(_stop_rad) * 1.6)))
    node = [0.0, shoulder_y, 0.0]
    for i, seg_len in enumerate(lens):
        rad = math.radians(angle)
        mid = [node[0], node[1] + math.sin(rad) * seg_len / 2, node[2] - math.cos(rad) * seg_len / 2]
        features.append(tube(f"segment {i + 1} beam", TUBE_2X1, seg_len, _at(*mid), _rot(angle, 0, 0),
                             pockets=True, note=f"{seg_len:g} in between pivot centres"))
        features.append(fastener_row(f"segment {i + 1} bolts",
                                     _at(mid[0] + 0.56, mid[1], mid[2]),
                                     max(2, int(seg_len // 2.5)),
                                     [0, 0, -2.5], rot=_rot(angle, 0, 0)))
        features.append(bearing(f"joint {i + 1} bearing", _at(*node), _rot(0, 0, 90),
                                bore=0.625, od=1.5, width=0.5))
        node = [node[0], node[1] + math.sin(rad) * seg_len, node[2] - math.cos(rad) * seg_len]
        if i + 1 < len(lens):
            # The elbow package sits against the beam it drives, not 0.3 in off it.
            features.append(gearbox(f"elbow gearbox {i + 1}", (2.0, 2.0, 1.2), _at(node[0] - 1.4, node[1], node[2]),
                                    ratio="60:1", stages=2))
            features.append(motor("elbow motor", mkey, _at(node[0] - 3.0, node[1], node[2]), _rot(0, 0, 90)))
        angle += 52.0
    if am.get("wrist"):
        features.append(shaft("wrist shaft", 0.375, 2.0, _at(*node), _rot(0, 0, 90), form="round"))
        features.append(gearbox("wrist gearbox", (1.6, 1.6, 1.0), _at(node[0] - 1.1, node[1], node[2]),
                                ratio="45:1", stages=2))
        features.append(motor("wrist motor", "neo_550", _at(node[0] - 2.3, node[1], node[2]), _rot(0, 0, 90)))
    # End effector: a jaw plate with two driven compliant wheels, or a passive claw. The
    # mount plate bridges the beam end and the jaw so the effector hangs on structure.
    tip = [node[0], node[1] - 0.4, node[2] - 1.0]
    features.append(plate("effector mount plate", (2.0, 0.190, 1.8),
                          _at(node[0], node[1] - 0.15, node[2] - 0.5)))
    features.append(plate("end effector jaw", (2.6, 0.250, 1.8), _at(*tip)))
    if "compliant" in (am.get("end_effector") or ""):
        for sx in (-1, 1):
            features.append(wheel("gripper wheel", 1.7, 0.65, _at(tip[0] + sx * 1.0, tip[1] - 0.55, tip[2] - 0.6),
                                  _rot(0, 0, 90), kind="compliant", durometer="30A green"))
        features.append(motor("gripper motor", "neo_550", _at(tip[0], tip[1] + 1.0, tip[2] + 0.6), _rot(0, 0, 90)))
    else:
        for sx in (-1, 1):
            features.append(plate("claw finger", (0.25, 1.4, 2.2), _at(tip[0] + sx * 1.1, tip[1] - 0.9, tip[2] - 0.9)))
    for sx in (-1, 1):
        side = "left" if sx < 0 else "right"
        features.append(plate(f"shoulder foot {side}",
                              (3.0, 0.190, 3.0), _at(sx * 2.2, 0.095, 0), pockets=2,
                              note="bolts through the crossmember at this station"))
        features.append(_foot_bolts(f"shoulder foot bolts {side}", sx * 2.2, 0))
        features.append(gusset(f"shoulder foot gusset {side}", (2.2, 1.8),
                               _at(sx * 2.2, 0.15, 1.1), _rot(90, 0, 0)))
    return _asm("manipulator", "Arm", "mechanism", features,
                origin=_at(lane_x, 2.0, station_z),
                note=am.get("type", ""),
                mates=["shoulder feet bolt through the crossmember at this station, two 10-32s each",
                       "each segment revolute about the joint bearing pair"])


def _climber(spec: dict[str, Any], lane_x: float, c: Choices,
             station_z: float, host: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Winch climber. It builds its own telescoping tower only when there is nothing on the
    robot to climb on; when an elevator is already standing, the climber rides that.

    A robot with a cascade elevator does not erect a SECOND telescoping tower six inches
    away from the first one. This used to: same 2x2 outer section, same height, same nesting
    stages, same rolling blocks, its own foot plate and its own crossmember — a duplicate
    elevator with a hook on it. Every team that climbs on an elevator hangs the hook off the
    carriage and puts the winch on the tower they already paid for, and that is a packaging
    decision, not a saving: the two towers were fighting for the same lane.
    """
    cl = spec.get("climber") or {}
    if not cl.get("included"):
        return None
    frame = spec["frame"]
    ln = frame["length_in"]
    h = min(cl.get("stowed_height_in", 28), 30)
    stages = max(1, min(int(cl.get("stages", 1)), 3))
    drum_d = cl.get("winch_drum_diameter_in", 1.25)
    ladder = c.ladder(max(stages, 2))
    features: list[dict[str, Any]] = []
    riding = bool(host)
    if riding:
        # No tower, no stages, no rolling blocks, no foot plate: the elevator supplies all of
        # them. What is left is the part that is genuinely the climber — the winch package and
        # the hook — bolted to the tower's inboard upright and to the carriage.
        h = float(host["height_in"])
        upright_x = float(host["upright_x"])
        top = float(host["carriage_y"])
        features.append(plate("winch shelf", (3.2, 0.250, 3.6), _at(upright_x, 2.4, -1.6),
                              pockets=2,
                              note="bolts to the elevator upright's web; the winch hangs on this"))
        features.append(_feat("hook", cl.get("hook", "hook"),
                              _at(0, top + 2.6, float(host["carriage_z"])),
                              size=[0.6, 1.8, 3.0], kind=cl.get("hook", ""),
                              note="bolted to the elevator carriage plate; the elevator is the lift"))
        features.append(gusset("winch shelf gusset", (2.4, 2.0), _at(upright_x, 2.55, 0.15),
                               _rot(90, 0, 0), form="triangle",
                               note="takes the winch load into the upright"))
    else:
        features.append(tube("climber tower", ladder[0][0], h, _at(0, h / 2, 0), _rot(90, 0, 0),
                             wall=ladder[0][1], pockets=True))
        # The row is CENTRED on `at` and its step is LOCAL to the rotated group: under
        # rot(90,0,0) a [0,0,s] step marches along the tower. Anchored mid-height so the
        # column spans the tower instead of half of it spanning the floor.
        features.append(fastener_row("tower bolt column", _at(0, h / 2, 1.05),
                                     max(2, int((h - 5) // 3.2)), [0, 0, 3.2], rot=_rot(90, 0, 0)))
        top = h * 0.55
        for s in range(1, min(stages, len(ladder) - 1) + 1):
            inner_len = h * 0.8
            sec, wall = ladder[s]
            cy = h * 0.5 + s * 1.5
            features.append(tube(f"climb stage {s}", sec, inner_len, _at(0, cy, 0), _rot(90, 0, 0),
                                 wall=wall, mat="aluminium-dark",
                                 note=f"nests inside {ladder[s - 1][0][0]:g}x{ladder[s - 1][0][1]:g}",
                                 allow=["climber tower"] + [f"climb stage {k}" for k in range(1, s)]))
            for sz in (-1, 1):
                features.append(bearing(f"stage {s} rolling block", _at(0, cy - inner_len / 2 + 0.6, sz * 1.0),
                                        _rot(90, 0, 0), bore=0.375, od=0.875, width=0.45))
            top = cy + inner_len * 0.4
        # The hook is BOLTED to the top of the innermost stage — that overlap is the joint,
        # not a packaging fault, so it is declared rather than nudged apart until the audit
        # stops noticing. Both the tower and every stage are named because which member is
        # innermost depends on how many stages this climber got.
        features.append(_feat("hook", cl.get("hook", "hook"), _at(0, top, 1.4), size=[0.6, 1.8, 3.0],
                              kind=cl.get("hook", ""),
                              allow=["climber tower"]
                                    + [f"climb stage {k}" for k in range(1, stages + 1)]))
    # The winch package hangs on one shaft: gearbox output, brake disc, drum and ratchet all
    # ride it, and the plate ties the package to the tower web. Each part used to be placed
    # at its own x with nothing continuous through them.
    # The winch package hangs on one shaft wherever it lives. Riding the elevator, it moves
    # onto the shelf on the tower's outboard upright and marches AWAY from the tower, so it
    # never lands on the elevator's own gearbox shelf on the opposite upright.
    # The motors march away from the mount, so the reach is measured from `wx` to the far
    # end of the last one — and measured against the FRAME, not against the tower it hangs
    # on. The second motor also goes beside the first along the robot rather than further
    # outboard, which is how a two-motor gearbox is built and is what kept the outer one on
    # the robot at all.
    motor_count = max(1, int(cl.get("motor_count", 2)))
    m_len = MOTORS.get(cl.get("motor_key", "kraken_x60"), MOTORS["kraken_x60"])["length_in"]
    reach = 3.4 + m_len / 2 + 0.3
    if riding:
        ws = -1.0                                  # the package marches +x off the upright
        wx = _package_x(spec, float(host["lane_x"]), float(host["upright_x"]), reach, side=1.0)
    else:
        ws = 1.0                                   # ...and -x off a tower of its own
        wx = _package_x(spec, lane_x, 0.0, reach, side=-1.0)

    def _wat(x: float, y: float, z: float) -> list[float]:
        return _at(wx + ws * x, y, z)

    features.append(shaft("winch shaft", _HEX_BORE, 4.8, _wat(-0.2, 3.0, -1.6), _rot(0, 0, 90)))
    features.append(plate("winch mount plate", (0.190, 2.6, 2.6), _wat(-1.05, 3.0, -1.6),
                          note="bolts to the elevator upright" if riding
                               else "bolts to the tower web; carries the winch bearing"))
    features.append(_feat("drum", "grooved winch drum", _wat(0, 3.0, -1.6), dia=drum_d, w=2.2,
                          rot=_rot(0, 0, 90), rope=cl.get("rope", "1/8 in Dyneema")))
    features.append(gear("winch ratchet", 24, _wat(1.4, 3.0, -1.6), _rot(0, 0, 90), dp=20, face=0.32))
    features.append(_feat("pawl", "ratchet pawl", _wat(1.4, 3.75, -1.6), size=[0.25, 1.2, 0.6]))
    if c.climber_hold == "brake+ratchet":
        # A disc brake on the drum shaft holds under power; the ratchet is what holds when the
        # match ends and the motors go dead. Belt and braces, on the one mechanism where
        # letting go drops the whole robot.
        features.append(_feat("brake", "drum disc brake", _wat(-0.6, 3.0, -1.6),
                              dia=2.2, w=0.25, rot=_rot(0, 0, 90)))
    features.append(gearbox("winch gearbox", (2.6, 3.0, 1.6), _wat(-1.8, 3.0, -1.6),
                            ratio="100:1", stages=3))
    for i in range(motor_count):
        features.append(motor("climb motor", cl.get("motor_key", "kraken_x60"),
                              _wat(-3.4, 3.0, -1.6 + i * 2.1), _rot(0, 0, 90)))
    features.append(rope("winch rope", _wat(0, 3.0, -1.0),
                         _at(0, top + (2.0 if riding else -0.6), -0.2),
                         dia=0.125, material=cl.get("rope", "Dyneema")))
    if not riding:
        features.append(plate("tower foot plate", (4.2, 0.250, 4.2), _at(0, 0.125, 0), pockets=4,
                              note="bolts through the crossmember at this station; the tower's "
                                   "whole overturning moment goes through this joint"))
        features.append(_foot_bolts("tower foot bolts a", 0, -1.3, spread_x=2.6, length=1.4))
        features.append(_foot_bolts("tower foot bolts b", 0, 1.3, spread_x=2.6, length=1.4))
        for sz in (-1, 1):
            features.append(gusset(f"tower foot gusset {'front' if sz < 0 else 'back'}",
                                   (2.4, 2.4), _at(0, 0.25, sz * 1.2), _rot(90, 0, 0)))
    if riding:
        return _asm("climber", "Climber (on the elevator)", "mechanism", features,
                    origin=_at(host["lane_x"], 2.0, host["station_z"]),
                    note=(cl.get("type", "") + " · winch on the elevator upright, hook on the "
                          "carriage — no second tower").strip(" ·"),
                    mates=["winch shelf bolts to the elevator upright's web",
                           "hook rides the elevator carriage; the elevator IS the lift",
                           "winch package on one shaft, plate-mounted to the shelf",
                           "ratchet holds the load with the motor unpowered"])
    return _asm("climber", "Climber", "mechanism", features,
                origin=_at(lane_x, 2.0, station_z),
                note=cl.get("type", ""),
                mates=["tower foot plate bolts through the crossmember, four bolts",
                       "stages slide inside each other on the rolling blocks",
                       "winch package on one shaft, plate-mounted to the tower",
                       "ratchet holds the load with the motor unpowered"])


# How far a turret's pedestal may lift it before the design is something else. A turret two
# feet in the air moves the centre of gravity where no drivetrain wants it, and at that point
# the right answer is a different shooter, not a taller tower.
_MAX_TURRET_LIFT_IN = 20.0

# Air between the underside of the rotating head and the top of whatever it sweeps over.
_TURRET_SWEEP_CLEARANCE_IN = 0.75


def _height_headroom(spec: dict[str, Any], provisional: list[dict[str, Any]],
                     drivetrain: list[dict[str, Any]]) -> float:
    """Inches left under the season's height rule, measured on the provisional robot.

    R107 caps a 2026 robot at 30 in for the whole match. A pedestal tall enough to clear a
    spindexer is 15 in, and spending all of it put a legal robot at 36.8 in — trading a
    geometry fault for a rule violation, which is a worse deal because the rule is the one
    that gets you turned away at inspection.
    """
    from app.services.cad_contract import geometry_envelope  # noqa: PLC0415

    # Same source `season_rule_report` reads, so the cap and the rule check agree.
    season = spec.get("season") or {}
    limits = season.get("limits") or season.get("rules") or {}
    max_h = limits.get("max_height_in")
    if not max_h:
        return _MAX_TURRET_LIFT_IN
    env = geometry_envelope({"assemblies": list(provisional) + list(drivetrain)})
    if not env.get("measured"):
        return _MAX_TURRET_LIFT_IN
    return max(0.0, float(max_h) - float(env["height_in"]) - 0.25)


def _turret_lift(provisional: list[dict[str, Any]],
                 drivetrain: list[dict[str, Any]], headroom: float = _MAX_TURRET_LIFT_IN
                 ) -> float:
    """How high the slew bearing has to sit for the head to sweep clear, measured not guessed.

    Run on the provisional pass, the same throwaway measurement the placement optimiser uses:
    the height a turret needs depends on what ends up beside it, which is not known until
    everything has been built once.

    Only obstacles the head would actually meet count — those whose footprint falls inside the
    circle the head sweeps — so a hopper packed against the turret raises it and a climber in
    the far corner does not.
    """
    from app.services.cad_contract import _floats, _world_box, expand_mirrors  # noqa: PLC0415

    shooter = next((a for a in provisional if a.get("sweep")), None)
    if shooter is None:
        return 0.0
    origin = _floats(shooter.get("origin"), 3) or [0.0, 0.0, 0.0]
    sweep = shooter["sweep"]
    axis_x = origin[0] + float(sweep.get("x") or 0.0)
    axis_z = origin[2] + float(sweep.get("z") or 0.0)
    floor = origin[1] + float(sweep.get("from_y") or 0.0)

    radius, head_bottom = 0.0, float("inf")
    for feature in expand_mirrors(shooter.get("features") or []):
        box = _world_box(feature, origin)
        if not box or box[1][1] < floor:
            continue                      # below the bearing: pedestal and base, not the head
        low, high = box
        radius = max(radius, abs(low[0] - axis_x), abs(high[0] - axis_x),
                     abs(low[2] - axis_z), abs(high[2] - axis_z))
        head_bottom = min(head_bottom, low[1])
    if radius <= 0.0 or head_bottom == float("inf"):
        return 0.0

    tallest = 0.0
    for assembly in list(provisional) + list(drivetrain):
        if assembly is shooter:
            continue
        aid = str(assembly.get("id") or "")
        asm_origin = _floats(assembly.get("origin"), 3) or [0.0, 0.0, 0.0]
        for feature in expand_mirrors(assembly.get("features") or []):
            box = _world_box(feature, asm_origin)
            if not box:
                continue
            low, high = box
            # Nearest point of this body to the turret axis, in plan.
            dx = max(low[0] - axis_x, 0.0, axis_x - high[0])
            dz = max(low[2] - axis_z, 0.0, axis_z - high[2])
            if (dx * dx + dz * dz) ** 0.5 > radius:
                continue                  # outside the swept circle: never in the way
            tallest = max(tallest, high[1])
    needed = tallest + _TURRET_SWEEP_CLEARANCE_IN - head_bottom
    if needed <= 0.1:
        return 0.0
    allowed = min(_MAX_TURRET_LIFT_IN, max(headroom, 0.0))
    # All of it, or none of it. A tower tall enough to clear the hopper is 15 in and R107 only
    # ever leaves about one — and a one-inch pedestal is the worst of both: it adds four posts,
    # two braces and a deck for the head to catch on while lifting it over nothing. Measured:
    # the flagship went from 3 unresolved issues to 7 when a partial pedestal was built.
    #
    # So when the lift cannot be afforded the turret stays on the crossmembers and the motion
    # audit reports the arc it is actually blocked over. That is a real design conflict — a
    # 30 in height limit does not admit a turret sweeping over an 11 in hopper — and saying so
    # is worth more than a tower that pretends to solve it.
    if allowed < needed - 0.5:
        return 0.0
    return round(allowed, 2)


def _motor_points(assemblies: list[dict[str, Any]],
                  assignments: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Where each channel's load physically is, in world space.

    The power analysis names loads by motor and subsystem ("Kraken X60 drive 1",
    "drivetrain"); the geometry names them by their job in an assembly ("drive motor"). There
    is no reliable string match between the two, and inventing one would silently mis-route
    half the harness. Matching on the SUBSYSTEM and then taking that subsystem's motors in
    order is honest about what is known: the wire lands on a real motor of the right
    mechanism, which is what the run has to clear.
    """
    from app.services.cad_contract import _floats  # noqa: PLC0415

    pools: dict[str, list[list[float]]] = {}
    for asm in assemblies:
        aid = str(asm.get("id") or "")
        # A swerve module's motors are drivetrain motors.
        subsystem = "drivetrain" if aid.startswith("swerve") or aid == "drivetrain" else aid
        origin = _floats(asm.get("origin"), 3) or [0.0, 0.0, 0.0]
        for feature in asm.get("features") or []:
            if not isinstance(feature, dict) or feature.get("t") != "motor":
                continue
            at = _floats(feature.get("at"), 3)
            if at is None:
                continue
            pools.setdefault(subsystem, []).append([origin[i] + at[i] for i in range(3)])
    # "arm" in the power analysis is "manipulator" in the tree, the same rename the model
    # geometry pass has to make.
    if "manipulator" in pools:
        pools.setdefault("arm", pools["manipulator"])
    out: dict[str, list[float]] = {}
    used: dict[str, int] = {}
    for entry in assignments:
        pool = pools.get(str(entry.get("subsystem") or ""))
        if not pool:
            continue
        index = used.get(entry["subsystem"], 0)
        out[str(entry.get("load"))] = pool[index % len(pool)]
        used[entry["subsystem"]] = index + 1
    return out


def _electrical(spec: dict[str, Any],
                assemblies: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The control system, placed where electrical_layout put it, as real boxes plus the
    battery → main breaker → distributor cable run."""
    elec = spec.get("electrical") or {}
    placements = elec.get("placements") or []
    if not placements:
        return None
    frame = spec["frame"]
    w, ln = frame["width_in"], frame["length_in"]
    features: list[dict[str, Any]] = []
    by_key: dict[str, list[float]] = {}
    # The radio and RSL are the two components that genuinely belong up high — the radio out
    # of the crush zone, the RSL where an inspector can see it — so they get a real mast: a
    # 1x1 at the back corner with a mount plate, not a box drawn at altitude with nothing
    # under it. Everything else bolts flat to the bellypan; the layout's z is advisory and
    # was floating the battery half a foot in the air.
    mast_h = 14.0
    # Inboard of the drivetrain, not on top of it. 2.2 in from each edge is exactly where a
    # wheel is — a swerve module's on a swerve robot, a drive wheel's on a west-coast — so
    # the mast used to stand inside one either way.
    dt = spec.get("drivetrain") or {}
    inset = _module_inset(spec)
    if inset:
        corner_clear = inset + dt.get("wheel_diameter_in", 4.0) / 2 + 0.6
    elif dt.get("type") == "west-coast":
        corner_clear = 2.6 + TUBE_2X1[1] / 2 + 0.9      # clear of the drive rail and its wheel
    else:
        corner_clear = 2.2
    mast_x = -w / 2 + max(2.2, corner_clear)
    mast_z = ln / 2 - 1.9
    mast_needed = any(pl["key"] in ("radio", "rsl") for pl in placements)
    if mast_needed:
        features.append(tube("radio mast", TUBE_1X1, mast_h,
                             _at(mast_x, mast_h / 2, mast_z), _rot(90, 0, 0), bolts=3.0,
                             note="1x1 at the back corner, gusseted to the frame rail"))
        features.append(gusset("mast foot gusset", (2.2, 2.2),
                               _at(mast_x, 1.15, mast_z - 0.55), _rot(90, 0, 0)))
        features.append(plate("radio mount plate", (4.6, 0.090, 3.0),
                              _at(mast_x + 2.3, mast_h - 2.6, mast_z), _rot(0, 0, 0)))
    battery: tuple[list[float], float, float] | None = None
    for pl in placements:
        size = pl.get("size_mm") or [0, 0, 0]
        centre = pl.get("center_mm") or [0, 0, 0]
        sx, sy, sz = size[0] / 25.4, size[1] / 25.4, size[2] / 25.4
        cx, cz = centre[0] / 25.4 - w / 2, centre[1] / 25.4 - ln / 2
        # Flat ON the pan, not hovering a visible sixteenth over it.
        cy = sz / 2 + 0.02
        on_pan = True
        if pl["key"] == "radio":
            cx, cz = mast_x + 2.3, mast_z
            cy = mast_h - 2.6 + 0.045 + sz / 2
            on_pan = False
        elif pl["key"] == "rsl":
            cx, cz = mast_x, mast_z
            cy = mast_h + sz / 2
            on_pan = False
        by_key[pl["key"]] = _at(cx, cy, cz)
        # The device envelope stays as the placeable body — it is what the layout engine
        # packed against and what the geometry audit checks against structure, so a roboRIO
        # buried in a frame rail is still caught. Its internals hang off it as sub-parts that
        # declare `allow` against it.
        name = pl.get("name", pl["key"])
        features.append(_feat("component", name, _at(cx, cy, cz),
                              key=pl["key"], size=[round(sx, 3), round(sz, 3), round(sy, 3)],
                              rot=_rot(0, pl.get("rotation_deg", 0), 0),
                              note=pl.get("note") or None))
        features.extend(device_bodies(pl["key"], name, _at(cx, cy, cz),
                                      {"channels": len(elec.get("assignments") or []) or 20}))
        if on_pan:
            # The mate string says "bolts through the bellypan" — these are those bolts.
            features.append(fastener_row(f"{pl['key']} mount bolts", _at(cx, 0.03, cz), 2,
                                         [max(sx * 0.7, 0.8), 0, 0], rot=_rot(90, 0, 0),
                                         length=0.5))
        if pl["key"] == "battery":
            battery = (_at(cx, sz + 0.05, cz), sx, sy)
    if battery:
        # An unrestrained battery is the classic failed-inspection item; the strap is real
        # geometry, not a note.
        at, bw, bd = battery
        features.append(polycarb("battery strap", (bw + 0.8, 0.093, 1.6), at))
    # The harness from the control-system diagram: the battery loop, one power pair per
    # high-current channel at that breaker's gauge, the CAN daisy-chain, the roboRIO feed,
    # the radio and RSL runs, and the pneumatics. Motor endpoints come from the mechanism
    # assemblies that were already built, so a channel wire ends at the motor it powers
    # rather than in the air over the bellypan.
    features.extend(harness_cables(by_key, elec.get("assignments") or [],
                                   motor_points=_motor_points(assemblies or [],
                                                              elec.get("assignments") or [])))
    return _asm("electrical", "Control system", "electrical", features,
                note=f"{elec.get('main_breaker_a', 120)} A main breaker, "
                     f"{ELECTRONICS[elec.get('distributor_key', 'pdh')]['name']}",
                mates=["every component bolts through the bellypan"])


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def _lanes(spec: dict[str, Any]) -> dict[str, float]:
    """Give coexisting mechanisms their own lateral band so the packaging reads like a
    real robot instead of every mechanism stacked on the centreline."""
    w = spec["frame"]["width_in"]
    has = lambda key: bool((spec.get(key) or {}).get("included"))  # noqa: E731
    # The climber lane is capped so its winch motors — the widest thing on the tower — stay
    # inside the frame perimeter instead of poking through the left bumper.
    lanes = {"elevator": 0.0, "shooter": 0.0, "hopper": 0.0,
             "manipulator": w * 0.22, "climber": -w * 0.22}
    if has("elevator") and has("shooter"):
        lanes["elevator"], lanes["shooter"] = -w * 0.10, w * 0.12
    if has("manipulator") and has("elevator"):
        lanes["manipulator"] = w * 0.30
    if has("hopper"):
        # A hopper wants the middle of the robot — it is the biggest single volume and every
        # other mechanism connects to it. The shooter sits above and behind it, so they share
        # the centreline rather than competing for it.
        lanes["hopper"] = 0.0
        # ...unless there is a tower to miss. Returning the shooter to the centreline
        # unconditionally silently cancelled the separation the rule above had just made, and
        # a turret on the centreline sweeps a circle the elevator is standing in. The hopper
        # is low and forward, so it can share its lane with a tower that clears it in Z; a
        # rotating turret cannot share with anything.
        if not has("elevator"):
            lanes["shooter"] = 0.0
        lanes["climber"] = -w * 0.24
    if has("elevator") and has("climber"):
        # The climber rides the elevator when one exists, so it inherits that lane rather
        # than reserving a second one for a tower it no longer builds.
        lanes["climber"] = lanes["elevator"]
    return lanes


# Who yields when two mechanisms want the same space. Earliest stays put: the intake is
# bolted to the front rail and cannot move at all; the hopper is the biggest single volume and
# everything feeds it; the elevator comes next because where a tower stands is a STRUCTURAL
# decision — it is the tallest and heaviest thing on the robot, its feet want the back rail,
# and its climb load goes through that joint — whereas a turret can be aimed from wherever
# there is room. Ranking the shooter above it meant the turret took the back and the tower
# got pushed into the middle of the robot, between the hopper and the shooter, which is the
# one place a tower should never be. The climber is last because when an elevator exists it
# has no placement of its own — it rides the tower.
_PLACEMENT_ORDER = ("intake", "hopper", "elevator", "shooter", "manipulator", "climber")
PLACEMENT_GAP_IN = 0.75          # air left between two mechanisms once they are pulled apart


def _footprints(assemblies: list[dict[str, Any]], *, below: float | None = None
                ) -> dict[str, tuple[float, float, float, float, float, float]]:
    """World (x0, x1, y0, y1, z0, z1) per mechanism, from the bodies it actually emitted.

    Three dimensions, not two. A shooter head sitting a foot above a hopper shares its plan
    view completely and touches none of it, and a resolver working in plan alone spends every
    move it has trying to separate the two — which is how a turret got pushed off a tower and
    into the hopper it was being fed by.

    Measured, not declared. A mechanism's footprint is whatever its motors, shelves and
    carriage plates end up reaching, and no hand-written bias table knows that number.

    ``below`` keeps only the bodies that reach under that height. Passing the bumper's top
    gives the footprint that has to stay ON the frame: above the bumper a mechanism may hang
    over the edge all it likes, and below it the bumper is in the way.
    """
    from app.services.cad_contract import _floats, _world_box, expand_mirrors  # noqa: PLC0415
    out: dict[str, tuple[float, float, float, float, float, float]] = {}
    for asm in assemblies:
        origin = _floats(asm.get("origin"), 3) or [0.0, 0.0, 0.0]
        lo = [1e9, 1e9, 1e9]
        hi = [-1e9, -1e9, -1e9]
        for feature in expand_mirrors(asm.get("features") or []):
            if feature.get("t") in ("belt", "rope", "cable", "envelope"):
                continue
            box = _world_box(feature, origin)
            if not box:
                continue
            if below is not None and box[0][1] >= below:
                continue
            for k in range(3):
                lo[k], hi[k] = min(lo[k], box[0][k]), max(hi[k], box[1][k])
        if hi[0] > lo[0]:
            out[str(asm.get("id"))] = (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])
    # A turret does not occupy its footprint, it occupies the circle it sweeps. Checking the
    # drawn pose is how a rotating head ends up sharing a lane with a tower.
    for asm in assemblies:
        sweep = asm.get("sweep")
        aid = str(asm.get("id"))
        if not sweep or aid not in out:
            continue
        origin = _floats(asm.get("origin"), 3) or [0.0, 0.0, 0.0]
        cx, cz = origin[0] + float(sweep.get("x", 0.0)), origin[2] + float(sweep.get("z", 0.0))
        x0, x1, y0, y1, z0, z1 = out[aid]
        r = max(abs(x0 - cx), abs(x1 - cx), abs(z0 - cz), abs(z1 - cz))
        out[aid] = (cx - r, cx + r, y0, y1, cz - r, cz + r)
    return out


_Box = tuple[float, float, float, float, float, float]

# How many bodies of one assembly the placement pass reasons about. A hopper is a big hollow
# box: one AABB round it is almost entirely air, so an optimiser working from that overstates
# what it occupies and makes bad trades — it will happily shove a shooter into the middle of
# a hopper because the box says the space was taken anyway. Bodies give it the same view the
# clearance audit has. The cap keeps the inner loop bounded; the largest bodies are kept,
# because those are the ones a collision is actually about.
_PLACEMENT_BODY_CAP = 48


def _body_boxes(assemblies: list[dict[str, Any]], cap: int = _PLACEMENT_BODY_CAP
                ) -> dict[str, list[_Box]]:
    """Per assembly, the world boxes of the bodies that can collide with another mechanism.

    Same exclusions and the same swept-turret treatment as `clearance_report`, so the thing
    the placement pass minimises is the thing the audit measures.
    """
    from app.services.cad_contract import (  # noqa: PLC0415
        _PASSES_THROUGH, _floats, _world_box, expand_mirrors)
    out: dict[str, list[_Box]] = {}
    for asm in assemblies:
        aid = str(asm.get("id"))
        origin = _floats(asm.get("origin"), 3) or [0.0, 0.0, 0.0]
        sweep = asm.get("sweep")
        cx = origin[0] + float((sweep or {}).get("x", 0.0))
        cz = origin[2] + float((sweep or {}).get("z", 0.0))
        floor = origin[1] + float((sweep or {}).get("from_y", 0.0))
        boxes: list[_Box] = []
        for feature in expand_mirrors(asm.get("features") or []):
            if feature.get("t") in _PASSES_THROUGH or feature.get("to") is not None:
                continue
            box = _world_box(feature, origin)
            if not box:
                continue
            low, high = box
            if sweep and high[1] >= floor:
                r = max(abs(low[0] - cx), abs(high[0] - cx), abs(low[2] - cz), abs(high[2] - cz))
                low = [cx - r, low[1], cz - r]
                high = [cx + r, high[1], cz + r]
            boxes.append((low[0], high[0], low[1], high[1], low[2], high[2]))
        if len(boxes) > cap:
            boxes.sort(key=lambda b: -((b[1] - b[0]) * (b[3] - b[2]) * (b[5] - b[4])))
            boxes = boxes[:cap]
        if boxes:
            out[aid] = boxes
    return out


def _separate(spec: dict[str, Any], prints: dict[str, _Box],
              stations: dict[str, float], lanes: dict[str, float],
              fixed: dict[str, _Box] | None = None,
              swept: frozenset[str] = frozenset(),
              low: dict[str, _Box] | None = None,
              bodies: dict[str, list[_Box]] | None = None) -> tuple[dict, dict, list]:
    """Pull interfering mechanisms apart along Z, then X, and say what could not be resolved.

    The placement used to be a table of biases that knew nothing about how big anything was.
    A crossmember can be put under a mechanism by arithmetic; keeping two mechanisms out of
    each other cannot, because the answer depends on geometry that only exists after both
    have been built. So this runs on the measured footprints and `build_cad` rebuilds.
    """
    frame = spec["frame"]
    half_w, half_l = frame["width_in"] / 2, frame["length_in"] / 2
    stations, lanes = dict(stations), dict(lanes)
    order = [k for k in _PLACEMENT_ORDER if k in prints]
    boxes = dict(prints)
    has_elevator = bool((spec.get("elevator") or {}).get("included"))

    def exempt(a: str, b: str) -> bool:
        pair = {a, b}
        # The intake hands the gamepiece over INTO the hopper, and the climber rides the
        # elevator on purpose. Both are meant to occupy each other's space.
        return pair == {"intake", "hopper"} or (pair == {"climber", "elevator"} and has_elevator)

    # The drivetrain does not move for anything. A swerve module owns its corner, and a
    # mechanism shoved into one has not been placed, it has been hidden. Counting them in the
    # cost is what stopped the turret being pushed out of the elevator and into a module.
    obstacles = dict(fixed or {})
    bodies = dict(bodies or {})
    shifts: dict[str, tuple[float, float]] = {}

    def box_overlap(a: _Box, b: _Box) -> float:
        """How deep two boxes interpenetrate: the smallest of the three axis overlaps."""
        return min(min(a[2 * k + 1], b[2 * k + 1]) - max(a[2 * k], b[2 * k]) for k in range(3))

    from app.services.cad_contract import PENETRATION_TOL_IN  # noqa: PLC0415

    def pair_overlap(a: _Box, b: _Box) -> float:
        """Assembly against assembly, from their bodies when we have them.

        Falls back to the envelope when a set is missing — the drivetrain obstacles and the
        outside-the-frame term still work box to box.
        """
        return max(0.0, box_overlap(a, b))

    def set_overlap(a: str, b: str, shift: dict[str, tuple[float, float]],
                    state: dict[str, _Box] | None = None) -> float:
        """Total body-on-body penetration between two mechanisms, past the audit's tolerance.

        This is deliberately the same quantity `clearance_report` counts, so a move the
        optimiser calls an improvement is an improvement in the report too. Optimising the
        assembly envelopes instead let it trade a real collision for a phantom one.

        The envelopes are still worth keeping for one thing: if two of them do not touch,
        no pair of their bodies can, and the quadratic loop is skipped. Most pairs on a robot
        are in that case, and without the check the placement pass took seconds.
        """
        ba, bb = bodies.get(a), bodies.get(b)
        if not ba or not bb:
            return 0.0
        if state is not None and a in state and b in state:
            if box_overlap(state[a], state[b]) <= PENETRATION_TOL_IN:
                return 0.0
        dxa, dza = shift.get(a, (0.0, 0.0))
        dxb, dzb = shift.get(b, (0.0, 0.0))
        total = 0.0
        for p in ba:
            px0, px1, py0, py1, pz0, pz1 = p
            px0 += dxa; px1 += dxa; pz0 += dza; pz1 += dza
            for q in bb:
                qx0, qx1, qy0, qy1, qz0, qz1 = q
                depth = min(min(px1, qx1 + dxb) - max(px0, qx0 + dxb),
                            min(py1, qy1) - max(py0, qy0),
                            min(pz1, qz1 + dzb) - max(pz0, qz0 + dzb))
                if depth > PENETRATION_TOL_IN:
                    total += depth - PENETRATION_TOL_IN
        return total

    # How far below the bumper's top a body has to be for the frame line to be a wall rather
    # than a suggestion. The offsets a mechanism is shifted by are the same for every body it
    # owns, so the low footprint is tracked alongside the full one and shifted with it.
    lows = dict(low or {})

    # An inch of one mechanism inside another is not the same problem as an inch of overhang:
    # the first cannot be built at all and the second is a packaging choice teams make every
    # season. Weighting them equally made the optimiser stack mechanisms on top of each other
    # rather than let anything past the frame line, which is the wrong trade in every case.
    INTERFERENCE_WEIGHT = 3.0

    def _over(box: _Box) -> float:
        """How far a box reaches past the frame perimeter, in plan."""
        return (max(0.0, -half_w - box[0]) + max(0.0, box[1] - half_w)
                + max(0.0, -half_l - box[4]) + max(0.0, box[5] - half_l))

    def outside(key: str, box: _Box, low_box: _Box | None) -> float:
        """How far this mechanism hangs off the frame, weighted by how much that matters.

        A hard "must stay on the frame" gate cannot work here: an over-bumper intake is
        supposed to hang off, and a design that starts out of bounds would freeze with no
        legal move. As a cost term it just makes leaving the frame expensive.

        Height is the whole distinction. Above the bumper, hanging over the frame line is a
        packaging choice teams make every season. Below it there is five inches of plywood
        and foam in the way, so it is not overhang at all — it is a collision, and it is
        priced like one. Without that split the optimiser cheerfully relieved an inch of
        mechanism-on-mechanism overlap by driving a tower three inches into the bumper.
        """
        cost_ = _over(box) * (1.4 if key in swept else 0.35)
        if low_box is not None:
            cost_ += _over(low_box) * INTERFERENCE_WEIGHT
        return cost_

    def key_cost(key: str, state: dict[str, _Box], low_state: dict[str, _Box],
                 shift: dict[str, tuple[float, float]]) -> float:
        """Every term in the total that involves one mechanism.

        Moving a mechanism cannot change a term it does not appear in, so a candidate is
        scored by the delta on this alone. Rescoring the whole robot per candidate meant
        recomputing hundreds of pairs that had not moved, and turned a 0.1 s build into
        nearly two seconds.
        """
        total = outside(key, state[key], low_state.get(key))
        for b in order:
            if b == key or exempt(key, b):
                continue
            total += INTERFERENCE_WEIGHT * set_overlap(key, b, shift, state)
        for name, box in obstacles.items():
            total += INTERFERENCE_WEIGHT * (
                set_overlap(key, name, shift, {**state, name: box}) if name in bodies
                else pair_overlap(state[key], box))
        return total

    def cost(state: dict[str, _Box], low_state: dict[str, _Box] | None = None,
             shift: dict[str, tuple[float, float]] | None = None) -> float:
        low_state = lows if low_state is None else low_state
        shift = shift or {}
        total = 0.0
        for i, a in enumerate(order):
            total += outside(a, state[a], low_state.get(a))
            for b in order[i + 1:]:
                if exempt(a, b):
                    continue
                total += INTERFERENCE_WEIGHT * set_overlap(a, b, shift, state)
            for name, box in obstacles.items():
                total += INTERFERENCE_WEIGHT * (
                    set_overlap(a, name, shift, {**state, name: box}) if name in bodies
                    else pair_overlap(state[a], box))
        return total

    def inside(box: _Box) -> bool:
        # A loose sanity bound only — how far off the frame is worth going is decided by the
        # cost, not here. This just stops a mechanism being flung into the next postcode.
        return (-half_w <= (box[0] + box[1]) / 2 <= half_w
                and -half_l <= (box[4] + box[5]) / 2 <= half_l)

    def move(key: str, dx: float, dz: float) -> None:
        """Shift one mechanism, keeping every view of it in step."""
        x0, x1, y0, y1, z0, z1 = boxes[key]
        boxes[key] = (x0 + dx, x1 + dx, y0, y1, z0 + dz, z1 + dz)
        if key in lows:                    # the low footprint rides along with its mechanism
            a0, a1, b0, b1, c0, c1 = lows[key]
            lows[key] = (a0 + dx, a1 + dx, b0, b1, c0 + dz, c1 + dz)
        sx0, sz0 = shifts.get(key, (0.0, 0.0))
        shifts[key] = (sx0 + dx, sz0 + dz)
        if key in stations:
            stations[key] = round(stations[key] + dz, 3)
        if key in lanes:
            lanes[key] = round(lanes[key] + dx, 3)

    # Once the clamp below has run, no move may put a mechanism back outside the frame.
    # As an up-front constraint this deadlocked — a design that starts outside has no move
    # that does not look equally bad — but once the state is already feasible it is just a
    # bound, and the optimiser goes on working inside it.
    keep_in = {"on": False}

    def stays_in(key: str, box: _Box) -> bool:
        if not keep_in["on"] or key == "intake":
            return True
        return (box[0] >= -half_w - 1e-6 and box[1] <= half_w + 1e-6
                and box[4] >= -half_l - 1e-6 and box[5] <= half_l + 1e-6)

    def optimise() -> None:
        """Greedy, and it may never make a design worse: a shift is committed only if it
        strictly reduces the total interference. The first version pushed whichever mechanism
        was lower priority by exactly the overlap and hoped, which walked a turret out of an
        elevator and straight into the hopper."""
        best = cost(boxes)
        for _ in range(6):
            if best <= 0.0:
                return
            winner: tuple[float, str, float, float] | None = None
            # Lowest priority moves first, and moves furthest: the intake is bolted to the
            # front rail and never moves at all.
            for key in reversed(order[1:]):
                x0, x1, y0, y1, z0, z1 = boxes[key]
                here = key_cost(key, boxes, lows, shifts)
                if here <= 1e-9:
                    continue                   # already clear; no move can improve on it
                for dx, dz in [(0.0, sg * d) for d in (1.0, 2.0, 3.5, 5.0, 7.5) for sg in (1, -1)]                             + [(sg * d, 0.0) for d in (1.0, 2.0, 3.5, 5.0, 7.5) for sg in (1, -1)]:
                    moved_box = (x0 + dx, x1 + dx, y0, y1, z0 + dz, z1 + dz)
                    if not inside(moved_box) or not stays_in(key, moved_box):
                        continue
                    trial = dict(boxes)
                    trial[key] = moved_box
                    trial_low = dict(lows)
                    if key in trial_low:
                        a0, a1, b0, b1, c0, c1 = trial_low[key]
                        trial_low[key] = (a0 + dx, a1 + dx, b0, b1, c0 + dz, c1 + dz)
                    trial_shift = dict(shifts)
                    sx0, sz0 = trial_shift.get(key, (0.0, 0.0))
                    trial_shift[key] = (sx0 + dx, sz0 + dz)
                    value = best + key_cost(key, trial, trial_low, trial_shift) - here
                    if value < best - 1e-6 and (winner is None or value < winner[0]):
                        winner = (value, key, dx, dz)
            if winner is None:
                return
            best, key, dx, dz = winner
            move(key, dx, dz)

    optimise()

    # Last word: nothing but the intake may finish outside the frame. The intake is an
    # over-bumper mechanism and is supposed to be out there; everything else has to be on the
    # robot, and a mechanism hanging past the frame line is hanging past the BUMPER, which
    # makes it the first thing another robot hits.
    #
    # This runs after the interference pass rather than as a constraint on it, and that
    # ordering is deliberate. As a constraint it deadlocked: a design that starts outside has
    # no move that does not look equally bad, so nothing moved at all and the interference
    # stayed too. As a final clamp it costs some interference on packed robots and says so.
    for key in order:
        if key == "intake":
            continue
        x0, x1, _y0, _y1, z0, z1 = boxes[key]
        dx = min(0.0, half_w - x1) + max(0.0, -half_w - x0)
        dz = min(0.0, half_l - z1) + max(0.0, -half_l - z0)
        if abs(dx) > 1e-6 or abs(dz) > 1e-6:
            move(key, dx, dz)
    keep_in["on"] = True

    optimise()

    # Whatever is left is a real answer about this robot, not a licence to overlap quietly.
    unresolved: list[str] = []
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if exempt(a, b):
                continue
            if set_overlap(a, b, shifts) > PLACEMENT_GAP_IN:
                unresolved.append(f"{a} and {b} still interfere and this frame has nowhere "
                                  f"left to put either")
        for name in obstacles:
            if name in bodies and set_overlap(a, name, shifts) > PLACEMENT_GAP_IN:
                unresolved.append(f"{a} reaches into {name}, which cannot move")
    return stations, lanes, unresolved


def build_cad(spec: dict[str, Any]) -> dict[str, Any]:
    """Expand a robot spec into the dimensioned CAD tree.

    Pure and deterministic: the same spec always produces the same geometry, so a design can
    be regenerated, diffed and re-rendered without drift.

    Built in two passes. The first is a measurement: mechanisms are laid out at their bias
    stations and their real footprints are read off the bodies they emitted. The second is
    the build, at stations that have been pulled apart so the footprints do not intersect.
    One pass could not do this — how much room a mechanism needs is not known until it has
    been built, which is how a turret ended up sweeping through an elevator tower.
    """
    lanes = _lanes(spec)
    c = Choices(spec)
    stations = _stations(spec)
    # The climber rides the elevator when one exists (see `_climber`), so its own station is
    # not a station at all — leaving it in would put a crossmember under a foot plate that no
    # longer gets built.
    if (spec.get("elevator") or {}).get("included"):
        stations.pop("climber", None)

    def mechanisms(lanes: dict[str, float], stations: dict[str, float],
                   turret_lift: float = 0.0) -> list[dict[str, Any]]:
        elevator = _elevator(spec, lanes["elevator"], c, stations.get("elevator", 0.0))
        built = [_intake(spec, c),
                 _hopper(spec, lanes["hopper"], c, stations.get("hopper", 0.0)),
                 _shooter(spec, lanes["shooter"], c, stations.get("shooter", 0.0),
                          turret_lift=turret_lift),
                 elevator,
                 _arm(spec, lanes["manipulator"], c, stations.get("manipulator", 0.0)),
                 _climber(spec, lanes["climber"], c, stations.get("climber", 0.0),
                          host=(elevator or {}).get("host"))]
        return [asm for asm in built if asm]

    drivetrain = _drivetrain(spec, c) if spec.get("include_drivetrain", True) else []
    # Pass one is a measurement, thrown away; pass two is the build. See the docstring.
    provisional = mechanisms(lanes, stations)
    stations, lanes, unresolved = _separate(
        spec, _footprints(provisional), stations, lanes, _footprints(drivetrain),
        swept=frozenset(str(a["id"]) for a in provisional if a.get("sweep")),
        # Below the bumper's top the frame line is plywood and foam, not open air. The
        # intake is meant to be out there, and it never moves, so it is left out.
        low={k: v for k, v in _footprints(provisional, below=BUMPER_HEIGHT_IN).items()
             if k != "intake"},
        bodies={**_body_boxes(provisional), **_body_boxes(drivetrain)})

    assemblies: list[dict[str, Any]] = [_chassis(spec, c, stations)]
    # A chassis-only request gets a chassis. The drivetrain and the control system are
    # additions the prompt has to ask for; adding them anyway is how "only a 27 inch chassis"
    # used to come back with four swerve modules and a PDH.
    assemblies += drivetrain
    # The control system is built LAST and is handed everything already built, so each
    # high-current channel's wire can end at the motor it actually powers instead of stopping
    # in the air over the bellypan.
    # How tall the turret's pedestal has to be depends on what the placement pass finally put
    # beside it, so it is measured from the provisional pass — the same throwaway build the
    # station optimiser measures — and spent on the real one.
    built = mechanisms(lanes, stations,
                       _turret_lift(provisional, drivetrain,
                                    _height_headroom(spec, provisional, drivetrain)))
    for asm in built + [
            _electrical(spec, assemblies + built)
            if spec.get("include_electrical", True) else None]:
        if asm:
            asm.pop("host", None)          # a build-time mounting fact, not geometry
            assemblies.append(asm)

    # Belts and chains are loops, not bars: stamp each one with the pitch radii
    # of the pulley/sprocket its endpoints land on, so the viewer and the STEP
    # worker can both draw the wrap from the same numbers. An endpoint that
    # resolves to nothing stays unstamped — the transmission report calls it out.
    from app.services.transmission_geom import (  # noqa: PLC0415 — avoids an import cycle at module load
        BELT_THICKNESS_IN, CHAIN_THICKNESS_IN, resolve_wrap)
    for asm in assemblies:
        feats = asm["features"]
        for f in feats:
            if f.get("t") != "belt":
                continue
            r1, r2, axis = resolve_wrap(feats, f)
            if r1 is not None:
                f["r1"] = round(r1, 3)
            if r2 is not None:
                f["r2"] = round(r2, 3)
            if axis is not None:
                # Only the axis LINE matters, so flip to a canonical sign: the CAD
                # contract reads every numeric field as a dimension and rejects
                # negatives, and (-1,0,0) is the same plane normal as (1,0,0).
                lead = next((v for v in axis if abs(v) > 1e-9), 1.0)
                sign = -1.0 if lead < 0 else 1.0
                f["axis"] = [round(sign * v, 4) + 0.0 for v in axis]
            f["thick"] = CHAIN_THICKNESS_IN if "chain" in (f.get("kind") or "") else BELT_THICKNESS_IN

    counts: dict[str, int] = {}
    total = 0
    height = 0.0
    for asm in assemblies:
        for feature in asm["features"]:
            counts[feature["t"]] = counts.get(feature["t"], 0) + int(feature.get("rep", {}).get("n", 1))
            total += 1
            height = max(height, asm["origin"][1] + feature["at"][1])
    cad = {
        "version": CAD_VERSION,
        "units": "in",
        "frame_of_reference": ("origin at frame centre on the top face of the bellypan; "
                               "+X right, +Y up, -Z toward the front of the robot"),
        "assemblies": assemblies,
        "feature_counts": counts,
        "feature_total": total,
        "envelope_in": [spec["frame"]["width_in"], round(height + 2, 1), spec["frame"]["length_in"]],
        "caveat": ("Dimensioned concept geometry. Sections, bores and centre distances are "
                   "consistent with each other and with the parts catalog, but nothing here "
                   "has been checked against a vendor drawing, a stress case or the current "
                   "game manual. Do not machine from it."),
    }
    # The structural audit ships WITH the tree: every body's contacts are checked against
    # real primitive extents, so "nothing floats" is a verified property of this design,
    # not a caption. Lazy import for the same circular-import reason as cut_list's.
    from app.services.cad_contract import clearance_report, structural_report  # noqa: PLC0415
    cad["integrity"] = structural_report(cad)
    # And the other half of "is this a real robot": nothing floats, AND nothing is inside
    # anything else. A packaging the frame cannot close is reported here rather than shipped
    # as geometry that renders but could not be built.
    cad["clearance"] = clearance_report(cad)
    if unresolved:
        cad["clearance"]["unresolved"] = unresolved
    # The narrow-phase audit. `clearance_report` compares whole mechanisms in the pose they
    # are drawn in; this one goes inside each assembly, tells a swallowed part from an
    # overlapping one, and samples every mechanism through its travel — which is where a
    # turret that looks fine on screen turns out to sweep through the hopper.
    from app.services.geometry_validation import validate_geometry  # noqa: PLC0415
    cad["geometry"] = validate_geometry(cad)
    return cad


def cut_list(cad: dict[str, Any]) -> list[dict[str, Any]]:
    """Roll the tube features up into a cut list — one row per section and length.

    Every row is orderable, so there is nothing to flag. Telescoping stages come off the
    nesting ladder rather than being shrunk to fit, which means the whole list is catalog
    stock and a team can buy straight from it.
    """
    # Mirrors expand first: a mirrored tube is two pieces of stock, and a cut list that says
    # one is a BOM lying by half.
    from app.services.cad_contract import expand_mirrors  # noqa: PLC0415
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for asm in cad["assemblies"]:
        for feature in expand_mirrors(asm["features"]):
            if feature["t"] != "tube":
                continue
            section = (round(float(feature["sec"][0]), 2), round(float(feature["sec"][1]), 2))
            key = (section, round(feature["len"], 2), feature.get("wall", 0.100))
            row = rows.setdefault(key, {
                "section_in": list(section), "wall_in": feature.get("wall", 0.100),
                "length_in": round(feature["len"], 2), "qty": 0, "used_in": [],
            })
            row["qty"] += 1
            if asm["name"] not in row["used_in"]:
                row["used_in"].append(asm["name"])
    return sorted(rows.values(), key=lambda r: (-r["length_in"], r["section_in"]))
