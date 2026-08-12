"""Component placement: every part gets a real position on a real board outline.

This is the second rung of the completion ladder in `pcb_design`, and the rule that governs
that ladder governs this module too — `PLACEMENT_COMPLETE` may only be claimed if placement
actually happened and actually passes. So the output is checked, not asserted:

  * every component sits inside the board, clear of the edge keepout;
  * no two courtyards overlap;
  * nothing sits on a mounting hole;
  * every decoupling capacitor is within 5 mm of the part it decouples.

If any of those fail, the checks say so and the stage stays incomplete. A placement that is
wrong is worse than no placement, because a wrong one looks finished.

The strategy is the one a person uses on a board this small, in this order:

    connectors on the edges          they have to be reachable from outside
    input side first                 power enters at one end and leaves at the other
    regulators between them          on the shortest path from input to output
    decoupling against its regulator loop area is the whole point of a decoupling cap
    passives and indicators filled   into whatever row is still free

Not an autorouter and not a real placer with a cost function. It is a deterministic
constructive placement for boards of ten to thirty parts, which is the size of board this
generator makes.

Everything is millimetres, origin at the board's bottom-left corner, +X right, +Y up.

Stdlib-only.
"""
from __future__ import annotations

import math
from typing import Any

from app.services.design_intent import ASSUMED, CALCULATED, VERIFIED

LAYOUT_VERSION = "kale-pcbplace-1.0"

EDGE_KEEPOUT_MM = 2.0       # nothing within this of the board edge
HOLE_KEEPOUT_MM = 3.2       # radius around an M3 mounting hole
COURTYARD_GAP_MM = 0.5      # minimum clear space between two parts
DECOUPLE_MAX_MM = 5.0       # a decoupling cap further away than this is not decoupling

# Package body and courtyard sizes, in mm. These are the IPC nominal envelopes for the
# generic packages this generator uses; they are typical, not vendor drawings, and every one
# is reported as ASSUMED. A courtyard is the body plus its pads plus clearance, which is why
# it is bigger than the body — placing on body size alone puts parts on top of each other.
PACKAGES: dict[str, dict[str, Any]] = {
    "0603":            {"body": (1.6, 0.8), "court": (2.6, 1.6), "height": 0.5},
    "0805":            {"body": (2.0, 1.25), "court": (3.2, 2.2), "height": 0.7},
    "1206":            {"body": (3.2, 1.6), "court": (4.4, 2.6), "height": 0.9},
    "SOT-23":          {"body": (2.9, 1.3), "court": (4.2, 3.2), "height": 1.1},
    "SOT-223":         {"body": (6.5, 3.5), "court": (8.0, 7.2), "height": 1.8},
    "SOIC-8":          {"body": (4.9, 3.9), "court": (7.0, 6.2), "height": 1.8},
    "SOIC-14":         {"body": (8.7, 3.9), "court": (10.8, 6.2), "height": 1.8},
    "DFN-8":           {"body": (3.0, 3.0), "court": (4.2, 4.2), "height": 0.9},
    "POWER-INDUCTOR":  {"body": (7.3, 6.6), "court": (8.5, 7.8), "height": 3.2},
    "SCREW-2":         {"body": (10.2, 8.5), "court": (11.4, 10.5), "height": 10.0},
    "JST-PH-4":        {"body": (9.0, 4.5), "court": (10.4, 6.5), "height": 6.0},
    "JST-PH-3":        {"body": (7.0, 4.5), "court": (8.4, 6.5), "height": 6.0},
    "JST-PH-6":        {"body": (13.0, 4.5), "court": (14.4, 6.5), "height": 6.0},
    "WEIDMULLER-2":    {"body": (10.0, 9.0), "court": (11.2, 11.0), "height": 11.0},
    "FUSE-HOLDER":     {"body": (11.0, 7.0), "court": (12.4, 8.4), "height": 8.0},
    "LED-0603":        {"body": (1.6, 0.8), "court": (2.6, 1.6), "height": 0.7},
    "TO-220":          {"body": (10.2, 4.6), "court": (11.5, 14.0), "height": 15.0},
}
_DEFAULT_PACKAGE = "SOIC-8"

PACKAGE_NOTE = ("package envelopes are IPC nominal sizes for the generic package, not a "
                "vendor drawing — confirm against the part you actually buy")


