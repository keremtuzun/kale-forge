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

CAD_VERSION = "kale-cad-2.1"

# Materials the renderer and the BOM both understand.  Keeping this closed means a feature
# can never arrive with a finish nobody knows how to draw or price.
MATERIALS = ("aluminium", "aluminium-dark", "steel", "polycarb", "urethane", "delrin",
             "nylon", "rubber", "anodised", "copper", "plastic-black")

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
         mates: list[str] | None = None) -> dict[str, Any]:
    return {"id": asm_id, "name": name, "kind": kind,
            "origin": origin or [0.0, 0.0, 0.0],
            "features": [f for f in features if f],
            "mates": mates or [], "note": note}


def _chassis(spec: dict[str, Any], c: Choices) -> dict[str, Any]:
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
    for sx in (-1, 1):
        features.append(tube(f"side rail {'left' if sx < 0 else 'right'}", sec, ln,
                             _at(sx * (half_w - inset), y, 0), pockets=c.rail_pockets,
                             note="2x1x0.100 in, strong axis vertical"))
    cross_len = w - sec[1] * 2
    for sz, tag in ((-1, "front"), (1, "back")):
        features.append(tube(f"{tag} rail", sec, cross_len,
                             _at(0, y, sz * (half_l - inset)), _rot(0, 90, 0),
                             pockets=c.rail_pockets))
    # Interior crossmembers tie the side rails together and carry superstructure loads into
    # them. The count follows the unsupported span, not a fixed layout.
    for i in range(c.crossmembers):
        frac = -0.3 + (i + 1) * (0.6 / (c.crossmembers + 1))
        features.append(tube("crossmember", c.crossmember_section, cross_len,
                             _at(0, y, frac * ln), _rot(0, 90, 0), bolts=2.5))
    # Corner gussets: on the top face always, on the inboard web too when the corner is
    # carrying a module.
    for sx in (-1, 1):
        for sz in (-1, 1):
            features.append(gusset("corner gusset", (3.0, 3.0),
                                   _at(sx * (half_w - 2.0), rail_h, sz * (half_l - 2.0))))
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
    # Bumper backing on all four sides — plywood-equivalent envelope, not a structural claim.
    for sz, tag in ((-1, "front"), (1, "back")):
        features.append(plate(f"{tag} bumper backing", (w + 1.5, 2.5, 0.75),
                              _at(0, 2.0, sz * (half_l + 0.6)), mat="plastic-black"))
    for sx, tag in ((-1, "left"), (1, "right")):
        features.append(plate(f"{tag} bumper backing", (0.75, 2.5, ln + 1.5),
                              _at(sx * (half_w + 0.6), 2.0, 0), mat="plastic-black"))
    return _asm("chassis", f"Chassis {w:g} × {ln:g} in", "structure", features,
                note="Welded-free bolted tube frame; every joint is a gusset and four 10-32s.",
                mates=["bellypan fixed to rails", "rails fixed to each other at the corners"])


