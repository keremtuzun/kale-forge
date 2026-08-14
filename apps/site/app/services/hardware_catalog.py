"""Catalog hardware: the parts a design specifies rather than makes.

`frc_parts` already holds verified vendor data for the things the robot compiler needed —
motors, tube sections, swerve modules, electronics. This module adds the hardware an
individual mechanical part is dimensioned AROUND: bearings, shafts, fasteners, belts and
chain. It deliberately re-exports from `frc_parts` rather than restating those numbers,
because two catalogs disagreeing is worse than one being incomplete.

The rule that matters here is the same one that governs component selection on the
electronics side: **never invent a part number**. Every entry carries `verified`, and an
entry that is a nominal standard rather than a specific vendor SKU says so. A design that
needs a part this catalog does not have gets a REQUIREMENT — "flanged ball bearing, 1.125 in
OD, 0.500 in hex bore" — not a fabricated MPN.

Stdlib-only: imported by the Vercel function.
"""
from __future__ import annotations

from typing import Any

from app.services.design_intent import ASSUMED, UNRESOLVED, VERIFIED
from app.services.frc_parts import MOTORS, STRUCTURE

CATALOG_VERSION = "kale-hardware-1.0"

MM = 1 / 25.4


# ── bearings ─────────────────────────────────────────────────────────────────
# The FRC hex bearings come from `frc_parts.STRUCTURE`, which the robot compiler already
# uses; the metric series here are nominal ISO 15 sizes, which are standards rather than one
# vendor's SKU, and are labelled as such.
def _bearing(key: str, name: str, bore: float, od: float, width: float, *,
             bore_form: str = "round", flanged: bool = False, flange_od: float = 0.0,
             source: str = VERIFIED, note: str = "") -> dict[str, Any]:
    return {"key": key, "name": name, "category": "bearing",
            "bore_in": round(bore, 4), "od_in": round(od, 4), "width_in": round(width, 4),
            "bore_form": bore_form, "flanged": flanged,
            "flange_od_in": round(flange_od, 4) if flange_od else 0.0,
            "verified": source == VERIFIED, "source": source, "note": note}


BEARINGS: dict[str, dict[str, Any]] = {
    # FRC hex bearings — the sizes the robot compiler is already built on.
    "hex_half_flanged": _bearing(
        "hex_half_flanged", '1/2 in hex flanged bearing', 0.500, 1.125, 0.313,
        bore_form="hex", flanged=True, flange_od=1.250,
        note="the standard FRC bearing; the one a 1/2 in hex shaft rides in"),
    "hex_half_thunderhex": _bearing(
        "hex_half_thunderhex", '1/2 in ThunderHex bearing', 0.500, 1.125, 0.313,
        bore_form="hex", flanged=True, flange_od=1.250,
        note="ThunderHex profile; same envelope as the standard hex bearing"),
    "hex_three_eighth": _bearing(
        "hex_three_eighth", '3/8 in hex flanged bearing', 0.375, 0.875, 0.280,
        bore_form="hex", flanged=True, flange_od=1.000),
    "round_half": _bearing(
        "round_half", '1/2 in bore radial bearing (R8)', 0.500, 1.125, 0.313),
    "round_375": _bearing(
        "round_375", '3/8 in bore radial bearing (R6)', 0.375, 0.875, 0.2188),
    # Metric standards. Nominal ISO sizes, not a vendor SKU — flagged accordingly.
    "mr6001": _bearing("mr6001", "6001 (12 x 28 x 8 mm)", 12 * MM, 28 * MM, 8 * MM,
                       source=ASSUMED, note="nominal 6001 series dimensions, not a vendor SKU"),
    "mr6002": _bearing("mr6002", "6002 (15 x 32 x 9 mm)", 15 * MM, 32 * MM, 9 * MM,
                       source=ASSUMED, note="nominal 6002 series dimensions, not a vendor SKU"),
    "mr6003": _bearing("mr6003", "6003 (17 x 35 x 10 mm)", 17 * MM, 35 * MM, 10 * MM,
                       source=ASSUMED, note="nominal 6003 series dimensions, not a vendor SKU"),
    "mr6004": _bearing("mr6004", "6004 (20 x 42 x 12 mm)", 20 * MM, 42 * MM, 12 * MM,
                       source=ASSUMED, note="nominal 6004 series dimensions, not a vendor SKU"),
    "mr608": _bearing("mr608", "608 (8 x 22 x 7 mm)", 8 * MM, 22 * MM, 7 * MM,
                      source=ASSUMED, note="nominal 608 series dimensions, not a vendor SKU"),
}