def package_for(part: dict[str, Any]) -> str:
    """Which physical envelope this component occupies.

    Read from the part's own `package` when it has one (passives do), otherwise inferred
    from what the part IS, because a screw terminal and a transceiver are not the same size
    however similar their BOM lines look.
    """
    stated = str(part.get("package") or "").upper()
    if stated in PACKAGES:
        return stated
    category = part.get("category")
    value = str(part.get("value") or "").lower()
    if category == "connector":
        if "can" in value:
            return "WEIDMULLER-2"
        if "input" in value or " out" in value:
            return "SCREW-2"
        if "spi" in value:
            return "JST-PH-6"
        if "uart" in value:
            return "JST-PH-3"
        return "JST-PH-4"
    if category == "protection":
        return "FUSE-HOLDER" if "A" in str(part.get("value") or "") and part.get(
            "reference", "").startswith("F") else "SOT-23"
    if category == "regulator":
        return "SOIC-8"
    if category == "transceiver":
        return "SOIC-8"
    if category == "inductor":
        return "POWER-INDUCTOR"
    if category == "indicator":
        return "LED-0603"
    if category in ("resistor", "capacitor"):
        return "0603"
    return _DEFAULT_PACKAGE


def _courtyard(part: dict[str, Any], rotated: bool) -> tuple[float, float]:
    w, h = PACKAGES.get(package_for(part), PACKAGES[_DEFAULT_PACKAGE])["court"]
    return (h, w) if rotated else (w, h)


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ax, ay, aw, ah = a["x"], a["y"], a["court_mm"][0], a["court_mm"][1]
    bx, by, bw, bh = b["x"], b["y"], b["court_mm"][0], b["court_mm"][1]
    gap = COURTYARD_GAP_MM
    return (abs(ax - bx) * 2 < aw + bw + gap) and (abs(ay - by) * 2 < ah + bh + gap)


def mounting_holes(size: tuple[float, float], inset: float = 3.5) -> list[dict[str, float]]:
    w, h = size
    return [{"x": x, "y": y, "d": 3.2}
            for x in (inset, w - inset) for y in (inset, h - inset)]


def _decouples(part: dict[str, Any]) -> bool:
    return part["category"] == "capacitor" and part.get("decouples")


def place(spec: dict[str, Any], parts: list[dict[str, Any]],
          size: list[float]) -> dict[str, Any]:
    """Put every part somewhere, then say whether that somewhere is legal.

    Grows the board rather than overlapping parts: a placement that does not fit is a board
    that is too small, and silently cramming it would produce a layout nobody can build.
    """
    width, height = float(size[0]), float(size[1])
    placed: list[dict[str, Any]] = []
    grown = False

    # Start from the real courtyard area rather than a guess: the parts know how big they
    # are, and sizing from anything else means the first attempt is wrong on every board.
    needed = sum(_courtyard(p, False)[0] * _courtyard(p, False)[1] for p in parts)
    side = (needed / 0.34) ** 0.5           # 34% courtyard density leaves room to route
    width = max(width, round(side * 1.18, 1))
    height = max(height, round(side * 0.85, 1))

    for _ in range(6):
        holes = mounting_holes((width, height))
        placed, ok = _try_place(parts, width, height, holes)
        if ok and all(c["ok"] for c in _placement_checks(placed, parts, width, height, holes)):
            break
        width, height, grown = round(width * 1.12, 1), round(height * 1.18, 1), True

    holes = mounting_holes((width, height))
    checks = _placement_checks(placed, parts, width, height, holes)
    complete = bool(placed) and all(c["ok"] for c in checks)

    return {
        "version": LAYOUT_VERSION,
        "board_mm": [round(width, 1), round(height, 1)],
        "board_grew": grown,
        "origin": "bottom-left of the board outline, +X right, +Y up",
        "edge_keepout_mm": EDGE_KEEPOUT_MM,
        "mounting_holes": holes,
        "placements": placed,
        "checks": checks,
        "complete": complete,
        "package_note": PACKAGE_NOTE,
        "source": CALCULATED if complete else ASSUMED,
    }


