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
         pockets: bool = False, note: str = "") -> dict[str, Any]:
    """A length of rectangular tube.  ``bolts`` is the hole pitch down the long face."""
    return _feat("tube", name, at, sec=[section[0], section[1]], len=round(length, 3),
                 wall=wall, rot=rot, mat=mat, bolt_pitch=bolts, pockets=pockets or None,
                 note=note or None)


def plate(name: str, size: tuple[float, float, float], at: list[float],
          rot: list[float] | None = None, *, mat: str = "aluminium",
          pockets: int = 0, note: str = "") -> dict[str, Any]:
    """A flat plate.  ``size`` is (width, thickness, depth) in its own frame."""
    return _feat("plate", name, at, size=[round(v, 3) for v in size], rot=rot, mat=mat,
                 pockets=pockets or None, note=note or None)


def shaft(name: str, dia: float, length: float, at: list[float],
          rot: list[float] | None = None, *, form: str = "hex",
          mat: str = "steel") -> dict[str, Any]:
    return _feat("shaft", name, at, dia=dia, len=round(length, 3), rot=rot, form=form, mat=mat)


def bearing(name: str, at: list[float], rot: list[float] | None = None, *,
            bore: float = _HEX_BORE, od: float = _BEARING_OD, width: float = 0.313,
            flanged: bool = True) -> dict[str, Any]:
    return _feat("bearing", name, at, bore=bore, od=od, w=width, rot=rot, flanged=flanged)


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
          durometer: str = "") -> dict[str, Any]:
    return _feat("wheel", name, at, dia=dia, w=width, kind=kind, rot=rot,
                 duro=durometer or None)


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
           rot: list[float] | None = None, *, thickness: float = 0.090) -> dict[str, Any]:
    return _feat("gusset", name, at, size=[size[0], size[1]], th=thickness, rot=rot)


def polycarb(name: str, size: tuple[float, float, float], at: list[float],
             rot: list[float] | None = None) -> dict[str, Any]:
    return _feat("polycarb", name, at, size=[round(v, 3) for v in size], rot=rot)


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
_STATION_DEFAULTS = (("hopper", 0.42), ("shooter", 0.65), ("elevator", 0.58),
                     ("manipulator", 0.54), ("climber", 0.85))


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
        self.intake_drive = pick(["belt", "belt", "chain", "gear"])
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
BUMPER_HEIGHT_IN = 5.00
BUMPER_THICKNESS_IN = 3.31
BUMPER_PLYWOOD_IN = 0.75
BUMPER_NOODLE_DIA_IN = 2.50
BUMPER_FABRIC_IN = 0.03
BUMPER_NOODLE_IN = round(BUMPER_THICKNESS_IN - BUMPER_PLYWOOD_IN, 3)


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
           rot: list[float] | None = None, *, note: str = "") -> dict[str, Any]:
    """A bumper fabric wrap: a U-channel of cloth (outer face + top + bottom returns) whose
    ``size`` is (length along the segment, height, wrapped depth). Carries the alliance
    colour in the viewer and the team number rides on it."""
    return _feat("fabric", name, at, size=[round(v, 3) for v in size], rot=rot,
                 note=note or None)


def bumper_envelope(width_in: float, length_in: float) -> dict[str, float]:
    """The bumper's outer envelope, which is what the robot is actually measured at.

    Kept separate from the features so the viewer, the rule check and the packaging maths all
    read one definition instead of each re-deriving it from a plate size.
    """
    t = BUMPER_THICKNESS_IN
    return {"thickness_in": t, "height_in": BUMPER_HEIGHT_IN,
            "plywood_in": BUMPER_PLYWOOD_IN, "noodle_in": BUMPER_NOODLE_IN,
            "noodle_dia_in": BUMPER_NOODLE_DIA_IN, "noodles_per_segment": 2,
            "fabric_in": BUMPER_FABRIC_IN,
            "construction": (f"{BUMPER_PLYWOOD_IN:g} in plywood + 2 stacked Ø"
                             f"{BUMPER_NOODLE_DIA_IN:g} in noodles + fabric wrap"),
            "outer_width_in": round(width_in + 2 * t, 3),
            "outer_length_in": round(length_in + 2 * t, 3),
            "bottom_y_in": 0.0, "top_y_in": BUMPER_HEIGHT_IN}