def _swerve_module(dt: dict[str, Any], index: int, at: list[float],
                   detailed: bool, c: Choices) -> dict[str, Any]:
    """One swerve module, built from its real plate size, drop and wheel.

    The detailed flag opens up the gear train — pinion, spur, bevel pair and the azimuth
    ring — on one module, so the assembly reads as a mechanism instead of a black box.
    """
    plate_in = dt.get("plate_in") or (4.10, 4.10)
    p = plate_in[0]
    drop = dt.get("drop_in", 4.60)
    wheel_d = dt.get("wheel_diameter_in", 4.0)
    top_y = 2.4
    mkey = dt.get("motor_key", "kraken_x60")
    skey = dt.get("steer_motor_key", mkey)
    features: list[dict[str, Any]] = [
        plate("module top plate", (p, 0.190, p), _at(0, top_y, 0), pockets=4),
        plate("module bottom plate", (p, 0.190, p), _at(0, 0.20, 0), pockets=4),
    ]
    for sx in (-1, 1):
        for sz in (-1, 1):
            features.append(_feat("standoff", "module standoff",
                                  _at(sx * (p / 2 - 0.5), top_y / 2, sz * (p / 2 - 0.5)),
                                  dia=0.375, len=top_y - 0.2))
    features.append(motor("drive motor", mkey, _at(-1.0, top_y + 0.1 + MOTORS[mkey]["length_in"] / 2, -1.0), _rot(180, 0, 0)))
    features.append(motor("steer motor", skey, _at(1.0, top_y + 0.1 + MOTORS.get(skey, MOTORS[mkey])["length_in"] / 2, 1.0), _rot(180, 0, 0)))
    features.append(wheel("drive wheel", wheel_d, 1.5, _at(0.2, -(drop - wheel_d / 2 - 0.4), 0.2),
                          _rot(0, 0, 90), kind="tread", durometer="billet with grip tread"))
    features.append(bearing("azimuth main bearing", _at(0, 0.9, 0), bore=2.5, od=3.5, width=0.4))
    if c.module_mount == "corner-plate":
        # A corner plate spreads the module's load into both rails instead of relying on the
        # bolts through one rail wall.
        features.append(plate("module corner plate", (p + 1.4, 0.190, p + 1.4),
                              _at(0, top_y + 0.15, 0), pockets=2))
    else:
        features.append(fastener_row("module through-bolts", _at(-p / 2 + 0.6, top_y + 0.1, 0),
                                     4, [p / 3, 0, 0]))
    if detailed:
        features += [
            gear("drive pinion", 12, _at(-1.0, top_y - 0.35, -1.0), dp=20, face=0.35, bore=0.312),
            gear("drive spur", 40, _at(-0.1, top_y - 0.35, -0.1), dp=20, face=0.35, lightened=6, mat="anodised"),
            gear("idler", 22, _at(0.8, top_y - 0.35, 0.4), dp=20, face=0.35, lightened=5, mat="anodised"),
            _feat("bevel", "bevel pair", _at(0.8, 0.55, 0.4), teeth=20, pd=1.6, face=0.4),
            gear("azimuth ring gear", 60, _at(0, 0.9, 0), dp=20, face=0.4, bore=p / 2 - 0.55, mat="anodised"),
            gear("azimuth pinion", 14, _at(1.0, 0.9, 1.0), dp=20, face=0.4, bore=0.312),
            shaft("drive output shaft", _HEX_BORE, drop, _at(0.2, top_y / 2 - 0.6, 0.2)),
        ]
    return _asm(f"swerve_{index}", f"Swerve module {index + 1}", "drivetrain", features,
                origin=at, note=dt.get("selection_reason", ""),
                mates=["module plates bolt to the frame corner", "azimuth revolute about Y"])