def _try_place(parts: list[dict[str, Any]], width: float, height: float,
               holes: list[dict[str, float]]) -> tuple[list[dict[str, Any]], bool]:
    """One placement attempt on a board of this size.

    Returns the placements and whether everything fit. Deliberately simple and deterministic:
    the same netlist always lands in the same layout, so a revision that changes one part
    does not reshuffle the whole board.
    """
    placed: list[dict[str, Any]] = []
    by_ref = {p["reference"]: p for p in parts}

    def fits(entry: dict[str, Any]) -> bool:
        cw, ch = entry["court_mm"]
        if entry["x"] - cw / 2 < EDGE_KEEPOUT_MM or entry["x"] + cw / 2 > width - EDGE_KEEPOUT_MM:
            return False
        if entry["y"] - ch / 2 < EDGE_KEEPOUT_MM or entry["y"] + ch / 2 > height - EDGE_KEEPOUT_MM:
            return False
        # The mounting holes are part of the board, and a screw needs its head as well as its
        # shank. Leaving this out of `fits` is what put edge connectors on the corner holes.
        for hole in holes:
            if (abs(entry["x"] - hole["x"]) < HOLE_KEEPOUT_MM + cw / 2
                    and abs(entry["y"] - hole["y"]) < HOLE_KEEPOUT_MM + ch / 2):
                return False
        return not any(_overlap(entry, other) for other in placed)

    def put(part: dict[str, Any], x: float, y: float, rot: int, why: str) -> bool:
        entry = {"reference": part["reference"], "package": package_for(part),
                 "x": round(x, 2), "y": round(y, 2), "rotation": rot,
                 "court_mm": [round(v, 2) for v in _courtyard(part, rot % 180 == 90)],
                 "side": "top", "why": why}
        if not fits(entry):
            return False
        placed.append(entry)
        return True

    def anywhere(part: dict[str, Any], why: str, band: tuple[float, float] | None = None) -> bool:
        """Sweep the free area on a fixed grid. Coarse, but it terminates and it is stable."""
        lo, hi = band or (EDGE_KEEPOUT_MM, height - EDGE_KEEPOUT_MM)
        y = lo + 1.0
        while y < hi:
            x = EDGE_KEEPOUT_MM + 1.0
            while x < width - EDGE_KEEPOUT_MM:
                if put(part, x, y, 0, why):
                    return True
                x += 1.0
            y += 1.0
        return False

    conns = [p for p in parts if p["category"] == "connector"]
    inputs = [p for p in conns if "input" in str(p["value"]).lower()]
    outputs = [p for p in conns if " out" in str(p["value"]).lower()
               and "can" not in str(p["value"]).lower()]
    buses = [p for p in conns if p not in inputs and p not in outputs]

    # Power enters on the left edge and leaves on the right, so the current has one direction
    # to travel and the regulators sit on that path rather than across it.
    _edge_column(inputs, EDGE_KEEPOUT_MM, height, put, left=True)
    _edge_column(outputs, width - EDGE_KEEPOUT_MM, height, put, left=False)
    # Signal connectors go along the bottom edge, away from the power path.
    _edge_row(buses, EDGE_KEEPOUT_MM, width, put)
    # Any connector the edges could not take still has to go somewhere it can be reached, so
    # it gets the top edge before it gets dropped into the middle of the board.
    for part in conns:
        if any(e["reference"] == part["reference"] for e in placed):
            continue
        ch = _courtyard(part, False)[1]
        if not anywhere(part, "no edge slot was free; kept as close to one as possible",
                        band=(height - EDGE_KEEPOUT_MM - ch - 1.0, height - EDGE_KEEPOUT_MM)):
            anywhere(part, "connector; no edge slot was free anywhere")

    # Protection sits immediately after the input, in the order it protects: fuse, then
    # reverse-polarity, then the TVS clamping what gets through.
    protection = [p for p in parts if p["category"] == "protection"]
    x = width * 0.24
    for part in protection:
        if not put(part, x, height * 0.78, 0, "in the input path, before anything it protects"):
            anywhere(part, "in the input path; the preferred spot was taken")
        x += _courtyard(part, False)[0] / 2 + 4.0

    # Regulators across the middle, evenly spaced, with their decoupling beside them.
    regulators = [p for p in parts if p["category"] == "regulator"]
    for i, reg in enumerate(regulators):
        rx = width * (0.38 + 0.22 * i)
        if not put(reg, min(rx, width * 0.72), height * 0.5, 0,
                   "on the input-to-output path"):
            anywhere(reg, "regulator; the preferred spot was taken")
        seat = next((e for e in placed if e["reference"] == reg["reference"]), None)
        if seat:
            _decoupling_for(reg, parts, by_ref, seat, put, anywhere)

    # Transceivers below the regulator row, near their bus connectors.
    for part in [p for p in parts if p["category"] == "transceiver"]:
        if not put(part, width * 0.5, height * 0.26, 0, "between its bus connectors"):
            anywhere(part, "bus transceiver; the preferred spot was taken")

    # Inductors immediately beside their switcher: the switch node is the loudest net on the
    # board and it should be as short as the package allows.
    for part in [p for p in parts if p["category"] == "inductor"]:
        near = next((e for e in placed if by_ref.get(e["reference"], {}).get("category")
                     == "regulator"), None)
        if near and put(part, near["x"] + 7.5, near["y"], 0, "hard against the switch node"):
            continue
        anywhere(part, "power inductor; could not sit against its regulator")

    # Everything left over: indicators near an edge where they can be seen, the rest anywhere.
    for part in parts:
        if any(e["reference"] == part["reference"] for e in placed):
            continue
        if part["category"] == "indicator":
            if put(part, width * 0.5, height - EDGE_KEEPOUT_MM - 2.0, 0,
                   "near the edge so it is visible"):
                continue
        anywhere(part, "no constraint on this part; filled into free area")

    return placed, len(placed) == len(parts)


