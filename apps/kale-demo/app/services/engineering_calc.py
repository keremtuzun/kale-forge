"""Deterministic engineering calculations.

The division of labour this module exists to enforce: **a model decides the architecture, code
does the arithmetic.** A language model asked for a pitch diameter will produce a plausible
number, and plausible is exactly the wrong property for a dimension somebody is going to cut
metal to. Every number a Kale design quotes as CALCULATED comes from here.

Two halves, mechanical and electrical, kept in one module because they share the same rule
and the same provenance vocabulary. Stdlib-only.
"""
from __future__ import annotations

import math
from typing import Any

CALC_VERSION = "kale-calc-1.0"

MM = 1 / 25.4

# Densities in lb/in^3. Aluminium, steel and the two plastics FRC actually cuts.
DENSITY_LB_IN3: dict[str, float] = {
    "6061-T6 aluminium": 0.0975, "6061": 0.0975, "aluminium": 0.0975, "aluminum": 0.0975,
    "7075-T6 aluminium": 0.102, "7075": 0.102,
    "5052 aluminium": 0.0968, "5052": 0.0968,
    "4140 steel": 0.284, "1018 steel": 0.284, "steel": 0.284,
    "303 stainless": 0.290, "stainless": 0.290,
    "delrin": 0.0513, "acetal": 0.0513, "pom": 0.0513,
    "nylon": 0.0412, "uhmw": 0.0339, "polycarbonate": 0.0433, "polycarb": 0.0433,
    "abs": 0.0376, "pla": 0.0448, "petg": 0.0459,
}


def density(material: str) -> float | None:
    """lb/in^3 for a named material, or None when it is not one we have a figure for.

    None rather than a guess: a mass estimate quoted from an invented density is worse than
    no mass estimate, because it looks like it came from somewhere.
    """
    key = (material or "").strip().lower()
    if key in DENSITY_LB_IN3:
        return DENSITY_LB_IN3[key]
    for name, value in DENSITY_LB_IN3.items():
        if name in key or key in name:
            return value
    return None


# ── mechanical ───────────────────────────────────────────────────────────────
def gear_ratio(driving_teeth: int, driven_teeth: int) -> float | None:
    if driving_teeth <= 0 or driven_teeth <= 0:
        return None
    return driven_teeth / driving_teeth


def stage_ratio(stages: list[tuple[int, int]]) -> float | None:
    """Overall reduction of a gear train given (driving, driven) per stage."""
    total = 1.0
    for driving, driven in stages:
        step = gear_ratio(driving, driven)
        if step is None:
            return None
        total *= step
    return total


def pitch_diameter(teeth: int, *, dp: float = 0.0, pitch_in: float = 0.0) -> float | None:
    """PD from a tooth count. Diametral pitch for gears (PD = N/DP), linear pitch for belts
    and chain (PD = N x p / pi). Getting this from the space available instead of the tooth
    count is the single most common way a generated transmission stops meshing."""
    if teeth <= 0:
        return None
    if dp > 0:
        return teeth / dp
    if pitch_in > 0:
        return teeth * pitch_in / math.pi
    return None


def center_distance(teeth_a: int, teeth_b: int, *, dp: float = 0.0,
                    pitch_in: float = 0.0) -> float | None:
    """Where two meshing toothed parts have to sit: the sum of their pitch radii, exactly."""
    a = pitch_diameter(teeth_a, dp=dp, pitch_in=pitch_in)
    b = pitch_diameter(teeth_b, dp=dp, pitch_in=pitch_in)
    if a is None or b is None:
        return None
    return (a + b) / 2


def output_speed_rpm(free_rpm: float, ratio: float) -> float | None:
    if ratio <= 0:
        return None
    return free_rpm / ratio


def output_torque_nm(stall_nm: float, ratio: float, efficiency: float = 0.95) -> float | None:
    if ratio <= 0:
        return None
    return stall_nm * ratio * efficiency


def linear_speed_fps(wheel_dia_in: float, rpm: float) -> float | None:
    if wheel_dia_in <= 0:
        return None
    return math.pi * wheel_dia_in * rpm / 12.0 / 60.0