def _drivetrain(spec: dict[str, Any], c: Choices) -> list[dict[str, Any]]:
    dt = spec.get("drivetrain") or {}
    frame = spec["frame"]
    w, ln = frame["width_in"], frame["length_in"]
    if dt.get("type") == "west-coast":
        wheel_d = dt.get("wheel_diameter_in", 6.0)
        count = int(dt.get("drive_motors", 6))
        per_side = max(2, count // 2)
        features: list[dict[str, Any]] = []
        for sx in (-1, 1):
            features.append(tube("drive rail", TUBE_2X1, ln - 4, _at(sx * (w / 2 - 2.6), 1.0, 0),
                                 note="wheel rail, inboard of the frame rail"))
            for i in range(per_side):
                z = (i - (per_side - 1) / 2) * ((ln - 8) / max(1, per_side - 1))
                centre = abs(i - (per_side - 1) / 2) < 0.25
                drop = 0.125 if centre else 0.0
                features.append(wheel("drive wheel", wheel_d, 1.4,
                                      _at(sx * (w / 2 - 1.6), wheel_d / 2 - drop, z), _rot(0, 0, 90),
                                      kind="tread"))
                features.append(sprocket("#25 sprocket", 22 if not centre else 24,
                                         _at(sx * (w / 2 - 2.6), wheel_d / 2 - drop, z), _rot(0, 0, 90)))
                features.append(bearing("wheel bearing", _at(sx * (w / 2 - 2.4), wheel_d / 2 - drop, z),
                                        _rot(0, 0, 90)))
            features.append(gearbox("drop-centre gearbox", (3.2, 4.0, 2.0),
                                    _at(sx * (w / 2 - 4.2), 3.0, 0),
                                    ratio=f"{dt.get('drive_ratio', 8.45):g}:1", stages=2))
            for m in range(count // 2):
                features.append(motor("drive motor", dt.get("motor_key", "kraken_x60"),
                                      _at(sx * (w / 2 - 4.2), 3.0 + m * 2.6, -1.8), _rot(0, 0, 90)))
        return [_asm("drivetrain", "West-coast drivetrain", "drivetrain", features,
                     note=dt.get("selection_reason", ""),
                     mates=["centre wheels dropped 1/8 in so the robot turns on four patches"])]

    plate_in = dt.get("plate_in") or (4.10, 4.10)
    inset = plate_in[0] / 2 + 1.2
    count = int(dt.get("module_count") or 0)
    if count == 0:
        env = [_feat("envelope", "reserved module envelope",
                     _at(sx * (w / 2 - inset), 2.5, sz * (ln / 2 - inset)),
                     size=[plate_in[0], 5.0, plate_in[1]])
               for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        return [_asm("drivetrain", "Reserved swerve envelope", "drivetrain", env,
                     note="Corners cut and drilled; nothing installed.")]
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)][:count]
    return [_swerve_module(dt, i, _at(sx * (w / 2 - inset), 0, sz * (ln / 2 - inset)), i == 0, c)
            for i, (sx, sz) in enumerate(corners)]


def _intake(spec: dict[str, Any], c: Choices) -> dict[str, Any] | None:
    """Pivoting roller intake: side plates, roller shafts on bearings, compliant wheels,
    the belt reduction from a static gearbox, hard stops on the deploy arc."""
    ik = spec.get("intake") or {}
    if not ik.get("included"):
        return None
    frame = spec["frame"]
    w, ln = frame["width_in"], frame["length_in"]
    width = min(ik.get("width_in", 20), w - 4)
    roller_d = ik.get("roller_diameter_in", 2.0)
    count = int(ik.get("roller_count", 1))
    centre = ik.get("roller_center_distance_in", roller_d + 2.0)
    mkey = ik.get("motor_key", "neo")
    duro = ik.get("compliant_wheel", "")
    span = (count - 1) * centre
    features: list[dict[str, Any]] = []

    # Side supports carry every bearing; they are the datum of the whole mechanism. Plate is
    # lighter and easier to pocket; tube is stiffer and bolts straight to the frame.
    for sx in (-1, 1):
        if c.intake_supports == "tube":
            features.append(tube("intake side rail", TUBE_1X1, span + roller_d + 2.4,
                                 _at(sx * width / 2, 0.6, 0), bolts=2.0))
        else:
            features.append(plate("intake side plate", (0.190, 2.4, span + roller_d + 2.4),
                                  _at(sx * width / 2, 0.6, 0), pockets=3))
    # How power reaches the rollers changes the parts, not just the ratio.
    drive = c.intake_drive
    drive_x = width / 2 + 0.55

    def transmit(name: str, teeth: int, at: list[float]) -> dict[str, Any]:
        if drive == "chain":
            return sprocket(f"{name} sprocket", teeth, at, _rot(0, 0, 90))
        if drive == "gear":
            return gear(f"{name} gear", teeth * 2, at, _rot(0, 0, 90), dp=20, face=0.375)
        return pulley(f"{name} pulley", teeth, 0.45, at, _rot(0, 0, 90))

    for r in range(count):
        z = (r - (count - 1) / 2) * centre
        features.append(shaft(f"roller {r + 1} shaft", _HEX_BORE, width + 1.0,
                              _at(0, 0.6, z), _rot(0, 0, 90)))
        for sx in (-1, 1):
            features.append(bearing("roller bearing", _at(sx * width / 2, 0.6, z), _rot(0, 0, 90)))
        wheels = max(3, int(round(width / 4)))
        for i in range(wheels):
            x = -width / 2 + (i + 0.5) * (width / wheels)
            features.append(wheel(f"compliant wheel {r + 1}.{i + 1}", roller_d,
                                  width / wheels * 0.55, _at(x, 0.6, z), _rot(0, 0, 90),
                                  kind="compliant", durometer=duro))
        features.append(transmit(f"roller {r + 1}", 18, _at(drive_x, 0.6, z)))
    features.append(gearbox("intake gearbox", (2.0, 2.2, 1.2),
                            _at(width / 2 + 1.4, 0.9, -span / 2 - 1.6),
                            ratio=ik.get("gear_reduction", "4:1"), stages=2))
    features.append(motor("intake motor", mkey, _at(width / 2 + 2.6, 0.9, -span / 2 - 1.6), _rot(0, 0, 90)))
    features.append(transmit("gearbox output", 36, _at(drive_x, 0.9, -span / 2 - 1.6)))
    if drive == "gear":
        # A gear train meshes directly, so there is no run to tension — but it does need an
        # idler to get the rotation direction right at the roller.
        features.append(gear("intake idler", 24, _at(drive_x, 0.75, -span / 2 - 0.8),
                             _rot(0, 0, 90), dp=20, face=0.375))
    else:
        kind = "#25 chain" if drive == "chain" else "HTD 5 mm 15 mm belt"
        features.append(belt("reduction run", _at(drive_x, 0.9, -span / 2 - 1.6),
                             _at(drive_x, 0.6, -span / 2), 0.35, kind=kind))
        for r in range(count - 1):
            z0 = (r - (count - 1) / 2) * centre
            features.append(belt(f"roller {r + 1}→{r + 2} run", _at(drive_x, 0.6, z0),
                                 _at(drive_x, 0.6, z0 + centre), 0.35, kind=kind))
        features.append(_feat("tensioner", f"{drive} tensioner",
                              _at(drive_x + 0.7, 0.75, -span / 2 - 0.9), dia=0.9, w=0.4,
                              rot=_rot(0, 0, 90)))
    # Deploy pivot, with metal hard stops at each end of the arc when the design uses them.
    limits = ik.get("pivot_limit_deg") or [0, 0]
    if limits and limits != [0, 0]:
        features.append(shaft("pivot dead axle", 0.625, width + 2.0,
                              _at(0, 1.6, span / 2 + 1.6), _rot(0, 0, 90), form="round"))
        for sx in (-1, 1):
            features.append(bearing("pivot bearing block", _at(sx * (width / 2 + 0.4), 1.6, span / 2 + 1.6),
                                    _rot(0, 0, 90), bore=0.625, od=1.375, width=0.5))
            if c.intake_hardstops:
                features.append(hardstop("stowed hard stop", _at(sx * (width / 2 + 0.4), 2.6, span / 2 + 2.4)))
                features.append(hardstop("deployed hard stop", _at(sx * (width / 2 + 0.4), 0.4, span / 2 + 2.6)))
    if ik.get("indexer"):
        features.append(polycarb("indexer guide", (width - 1.0, 0.093, centre + 2.0),
                                 _at(0, 2.2, span / 2 + 0.4), _rot(-18, 0, 0)))
    bias = ik.get("position_bias", 0.10)
    return _asm("intake", "Intake", "mechanism", features,
                origin=_at(0, 1.2, -ln / 2 + bias * ln),
                note=ik.get("type", ""),
                mates=["side plates bolt to the front rail", "roller shafts revolute in the plates",
                       "pivot revolute about the dead axle, limited by the hard stops"])


def _hopper(spec: dict[str, Any], lane_x: float, c: Choices) -> dict[str, Any] | None:
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
    funnel_span = max((width - exit_w * lanes) / 2, 1.0)
    for sx in (-1, 1):
        features.append(wall(f"hopper {'left' if sx < 0 else 'right'} funnel",
                             (funnel_span, wall_h * 0.75, 0.093),
                             _at(sx * (exit_w * lanes / 2 + funnel_span / 2),
                                 floor_y + wall_h * 0.4, -depth / 2 + 1.4),
                             _rot(0, sx * 28, 0)))

    # Driven wheels on one shaft across the floor, standing `proud` above it. On a spindexer
    # the floor itself rotates and the wheels index the pieces off it; on a belt-floor hopper
    # the same wheels drive a belt underneath.
    shaft_y = floor_y - wheel_d / 2 + proud
    features.append(shaft("hopper drive shaft", _HEX_BORE, width + 1.2,
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
        features.append(gear("spindexer rim gear", 96, _at(0, floor_y - 0.55, 0), dp=20, face=0.35,
                             bore=disc_d - 1.6, mat="anodised"))
        features.append(gear("spindexer pinion", 16, _at(disc_d / 2 - 0.6, floor_y - 0.55, 0),
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

    # Power: gearbox outboard of the wall, one run to the index shaft.
    drive_x = width / 2 + 0.9
    features.append(gearbox("hopper gearbox", (2.0, 2.2, 1.2), _at(drive_x + 0.9, shaft_y + 1.6, depth * 0.1),
                            ratio=hp.get("gear_reduction", "12:1"), stages=2))
    for i in range(int(hp.get("motor_count", 1))):
        features.append(motor("hopper motor", mkey,
                              _at(drive_x + 2.4 + i * 2.2, shaft_y + 1.6, depth * 0.1), _rot(0, 0, 90)))
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
    features.append(_feat("tensioner", f"{c.hopper_drive} tensioner",
                          _at(drive_x + 0.7, shaft_y + 0.8, -depth * 0.04), dia=0.9, w=0.4,
                          rot=_rot(0, 0, 90)))
    # Structure under it all: two rails the floor and walls bolt to, and a corner post at
    # each wall corner running down to the chassis — a glass box with nothing visibly
    # holding it up reads as floating, because it would be.
    for sx in (-1, 1):
        features.append(tube("hopper support rail", TUBE_1X1, depth + 1.0,
                             _at(sx * (width / 2 - 0.6), floor_y - 0.8, 0), bolts=2.5))
    for sx in (-1, 1):
        for sz in (-1, 1):
            features.append(tube("hopper corner post", TUBE_1X1, 2.4,
                                 _at(sx * (width / 2 - 0.5), floor_y - 1.2, sz * (depth / 2 - 0.5)),
                                 _rot(90, 0, 0)))
    features.append(plate("hopper access panel", (width * 0.6, 0.090, depth * 0.4),
                          _at(0, floor_y + wall_h + 0.1, depth * 0.2), pockets=3,
                          note="thumbscrewed — a jam clears without removing the shooter"))

    bias = hp.get("position_bias", 0.42)
    return _asm("hopper", "Hopper / indexer", "mechanism", features,
                origin=_at(lane_x, 2.4, -ln / 2 + bias * ln),
                note=kind,
                mates=["support rails bolt to two crossmembers",
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
    stack: list[dict[str, Any]] = [
        plate("turret base plate", (side, 0.250, side), _at(0, 0.125, 0), pockets=8,
              note="bolts to two crossmembers; the slew bearing bolts to this"),
        bearing("turret slew bearing", _at(0, 0.50, 0),
                bore=ring_bore, od=ring_bore + 1.6, width=0.50),
        gear("turret ring gear", 120, _at(0, 0.975, 0), dp=20, face=0.45,
             bore=ring_bore - 0.4, mat="anodised"),
        wheel("turret platform", ring_bore + 2.4, 0.32, _at(0, 1.45, 0), kind="smooth",
              durometer="0.25 in 6061 disc — everything above this rotates"),
        gear("turret pinion", 14, _at(r + 1.35, 0.975, 0), dp=20, face=0.45, bore=0.375),
        motor("turret motor", "neo_550", _at(r + 1.35, 2.35, 0), _rot(180, 0, 0)),
    ]
    for sz in (-1, 1):
        stack.append(hardstop("turret rotation stop",
                              _at(-(r + 0.6), 1.45, sz * (r + 0.4))))
    stack.append(_feat("chain_track", "turret energy chain", _at(0, 0.45, r + 0.9),
                       size=[0.9, 0.7, ring_bore * 1.5], kind="energy chain",
                       note="constant-force spring keeps it tensioned through the sweep"))
    return stack


def _shooter(spec: dict[str, Any], lane_x: float, c: Choices) -> dict[str, Any] | None:
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
        for sy in (-1, 1):
            features.append(shaft("flywheel shaft", _HEX_BORE, width * 0.5,
                                  _at(0, y0 + 4.5 + sy * (fw + 0.25), 0), _rot(0, 0, 90)))
            for sx in (-1, 1):
                features.append(wheel("flywheel", fw_d, 0.8, _at(sx * 1.0, y0 + 4.5 + sy * (fw + 0.25), 0),
                                      _rot(0, 0, 90), kind="urethane", durometer="grey 60A"))
                features.append(bearing("flywheel bearing", _at(sx * (fw + 0.9), y0 + 4.5 + sy * (fw + 0.25), 0),
                                        _rot(0, 0, 90)))
            features.append(pulley("flywheel pulley", 24, 0.45,
                                   _at(fw + 1.4, y0 + 4.5 + sy * (fw + 0.25), 0), _rot(0, 0, 90)))
            features.append(motor("flywheel motor", mkey,
                                  _at(fw + 2.2, y0 + 4.5 + sy * (fw + 0.25), -1.4), _rot(0, 0, 90)))
        hood_name = "fixed hood" if c.shooter_hood_drive == "fixed" else "adjustable hood"
        features.append(_feat("hood", hood_name, _at(0, y0 + 4.5, 0),
                              r=fw + 1.0, w=2.6, arc=180,
                              range_deg=[0, 0] if c.shooter_hood_drive == "fixed"
                                        else sh.get("hood_angle_deg", [18, 62])))
        if c.shooter_hood_drive == "servo":
            features.append(_feat("actuator", "hood servo", _at(fw + 1.6, y0 + 5.6, 1.0),
                                  size=[1.6, 0.8, 0.8], kind="linear servo"))
        elif c.shooter_hood_drive == "rack":
            features.append(gear("hood sector gear", 60, _at(fw + 1.2, y0 + 4.5, 0), _rot(0, 0, 90),
                                 dp=20, face=0.3, mat="anodised"))
            features.append(gear("hood pinion", 12, _at(fw + 1.2, y0 + 6.3, 0), _rot(0, 0, 90),
                                 dp=20, face=0.3, bore=0.375))
        for sx in (-1, 1):
            features.append(tube("shooter post", TUBE_2X1, 4.4, _at(sx * 1.6, y0 + 2.2, 0), _rot(90, 0, 0),
                                 bolts=2.0))
        # How the gamepiece is presented to the flywheels is its own small mechanism.
        if c.shooter_feeder == "kicker":
            features.append(wheel("kicker wheel", 3.0, 1.2, _at(0, y0 + 3.0, 1.8), _rot(0, 0, 90),
                                  kind="compliant", durometer="40A"))
            features.append(motor("kicker motor", "neo_550", _at(width * 0.4, y0 + 3.0, 1.8), _rot(0, 0, 90)))
        elif c.shooter_feeder == "belt":
            features.append(pulley("feeder pulley", 18, 0.5, _at(0, y0 + 3.0, 2.4), _rot(0, 0, 90)))
            features.append(belt("feeder belt", _at(0, y0 + 3.0, 2.4), _at(0, y0 + 3.6, 1.2), 0.5))
            features.append(motor("feeder motor", "neo_550", _at(width * 0.4, y0 + 3.0, 2.4), _rot(0, 0, 90)))
        else:
            features.append(shaft("feeder roller shaft", _HEX_BORE, width * 0.6,
                                  _at(0, y0 + 3.1, 1.9), _rot(0, 0, 90)))
            features.append(wheel("feeder roller", 2.0, width * 0.5, _at(0, y0 + 3.1, 1.9),
                                  _rot(0, 0, 90), kind="compliant", durometer="35A"))
            features.append(motor("feeder motor", "neo_550", _at(width * 0.4, y0 + 3.1, 1.9), _rot(0, 0, 90)))
        features.append(polycarb("feeder guide", (width * 0.7, 0.093, 3.0), _at(0, y0 + 3.2, 1.6), _rot(-24, 0, 0)))
        if turret:
            features += _turret_stack(max(width * 0.45, 5.0))
    bias = sh.get("position_bias", 0.65)
    return _asm("shooter", "Shooter", "mechanism", features,
                origin=_at(lane_x, 1.2, -ln / 2 + bias * ln),
                note=sh.get("type", ""),
                mates=["posts bolt to a crossmember", "barrel revolute on the trunnions"]
                      + (["turret revolute about Y on the slew bearing"] if sh.get("turreted") else []))


def _elevator(spec: dict[str, Any], lane_x: float, c: Choices) -> dict[str, Any] | None:
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
        features.append(tube(f"{'left' if sx < 0 else 'right'} upright", outer_sec, h,
                             _at(sx * span / 2, h / 2, 0), _rot(90, 0, 0), wall=outer_wall,
                             pockets=True, note="static tower, strong axis fore-aft"))
        features.append(fastener_row("upright bolt column", _at(sx * span / 2, 3.0, 1.0),
                                     max(2, int((h - 5) // 3)), [0, 3.0, 0], rot=_rot(90, 0, 0)))
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
    for sx in (-1, 1):
        features.append(belt(f"{'left' if sx < 0 else 'right'} rigging run",
                             _at(sx * (span / 2 - 1.5), 2.2, 0.7),
                             _at(sx * (span / 2 - 1.5), h - 1.2, 0.7), 0.36, kind=rig_kind))
    drive_at = _at(-span / 2 + 1.5, 2.2, 0.7)
    idler_at = _at(span / 2 - 1.5, h - 1.2, 0.7)
    if rig == "chain":
        features.append(sprocket("rigging drive sprocket", 18, drive_at, _rot(0, 0, 90)))
        features.append(sprocket("rigging idler", 18, idler_at, _rot(0, 0, 90)))
    elif rig == "belt":
        features.append(pulley("rigging drive pulley", 24, 0.45, drive_at, _rot(0, 0, 90)))
        features.append(pulley("rigging idler", 24, 0.45, idler_at, _rot(0, 0, 90)))
    else:
        features.append(_feat("drum", "rigging drum", drive_at, dia=1.5, w=1.6,
                              rot=_rot(0, 0, 90), rope="1/8 in Dyneema"))
        features.append(_feat("tensioner", "rope sheave", idler_at, dia=1.2, w=0.5,
                              rot=_rot(0, 0, 90)))
    features.append(_feat("tensioner", f"{rig} tensioner", _at(span / 2 - 1.5, 3.4, 0.7),
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
    features.append(shaft("rigging drive shaft", _HEX_BORE, span, _at(0, 2.2, 0.7), _rot(0, 0, 90)))
    bias = el.get("position_bias", 0.55)
    return _asm("elevator", "Elevator", "mechanism", features,
                origin=_at(lane_x, 2.0, -ln / 2 + bias * ln),
                note=f"{el.get('architecture', '')} · {stages} stage",
                mates=["uprights bolt to two crossmembers",
                       "each stage slides in the one outboard of it",
                       "carriage slides on the final stage"])


def _arm(spec: dict[str, Any], lane_x: float, c: Choices) -> dict[str, Any] | None:
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
        features.append(_feat("tensioner", "chain tensioner", _at(3.0, shoulder_y - 2.0, 0),
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
    angle = -32.0
    node = [0.0, shoulder_y, 0.0]
    for i, seg_len in enumerate(lens):
        rad = math.radians(angle)
        mid = [node[0], node[1] + math.sin(rad) * seg_len / 2, node[2] - math.cos(rad) * seg_len / 2]
        features.append(tube(f"segment {i + 1} beam", TUBE_2X1, seg_len, _at(*mid), _rot(angle, 0, 0),
                             pockets=True, note=f"{seg_len:g} in between pivot centres"))
        features.append(fastener_row(f"segment {i + 1} bolts",
                                     _at(mid[0] + 0.56, mid[1], mid[2]),
                                     max(2, int(seg_len // 2.5)),
                                     [0, math.sin(rad) * 2.5, -math.cos(rad) * 2.5], rot=_rot(angle, 0, 0)))
        features.append(bearing(f"joint {i + 1} bearing", _at(*node), _rot(0, 0, 90),
                                bore=0.625, od=1.5, width=0.5))
        node = [node[0], node[1] + math.sin(rad) * seg_len, node[2] - math.cos(rad) * seg_len]
        if i + 1 < len(lens):
            features.append(gearbox(f"elbow gearbox {i + 1}", (2.0, 2.0, 1.2), _at(node[0] - 1.8, node[1], node[2]),
                                    ratio="60:1", stages=2))
            features.append(motor("elbow motor", mkey, _at(node[0] - 3.4, node[1], node[2]), _rot(0, 0, 90)))
        angle += 52.0
    if am.get("wrist"):
        features.append(shaft("wrist shaft", 0.375, 2.0, _at(*node), _rot(0, 0, 90), form="round"))
        features.append(gearbox("wrist gearbox", (1.6, 1.6, 1.0), _at(node[0] - 1.4, node[1], node[2]),
                                ratio="45:1", stages=2))
        features.append(motor("wrist motor", "neo_550", _at(node[0] - 2.6, node[1], node[2]), _rot(0, 0, 90)))
    # End effector: a jaw plate with two driven compliant wheels, or a passive claw.
    tip = [node[0], node[1] - 0.4, node[2] - 1.0]
    features.append(plate("end effector jaw", (2.6, 0.250, 1.8), _at(*tip)))
    if "compliant" in (am.get("end_effector") or ""):
        for sx in (-1, 1):
            features.append(wheel("gripper wheel", 1.7, 0.65, _at(tip[0] + sx * 1.0, tip[1] - 0.55, tip[2] - 0.6),
                                  _rot(0, 0, 90), kind="compliant", durometer="30A green"))
        features.append(motor("gripper motor", "neo_550", _at(tip[0], tip[1] + 1.0, tip[2] + 0.6), _rot(0, 0, 90)))
    else:
        for sx in (-1, 1):
            features.append(plate("claw finger", (0.25, 1.4, 2.2), _at(tip[0] + sx * 1.1, tip[1] - 0.9, tip[2] - 0.9)))
    bias = am.get("position_bias", 0.5)
    return _asm("manipulator", "Arm", "mechanism", features,
                origin=_at(lane_x, 1.2, -ln / 2 + bias * ln),
                note=am.get("type", ""),
                mates=["shoulder posts bolt to a crossmember",
                       "each segment revolute about the joint bearing pair"])


def _climber(spec: dict[str, Any], lane_x: float, c: Choices) -> dict[str, Any] | None:
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
        fastener_row("tower bolt column", _at(0, 3.0, 1.05), max(2, int((h - 5) // 3.2)), [0, 3.2, 0],
                     rot=_rot(90, 0, 0)),
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
    features.append(_feat("drum", "grooved winch drum", _at(0, 3.0, -1.6), dia=drum_d, w=2.2,
                          rot=_rot(0, 0, 90), rope=cl.get("rope", "1/8 in Dyneema")))
    features.append(gear("winch ratchet", 24, _at(1.4, 3.0, -1.6), _rot(0, 0, 90), dp=20, face=0.32))
    features.append(_feat("pawl", "ratchet pawl", _at(1.4, 4.4, -1.6), size=[0.25, 1.2, 0.6]))
    if c.climber_hold == "brake+ratchet":
        # A disc brake on the drum shaft holds under power; the ratchet is what holds when the
        # match ends and the motors go dead. Belt and braces, on the one mechanism where
        # letting go drops the whole robot.
        features.append(_feat("brake", "drum disc brake", _at(-0.6, 3.0, -1.6),
                              dia=2.2, w=0.25, rot=_rot(0, 0, 90)))
    features.append(gearbox("winch gearbox", (2.6, 3.0, 1.6), _at(-1.8, 3.0, -1.6),
                            ratio="100:1", stages=3))
    for i in range(int(cl.get("motor_count", 2))):
        features.append(motor("climb motor", cl.get("motor_key", "kraken_x60"),
                              _at(-3.4 - i * 2.4, 3.0, -1.6), _rot(0, 0, 90)))
    features.append(rope("winch rope", _at(0, 3.0, -1.0), _at(0, top - 0.6, -0.2),
                         dia=0.125, material=cl.get("rope", "Dyneema")))
    bias = cl.get("position_bias", 0.85)
    return _asm("climber", "Climber", "mechanism", features,
                origin=_at(lane_x, 1.2, -ln / 2 + bias * ln),
                note=cl.get("type", ""),
                mates=["tower tied into two frame rails",
                       "stages slide inside each other on the rolling blocks",
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
    for pl in placements:
        size = pl.get("size_mm") or [0, 0, 0]
        centre = pl.get("center_mm") or [0, 0, 0]
        sx, sy, sz = size[0] / 25.4, size[1] / 25.4, size[2] / 25.4
        cx, cz = centre[0] / 25.4 - w / 2, centre[1] / 25.4 - ln / 2
        # Everything electrical bolts flat to the bellypan — the layout's z is advisory and
        # was floating the battery half a foot in the air. The radio and RSL belong up on a
        # mast in real life, but a component drawn at mast height with no mast under it reads
        # as a floating box, so at concept level they mount to the pan like everything else.
        cy = sz / 2 + 0.06
        by_key[pl["key"]] = _at(cx, cy, cz)
        features.append(_feat("component", pl.get("name", pl["key"]), _at(cx, cy, cz),
                              key=pl["key"], size=[round(sx, 3), round(sz, 3), round(sy, 3)],
                              rot=_rot(0, pl.get("rotation_deg", 0), 0),
                              note=pl.get("note") or None))
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
    lanes = {"elevator": 0.0, "shooter": 0.0, "hopper": 0.0,
             "manipulator": w * 0.22, "climber": -w * 0.26}
    if has("elevator") and has("shooter"):
        lanes["elevator"], lanes["shooter"] = -w * 0.10, w * 0.12
    if has("manipulator") and has("elevator"):
        lanes["manipulator"] = w * 0.30
    if has("hopper"):
        # A hopper wants the middle of the robot — it is the biggest single volume and every
        # other mechanism connects to it. The shooter sits above and behind it, so they share
        # the centreline rather than competing for it.
        lanes["hopper"], lanes["shooter"] = 0.0, 0.0
        lanes["climber"] = -w * 0.30
    return lanes


def build_cad(spec: dict[str, Any]) -> dict[str, Any]:
    """Expand a robot spec into the dimensioned CAD tree.

    Pure and deterministic: the same spec always produces the same geometry, so a design can
    be regenerated, diffed and re-rendered without drift.
    """
    lanes = _lanes(spec)
    c = Choices(spec)
    assemblies: list[dict[str, Any]] = [_chassis(spec, c)]
    assemblies += _drivetrain(spec, c)
    for asm in (_intake(spec, c),
                _hopper(spec, lanes["hopper"], c),
                _shooter(spec, lanes["shooter"], c),
                _elevator(spec, lanes["elevator"], c),
                _arm(spec, lanes["manipulator"], c),
                _climber(spec, lanes["climber"], c),
                _electrical(spec)):
        if asm:
            assemblies.append(asm)

    counts: dict[str, int] = {}
    total = 0
    height = 0.0
    for asm in assemblies:
        for feature in asm["features"]:
            counts[feature["t"]] = counts.get(feature["t"], 0) + int(feature.get("rep", {}).get("n", 1))
            total += 1
            height = max(height, asm["origin"][1] + feature["at"][1])
    return {
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


def cut_list(cad: dict[str, Any]) -> list[dict[str, Any]]:
    """Roll the tube features up into a cut list — one row per section and length.

    Every row is orderable, so there is nothing to flag. Telescoping stages come off the
    nesting ladder rather than being shrunk to fit, which means the whole list is catalog
    stock and a team can buy straight from it.
    """
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for asm in cad["assemblies"]:
        for feature in asm["features"]:
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
