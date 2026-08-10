"""Closed CAD contract between the design model and deterministic Onshape compiler."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any

ALLOWED_FEATURES = frozenset({
    "actuator", "bearing", "belt", "bevel", "bolts", "brake", "cable",
    "chain_track", "component", "drum", "envelope", "fabric", "gear", "gearbox",
    "gusset", "hardstop", "hood", "hook", "motor", "noodle", "noodle_corner", "pawl", "plate",
    "polycarb", "pulley", "rope", "sensor", "shaft", "slide", "sprocket",
    "standoff", "tensioner", "tube", "wheel",
})
CATALOG_SECTIONS = frozenset({
    (2.0, 1.0), (1.0, 1.0), (2.0, 2.0), (1.5, 1.5), (1.5, 0.5),
})

# Pitch diameters are emitted as round(pd, 3), so anything beyond half a thousandth is a real
# disagreement rather than a rounding artefact.  The bound is shared by all three toothed parts.
_PITCH_TOLERANCE = 0.011
_DEFAULT_CHAIN_PITCH_IN = 0.25


class CadContractError(ValueError):
    """Raised when generated CAD cannot safely enter the compiler."""

    def __init__(self, errors: list[str]):
        super().__init__("CAD contract rejected: " + "; ".join(errors[:12]))
        self.errors = errors


def normalize_cad(cad: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with stable unique assembly/part names before compilation.

    Repeated physical parts remain separate editable bodies.  Their display names receive a
    deterministic numeric suffix instead of being flattened into one repeated feature.
    """
    normalized = deepcopy(cad)
    assembly_ids: dict[str, int] = {}
    for assembly in normalized.get("assemblies") or []:
        original_id = str(assembly.get("id") or "assembly")
        assembly_ids[original_id] = assembly_ids.get(original_id, 0) + 1
        if assembly_ids[original_id] > 1:
            assembly["id"] = f"{original_id}-{assembly_ids[original_id]}"
        names: dict[str, int] = {}
        for feature in assembly.get("features") or []:
            # Model-authored lists can contain things that are not feature objects at all — a
            # bare string turned this into an AttributeError and a 500. Normalization skips
            # them; validation still rejects them ("object required"), so nothing is hidden.
            if not isinstance(feature, dict):
                continue
            original_name = str(feature.get("n") or feature.get("t") or "part").strip()
            key = original_name.casefold()
            names[key] = names.get(key, 0) + 1
            if names[key] > 1:
                feature["n"] = f"{original_name} {names[key]}"
    return normalized


def _numbers(value: Any):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _numbers(item)


def _floats(value: Any, count: int) -> list[float] | None:
    """Coerce a list of exactly `count` real numbers, or return None.

    Everything reaching this validator may be model-authored, so a malformed value has to
    become a contract error.  Coercing inline with float() would raise out of validate_cad and
    turn a rejected design into a 500.
    """
    if not isinstance(value, (list, tuple)) or len(value) != count:
        return None
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        out.append(number)
    return out


def _canonical_section(sec: Any) -> tuple[float, float] | None:
    """Stock is a physical extrusion, so 1x2 and 2x1 are the same tube rotated.

    The catalog is stored widest-first; a section is looked up in that orientation rather than
    rejected for arriving transposed.
    """
    values = _floats(sec, 2)
    if values is None:
        return None
    ordered = sorted((round(values[0], 3), round(values[1], 3)), reverse=True)
    return (ordered[0], ordered[1])


def _toothed_pitch_error(feature: dict[str, Any]) -> str | None:
    """Pitch diameter follows from tooth count; a part that disagrees could not be cut."""
    kind = feature.get("t")
    teeth, pd = feature.get("teeth"), feature.get("pd")
    if isinstance(teeth, bool) or not isinstance(teeth, int) or teeth <= 0:
        return None
    if isinstance(pd, bool) or not isinstance(pd, (int, float)) or not math.isfinite(float(pd)):
        return None
    if kind == "pulley":
        pitch = feature.get("pitch_mm")
        if not isinstance(pitch, (int, float)) or isinstance(pitch, bool):
            return None
        expected = teeth * float(pitch) / math.pi / 25.4
    elif kind == "gear":
        dp = feature.get("dp")
        if isinstance(dp, bool) or not isinstance(dp, (int, float)) or float(dp) <= 0:
            return None
        expected = teeth / float(dp)
    elif kind == "sprocket":
        pitch_in = feature.get("pitch_in", _DEFAULT_CHAIN_PITCH_IN)
        if isinstance(pitch_in, bool) or not isinstance(pitch_in, (int, float)) or float(pitch_in) <= 0:
            return None
        if teeth < 3:
            return "teeth: a sprocket needs at least three teeth"
        expected = float(pitch_in) / math.sin(math.pi / teeth)
    else:
        return None
    if abs(float(pd) - expected) > _PITCH_TOLERANCE:
        return f"pd: {pd} disagrees with {teeth} teeth (expected {expected:.3f})"
    return None


