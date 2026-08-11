"""Natural language to a resolved mechanical specification.

This is the stage between "make a bearing block for a 1/2 in hex shaft" and a generator that
knows what to cut. Two jobs:

  * work out WHICH part it is — the generator key;
  * bind the numbers in the sentence to the things they dimension.

The binding is the hard half and the reason this is not a regex table. "1/2 inch hex shaft
with a 1.125 in bearing" contains two diameters, and which is which is decided by the words
next to them, not by their order. Getting that backwards produces a part that is confidently,
precisely wrong — the worst kind of output this tool can produce.

Anything not stated is filled by the generator as an ASSUMED value and reported. Anything
that cannot be assumed safely is recorded as UNRESOLVED, and if it is critical the caller
asks instead of guessing.

Stdlib-only.
"""
from __future__ import annotations

import re
from typing import Any

from app.services import hardware_catalog as hw
from app.services.design_intent import EngineeringSpec, dimensions_in, parse_number

SPEC_VERSION = "kale-mechspec-1.0"


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


# ── which part ───────────────────────────────────────────────────────────────
# Ordered: the first match wins, so compound names are listed before the nouns they contain.
# "gearbox plate" must be tested before "plate" and before "gearbox".
_PART_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_p(r"\bbearing\s*(?:block|housing|holder|carrier|mount|pocket)\b"), "bearing_block"),
    (_p(r"\bgearbox\s*plate\b"), "gearbox_plate"),
    (_p(r"\b(?:motor|neo|kraken|falcon|cim)\s*(?:mount(?:ing)?\s*)?plate\b"), "motor_plate"),
    (_p(r"\bmount(?:ing)?\s*plate\b"), "motor_plate"),
    (_p(r"\bgusset\b"), "gusset"),
    (_p(r"\b(?:l[- ]?bracket|bracket|angle)\b"), "bracket"),
    (_p(r"\bspacer\b|\bshim\b"), "spacer"),
    (_p(r"\bstandoff\b"), "spacer"),
    # A roller or a pulley is almost always described by the shaft it runs on, so both have
    # to be tested before the shaft patterns, or "a roller on a 1/2 in hex shaft" is a shaft.
    (_p(r"\broller\b"), "roller"),
    (_p(r"\bpulley\b|\bsprocket\b"), "pulley"),
    (_p(r"\bhex\s*shaft\b"), "hex_shaft"),
    (_p(r"\bshaft\b|\baxle\b"), "shaft"),
    (_p(r"\b(?:plate|panel)\b"), "plate"),
    (_p(r"\bbearing\b"), "bearing_block"),
]


def part_type(prompt: str) -> str:
    """The generator key for this request, or "" when nothing here makes it."""
    text = " " + (prompt or "").lower() + " "
    for pattern, key in _PART_PATTERNS:
        if pattern.search(text):
            return key
    return ""


# ── dimension binding ────────────────────────────────────────────────────────
# Each role is a set of words that, appearing near a dimension, mean the dimension is that
# role. Searched in a window around the number so "1/2 inch hex shaft, 220 mm long" binds
# 0.5 to the shaft and 220 mm to the length.
_ROLE_WORDS: dict[str, tuple[str, ...]] = {
    "bearing_od_in": ("bearing", "od", "outside diameter", "outer diameter", "o.d"),
    "bearing_width_in": ("bearing width", "bearing thickness"),
    "shaft_size_in": ("shaft", "hex", "bore", "axle", "id", "inside diameter"),
    "length_in": ("long", "length", "tall"),
    "width_in": ("wide", "width", "across"),
    "depth_in": ("deep", "depth"),
    "thickness_in": ("thick", "thickness", "gauge"),
    "od_in": ("od", "outside diameter", "outer diameter"),
    "roller_diameter_in": ("roller", "wheel"),
    "hole_pitch_in": ("pitch", "spacing", "on centre", "on center"),
    "leg_a_in": ("leg", "legs", "first leg", "flange"),
    "leg_b_in": ("second leg", "other leg", "upstand"),
}

_MATERIAL_AFTER = _p(r"(?:thick\s+)?(?:6061|5052|7075|2024|aluminium|aluminum|alu\b|steel|"
                     r"stainless|polycarb\w*|lexan|delrin|acetal|abs\b|nylon|sheet|stock|"
                     r"plate\b)")