def hex_across_corners(across_flats: float) -> float:
    """A regular hexagon's corner-to-corner distance. Bores and clearance holes are sized on
    this, not on the across-flats number the stock is sold by."""
    return across_flats * 2 / math.sqrt(3)


def volume_box_in3(w: float, h: float, d: float) -> float:
    return max(0.0, w) * max(0.0, h) * max(0.0, d)


def volume_cylinder_in3(dia: float, length: float) -> float:
    return math.pi * (max(0.0, dia) / 2) ** 2 * max(0.0, length)


def plate_volume_in3(w: float, t: float, d: float,
                     bores: list[dict[str, Any]] | None = None) -> float:
    """A plate's volume with its holes removed — which is what it weighs, and the reason a
    mass estimate that ignores a 1.125 in pocket is wrong by a useful fraction."""
    total = volume_box_in3(w, t, d)
    for hole in bores or []:
        dia = float(hole.get("d") or 0)
        depth = float(hole.get("depth") or 0) or t
        if dia <= 0:
            continue
        cut = volume_cylinder_in3(dia, min(depth, t))
        if (hole.get("form") or "round") == "hex":
            # A hexagon of a given across-flats has area (sqrt(3)/2) * AF^2, which is
            # 2*sqrt(3)/pi of the circumscribed circle's — about 83%.
            cut *= (2 * math.sqrt(3) / math.pi) * 0.5 * 2
        total -= cut
    return max(0.0, total)


def mass_lb(volume_in3: float, material: str) -> float | None:
    rho = density(material)
    return None if rho is None else volume_in3 * rho


def safety_factor(capacity: float, applied: float) -> float | None:
    if applied <= 0:
        return None
    return capacity / applied


def bolt_circle_positions(count: int, diameter: float,
                          phase_deg: float = 45.0) -> list[tuple[float, float]]:
    """Where the screws go on a face pattern."""
    if count <= 0 or diameter <= 0:
        return []
    return [(math.cos(math.radians(phase_deg) + i * 2 * math.pi / count) * diameter / 2,
             math.sin(math.radians(phase_deg) + i * 2 * math.pi / count) * diameter / 2)
            for i in range(count)]


def press_fit_interference(shaft_dia: float, hole_dia: float) -> dict[str, Any]:
    """Whether a pairing is press, slip or neither, and by how much."""
    delta = hole_dia - shaft_dia
    if delta < -0.0002:
        kind = "press"
    elif delta > 0.0005:
        kind = "slip"
    else:
        kind = "line-to-line"
    return {"interference_in": round(-delta, 5), "clearance_in": round(delta, 5), "fit": kind}


# ── electrical ───────────────────────────────────────────────────────────────
E24 = (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9,
       4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1)


def nearest_e24(value: float) -> float | None:
    """The nearest standard E24 resistor value. Quoting 1,847 ohms for a divider is a sign
    nobody checked whether it can be bought."""
    if value <= 0:
        return None
    decade = math.floor(math.log10(value))
    base = value / (10 ** decade)
    best = min(E24, key=lambda e: abs(e - base))
    return round(best * (10 ** decade), 6)


def led_resistor_ohm(supply_v: float, forward_v: float, current_a: float) -> dict[str, Any] | None:
    if current_a <= 0 or supply_v <= forward_v:
        return None
    exact = (supply_v - forward_v) / current_a
    standard = nearest_e24(exact)
    actual = (supply_v - forward_v) / standard if standard else current_a
    return {"exact_ohm": round(exact, 2), "standard_ohm": standard,
            "actual_current_a": round(actual, 5),
            "dissipation_w": round((supply_v - forward_v) * actual, 5)}


def voltage_divider(v_in: float, r_top: float, r_bottom: float) -> dict[str, Any] | None:
    if r_top + r_bottom <= 0:
        return None
    v_out = v_in * r_bottom / (r_top + r_bottom)
    return {"v_out": round(v_out, 4),
            "current_a": round(v_in / (r_top + r_bottom), 6),
            "dissipation_w": round(v_in ** 2 / (r_top + r_bottom), 6)}