def _decoupling_for(reg: dict[str, Any], parts: list[dict[str, Any]],
                    by_ref: dict[str, Any], seat: dict[str, Any], put, anywhere) -> None:
    """The caps that belong to this regulator, placed against it.

    Loop area is the entire reason a decoupling cap exists. One placed across the board is a
    part on a BOM, not a decoupling capacitor, which is why the checks measure this.
    """
    caps = [p for p in parts if p["category"] == "capacitor"
            and p.get("decouples") == reg["reference"]]
    for cap in caps:
        # Spiral out from the regulator in 0.5 mm steps and take the first legal spot. Four
        # fixed offsets was not enough on a crowded regulator, and the fallback put the cap
        # across the board — which is a part on a BOM, not decoupling.
        seated = False
        radius = 2.0
        while radius <= DECOUPLE_MAX_MM and not seated:
            for step in range(16):
                angle = step * 3.14159265 / 8
                dx = radius * math.cos(angle)
                dy = radius * math.sin(angle)
                if put(cap, seat["x"] + dx, seat["y"] + dy, 0,
                       f"decoupling {reg['reference']}, {radius:.1f} mm away"):
                    seated = True
                    break
            radius += 0.5
        if not seated:
            anywhere(cap, f"decoupling {reg['reference']}; could not sit beside it")


def _edge_column(items: list[dict[str, Any]], x: float, height: float, put, *,
                 left: bool) -> None:
    """Connectors down one edge, their bodies just inside it."""
    if not items:
        return
    step = (height - 2 * EDGE_KEEPOUT_MM) / (len(items) + 1)
    for i, part in enumerate(items, start=1):
        cw = _courtyard(part, False)[0]
        cx = x + cw / 2 if left else x - cw / 2
        put(part, cx, EDGE_KEEPOUT_MM + step * i, 0,
            "on the " + ("left" if left else "right") + " edge, reachable from outside")


def _edge_row(items: list[dict[str, Any]], y: float, width: float, put) -> None:
    if not items:
        return
    step = (width - 2 * EDGE_KEEPOUT_MM) / (len(items) + 1)
    for i, part in enumerate(items, start=1):
        ch = _courtyard(part, False)[1]
        put(part, EDGE_KEEPOUT_MM + step * i, y + ch / 2, 0,
            "on the bottom edge, reachable from outside")