def _bumper(w: float, ln: float) -> list[dict[str, Any]]:
    """Four bumper segments wrapping the frame, built exactly like the reference bumper.

    Per segment, inside out: a plywood backing standing the full 5.00 in face, two Ø2.5 in
    pool noodles stacked on the plywood's outer face, and the fabric wrap closing over the
    outer face and the top and bottom returns.  The front and back segments run the full
    outer width and the side segments are captured between them — four straight sections
    and four corner brackets, not a moulded ring.  Every segment hangs on two mount
    brackets reaching under the frame rail, so the bumper is attached, not adjacent.
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

    # Attachment: a corner bracket tying each pair of plywood ends together, and two mount
    # brackets per side hooking the segment onto the rail underside — the reference mounts
    # its bumper to the frame, so the model does too.
    for sx in (-1, 1):
        for sz in (-1, 1):
            features.append(gusset("bumper corner bracket", (2.5, 2.5),
                                   _at(sx * (half_w - 0.4), h - 0.6, sz * (half_l - 0.4)),
                                   thickness=0.125))
    for sx, sz, rot_y, tag in ((0, -1, 0, "front"), (0, 1, 0, "back"),
                               (-1, 0, 90, "left"), (1, 0, 90, "right")):
        for side in (-1, 1):
            at = (_at(side * (half_w - 3.0), 0.9, sz * (half_l + fab + ply / 2)) if sx == 0
                  else _at(sx * (half_w + fab + ply / 2), 0.9, side * (half_l - 3.0)))
            features.append(plate(f"{tag} bumper mount {'a' if side < 0 else 'b'}",
                                  (2.0, 1.8, 0.190), at, _rot(0, rot_y, 0),
                                  note="hooks the plywood to the rail; one bolt, quick-release"))
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
    for z, station in sorted(placed):
        features.append(tube(f"crossmember{' at ' + station if station else ''}",
                             cm_sec, cross_len,
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
            features.append(gusset("corner gusset", (2.95, 2.80),
                                   _at(sx * (half_w - 2.0), rail_h, sz * (half_l - 2.0)),
                                   thickness=0.118))
            if c.gusset_faces > 1:
                features.append(gusset("corner web gusset", (2.5, 1.6),
                                       _at(sx * (half_w - sec[1] - 0.1), rail_h / 2,
                                           sz * (half_l - 2.0)), _rot(0, 0, 90)))
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
    features += _bumper(w, ln)
    return _asm("chassis", f"Chassis {w:g} × {ln:g} in", "structure", features,
                note="Welded-free bolted tube frame; every joint is a gusset and four 10-32s.",
                mates=["bellypan fixed to rails", "rails fixed to each other at the corners"])


def _swerve_module(dt: dict[str, Any], index: int, at: list[float],
                   detailed: bool, c: Choices, corner: tuple[int, int],
                   rail_off: float) -> dict[str, Any]:
    """One swerve module, built from its real plate size, drop and wheel.

    The detailed flag opens up the gear train — pinion, spur, bevel pair and the azimuth
    ring — on one module, so the assembly reads as a mechanism instead of a black box.
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
    features.append(wheel("drive wheel", wheel_d, 1.5, _at(0.2, wheel_d / 2 - ground_clearance, 0.2),
                          _rot(0, 0, 90), kind="tread", durometer="billet with grip tread"))
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
        features.append(plate("module corner plate", (p + 1.4, 0.190, p + 1.4),
                              _at(0, top_y + 0.19, 0), pockets=2,
                              note="spreads the module load across both rails"))
    else:
        features.append(fastener_row("module through-bolts", _at(-p / 2 + 0.6, top_y + 0.1, 0),
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
            for i in range(per_side):
                z = (i - (per_side - 1) / 2) * ((ln - 8) / max(1, per_side - 1))
                centre = abs(i - (per_side - 1) / 2) < 0.25
                drop = 0.125 if centre else 0.0
                features.append(shaft(f"axle {i + 1}", _HEX_BORE, tie_w + 1.4,
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
            features.append(belt("gearbox→centre axle chain", _at(sx * rail_x, 3.0, 0),
                                 _at(sx * rail_x, axle_y - 0.125, 0), 0.3, kind="#25 chain"))
            motor_posts = [(2.6, -1.8), (4.4, -1.8), (3.5, 1.8)][:max(1, count // 2)]
            for m, (my, mz) in enumerate(motor_posts):
                features.append(motor("drive motor", dt.get("motor_key", "kraken_x60"),
                                      _at(sx * (rail_x - 1.6), my, mz), _rot(0, 0, 90)))
        return [_asm("drivetrain", "West-coast drivetrain", "drivetrain", features,
                     note=dt.get("selection_reason", ""),
                     mates=["drive rails tie to the frame rails on three bolted plates a side",
                            "axles ride bearings in both rails",
                            "centre wheels dropped 1/8 in so the robot turns on four patches"])]

    plate_in = dt.get("plate_in") or (4.10, 4.10)
    # Close enough to the corner that the module top plate laps onto the rail top faces —
    # the old 1.2 in margin held every module 0.2 in shy of the rails it claimed to bolt to.
    inset = plate_in[0] / 2 + 0.55
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
    return [_swerve_module(dt, i, _at(sx * (w / 2 - inset), 0, sz * (ln / 2 - inset)),
                           i == 0, c, (sx, sz), rail_off)
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
    pivot_y = BUMPER_HEIGHT_IN + 1.6
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
    for sx, side in ((-1, "left"), (1, "right")):
        tx = sx * tower_x
        features.append(plate(f"pivot tower {side}", (0.190, pivot_y - rail_top + 1.0, 2.6),
                              _at(tx, (pivot_y + rail_top) / 2 - 0.3, pivot_z), pockets=2,
                              note="stands on the front rail; carries the pivot bearing"))
        features.append(gusset(f"tower foot gusset {side}", (2.2, 1.8),
                               _at(tx, rail_top + 0.1, pivot_z + 0.9), _rot(90, 0, 0)))
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
        features.append(plate(f"intake arm {side}", (0.190, arm_len + 1.6, 2.4),
                              _at(ax, arm_mid[1], arm_mid[2]), _rot(-arm_angle, 0, 0),
                              pockets=3, note=f"{arm_len:.1f} in between pivot and roller"))

    # Rollers climb back up the arm toward the frame, so a gamepiece walks over the bumper.
    drive_x = width / 2 + 0.55

    def transmit(name: str, teeth: int, at: list[float]) -> dict[str, Any]:
        # Belt/chain modes only. The gear drive builds its own train further down,
        # with every centre computed from the pitch radii — a helper that doubled
        # tooth counts here once produced a 3.6 in gear overlapping its neighbours.
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

    # Power rides on the arm: gearbox and motor at the pivot end where the mass belongs,
    # one reduction run down to the first roller, roller-to-roller runs after that. They
    # hang on a drive plate held off the arm plate by two standoffs — a gearbox drawn in
    # free air beside the arm is exactly the floating-part defect this file must not make.
    drive_plate_x = drive_x + 0.32
    features.append(plate("intake drive plate", (0.190, 3.6, 3.4),
                          _at(drive_plate_x, pivot_y - 1.9, pivot_z - 1.0), pockets=1,
                          note="outboard of the arm on standoffs; gearbox and motor bolt to this"))
    for so, sy in ((1, 1.2), (2, -1.2)):
        features.append(_feat("standoff", f"drive plate standoff {so}",
                              _at((width / 2 + drive_plate_x) / 2, pivot_y - 1.9 + sy,
                                  pivot_z - 1.0),
                              dia=0.375, len=round(drive_plate_x - width / 2, 3),
                              rot=_rot(0, 0, 90)))
    if drive == "gear":
        # Gears only carry torque tooth to tooth, so the whole power package sits at
        # the roller bar: output gear meshes an idler meshes roller 1's gear, every
        # centre at exactly the sum of the pitch radii, laid out along the arm
        # direction. Rollers 2+ each get a pair idler so all rollers turn the same
        # way. (The old layout kept the gearbox at the pivot and asked one 24t idler
        # to bridge five inches of air.)
        gear_dp = 20
        r_roller, r_idler, r_out = 24 / gear_dp / 2, 22 / gear_dp / 2, 36 / gear_dp / 2
        p1y, p1z = roller_ats[0][1], roller_ats[0][2]
        idl_y = p1y - unit_y * (r_roller + r_idler)
        idl_z = p1z - unit_z * (r_roller + r_idler)
        out_y = p1y - unit_y * (r_roller + 2 * r_idler + r_out)
        out_z = p1z - unit_z * (r_roller + 2 * r_idler + r_out)
        features.append(gearbox("intake gearbox", (2.0, 2.2, 1.2), _at(drive_x + 0.85, out_y, out_z),
                                ratio=ik.get("gear_reduction", "4:1"), stages=2))
        features.append(motor("intake motor", mkey, _at(drive_x + 2.05, out_y, out_z), _rot(0, 0, 90)))
        features.append(gear("gearbox output gear", 36, _at(drive_x, out_y, out_z),
                             _rot(0, 0, 90), dp=gear_dp, face=0.375))
        features.append(gear("intake idler", 22, _at(drive_x, idl_y, idl_z),
                             _rot(0, 0, 90), dp=gear_dp, face=0.375))
        for r in range(count):
            features.append(gear(f"roller {r + 1} gear", 24,
                                 _at(drive_x, roller_ats[r][1], roller_ats[r][2]),
                                 _rot(0, 0, 90), dp=gear_dp, face=0.375))
        for r in range(count - 1):
            # Pair idler between adjacent rollers, sized so the on-line midpoint is a
            # true double mesh: 2·(r_roller + r_pair) = roller spacing.
            pair_teeth = int(round((centre - 2 * r_roller) * gear_dp))
            if pair_teeth >= 8:
                features.append(gear(f"roller {r + 1}→{r + 2} idler", pair_teeth,
                                     _at(drive_x,
                                         (roller_ats[r][1] + roller_ats[r + 1][1]) / 2,
                                         (roller_ats[r][2] + roller_ats[r + 1][2]) / 2),
                                     _rot(0, 0, 90), dp=gear_dp, face=0.375))
    else:
        gb_at = _at(drive_x + 0.85, pivot_y - 1.9, pivot_z - 1.4)
        features.append(gearbox("intake gearbox", (2.0, 2.2, 1.2), gb_at,
                                ratio=ik.get("gear_reduction", "4:1"), stages=2))
        features.append(motor("intake motor", mkey,
                              _at(drive_x + 2.05, pivot_y - 1.9, pivot_z - 1.4), _rot(0, 0, 90)))
        features.append(transmit("gearbox output", 36, _at(drive_x, pivot_y - 1.9, pivot_z - 1.4)))
        kind = "#25 chain" if drive == "chain" else "HTD 5 mm 15 mm belt"
        features.append(belt("reduction run", _at(drive_x, pivot_y - 1.9, pivot_z - 1.4),
                             _at(drive_x, roller_ats[0][1], roller_ats[0][2]), 0.35, kind=kind))
        for r in range(count - 1):
            features.append(belt(f"roller {r + 1}→{r + 2} run",
                                 _at(drive_x, roller_ats[r][1], roller_ats[r][2]),
                                 _at(drive_x, roller_ats[r + 1][1], roller_ats[r + 1][2]),
                                 0.35, kind=kind))
        features.append(_feat("tensioner", f"{drive} tensioner",
                              _at(drive_x + 0.7, (roller_ats[0][1] + pivot_y - 1.9) / 2,
                                  (roller_ats[0][2] + pivot_z - 1.4) / 2), dia=0.9, w=0.4,
                              rot=_rot(0, 0, 90)))
    if ik.get("indexer"):
        features.append(polycarb("indexer guide", (width - 1.0, 0.093, centre + 2.0),
                                 _at(0, pivot_y - 0.8, pivot_z + 1.6), _rot(-18, 0, 0)))

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
                              "static": ["pivot tower left", "pivot tower right",
                                         "tower foot gusset left", "tower foot gusset right",
                                         "tower foot bolts left", "tower foot bolts right",
                                         "pivot bearing left", "pivot bearing right",
                                         "stowed hard stop left", "stowed hard stop right",
                                         "deployed hard stop left", "deployed hard stop right",
                                         "pivot dead axle"],
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
    ln = frame["length_in"]
    width = hp.get("floor_width_in", 20.0)
    depth = hp.get("floor_depth_in", 14.0)
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
        for i in range(3):
            features.append(polycarb(f"agitator paddle {i + 1}", (width * 0.6, 0.093, 2.2),
                                     _at(0, floor_y + wall_h * 0.62, depth * 0.12),
                                     _rot(0, 0, 60 * i)))

    # Exit lane and gate. One piece wide, with a sensor that tells the code exactly one is
    # staged — a timer here is how you end up feeding two and jamming the shooter.
    for lane in range(lanes):
        offset = (lane - (lanes - 1) / 2) * (exit_w + 1.0)
        features.append(polycarb(f"exit lane {lane + 1} guide", (exit_w, wall_h * 0.6, 3.0),
                                 _at(offset, floor_y + wall_h * 0.3, -depth / 2 - 1.5)))
        features.append(wheel(f"exit lane {lane + 1} roller", 2.0, exit_w * 0.8,
                              _at(offset, floor_y + 1.0, -depth / 2 - 2.4), _rot(0, 0, 90),
                              kind="compliant", durometer="40A"))
        features.append(_feat("sensor", f"lane {lane + 1} beam-break",
                              _at(offset, floor_y + 1.6, -depth / 2 - 2.0),
                              size=[0.5, 0.5, 0.5], kind="beam-break",
                              note="one staged piece, sensed not timed"))
        features.append(hardstop(f"lane {lane + 1} gate stop",
                                 _at(offset + exit_w / 2, floor_y + 0.8, -depth / 2 - 2.4)))

    # Power: gearbox outboard of the wall on its own plate, one run to the index shaft.
    # The plate stands off the hopper wall, the gearbox bolts to the plate, and the motors
    # stack against the gearbox face — the whole package used to hang in free air.
    drive_x = width / 2 + 0.9
    features.append(plate("hopper drive plate", (0.190, 4.0, 4.5),
                          _at(width / 2 + 0.35, shaft_y + 1.0, -depth * 0.05),
                          note="stands off the wall; carries the gearbox and motors"))
    for so, sy in ((1, 1.3), (2, -1.3)):
        features.append(_feat("standoff", f"hopper drive standoff {so}",
                              _at(width / 2 + 0.18, shaft_y + 1.0 + sy, -depth * 0.05),
                              dia=0.375, len=0.36, rot=_rot(0, 0, 90)))
    features.append(gearbox("hopper gearbox", (2.0, 2.2, 1.2), _at(width / 2 + 1.35, shaft_y + 1.6, depth * 0.1),
                            ratio=hp.get("gear_reduction", "12:1"), stages=2))
    for i in range(int(hp.get("motor_count", 1))):
        features.append(motor("hopper motor", mkey,
                              _at(width / 2 + 3.0 + i * 2.2, shaft_y + 1.6, depth * 0.1), _rot(0, 0, 90)))
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


def _turret_stack(ring_bore: float) -> list[dict[str, Any]]:
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
        gear("turret pinion", 14, _at(mesh_x, 0.975, 0), dp=20, face=0.45, bore=0.375),
        motor("turret motor", "neo_550", _at(mesh_x, 2.2, 0), _rot(180, 0, 0)),
    ]
    for sz in (-1, 1):
        stack.append(hardstop("turret rotation stop",
                              _at(-(r + 0.6), 1.45, sz * (r + 0.4))))
    stack.append(_feat("chain_track", "turret energy chain", _at(0, 0.45, r + 0.9),
                       size=[0.9, 0.7, ring_bore * 1.5], kind="energy chain",
                       note="constant-force spring keeps it tensioned through the sweep"))
    return stack


def _shooter(spec: dict[str, Any], lane_x: float, c: Choices,
             station_z: float) -> dict[str, Any] | None:
    """Flywheel shooter — staged barrel or classic hooded pair — on a real pivot."""
    sh = spec.get("shooter") or {}
    if not sh.get("included"):
        return None
    frame = spec["frame"]
    ln = frame["length_in"]
    stages = max(1, min(int(sh.get("flywheel_stages", 1)), 3))
    fw_d = sh.get("flywheel_diameter_in", 4.0)
    stacked = bool(sh.get("stacked"))
    if stacked:
        fw_d = min(fw_d, 4.0)
    fw = fw_d / 2
    barrel = sh.get("barrel_length_in", 0.0)
    mkey = sh.get("motor_key", "kraken_x60")
    width = sh.get("width_in", 10.0)
    pivot_y = 6.0
    features: list[dict[str, Any]] = []

    if stacked and barrel > 4:
        # Two side rails carry every flywheel shaft; a polycarb floor guides the gamepiece.
        # 1x1 is the smallest square stock the catalog carries, so the rail is orderable; it
        # is centred to keep the original inner face at fw + 0.5, clear of the flywheel.
        for sx in (-1, 1):
            features.append(tube("barrel side rail", TUBE_1X1, barrel,
                                 _at(sx * (fw + 1.0), pivot_y, 0), _rot(-36, 0, 0), bolts=2.0))
        features.append(polycarb("barrel guide", (fw * 1.8, 0.093, barrel),
                                 _at(0, pivot_y - fw * 0.9, 0), _rot(-36, 0, 0)))
        # Every stage motor bolts to one raked plate riding the right barrel rail — motors
        # beside a barrel with nothing holding them are ballast, not a drivetrain.
        features.append(plate("barrel drive plate", (0.190, 3.2, barrel * 0.72),
                              _at(fw + 1.55, pivot_y, -0.2), _rot(-36, 0, 0),
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
        post_base = 1.61 if sh.get("turreted") else 0.0
        for sx in (-1, 1):
            features.append(bearing("pivot trunnion", _at(sx * (fw + 1.5), pivot_y, 0), _rot(0, 0, 90),
                                    bore=0.625, od=1.5, width=0.5))
            features.append(tube("shooter post", TUBE_2X1, pivot_y - post_base,
                                 _at(sx * (fw + 1.5), (pivot_y + post_base) / 2, 0),
                                 _rot(90, 0, 0), bolts=2.0))
        if sh.get("turreted"):
            features += _turret_stack(5.4)
    else:
        # On a turret the whole head sits on the rotating platform, so every head part is
        # lifted by the platform's top face. A head drawn at fixed heights over a turret is
        # how the old model ended up with posts starting mid-air.
        turret = bool(sh.get("turreted"))
        y0 = 1.61 if turret else 0.0
        # The side plates ARE the mechanism's structure: flywheel bearings, feeder shaft,
        # hood and hood drive all mount to them, and they stand on the posts. Shafts run
        # plate-to-plate — a bearing with no plate and a pulley past the end of its shaft
        # were the two ways this head used to come apart.
        side_x = fw + 1.05
        m_len = MOTORS.get(mkey, MOTORS["kraken_x60"])["length_in"]
        for sx in (-1, 1):
            features.append(plate(f"shooter side plate {'left' if sx < 0 else 'right'}",
                                  (0.190, 6.6, 5.6), _at(sx * side_x, y0 + 3.5, 0.5),
                                  pockets=3,
                                  note="carries the flywheel bearings, the feeder and the hood"))
        for sy in (-1, 1):
            fy = y0 + 4.5 + sy * (fw + 0.25)
            features.append(shaft("flywheel shaft", _HEX_BORE, (side_x + 0.4) * 2,
                                  _at(0, fy, 0), _rot(0, 0, 90)))
            for sx in (-1, 1):
                features.append(wheel("flywheel", fw_d, 0.8, _at(sx * 1.0, fy, 0),
                                      _rot(0, 0, 90), kind="urethane", durometer="grey 60A"))
                features.append(bearing("flywheel bearing", _at(sx * side_x, fy, 0),
                                        _rot(0, 0, 90)))
            features.append(pulley("flywheel pulley", 24, 0.45, _at(side_x + 0.3, fy, 0),
                                   _rot(0, 0, 90)))
            features.append(motor("flywheel motor", mkey,
                                  _at(side_x + 0.05 + m_len / 2, fy, -1.4), _rot(0, 0, 90)))
            features.append(pulley("flywheel motor pulley", 12, 0.45,
                                   _at(side_x + 0.3, fy, -1.4), _rot(0, 0, 90)))
            features.append(belt("flywheel drive belt", _at(side_x + 0.3, fy, -1.4),
                                 _at(side_x + 0.3, fy, 0), 0.45))
        hood_name = "fixed hood" if c.shooter_hood_drive == "fixed" else "adjustable hood"
        features.append(_feat("hood", hood_name, _at(0, y0 + 4.5, 0),
                              r=fw + 1.0, w=round(side_x * 2 - 0.1, 3), arc=180,
                              range_deg=[0, 0] if c.shooter_hood_drive == "fixed"
                                        else sh.get("hood_angle_deg", [18, 62])))
        if c.shooter_hood_drive == "servo":
            features.append(_feat("actuator", "hood servo", _at(side_x + 0.4, y0 + 5.6, 1.0),
                                  size=[1.6, 0.8, 0.8], kind="linear servo"))
        elif c.shooter_hood_drive == "rack":
            features.append(gear("hood sector gear", 60, _at(side_x + 0.25, y0 + 4.5, 0), _rot(0, 0, 90),
                                 dp=20, face=0.3, mat="anodised"))
            features.append(gear("hood pinion", 12, _at(side_x + 0.25, y0 + 6.3, 0), _rot(0, 0, 90),
                                 dp=20, face=0.3, bore=0.375))
        for sx in (-1, 1):
            features.append(tube("shooter post", TUBE_2X1, 4.4, _at(sx * side_x, y0 + 2.2, 0),
                                 _rot(90, 0, 0), bolts=2.0,
                                 note="stands under the side plate it carries"))
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
            features += _turret_stack(max(width * 0.45, 5.0))
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
    return _asm("shooter", "Shooter", "mechanism", features,
                origin=_at(lane_x, 2.0, station_z),
                note=sh.get("type", ""),
                mates=(["turret base plate bolts to the crossmember pair at this station",
                        "turret revolute about Y on the slew bearing"] if sh.get("turreted")
                       else ["feet bolt to the crossmember at this station, two 10-32s each"])
                      + ["barrel revolute on the trunnions",
                         "flywheel shafts ride bearings in both side plates"])


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
    for s in range(1, min(moving, len(ladder) - 1) + 1):
        sec, wall = ladder[s]
        sh_len = h - s * 6
        inset = (outer_sec[1] - sec[1]) / 2 + (s - 1) * 0.4
        for sx in (-1, 1):
            features.append(tube(f"stage {s + 1} rail", sec, sh_len,
                                 _at(sx * (span / 2 - inset), sh_len / 2 + s * 2, 0),
                                 _rot(90, 0, 0), wall=wall, mat="aluminium-dark",
                                 note=f"nests inside {outer_sec[0]:g}x{outer_sec[1]:g}"
                                      if s == 1 else f"nests inside {ladder[s - 1][0][0]:g}x{ladder[s - 1][0][1]:g}"))
            for end in (-1, 1):
                y = sh_len / 2 + s * 2 + end * (sh_len / 2 - 0.8)
                if c.elevator_bearings == "slide":
                    features.append(_feat("slide", f"stage {s + 1} slide pad",
                                          _at(sx * (span / 2 - inset), y, 0.9),
                                          size=[0.6, 1.2, 0.25], mat="delrin"))
                else:
                    features.append(bearing(f"stage {s + 1} block", bore=0.375, od=0.875,
                                            width=0.44,
                                            at=_at(sx * (span / 2 - inset), y, 0.9),
                                            rot=_rot(90, 0, 0)))
    carriage_y = h * 0.52
    features.append(plate("carriage plate", (span - 1.2, 0.250, 2.6), _at(0, carriage_y, 0), pockets=4))
    for sx in (-1, 1):
        for sz in (-1, 1):
            features.append(bearing("carriage roller", _at(sx * (span / 2 - 1.4), carriage_y, sz * 0.9),
                                    _rot(90, 0, 0), bore=0.375, od=0.875, width=0.5))
    # Rigging: a driven element at the base, an idler at the top, a tensioner on the return.
    # Belt, chain and rope are all built; each brings a different part to the assembly.
    rig = c.elevator_rigging
    rig_kind = {"chain": "#25 chain", "belt": "HTD 5 mm belt", "rope": "1/8 in Dyneema"}[rig]
    # One drive shaft spans the base, so BOTH sides carry a driven element, and both
    # sides get a top idler — each run starts and ends exactly on a wrap centre. The
    # old model drove the left side only, leaving the right run attached to air.
    for sx in (-1, 1):
        side = "left" if sx < 0 else "right"
        drive_at = _at(sx * (span / 2 - 1.5), 2.2, 0.7)
        idler_at = _at(sx * (span / 2 - 1.5), h - 0.9, 0.7)
        features.append(belt(f"{side} rigging run", drive_at, idler_at, 0.36, kind=rig_kind))
        if rig == "chain":
            features.append(sprocket(f"rigging drive sprocket {side}", 18, drive_at, _rot(0, 0, 90)))
            features.append(sprocket(f"rigging idler {side}", 18, idler_at, _rot(0, 0, 90)))
        elif rig == "belt":
            features.append(pulley(f"rigging drive pulley {side}", 24, 0.45, drive_at, _rot(0, 0, 90)))
            features.append(pulley(f"rigging idler {side}", 24, 0.45, idler_at, _rot(0, 0, 90)))
        else:
            features.append(_feat("drum", f"rigging drum {side}", drive_at, dia=1.5, w=0.8,
                                  rot=_rot(0, 0, 90), rope="1/8 in Dyneema"))
            features.append(_feat("tensioner", f"rope sheave {side}", idler_at, dia=1.2, w=0.5,
                                  rot=_rot(0, 0, 90)))
    # On the upright's inboard web, where its bracket bolts — not free-floating mid-run.
    # The web plane follows the ladder's outer section, so this holds for 2x2 and 2x1 towers.
    features.append(_feat("tensioner", f"{rig} tensioner",
                          _at(span / 2 - outer_sec[1] / 2, 3.4, 0.7),
                          dia=1.0, w=0.45, rot=_rot(0, 0, 90)))
    # The winch package hangs on a shelf off the upright, with the gearbox output on the
    # rigging drive shaft's own axis — drawn floating beside the tower it reads as wrong,
    # because it would be.
    features.append(plate("gearbox mount shelf", (2.9, 0.190, 3.4), _at(-span / 2 - 1.15, 0.60, 0.7),
                          pockets=2, note="bolts to the upright web; gearbox and motors hang on this"))
    features.append(gearbox("elevator gearbox", (2.6, 3.0, 1.6), _at(-span / 2 - 1.15, 2.2, 0.7),
                            ratio=el.get("reduction", "12:1"), stages=2))
    mkey = el.get("motor_key", "neo_vortex")
    for i in range(int(el.get("motor_count", 2))):
        features.append(motor("elevator motor", mkey,
                              _at(-span / 2 - 1.15, 2.2, 0.7 - 1.9 - i * 2.1), _rot(0, 0, 90)))
    features.append(shaft("rigging drive shaft", _HEX_BORE, span + 3.2, _at(-1.0, 2.2, 0.7), _rot(0, 0, 90)))
    return _asm("elevator", "Elevator", "mechanism", features,
                origin=_at(lane_x, 2.0, station_z),
                note=f"{el.get('architecture', '')} · {stages} stage",
                mates=["tower feet bolt through the crossmember at this station",
                       "each stage slides in the one outboard of it",
                       "carriage slides on the final stage"])


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
    features.append(hardstop("shoulder hard stop", _at(0, shoulder_y - 1.6, -1.6)))

    # Segments hinge off each other; each is a real 2x1 beam with a bolt column and pockets.
    #
    # The stowed pose has to keep the end effector ABOVE the carpet. The old fixed -32 deg
    # start swung the first segment down hard enough to bury the gripper ~4.6 in below the
    # wheel contact plane on a long-reach arm — geometry that cannot exist. Pick the first
    # angle from the natural stow that keeps the whole chain clear of the floor.
    _floor_local = -(2.0 + 1.125) + 0.5      # ground plane in this assembly's frame, + margin
    def _lowest_for(start_deg: float) -> float:
        y, ang, low = shoulder_y, start_deg, shoulder_y
        for seg in lens:
            y += math.sin(math.radians(ang)) * seg
            low = min(low, y)
            ang += 52.0
        return low - 1.4                      # jaw + gripper wheels hang below the last node

    angle = -32.0
    for _candidate in (-32.0, -24.0, -16.0, -8.0, 0.0, 8.0, 16.0, 24.0, 32.0):
        angle = _candidate
        if _lowest_for(_candidate) >= _floor_local:
            break
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
             station_z: float) -> dict[str, Any] | None:
    """Telescoping winch climber: 2x2 tower, staged inner tubes on rolling blocks, a grooved
    drum with a ratchet, and the hook."""
    cl = spec.get("climber") or {}
    if not cl.get("included"):
        return None
    frame = spec["frame"]
    ln = frame["length_in"]
    h = min(cl.get("stowed_height_in", 28), 30)
    stages = max(1, min(int(cl.get("stages", 1)), 3))
    drum_d = cl.get("winch_drum_diameter_in", 1.25)
    ladder = c.ladder(max(stages, 2))
    features: list[dict[str, Any]] = [
        tube("climber tower", ladder[0][0], h, _at(0, h / 2, 0), _rot(90, 0, 0),
             wall=ladder[0][1], pockets=True),
        # The row is CENTRED on `at` and its step is LOCAL to the rotated group: under
        # rot(90,0,0) a [0,0,s] step marches along the tower. Anchored mid-height so the
        # column spans the tower instead of half of it spanning the floor.
        fastener_row("tower bolt column", _at(0, h / 2, 1.05), max(2, int((h - 5) // 3.2)),
                     [0, 0, 3.2], rot=_rot(90, 0, 0)),
    ]
    top = h * 0.55
    for s in range(1, min(stages, len(ladder) - 1) + 1):
        inner_len = h * 0.8
        sec, wall = ladder[s]
        cy = h * 0.5 + s * 1.5
        features.append(tube(f"climb stage {s}", sec, inner_len, _at(0, cy, 0), _rot(90, 0, 0),
                             wall=wall, mat="aluminium-dark",
                             note=f"nests inside {ladder[s - 1][0][0]:g}x{ladder[s - 1][0][1]:g}"))
        for sz in (-1, 1):
            features.append(bearing(f"stage {s} rolling block", _at(0, cy - inner_len / 2 + 0.6, sz * 1.0),
                                    _rot(90, 0, 0), bore=0.375, od=0.875, width=0.45))
        top = cy + inner_len * 0.4
    features.append(_feat("hook", cl.get("hook", "hook"), _at(0, top, 1.4), size=[0.6, 1.8, 3.0],
                          kind=cl.get("hook", "")))
    # The winch package hangs on one shaft: gearbox output, brake disc, drum and ratchet all
    # ride it, and the plate ties the package to the tower web. Each part used to be placed
    # at its own x with nothing continuous through them.
    features.append(shaft("winch shaft", _HEX_BORE, 4.8, _at(-0.2, 3.0, -1.6), _rot(0, 0, 90)))
    features.append(plate("winch mount plate", (0.190, 2.6, 2.6), _at(-1.05, 3.0, -1.6),
                          note="bolts to the tower web; carries the winch bearing"))
    features.append(_feat("drum", "grooved winch drum", _at(0, 3.0, -1.6), dia=drum_d, w=2.2,
                          rot=_rot(0, 0, 90), rope=cl.get("rope", "1/8 in Dyneema")))
    features.append(gear("winch ratchet", 24, _at(1.4, 3.0, -1.6), _rot(0, 0, 90), dp=20, face=0.32))
    features.append(_feat("pawl", "ratchet pawl", _at(1.4, 3.75, -1.6), size=[0.25, 1.2, 0.6]))
    if c.climber_hold == "brake+ratchet":
        # A disc brake on the drum shaft holds under power; the ratchet is what holds when the
        # match ends and the motors go dead. Belt and braces, on the one mechanism where
        # letting go drops the whole robot.
        features.append(_feat("brake", "drum disc brake", _at(-0.6, 3.0, -1.6),
                              dia=2.2, w=0.25, rot=_rot(0, 0, 90)))
    features.append(gearbox("winch gearbox", (2.6, 3.0, 1.6), _at(-1.8, 3.0, -1.6),
                            ratio="100:1", stages=3))
    for i in range(int(cl.get("motor_count", 2))):
        # 1.9 in apart keeps the second motor's can inside the frame perimeter even in the
        # climber's outboard lane.
        features.append(motor("climb motor", cl.get("motor_key", "kraken_x60"),
                              _at(-3.4 - i * 1.9, 3.0, -1.6), _rot(0, 0, 90)))
    features.append(rope("winch rope", _at(0, 3.0, -1.0), _at(0, top - 0.6, -0.2),
                         dia=0.125, material=cl.get("rope", "Dyneema")))
    features.append(plate("tower foot plate", (4.2, 0.250, 4.2), _at(0, 0.125, 0), pockets=4,
                          note="bolts through the crossmember at this station; the tower's "
                               "whole overturning moment goes through this joint"))
    features.append(_foot_bolts("tower foot bolts a", 0, -1.3, spread_x=2.6, length=1.4))
    features.append(_foot_bolts("tower foot bolts b", 0, 1.3, spread_x=2.6, length=1.4))
    for sz in (-1, 1):
        features.append(gusset(f"tower foot gusset {'front' if sz < 0 else 'back'}",
                               (2.4, 2.4), _at(0, 0.25, sz * 1.2), _rot(90, 0, 0)))
    return _asm("climber", "Climber", "mechanism", features,
                origin=_at(lane_x, 2.0, station_z),
                note=cl.get("type", ""),
                mates=["tower foot plate bolts through the crossmember, four bolts",
                       "stages slide inside each other on the rolling blocks",
                       "winch package on one shaft, plate-mounted to the tower",
                       "ratchet holds the load with the motor unpowered"])


def _electrical(spec: dict[str, Any]) -> dict[str, Any] | None:
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
    mast_x, mast_z = -w / 2 + 2.2, ln / 2 - 2.2
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
        features.append(_feat("component", pl.get("name", pl["key"]), _at(cx, cy, cz),
                              key=pl["key"], size=[round(sx, 3), round(sz, 3), round(sy, 3)],
                              rot=_rot(0, pl.get("rotation_deg", 0), 0),
                              note=pl.get("note") or None))
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
    dist = by_key.get("pdh") or by_key.get("pdp")
    if by_key.get("battery") and by_key.get("main_breaker") and dist:
        features.append(_feat("cable", "battery + to main breaker", by_key["battery"],
                              to=by_key["main_breaker"], gauge="6 AWG", polarity="+", dia=0.26))
        features.append(_feat("cable", "main breaker to distributor", by_key["main_breaker"],
                              to=dist, gauge="6 AWG", polarity="+", dia=0.26))
        features.append(_feat("cable", "battery - to distributor", by_key["battery"],
                              to=dist, gauge="6 AWG", polarity="-", dia=0.26))
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
        lanes["hopper"], lanes["shooter"] = 0.0, 0.0
        lanes["climber"] = -w * 0.24
    return lanes


def build_cad(spec: dict[str, Any]) -> dict[str, Any]:
    """Expand a robot spec into the dimensioned CAD tree.

    Pure and deterministic: the same spec always produces the same geometry, so a design can
    be regenerated, diffed and re-rendered without drift.
    """
    lanes = _lanes(spec)
    c = Choices(spec)
    stations = _stations(spec)
    assemblies: list[dict[str, Any]] = [_chassis(spec, c, stations)]
    # A chassis-only request gets a chassis. The drivetrain and the control system are
    # additions the prompt has to ask for; adding them anyway is how "only a 27 inch chassis"
    # used to come back with four swerve modules and a PDH.
    if spec.get("include_drivetrain", True):
        assemblies += _drivetrain(spec, c)
    for asm in (_intake(spec, c),
                _hopper(spec, lanes["hopper"], c, stations.get("hopper", 0.0)),
                _shooter(spec, lanes["shooter"], c, stations.get("shooter", 0.0)),
                _elevator(spec, lanes["elevator"], c, stations.get("elevator", 0.0)),
                _arm(spec, lanes["manipulator"], c, stations.get("manipulator", 0.0)),
                _climber(spec, lanes["climber"], c, stations.get("climber", 0.0)),
                _electrical(spec) if spec.get("include_electrical", True) else None):
        if asm:
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
    from app.services.cad_contract import structural_report  # noqa: PLC0415
    cad["integrity"] = structural_report(cad)
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