def divider_for(v_in: float, v_out: float, r_bottom: float) -> dict[str, Any] | None:
    """The top resistor that produces a wanted output, snapped to E24, with the error it
    leaves. The error matters: a divider into an ADC reference that is 3% off is a 3% reading
    error nobody is looking for."""
    if v_out <= 0 or v_out >= v_in or r_bottom <= 0:
        return None
    exact = r_bottom * (v_in - v_out) / v_out
    standard = nearest_e24(exact)
    actual = voltage_divider(v_in, standard or exact, r_bottom)
    return {"r_top_exact_ohm": round(exact, 2), "r_top_ohm": standard,
            "r_bottom_ohm": r_bottom, "actual_v_out": actual["v_out"] if actual else None,
            "error_pct": round(abs((actual["v_out"] - v_out) / v_out) * 100, 3)
            if actual else None}


def regulator_dissipation_w(v_in: float, v_out: float, current_a: float,
                            topology: str = "linear", efficiency: float = 0.88) -> dict[str, Any]:
    """How much heat a regulator has to lose. This is the number that decides whether a
    5 V 3 A rail needs a buck or a heatsink the board has no room for."""
    if topology == "linear":
        watts = max(0.0, (v_in - v_out)) * current_a
        note = "linear: every volt dropped becomes heat"
    else:
        out_w = v_out * current_a
        watts = out_w * (1 - efficiency) / max(efficiency, 0.01)
        note = f"switching at {efficiency * 100:.0f}% efficiency"
    return {"dissipation_w": round(watts, 3), "topology": topology, "note": note,
            "needs_switching": bool(topology == "linear" and watts > 1.0)}


def power_budget(rails: list[dict[str, Any]]) -> dict[str, Any]:
    """Total draw across rails, each {name, voltage, current_a}."""
    total_w = 0.0
    rows = []
    for rail in rails:
        v = float(rail.get("voltage") or 0)
        a = float(rail.get("current_a") or 0)
        w = v * a
        total_w += w
        rows.append({"name": rail.get("name", ""), "voltage": v, "current_a": a,
                     "power_w": round(w, 3)})
    return {"rails": rows, "total_w": round(total_w, 3)}


def input_current_a(total_w: float, v_in: float, efficiency: float = 0.85) -> float | None:
    if v_in <= 0:
        return None
    return round(total_w / (v_in * max(efficiency, 0.01)), 4)


def trace_width_in(current_a: float, *, copper_oz: float = 1.0, rise_c: float = 10.0,
                   internal: bool = False) -> dict[str, Any] | None:
    """IPC-2221 trace width for a current and a temperature rise.

    This is an empirical curve fit, not a thermal simulation, and it is reported that way:
    it assumes still air and no adjacent heat sources, which a real board rarely is.
    """
    if current_a <= 0 or copper_oz <= 0:
        return None
    k = 0.024 if internal else 0.048
    area_mils2 = (current_a / (k * (rise_c ** 0.44))) ** (1 / 0.725)
    width_mils = area_mils2 / (copper_oz * 1.378)
    return {"width_in": round(width_mils / 1000, 5),
            "width_mm": round(width_mils * 0.0254, 4),
            "layer": "internal" if internal else "external",
            "assumption": (f"IPC-2221 curve fit, {copper_oz:g} oz copper, {rise_c:g} C rise, "
                           "still air, no adjacent heat sources")}


def voltage_drop_v(current_a: float, length_in: float, width_in: float,
                   copper_oz: float = 1.0) -> float | None:
    """Drop along a trace. Copper resistivity 0.67 uohm-in at 20 C; 1 oz is 1.37 mil thick."""
    if current_a <= 0 or length_in <= 0 or width_in <= 0 or copper_oz <= 0:
        return None
    thickness_in = 0.00137 * copper_oz
    resistance = 0.67e-6 * length_in / (width_in * thickness_in)
    return round(current_a * resistance, 5)


def fuse_rating_a(steady_a: float, margin: float = 1.5) -> dict[str, Any]:
    """A protection rating above the steady draw with headroom, snapped to a size that is
    actually sold."""
    common = (1, 2, 3, 5, 7.5, 10, 15, 20, 25, 30, 40)
    want = steady_a * margin
    pick = next((c for c in common if c >= want), common[-1])
    return {"steady_a": round(steady_a, 3), "margin": margin,
            "recommended_a": pick,
            "note": f"{margin:g}x the steady draw, rounded up to a stocked rating"}