_WINDOW = 28        # characters either side of a dimension to look for its role

# Belt and chain pitches. Named profiles, so they can be recognised rather than guessed.
_BELT_PITCH = _p(r"\b(?:htd\s*)?(\d+(?:\.\d+)?)\s*mm\s*(?:htd|gt2|gt3|belt|pitch|pulley)\b"
                 r"|\bhtd\s*(\d+(?:\.\d+)?)\s*mm\b"
                 r"|\b#?(25|35)\s*(?:chain|pitch)\b")

# A generic role word means a part-specific property. "3.5 in wide" is a width, but a bearing
# block's width is `block_width_in`, and without this the number bound to a key the generator
# never reads — so the user's stated dimension was silently ignored and the assumed one used.
_ROLE_ALIASES: dict[str, dict[str, str]] = {
    "bearing_block": {"width_in": "block_width_in", "depth_in": "block_thickness_in",
                      "thickness_in": "block_thickness_in", "height_in": "block_height_in"},
    "bearing_housing": {"width_in": "block_width_in", "depth_in": "block_thickness_in",
                        "thickness_in": "block_thickness_in", "height_in": "block_height_in"},
    "bracket": {"length_in": "leg_a_in"},
    "gusset": {"width_in": "reach_in", "length_in": "reach_in"},
    "roller": {"length_in": "width_in", "od_in": "roller_diameter_in"},
}


def _alias(part_type: str, role: str) -> str:
    return _ROLE_ALIASES.get(part_type, {}).get(role, role)


def _role_for(text: str, dim: dict[str, Any], used: set[str]) -> str:
    """Which property this dimension dimensions, by the words around it."""
    low = text.lower()
    before = low[max(0, dim["start"] - _WINDOW):dim["start"]]
    after = low[dim["end"]:dim["end"] + _WINDOW]
    scores: dict[str, int] = {}
    for role, words in _ROLE_WORDS.items():
        if role in used:
            continue
        for word in words:
            # A word directly after the number binds harder than one before it: English puts
            # the noun after the dimension ("1.125 in bearing"), and the adjective before
            # ("bearing OD of 1.125 in") is the weaker, less common form.
            #
            # The score is the BEST word for a role, never the sum of its words. Summing let
            # a role with many near-synonyms win on quantity: in "14 in long on a 1/2 in hex
            # shaft", the shaft scored twice — once for "hex", once for "shaft" — and beat
            # the "long" sitting directly against the number.
            if word in after[:14]:
                hit = 3
            elif word in after:
                hit = 2
            elif word in before[-16:]:
                hit = 2
            elif word in before:
                hit = 1
            else:
                continue
            scores[role] = max(scores.get(role, 0), hit)
    # Sheet and plate stock is called out by its material: "1/8 in aluminium" is a thickness,
    # and nothing thin enough to be stock is a length. Only reached when no word claimed the
    # number outright, so "a 2 in aluminium spacer" is still a 2 in spacer.
    if not scores and dim["inches"] <= 0.5 and _MATERIAL_AFTER.match(after.strip()):
        return "thickness_in"
    if not scores:
        return ""
    return max(scores, key=lambda r: scores[r])


# Positional fallbacks per part type, used only for dimensions no word claimed. Ordered by
# how a person would naturally list them.
_POSITIONAL: dict[str, tuple[str, ...]] = {
    "bearing_block": ("shaft_size_in", "bearing_od_in", "block_thickness_in"),
    "shaft": ("shaft_size_in", "length_in"),
    "hex_shaft": ("shaft_size_in", "length_in"),
    "spacer": ("length_in", "shaft_size_in", "od_in"),
    "plate": ("width_in", "depth_in", "thickness_in"),
    "gusset": ("reach_in", "thickness_in"),
    "motor_plate": ("width_in", "depth_in", "thickness_in"),
    "gearbox_plate": ("width_in", "depth_in", "thickness_in"),
    "roller": ("roller_diameter_in", "width_in"),
    "pulley": ("shaft_size_in", "face_in"),
    "bracket": ("leg_a_in", "leg_b_in", "thickness_in"),
}