def bearing_by_od(od_in: float, tol: float = 0.02) -> dict[str, Any] | None:
    """The catalog bearing whose outside diameter matches, or None.

    Matching on OD is what a request like "a 35 mm OD bearing housing" gives you: the housing
    is dimensioned by the OD, and the bore follows from whatever bearing that turns out to be.
    """
    best, best_gap = None, tol
    for entry in BEARINGS.values():
        gap = abs(entry["od_in"] - od_in)
        if gap <= best_gap:
            best, best_gap = entry, gap
    return best


def bearing_for_shaft(bore_in: float, form: str = "hex") -> dict[str, Any] | None:
    """The catalog bearing a given shaft rides in."""
    for entry in BEARINGS.values():
        if abs(entry["bore_in"] - bore_in) < 0.01 and entry["bore_form"] == form:
            return entry
    for entry in BEARINGS.values():          # fall back to any bore form at that size
        if abs(entry["bore_in"] - bore_in) < 0.01:
            return entry
    return None


def bearing_requirement(od_in: float | None, bore_in: float | None,
                        form: str = "hex") -> dict[str, Any]:
    """A bearing this catalog does not carry, stated as a requirement rather than invented.

    This is the honest output when a user asks for something real that we have no verified
    numbers for: it names what has to be bought without pretending to know which SKU it is.
    """
    parts = []
    if od_in:
        parts.append(f"{od_in:.3f} in OD")
    if bore_in:
        parts.append(f"{bore_in:.3f} in {form} bore")
    return {"key": "", "name": "ball bearing, " + ", ".join(parts) if parts else "ball bearing",
            "category": "bearing", "od_in": od_in or 0.0, "bore_in": bore_in or 0.0,
            "width_in": 0.0, "bore_form": form, "flanged": False, "flange_od_in": 0.0,
            "verified": False, "source": UNRESOLVED,
            "note": "no catalog entry matches; width must be confirmed against the part bought"}


# ── shafts ───────────────────────────────────────────────────────────────────
SHAFTS: dict[str, dict[str, Any]] = {
    "hex_half": {"key": "hex_half", "name": '1/2 in hex shaft', "category": "shaft",
                 "across_flats_in": 0.500, "form": "hex", "verified": True, "source": VERIFIED,
                 "note": "the FRC standard; 0.500 across flats, 0.577 across corners"},
    "hex_three_eighth": {"key": "hex_three_eighth", "name": '3/8 in hex shaft',
                         "category": "shaft", "across_flats_in": 0.375, "form": "hex",
                         "verified": True, "source": VERIFIED},
    "round_half": {"key": "round_half", "name": '1/2 in round shaft', "category": "shaft",
                   "across_flats_in": 0.500, "form": "round", "verified": True, "source": VERIFIED},
    "round_375": {"key": "round_375", "name": '3/8 in round shaft', "category": "shaft",
                  "across_flats_in": 0.375, "form": "round", "verified": True, "source": VERIFIED},
}

# Across-corners for a regular hexagon is 2/sqrt(3) times across-flats. It matters because
# the bearing pocket and any clearance hole are sized on the corners, not the flats.
HEX_CORNER_RATIO = 1.1547005