def _placement_checks(placed: list[dict[str, Any]], parts: list[dict[str, Any]],
                      width: float, height: float,
                      holes: list[dict[str, float]]) -> list[dict[str, Any]]:
    """Whether the placement above is legal. These decide whether the stage may be claimed."""
    out: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        out.append({"check": name, "ok": bool(ok), "detail": detail})

    missing = [p["reference"] for p in parts
               if not any(e["reference"] == p["reference"] for e in placed)]
    check("Every component is placed", not missing,
          "all components have a position" if not missing
          else f"{len(missing)} unplaced: {', '.join(missing[:6])}")

    outside = [e["reference"] for e in placed
               if e["x"] - e["court_mm"][0] / 2 < EDGE_KEEPOUT_MM - 1e-6
               or e["x"] + e["court_mm"][0] / 2 > width - EDGE_KEEPOUT_MM + 1e-6
               or e["y"] - e["court_mm"][1] / 2 < EDGE_KEEPOUT_MM - 1e-6
               or e["y"] + e["court_mm"][1] / 2 > height - EDGE_KEEPOUT_MM + 1e-6]
    check("Everything is on the board", not outside,
          f"all inside the {EDGE_KEEPOUT_MM:g} mm edge keepout" if not outside
          else f"off the board or in the keepout: {', '.join(outside[:6])}")

    clashes = [f"{a['reference']}/{b['reference']}"
               for i, a in enumerate(placed) for b in placed[i + 1:] if _overlap(a, b)]
    check("No two courtyards overlap", not clashes,
          f"{len(placed)} courtyards, {COURTYARD_GAP_MM:g} mm minimum gap" if not clashes
          else f"{len(clashes)} overlapping: {', '.join(clashes[:5])}")

    on_holes = [e["reference"] for e in placed for h in holes
                if abs(e["x"] - h["x"]) < HOLE_KEEPOUT_MM + e["court_mm"][0] / 2
                and abs(e["y"] - h["y"]) < HOLE_KEEPOUT_MM + e["court_mm"][1] / 2]
    check("Mounting holes are clear", not on_holes,
          "no component fouls a mounting hole" if not on_holes
          else f"fouling a hole: {', '.join(sorted(set(on_holes))[:6])}")

    seats = {e["reference"]: e for e in placed}
    far = []
    for part in parts:
        owner = part.get("decouples")
        if not owner or part["reference"] not in seats or owner not in seats:
            continue
        a, b = seats[part["reference"]], seats[owner]
        d = ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5
        if d > DECOUPLE_MAX_MM:
            far.append(f"{part['reference']} is {d:.1f} mm from {owner}")
    check("Decoupling is close to what it decouples", not far,
          f"every decoupling cap within {DECOUPLE_MAX_MM:g} mm" if not far
          else "; ".join(far[:4]))

    area = width * height
    used = sum(e["court_mm"][0] * e["court_mm"][1] for e in placed)
    check("Board is not overcrowded", used < area * 0.62,
          f"{used / area * 100:.0f}% of the board is courtyard "
          f"({used:.0f} of {area:.0f} mm^2)")
    return out


# ── the board as geometry ────────────────────────────────────────────────────
MM = 1 / 25.4


def board_assembly(name: str, layout: dict[str, Any], parts: list[dict[str, Any]],
                   layers: int) -> dict[str, Any]:
    """The placed board as `frc_cad` features, so a PCB gets the 3D viewer and STEP for free.

    Same trick the mechanical library uses: emit the existing closed vocabulary and every
    consumer already knows how to draw it. The board is a plate with its mounting holes
    bored, and each component is a plate the size of its own package.
    """
    from app.services.frc_cad import _asm, _at, bore, plate  # noqa: PLC0415

    w_mm, h_mm = layout["board_mm"]
    w, d = w_mm * MM, h_mm * MM
    thickness = 0.062 if layers <= 2 else 0.062      # 1.6 mm, the default fab stackup

    holes = [bore(hole["d"] * MM,
                  (hole["x"] - w_mm / 2) * MM, (hole["y"] - h_mm / 2) * MM,
                  note="M3 mounting hole")
             for hole in layout["mounting_holes"]]
    features = [plate(f"{name} substrate", (w, thickness, d), _at(0, thickness / 2, 0),
                      mat="polycarb", bores=holes,
                      note=f"{layers}-layer FR-4, 1.6 mm, {w_mm:g} x {h_mm:g} mm")]

    by_ref = {p["reference"]: p for p in parts}
    for entry in layout["placements"]:
        part = by_ref.get(entry["reference"], {})
        pkg = PACKAGES.get(entry["package"], PACKAGES[_DEFAULT_PACKAGE])
        bw, bh = pkg["body"]
        if entry["rotation"] % 180 == 90:
            bw, bh = bh, bw
        tall = pkg["height"] * MM
        features.append(plate(
            f"{entry['reference']} {part.get('value') or entry['package']}",
            (bw * MM, tall, bh * MM),
            _at((entry["x"] - w_mm / 2) * MM, thickness + tall / 2,
                (entry["y"] - h_mm / 2) * MM),
            mat="polycarb" if part.get("category") == "connector" else "steel",
            note=entry["why"]))

    return _asm("board", name, "part", features,
                note=f"{len(layout['placements'])} placed components on a "
                     f"{w_mm:g} x {h_mm:g} mm outline",
                mates=["mounts on four M3 screws through the corner holes"])


__all__ = ["place", "board_assembly", "package_for", "PACKAGES", "PACKAGE_NOTE",
           "LAYOUT_VERSION", "VERIFIED"]