_MIRROR_AXES = {"x": 0, "y": 1, "z": 2}


def mirrored_copy(feature: dict[str, Any]) -> dict[str, Any]:
    """The reflected twin of a mirrored feature, in the same assembly frame.

    Reflection across the plane normal to the axis: the position component negates, and of the
    XYZ Euler angles the rotation ABOUT the mirror axis survives while the other two negate
    (conjugating each elemental rotation by the reflection). Exact for the placement; the
    primitives themselves are achiral boxes and cylinders, so nothing is lost to handedness.
    """
    axis = _MIRROR_AXES[feature["mirror"]]
    twin = deepcopy(feature)
    twin.pop("mirror", None)
    twin["n"] = f"{feature.get('n', 'part')} mirror"
    for key in ("at", "to"):
        if isinstance(twin.get(key), list) and len(twin[key]) == 3:
            twin[key][axis] = -twin[key][axis]
    rot = twin.get("rot")
    if isinstance(rot, list) and len(rot) == 3:
        twin["rot"] = [value if index == axis else -value for index, value in enumerate(rot)]
    return twin


def expand_mirrors(features: list[Any]) -> list[dict[str, Any]]:
    """Features with every `mirror` resolved into its two bodies.

    Every consumer that turns features into physical things — FeatureScript, the cut list, the
    viewer — must count a mirrored pair as two parts, or the BOM lies by half. This is the one
    place that expansion is defined.
    """
    out: list[dict[str, Any]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        if feature.get("mirror") in _MIRROR_AXES:
            original = {k: v for k, v in feature.items() if k != "mirror"}
            out.append(original)
            out.append(mirrored_copy(feature))
        else:
            out.append(feature)
    return out


def _coincidence_key(feature: dict[str, Any]) -> str:
    """Everything that decides where a body is and what it is, ignoring only its label.

    Two features matching on this key are the same solid in the same place.  The deterministic
    compiler never emits one; a model looping on a feature emits dozens.
    """
    return json.dumps({k: v for k, v in feature.items() if k != "n"},
                      sort_keys=True, default=str)


def validate_cad(cad: dict[str, Any]) -> list[str]:
    """Validate names, vocabulary, dimensions, stock, pitch math and bounded repetition."""
    errors: list[str] = []
    assemblies = cad.get("assemblies")
    if not isinstance(assemblies, list) or not assemblies:
        return ["$.assemblies: at least one assembly is required"]
    assembly_ids: set[str] = set()
    global_part_ids: set[str] = set()
    for ai, assembly in enumerate(assemblies):
        ap = f"$.assemblies[{ai}]"
        aid = assembly.get("id")
        if not isinstance(aid, str) or not aid:
            errors.append(f"{ap}.id: non-empty id required")
            continue
        if aid in assembly_ids:
            errors.append(f"{ap}.id: duplicate assembly id {aid!r}")
        assembly_ids.add(aid)
        features = assembly.get("features")
        if not isinstance(features, list):
            errors.append(f"{ap}.features: array required")
            continue
        local_names: set[str] = set()
        for fi, feature in enumerate(features):
            fp = f"{ap}.features[{fi}]"
            if not isinstance(feature, dict):
                errors.append(f"{fp}: object required")
                continue
            kind, name = feature.get("t"), feature.get("n")
            if kind not in ALLOWED_FEATURES:
                errors.append(f"{fp}.t: illegal feature {kind!r}")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{fp}.n: non-empty part name required")
            else:
                key = name.strip().casefold()
                if key in local_names:
                    errors.append(f"{fp}.n: duplicate part name {name!r} in {aid}")
                local_names.add(key)
                part_id = f"{aid}/{key}"
                if part_id in global_part_ids:
                    errors.append(f"{fp}.n: duplicate stable part id {part_id!r}")
                global_part_ids.add(part_id)
            at = feature.get("at")
            if not isinstance(at, list) or len(at) != 3:
                errors.append(f"{fp}.at: exactly three coordinates required")
            for number in _numbers({k: v for k, v in feature.items()
                                    if k not in {"rot", "at", "to", "rep"}}):
                if not math.isfinite(number):
                    errors.append(f"{fp}: dimensions must be finite")
                    break
                if number < 0:
                    errors.append(f"{fp}: dimensions cannot be negative")
                    break
                if number > 1000:
                    errors.append(f"{fp}: dimension exceeds compiler safety bound")
                    break
            if kind == "tube":
                sec = feature.get("sec")
                if _canonical_section(sec) not in CATALOG_SECTIONS:
                    errors.append(f"{fp}.sec: non-catalog tube section {sec!r}")
                length = feature.get("len")
                if (isinstance(length, bool) or not isinstance(length, (int, float))
                        or not math.isfinite(float(length)) or float(length) <= 0):
                    errors.append(f"{fp}.len: tube length must be positive")
            if kind == "pulley" and feature.get("pitch_mm") != 5.0:
                errors.append(f"{fp}.pitch_mm: only HTD 5 mm is allowed")
            pitch_error = _toothed_pitch_error(feature)
            if pitch_error:
                errors.append(f"{fp}.{pitch_error}")
            mirror = feature.get("mirror")
            if mirror is not None:
                # `mirror` exists because "dual X" was being modeled as two copies at one
                # position — the reflected twin is derived, so it cannot be wrong. A feature
                # sitting ON the mirror plane would reflect onto itself, which is the exact
                # coincident-body defect the field was added to prevent.
                if mirror not in _MIRROR_AXES:
                    errors.append(f"{fp}.mirror: axis must be one of x, y, z")
                else:
                    position = _floats(at, 3)
                    if position is not None and abs(position[_MIRROR_AXES[mirror]]) < 0.05:
                        errors.append(f"{fp}.mirror: feature sits on the mirror plane and "
                                      f"would reflect onto itself")
            rep = feature.get("rep")
            if rep:
                count = rep.get("n") if isinstance(rep, dict) else None
                step = rep.get("step") if isinstance(rep, dict) else None
                if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 64:
                    errors.append(f"{fp}.rep.n: repetition must be bounded to 1..64")
                elif count > 1:
                    offsets = _floats(step, 3)
                    if offsets is None or not any(value != 0 for value in offsets):
                        errors.append(f"{fp}.rep.step: repeated parts need a non-zero 3D step")
        errors.extend(_coincident_errors(assembly, ap))
    errors.extend(_duplicate_assembly_errors(assemblies))
    return errors


def _duplicate_assembly_errors(assemblies: list[Any]) -> list[str]:
    """Reject whole assemblies that are copies of each other.

    normalize_cad gives a repeated assembly a fresh id, which is right when a robot genuinely
    carries four of the same swerve module — those differ by origin.  Two assemblies with the
    same origin and the same features are one mechanism emitted twice, and renaming the second
    would compile it invisibly on top of the first.
    """
    seen: dict[str, str] = {}
    errors: list[str] = []
    for index, assembly in enumerate(assemblies):
        if not isinstance(assembly, dict):
            continue
        key = json.dumps({k: v for k, v in assembly.items() if k != "id"},
                         sort_keys=True, default=str)
        if key in seen:
            errors.append(f"$.assemblies[{index}]: identical in content and position to "
                          f"{seen[key]!r}")
        else:
            seen[key] = str(assembly.get("id"))
    return errors


def _coincident_errors(assembly: dict[str, Any], ap: str) -> list[str]:
    """Reject bodies that occupy the same place with the same dimensions.

    This is the degenerate-repetition failure mode: a model that loses the thread emits one
    feature over and over at a fixed position until it runs out of tokens.  Renaming those
    duplicates would satisfy every other check while compiling a stack of solids into a single
    point, so they are rejected here rather than normalised away.
    """
    seen: dict[str, int] = {}
    for feature in assembly.get("features") or []:
        if not isinstance(feature, dict):
            continue
        key = _coincidence_key(feature)
        seen[key] = seen.get(key, 0) + 1
    errors: list[str] = []
    for key, count in seen.items():
        if count > 1:
            name = json.loads(key).get("t", "feature")
            errors.append(f"{ap}: {count} coincident {name!r} bodies share one position "
                          f"and geometry")
    return errors


def require_valid_cad(cad: dict[str, Any]) -> dict[str, Any]:
    errors = validate_cad(cad)
    if errors:
        raise CadContractError(errors)
    return cad


# ─────────────────────────────────────────────────────────────────────────────
# Structural integrity: nothing floats.
#
# Every body gets a conservative world-space box from the SAME conventions the viewer, the
# FeatureScript compiler and the STEP worker share: feature `rot` is XYZ Euler degrees with
# R = Rx·Ry·Rz, a tube's length runs along local Z, every turned part's axis is local Y, and
# the assembly origin is a pure translation. Two bodies whose boxes come within CONTACT_TOL
# are in contact; a body in contact with nothing, or an assembly with no contact path back
# to the chassis, is floating — the defect this module exists to name.
# ─────────────────────────────────────────────────────────────────────────────
CONTACT_TOL_IN = 0.08

# Runs (belts, ropes, cables) and reserved volumes are not structure; a body may not claim
# support from them.
_NON_STRUCTURAL = frozenset({"belt", "rope", "cable", "envelope"})

# Turned parts: cylinder along local Y with (radius, half-height) from these keys.
_TURNED = {
    "shaft": ("dia", "len"), "noodle": ("dia", "len"), "standoff": ("dia", "len"),
    "motor": ("dia", "len"), "bearing": ("od", "w"), "wheel": ("dia", "w"),
    "gear": ("pd", "face"), "bevel": ("pd", "face"), "pulley": ("pd", "w"),
    "sprocket": ("pd", "w"), "drum": ("dia", "w"), "brake": ("dia", "w"),
    "tensioner": ("dia", "w"),
}


def _f(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return number if math.isfinite(number) else default


def _local_extents(feature: dict[str, Any]) -> list[float] | None:
    """Half-extents of the primitive in its own frame, before `rot`. None → unplaceable."""
    kind = feature.get("t")
    if kind == "tube":
        sec = feature.get("sec") or [1.0, 1.0]
        return [_f(sec[1], 1.0) / 2 if len(sec) > 1 else 0.5,
                _f(sec[0], 1.0) / 2, _f(feature.get("len"), 1.0) / 2]
    if kind == "gusset":
        size = feature.get("size") or [2.0, 2.0]
        return [_f(size[0], 2.0) / 2, _f(feature.get("th"), 0.09) / 2,
                _f(size[1] if len(size) > 1 else 2.0, 2.0) / 2]
    if kind == "hood":
        r = _f(feature.get("r"), 2.0)
        return [_f(feature.get("w"), 2.0) / 2, r, r]
    if kind == "bolts":
        dia = _f(feature.get("dia"), 0.19)
        length = _f(feature.get("len"), 0.75)
        ext = [dia, dia, length * 0.6]
        rep = feature.get("rep")
        if isinstance(rep, dict):
            count = rep.get("n")
            step = _floats(rep.get("step"), 3) or [0.0, 0.0, 0.0]
            if isinstance(count, int) and count > 1:
                for axis in range(3):
                    ext[axis] += abs(step[axis]) * (count - 1) / 2
        return ext
    if kind in _TURNED:
        dia_key, len_key = _TURNED[kind]
        radius = _f(feature.get(dia_key), 1.0) / 2
        return [radius, _f(feature.get(len_key), radius * 2) / 2, radius]
    size = feature.get("size")
    values = _floats(size, 3)
    if values is not None:
        return [values[0] / 2, values[1] / 2, values[2] / 2]
    if isinstance(size, (list, tuple)) and len(size) == 2:
        return [_f(size[0], 1.0) / 2, 0.05, _f(size[1], 1.0) / 2]
    dia = feature.get("dia")
    if dia is not None:
        radius = _f(dia, 1.0) / 2
        return [radius, _f(feature.get("len"), radius * 2) / 2, radius]
    return [0.4, 0.4, 0.4]


def _abs_rot(rot: Any) -> list[list[float]] | None:
    """|R| for R = Rx·Ry·Rz (the shared Euler convention), or None for no rotation."""
    values = _floats(rot, 3)
    if values is None or not any(values):
        return None
    rx, ry, rz = (math.radians(v) for v in values)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # R = Rx @ Ry @ Rz
    r = [[cy * cz, -cy * sz, sy],
         [cx * sz + sx * sy * cz, cx * cz - sx * sy * sz, -sx * cy],
         [sx * sz - cx * sy * cz, sx * cz + cx * sy * sz, cx * cy]]
    return [[abs(v) for v in row] for row in r]


def _world_box(feature: dict[str, Any], origin: list[float]) -> tuple[list[float], list[float]] | None:
    at = _floats(feature.get("at"), 3)
    if at is None:
        return None
    ext = _local_extents(feature)
    if ext is None:
        return None
    rot = _abs_rot(feature.get("rot"))
    if rot is not None:
        ext = [sum(rot[i][j] * ext[j] for j in range(3)) for i in range(3)]
    centre = [origin[i] + at[i] for i in range(3)]
    return ([centre[i] - ext[i] for i in range(3)],
            [centre[i] + ext[i] for i in range(3)])


def _boxes_touch(a: tuple[list[float], list[float]], b: tuple[list[float], list[float]],
                 tol: float = CONTACT_TOL_IN) -> bool:
    return all(a[0][i] - tol <= b[1][i] and b[0][i] - tol <= a[1][i] for i in range(3))


def _structural_bodies(cad: dict[str, Any]) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []
    for assembly in cad.get("assemblies") or []:
        if not isinstance(assembly, dict):
            continue
        aid = str(assembly.get("id") or "assembly")
        origin = _floats(assembly.get("origin"), 3) or [0.0, 0.0, 0.0]
        for feature in expand_mirrors(assembly.get("features") or []):
            if feature.get("t") in _NON_STRUCTURAL or feature.get("to") is not None:
                continue
            box = _world_box(feature, origin)
            if box is None:
                continue
            bodies.append({"aid": aid, "name": str(feature.get("n") or feature.get("t")),
                           "t": feature.get("t"), "kind": feature.get("kind"), "box": box})
    return bodies


def structural_report(cad: dict[str, Any]) -> dict[str, Any]:
    """The contact audit: which bodies touch nothing, which assemblies never reach the frame.

    Boxes are conservative (a rotated part's box only grows), so a reported FLOAT is real to
    within the tolerance, while a pass is a strong AABB-level claim, not a proof of bolted
    fit — the caveat string carries that nuance.
    """
    bodies = _structural_bodies(cad)
    n = len(bodies)
    touched = [False] * n
    assembly_ids = {body["aid"] for body in bodies}
    # Union-find over assemblies, seeded by body-to-body contact.
    parent: dict[str, str] = {aid: aid for aid in assembly_ids}

    def find(aid: str) -> str:
        while parent[aid] != aid:
            parent[aid] = parent[parent[aid]]
            aid = parent[aid]
        return aid

    contacts = 0
    for i in range(n):
        for j in range(i + 1, n):
            if _boxes_touch(bodies[i]["box"], bodies[j]["box"]):
                touched[i] = touched[j] = True
                contacts += 1
                ra, rb = find(bodies[i]["aid"]), find(bodies[j]["aid"])
                if ra != rb:
                    parent[ra] = rb
    floating = [f"{bodies[i]['aid']}/{bodies[i]['name']}" for i in range(n)
                if not touched[i] and n > 1]
    root = "chassis" if "chassis" in assembly_ids else (bodies[0]["aid"] if bodies else "")
    unreached = sorted(aid for aid in assembly_ids if root and find(aid) != find(root))
    return {"version": "kale-integrity-1.0", "tolerance_in": CONTACT_TOL_IN,
            "bodies": n, "contacts": contacts,
            "floating": sorted(floating), "unreached_assemblies": unreached,
            "ok": not floating and not unreached,
            "caveat": ("AABB contact audit at ±%.2f in against the shared geometry "
                       "conventions. A pass means every body touches structure and every "
                       "assembly chains back to the chassis; it is packaging-level, not a "
                       "fastener-torque claim." % CONTACT_TOL_IN)}


def geometry_envelope(cad: dict[str, Any]) -> dict[str, Any]:
    """The real bounding box of everything that was actually generated, in inches.

    This exists because declaring `maxHeight` in FeatureScript is not validation. The height
    that matters is the one the solids occupy, so it is measured here from the same
    world-space boxes the contact audit uses — including the bumpers and the wheels, which is
    what puts the floor at the bottom of the box rather than the bellypan.
    """
    bodies = _structural_bodies(cad)
    if not bodies:
        return {"bodies": 0, "measured": False}
    lo = [min(b["box"][0][i] for b in bodies) for i in range(3)]
    hi = [max(b["box"][1][i] for b in bodies) for i in range(3)]
    # The floor is the plane the robot STANDS on, i.e. the wheel contact patch — not simply
    # the lowest solid. Measuring from the lowest solid conflates height with a modelling
    # fault: an arm drawn dipping through the carpet made a legal robot read 4 in too tall.
    # Anything below the wheels is reported separately as a ground-clearance problem.
    # Only DRIVE wheels define the ground plane. An intake roller or a gripper's compliant
    # wheels are also `wheel` features and sit well off the carpet; letting one of those set
    # the floor moved the datum and made the robot read metres tall.
    wheels = [b for b in bodies
              if b.get("t") == "wheel"
              and (b["aid"].startswith("swerve") or b["aid"] == "drivetrain")
              and "drive" in b["name"].lower()]
    ground = min((b["box"][0][1] for b in wheels), default=lo[1])
    below = [{"part": f"{b['aid']}/{b['name']}", "below_in": round(ground - b["box"][0][1], 3)}
             for b in bodies if b["box"][0][1] < ground - 0.05]
    tallest = max(bodies, key=lambda b: b["box"][1][1])
    return {
        "bodies": len(bodies), "measured": True,
        "min_in": [round(v, 3) for v in lo], "max_in": [round(v, 3) for v in hi],
        "width_in": round(hi[0] - lo[0], 3),
        "depth_in": round(hi[2] - lo[2], 3),
        "height_in": round(hi[1] - ground, 3),
        "ground_y_in": round(ground, 3),
        "tallest_part": tallest["name"],
        "tallest_part_top_in": round(tallest["box"][1][1] - ground, 3),
        "below_floor": sorted(below, key=lambda x: -x["below_in"])[:8],
    }


def season_rule_report(spec: dict[str, Any]) -> dict[str, Any]:
    """Check the generated geometry against the season's published limits.

    Every check names the rule it came from and reports the measured value, so a failure can
    say *what* broke it and by how much rather than "invalid design". Checks whose inputs are
    estimates (mass) are marked `estimated` and never reported as a pass/fail certification.
    """
    season = spec.get("season") or {}
    limits = season.get("limits") or season.get("rules") or {}
    cad = spec.get("cad") or {}
    env = geometry_envelope(cad)
    frame = spec.get("frame") or {}
    checks: list[dict[str, Any]] = []

    def add(rule: str, name: str, measured, limit, unit: str, ok: bool,
            kind: str = "automatic", detail: str = "") -> None:
        checks.append({"rule": rule or "-", "check": name, "measured": measured,
                       "limit": limit, "unit": unit, "ok": bool(ok), "kind": kind,
                       "detail": detail})

    if env.get("measured"):
        max_h = limits.get("max_height_in")
        if max_h:
            over = env["height_in"] - float(max_h)
            add(limits.get("max_height_rule", "height"), "total height",
                env["height_in"], float(max_h), "in", over <= 1e-6,
                detail=(f"{limits.get('max_height_rule', 'height rule')} failed: "
                        f"{env['tallest_part']} reaches {env['tallest_part_top_in']:.1f} in, "
                        f"exceeding the {float(max_h):.1f} in total-height limit by "
                        f"{over:.1f} in." if over > 1e-6 else
                        f"tallest solid ({env['tallest_part']}) at "
                        f"{env['tallest_part_top_in']:.1f} in"))
        per = limits.get("perimeter_in")
        if per and frame.get("width_in") and frame.get("length_in"):
            measured = 2 * (float(frame["width_in"]) + float(frame["length_in"]))
            add(limits.get("perimeter_rule", "perimeter"), "frame perimeter",
                round(measured, 2), float(per), "in", measured <= float(per) + 1e-6,
                detail=f"{frame['width_in']:g} x {frame['length_in']:g} in frame")
        ext = limits.get("extension_in")
        if ext and frame.get("width_in") and frame.get("length_in"):
            out_x = env["width_in"] / 2 - float(frame["width_in"]) / 2
            out_z = env["depth_in"] / 2 - float(frame["length_in"]) / 2
            worst = round(max(out_x, out_z), 2)
            add(limits.get("extension_rule", "extension"), "horizontal extension",
                worst, float(ext), "in", worst <= float(ext) + 1e-6,
                detail="measured from the frame perimeter to the outermost solid")

    if env.get("measured") and env.get("below_floor"):
        worst = env["below_floor"][0]
        add("-", "ground clearance", worst["below_in"], 0.0, "in", False,
            detail=(f"{worst['part']} is modelled {worst['below_in']:.1f} in below the wheel "
                    "contact plane — it would be through the carpet."))

    mass = (spec.get("mass_estimate") or {}).get("total_lb")
    weight_limit = limits.get("weight_lb")
    if mass and weight_limit:
        add(limits.get("weight_rule", "weight"), "estimated mass", mass, float(weight_limit),
            "lb", float(mass) <= float(weight_limit) + 1e-6, kind="estimated",
            detail="summed from modelled part volumes; not a scale reading")

    hard_failures = [c for c in checks if not c["ok"] and c["kind"] == "automatic"]
    return {
        "version": "kale-rules-1.0",
        "season": season.get("label", ""),
        "envelope": env,
        "checks": checks,
        "passed": [c for c in checks if c["ok"]],
        "failed": hard_failures,
        "estimated": [c for c in checks if c["kind"] == "estimated"],
        "ok": not hard_failures,
        "export_blocked": bool(hard_failures),
        "caveat": ("Automatic checks measured against the generated solids. They are not an "
                   "inspection: weight is estimated from modelled volumes, and anything not "
                   "listed here was not checked."),
    }


# Per-feature-kind modelling fidelity. This is the single source of truth the
# UI, the exports and the marketing copy all quote, so the claim can never
# outrun the geometry again.
#   detailed — true governing dimensions AND functional form (teeth, bores,
#              wall sections, tread) are modelled;
#   concept  — true path/section but simplified form (a belt is a smooth wrapped
#              loop, not individual cogs; chain has no links);
#   envelope — catalog outer dimensions only; internals are not modelled;
#   layout   — a reserved volume, no part geometry at all.
FIDELITY_LEVELS = ("detailed", "concept", "envelope", "layout")
_FIDELITY_BY_KIND = {
    "tube": "detailed", "plate": "detailed", "gusset": "detailed", "shaft": "detailed",
    "gear": "detailed", "sprocket": "detailed", "pulley": "detailed", "bearing": "detailed",
    "wheel": "detailed", "polycarb": "detailed", "bolts": "detailed", "standoff": "detailed",
    "hardstop": "detailed", "hook": "detailed", "drum": "detailed", "hood": "detailed",
    "belt": "concept", "rope": "concept", "cable": "concept", "noodle": "concept",
    "noodle_corner": "concept",
    "fabric": "concept", "bevel": "concept", "pawl": "concept", "slide": "concept",
    "brake": "concept", "tensioner": "concept", "chain_track": "concept",
    "motor": "envelope", "gearbox": "envelope", "component": "envelope",
    "sensor": "envelope", "actuator": "envelope",
    "envelope": "layout",
}


def feature_fidelity(kind: str) -> str:
    return _FIDELITY_BY_KIND.get(kind, "concept")


def fidelity_report(cad: dict[str, Any]) -> dict[str, Any]:
    """Count what is actually modelled at each fidelity level.

    `overall` is the lowest level present among non-layout parts — the honest
    one-word answer to "how real is this CAD?".
    """
    counts = dict.fromkeys(FIDELITY_LEVELS, 0)
    for asm in cad.get("assemblies") or []:
        for f in asm.get("features") or []:
            n = int((f.get("rep") or {}).get("n", 1))
            counts[feature_fidelity(f.get("t", ""))] += n
    present = [level for level in FIDELITY_LEVELS if counts[level]]
    # One level → that level. Several → "mixed": a design whose motors are catalog
    # envelopes must not summarise itself by its most detailed part.
    overall = present[0] if len(present) == 1 else ("mixed" if present else "layout")
    return {
        "levels": counts,
        "overall": overall,
        "statement": ("Structure, gearing and wheels carry true dimensions, teeth and bores; "
                      "belts and chains are smooth wrapped loops; motors, gearboxes and "
                      "electronics are catalog outer envelopes without internals."),
    }


def transmission_report(cad: dict[str, Any]) -> dict[str, Any]:
    """Measure gear meshes and belt wraps in the generated geometry."""
    from app.services.transmission_geom import transmission_issues  # noqa: PLC0415 — import cycle
    issues = transmission_issues(cad.get("assemblies") or [])
    return {
        "ok": not issues,
        "issues": issues,
        "checked": ("gear pairs must sit at the sum of their pitch radii; "
                    "belt and chain endpoints must land on pulley or sprocket centres"),
    }


def structural_errors(cad: dict[str, Any]) -> list[str]:
    """The report as contract-style error strings, for gates and tests."""
    report = structural_report(cad)
    errors = [f"$.integrity: {name} touches nothing within {CONTACT_TOL_IN} in"
              for name in report["floating"]]
    errors.extend(f"$.integrity: assembly {aid!r} has no contact path to the chassis"
                  for aid in report["unreached_assemblies"])
    return errors


def editable_manifest(cad: dict[str, Any]) -> dict[str, Any]:
    """Stable, deterministic mapping used by Onshape publishing and later edits."""
    parts: list[dict[str, Any]] = []
    mates: list[dict[str, Any]] = []
    for assembly in cad["assemblies"]:
        aid = assembly["id"]
        for index, feature in enumerate(assembly["features"]):
            dimensions = {key: value for key, value in feature.items()
                          if key not in {"t", "n", "at", "rot", "to", "rep", "mat", "kind",
                                         "key", "mirror"}}
            parts.append({"id": f"{aid}:part:{index + 1}", "assembly_id": aid,
                          "name": feature["n"], "feature_type": feature["t"],
                          "dimensions": dimensions})
        for index, mate in enumerate(assembly.get("mates") or []):
            mates.append({"id": f"{aid}:mate:{index + 1}", "assembly_id": aid,
                          "definition": mate})
    return {"version": "kale-editable-1.0", "flattened": False,
            "parts": parts, "mates": mates}


def compact_design_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The small parametric contract retained beside deterministically compiled geometry."""
    mechanisms = []
    previous = "drivetrain"
    architecture_fields = {
        "intake": "type", "hopper": "type", "shooter": "type",
        "elevator": "architecture", "arm": "type", "climber": "type",
    }
    for kind in ("intake", "hopper", "shooter", "elevator", "arm", "climber"):
        block = spec.get("manipulator" if kind == "arm" else kind) or {}
        if not block.get("included"):
            continue
        item = {"id": kind, "kind": kind,
                "architecture": block.get(architecture_fields[kind], "deterministic-default"),
                "depends_on": [previous]}
        if kind == "elevator":
            item["stages"] = block.get("stages", 1)
        mechanisms.append(item)
        previous = kind
    frame = spec.get("frame") or {}
    drivetrain = spec.get("drivetrain") or {}
    return {
        "schema_version": "kale-parametric-1.0",
        "units": "in",
        "frame": {"width_in": frame.get("width_in"), "length_in": frame.get("length_in"),
                  "stock_section": frame.get("tube", "2x1x0.100 in")},
        "drivetrain": {"id": "drivetrain", "type": drivetrain.get("type"),
                       "wheel_diameter_in": drivetrain.get("wheel_diameter_in"),
                       "drive_ratio": drivetrain.get("drive_ratio")},
        "mechanisms": mechanisms,
        "compiler": "kale-cad-deterministic-1.0",
    }


def validate_parametric_design(design: dict[str, Any]) -> list[str]:
    """Reject duplicate nodes, dangling dependencies and dependency cycles."""
    errors: list[str] = []
    frame = design.get("frame") or {}
    for field in ("width_in", "length_in"):
        value = frame.get(field)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 18 <= value <= 40):
            errors.append(f"$.frame.{field}: must be a finite 18..40 inch dimension")
    nodes = [design.get("drivetrain") or {}, *(design.get("mechanisms") or [])]
    ids: set[str] = set()
    graph: dict[str, list[str]] = {}
    for index, node in enumerate(nodes):
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            errors.append(f"$.nodes[{index}].id: non-empty id required")
            continue
        if node_id in ids:
            errors.append(f"$.nodes[{index}].id: duplicate id {node_id!r}")
        ids.add(node_id)
        graph[node_id] = list(node.get("depends_on") or [])
    for node_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in ids:
                errors.append(f"$.nodes.{node_id}: unknown dependency {dependency!r}")
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node_id: str) -> None:
        if node_id in visiting:
            errors.append(f"$.nodes.{node_id}: dependency loop")
            return
        if node_id in visited: return
        visiting.add(node_id)
        for dependency in graph.get(node_id, []): visit(dependency)
        visiting.remove(node_id); visited.add(node_id)
    for node_id in graph: visit(node_id)
    return errors


def require_valid_parametric_design(design: dict[str, Any]) -> dict[str, Any]:
    errors = validate_parametric_design(design)
    if errors:
        raise CadContractError(errors)
    return design