# ── fasteners ────────────────────────────────────────────────────────────────
# Nominal thread and clearance sizes. These are standards, so they are verified in the sense
# that matters: they do not vary by vendor.
FASTENERS: dict[str, dict[str, Any]] = {
    "10-32": {"key": "10-32", "name": "10-32 socket head cap screw", "category": "fastener",
              "major_in": 0.190, "clearance_in": 0.201, "tap_in": 0.159,
              "head_dia_in": 0.312, "verified": True, "source": VERIFIED,
              "note": "the FRC default; #21 drill taps it, #7 clears it"},
    "1/4-20": {"key": "1/4-20", "name": "1/4-20 socket head cap screw", "category": "fastener",
               "major_in": 0.250, "clearance_in": 0.266, "tap_in": 0.201,
               "head_dia_in": 0.375, "verified": True, "source": VERIFIED},
    "8-32": {"key": "8-32", "name": "8-32 socket head cap screw", "category": "fastener",
             "major_in": 0.164, "clearance_in": 0.177, "tap_in": 0.136,
             "head_dia_in": 0.270, "verified": True, "source": VERIFIED},
    "6-32": {"key": "6-32", "name": "6-32 socket head cap screw", "category": "fastener",
             "major_in": 0.138, "clearance_in": 0.150, "tap_in": 0.107,
             "head_dia_in": 0.226, "verified": True, "source": VERIFIED},
    "m3": {"key": "m3", "name": "M3 socket head cap screw", "category": "fastener",
           "major_in": 3 * MM, "clearance_in": 3.4 * MM, "tap_in": 2.5 * MM,
           "head_dia_in": 5.5 * MM, "verified": True, "source": VERIFIED},
    "m5": {"key": "m5", "name": "M5 socket head cap screw", "category": "fastener",
           "major_in": 5 * MM, "clearance_in": 5.5 * MM, "tap_in": 4.2 * MM,
           "head_dia_in": 8.5 * MM, "verified": True, "source": VERIFIED},
}


# ── transmission ─────────────────────────────────────────────────────────────
# Pitch is the number that has to be right; everything else about a pulley or sprocket is
# derived from it and the tooth count.
BELTS: dict[str, dict[str, Any]] = {
    "htd5": {"key": "htd5", "name": "HTD 5 mm", "category": "belt", "pitch_in": 5 * MM,
             "widths_mm": [9, 15, 25], "verified": True, "source": VERIFIED},
    "gt2_3": {"key": "gt2_3", "name": "GT2 3 mm", "category": "belt", "pitch_in": 3 * MM,
              "widths_mm": [9, 15], "verified": True, "source": VERIFIED},
}
CHAINS: dict[str, dict[str, Any]] = {
    "25": {"key": "25", "name": "#25 roller chain", "category": "chain", "pitch_in": 0.250,
           "roller_in": 0.130, "verified": True, "source": VERIFIED},
    "35": {"key": "35", "name": "#35 roller chain", "category": "chain", "pitch_in": 0.375,
           "roller_in": 0.200, "verified": True, "source": VERIFIED},
}


# ── lookup across everything ─────────────────────────────────────────────────
_MOTOR_ALIASES = {
    "kraken x60": "kraken_x60", "kraken": "kraken_x60", "kraken x44": "kraken_x44",
    "falcon 500": "falcon_500", "falcon": "falcon_500", "neo vortex": "neo_vortex",
    "neo 550": "neo_550", "neo550": "neo_550", "neo": "neo", "minion": "minion", "cim": "cim",
}


def motor_by_name(text: str) -> dict[str, Any] | None:
    """A catalog motor named anywhere in the text. Longest alias first, so "neo 550" is not
    matched as "neo"."""
    low = " " + (text or "").lower() + " "
    for alias in sorted(_MOTOR_ALIASES, key=len, reverse=True):
        if alias in low:
            key = _MOTOR_ALIASES[alias]
            entry = dict(MOTORS[key])
            entry.update({"key": key, "category": "motor", "verified": True, "source": VERIFIED})
            return entry
    return None


def tube_by_section(width_in: float, height_in: float) -> dict[str, Any] | None:
    """A catalog structural tube matching a stated section, either way round."""
    for key, entry in STRUCTURE.items():
        sec = entry.get("section_in")
        if not sec:
            continue
        if ({round(sec[0], 3), round(sec[1], 3)} == {round(height_in, 3), round(width_in, 3)}):
            out = dict(entry)
            out.update({"key": key, "category": "structure", "verified": True, "source": VERIFIED})
            return out
    return None


def as_bom_row(entry: dict[str, Any], qty: int = 1) -> dict[str, Any]:
    """One catalog entry as a line a team can order from, provenance intact."""
    return {"name": entry.get("name") or entry.get("key") or "part",
            "category": entry.get("category", ""), "qty": qty,
            "verified": bool(entry.get("verified")),
            "source": entry.get("source", UNRESOLVED),
            "note": entry.get("note", "")}