_MATERIALS = (
    ("6061-T6 aluminium", _p(r"\b6061|aluminium|aluminum|alu\b")),
    ("7075-T6 aluminium", _p(r"\b7075\b")),
    ("5052 aluminium", _p(r"\b5052\b")),
    ("Delrin", _p(r"\bdelrin|acetal|pom\b")),
    ("Nylon", _p(r"\bnylon\b")),
    ("UHMW", _p(r"\buhmw\b")),
    ("Polycarbonate", _p(r"\bpolycarb\w*\b")),
    ("4140 steel", _p(r"\b4140\b")),
    ("1018 steel", _p(r"\bsteel\b")),
    ("303 stainless", _p(r"\bstainless\b")),
    ("PLA", _p(r"\bpla\b")), ("PETG", _p(r"\bpetg\b")), ("ABS", _p(r"\babs\b")),
)

_PROCESS = (
    ("3D printed", _p(r"\b3d\s*print\w*|printed|fdm|sls\b")),
    ("waterjet", _p(r"\bwaterjet|water\s*jet\b")),
    ("laser cut", _p(r"\blaser\b")),
    ("lathe", _p(r"\blathe|turned\b")),
    ("CNC milled", _p(r"\bcnc|mill\w*|machined\b")),
)


def resolve(prompt: str, design_type: str = "mechanical_part") -> EngineeringSpec:
    """Turn one request into a specification a generator can compile."""
    spec = EngineeringSpec(design_type, prompt)
    text = prompt or ""
    low = " " + text.lower() + " "
    spec.part_type = part_type(text)

    # Named hardware first: a motor or a bearing in the sentence brings its own verified
    # dimensions, which beat anything inferred from the prose.
    motor = hw.motor_by_name(text)
    if motor:
        spec.hardware.append(motor)
        spec.verified("motor", motor["name"], "", "from the parts catalog")
    count = re.search(r"\b(two|three|four|2|3|4)\s+(?:\w+\s+){0,2}motors?\b", low)
    if count:
        spec.require("motor_count", {"two": 2, "three": 3, "four": 4}.get(
            count.group(1), int(count.group(1)) if count.group(1).isdigit() else 1))
    elif motor:
        spec.infer("motor_count", 1, "", "one motor was named")

    # Bind stated dimensions to roles.
    #
    # A belt or chain pitch is a dimension in the sentence that is not a dimension OF the
    # part: "a 24 tooth HTD 5mm pulley for a 1/2 in hex shaft" has a 5 mm in it that is the
    # tooth profile, and letting it fall through to the positional pass bored the pulley
    # 5 mm and gave the 1/2 in hex to the face width.
    consumed = {(m.start(), m.end()) for m in _BELT_PITCH.finditer(text)}
    dims = [d for d in dimensions_in(text)
            if not any(s <= d["start"] < e for s, e in consumed)]
    used: set[str] = set()
    unbound: list[dict[str, Any]] = []
    for dim in dims:
        role = _role_for(text, dim, used)
        if role:
            used.add(role)
            spec.require(_alias(spec.part_type, role), dim["inches"], "in",
                         f"stated as {dim['raw']}")
        else:
            unbound.append(dim)
    for dim, role in zip(unbound, [r for r in _POSITIONAL.get(spec.part_type, ()) if r not in used]):
        spec.infer(role, dim["inches"], "in",
                   f"{dim['raw']} was not attached to anything by name; taken as the "
                   f"{role.replace('_in', '').replace('_', ' ')}")
        used.add(role)

    # Shaft form. "Hex" anywhere means hex; FRC shafting is hex by default and saying so is
    # more useful than silently choosing.
    if _p(r"\bhex\b").search(low):
        spec.require("shaft_form", "hex")
    elif _p(r"\bround\s*shaft|\bkeyed\b").search(low):
        spec.require("shaft_form", "round")
    elif spec.get("shaft_size_in") is not None:
        spec.assume("shaft_form", "hex", "", "FRC shafting is hex unless stated otherwise")

    # A named bearing OD or shaft size can often be resolved to a catalog part, which brings
    # a verified width along with it — the dimension nobody ever states.
    # Only for parts that actually contain one. A spacer on a hex shaft does not need a
    # bearing, and quietly adding one to its BOM is the same class of mistake as adding a
    # drivetrain to a bracket.
    _NEEDS_BEARING = ("bearing_block", "bearing_housing", "gearbox_plate", "roller", "pulley")
    od = spec.get("bearing_od_in")
    shaft_size = spec.get("shaft_size_in")
    catalog = None
    if spec.part_type in _NEEDS_BEARING:
        if od:
            catalog = hw.bearing_by_od(float(od))
        if catalog is None and shaft_size:
            catalog = hw.bearing_for_shaft(float(shaft_size), str(spec.get("shaft_form") or "hex"))
    if catalog:
        spec.hardware.append(catalog)
        if spec.get("bearing_width_in") is None:
            src = "from the catalog" if catalog["verified"] else "nominal series dimension"
            spec.inferred["bearing_width_in"] = {
                "value": catalog["width_in"], "source": catalog["source"], "unit": "in",
                "note": f"{catalog['name']} — {src}"}
        if od is None:
            spec.infer("bearing_od_in", catalog["od_in"], "in", f"{catalog['name']}")
    elif spec.part_type in ("bearing_block",) and od:
        spec.unknown("bearing_width_in",
                     "no catalog bearing matches that OD, so its width is unknown — measure "
                     "the bearing you are using before cutting the pocket")

    # Fit.
    if _p(r"\bpress\s*fit\b").search(low):
        spec.require("fit", "press")
    elif _p(r"\bslip\s*fit|\bclearance\s*fit\b").search(low):
        spec.require("fit", "slip")
    elif spec.part_type == "bearing_block":
        spec.assume("fit", "press", "", "bearings are pressed unless a retainer is specified")

    # Material and process.
    for name, pattern in _MATERIALS:
        if pattern.search(low):
            spec.require("material", name)
            break
    else:
        spec.assume("material", "6061-T6 aluminium", "",
                    "the default for a machined FRC part")
    for name, pattern in _PROCESS:
        if pattern.search(low):
            spec.require("process", name)
            break
    else:
        spec.assume("process", "CNC milled", "", "inferred from the material and the features")

    # Fastener size.
    thread = _p(r"\b(10-32|1/4-20|8-32|6-32|m3|m4|m5|m6)\b").search(low)
    if thread:
        spec.require("fastener", thread.group(1).lower())
    else:
        spec.assume("fastener", "10-32", "", "the FRC default")

    # "A x B" is a tube section on a gusset and a plate outline on a plate. Reading a plate's
    # outline as tube stock threw the user's own dimensions away: "a 4 x 6 in plate" became a
    # 6x1 tube and a plate of entirely assumed size.
    pair = _p(r"\b(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\b").search(low)
    if pair:
        a, b = float(pair.group(1)), float(pair.group(2))
        is_tube = (spec.part_type == "gusset"
                   or _p(r"\btub\w*|\bextrusion\b|\brail\b").search(low))
        if is_tube:
            spec.require("tube_section_in", [max(a, b), min(a, b)], "in")
            entry = hw.tube_by_section(min(a, b), max(a, b))
            if entry:
                spec.hardware.append(entry)
                spec.verified("tube", entry["name"], "", "from the structure catalog")
        elif spec.part_type in ("plate", "motor_plate", "gearbox_plate"):
            spec.require("width_in", a, "in", f"stated as {pair.group(0)}")
            spec.require("depth_in", b, "in", f"stated as {pair.group(0)}")

    # Retaining-ring grooves.
    grooves = _p(r"\b(?:retaining|snap)\s*[- ]?ring|circlip|groove").search(low)
    if grooves:
        n = re.search(r"\b(one|two|three|1|2|3)\s+(?:retaining\s*)?(?:ring\s*)?grooves?\b", low)
        spec.require("groove_count",
                     {"one": 1, "two": 2, "three": 3}.get(n.group(1), int(n.group(1)))
                     if n and n.group(1).isdigit() or (n and n.group(1) in ("one", "two", "three"))
                     else 2)

    # Teeth, for a pulley.
    teeth = _p(r"\b(\d{1,3})\s*(?:t\b|teeth|tooth)").search(low)
    if teeth:
        spec.require("teeth", int(teeth.group(1)))

    # Lightening.
    if _p(r"\blight(?:en|weight)\w*|\bpocket\w*\b").search(low):
        spec.infer("lightening", 6, "", "asked for lightweight")

    # Critical gaps. A part type this library does not make is the one thing that stops the
    # build, because the alternative is claiming to have made something else.
    if not spec.part_type:
        spec.unknown("part_type",
                     "nothing in the request names a part this library can generate",
                     critical=True)
    return spec
