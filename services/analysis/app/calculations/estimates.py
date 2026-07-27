"""Power estimate calculations. Every function returns a PowerCalculation that exposes its
formula, inputs, assumptions, missing data, and uncertainty. Nothing here is presented as a
guaranteed engineering result — these are approximations to guide review."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# Default per-class current draw (mA), all INFERRED — listed as assumptions wherever used.
DEFAULT_CURRENT_MA = {
    "mcu": 20.0,
    "ic": 10.0,
    "opamp": 5.0,
    "led": 10.0,
    "relay": 70.0,
    "regulator": 5.0,   # quiescent
    "sensor": 5.0,
    "connector": 0.0,
    "passive": 0.0,
    "default": 5.0,
}


class PowerCalculation(BaseModel):
    name: str
    value: Optional[float] = None
    unit: str = ""
    formula: str = ""
    inputs: dict[str, Optional[float]] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    uncertainty: str = ""


def rail_current(rail_name: str, load_currents_ma: dict[str, float]) -> PowerCalculation:
    total = sum(load_currents_ma.values())
    formula_terms = " + ".join(f"{ref}:{ma:g}mA" for ref, ma in sorted(load_currents_ma.items())) or "0"
    return PowerCalculation(
        name=f"Total current on {rail_name}",
        value=total if load_currents_ma else None,
        unit="mA",
        formula=f"I_rail = Σ I_load = {formula_terms} = {total:g} mA",
        inputs={ref: ma for ref, ma in load_currents_ma.items()},
        assumptions=["Per-load currents are estimates or user overrides, not measurements."],
        missing=[] if load_currents_ma else ["no load-current estimates available for this rail"],
        uncertainty="Actual current depends on operating mode, clock speed, and load; treat as an order-of-magnitude estimate.",
    )


def linear_regulator_dissipation(
    v_in: Optional[float], v_out: Optional[float], current_ma: Optional[float]
) -> PowerCalculation:
    missing = []
    if v_in is None:
        missing.append("input voltage")
    if v_out is None:
        missing.append("output voltage")
    if current_ma is None:
        missing.append("output current estimate")
    value = None
    if not missing:
        value = (v_in - v_out) * (current_ma / 1000.0)
    return PowerCalculation(
        name="Linear regulator dissipation",
        value=value,
        unit="W",
        formula="P = (V_in − V_out) × I_out",
        inputs={"v_in": v_in, "v_out": v_out, "current_ma": current_ma},
        assumptions=[
            "Linear (LDO) topology assumed — a switching regulator dissipates far less.",
            "Ignores quiescent current and ground-pin current.",
        ],
        missing=missing,
        uncertainty="Estimate only; verify against the datasheet's thermal data and real load.",
    )


def switching_dissipation(
    p_out_w: Optional[float], efficiency: float = 0.85
) -> PowerCalculation:
    value = None
    if p_out_w is not None and efficiency > 0:
        value = p_out_w * (1.0 / efficiency - 1.0)
    return PowerCalculation(
        name="Switching regulator dissipation",
        value=value,
        unit="W",
        formula="P_loss = P_out × (1/η − 1)",
        inputs={"p_out_w": p_out_w, "efficiency": efficiency},
        assumptions=[f"Efficiency assumed η = {efficiency:g} (typical; actual varies with load and Vin)."],
        missing=[] if p_out_w is not None else ["output power"],
        uncertainty="Efficiency is load-dependent; consult the converter's efficiency curve.",
    )


def efficiency(p_out_w: Optional[float], p_in_w: Optional[float]) -> PowerCalculation:
    value = None
    missing = []
    if p_out_w is None:
        missing.append("output power")
    if p_in_w is None or p_in_w == 0:
        missing.append("input power")
    if not missing:
        value = p_out_w / p_in_w * 100.0
    return PowerCalculation(
        name="Estimated efficiency",
        value=value,
        unit="%",
        formula="η = P_out / P_in × 100",
        inputs={"p_out_w": p_out_w, "p_in_w": p_in_w},
        assumptions=["Based on estimated currents, not measured."],
        missing=missing,
        uncertainty="Order-of-magnitude; measure on the bench for real figures.",
    )


def thermal_risk(
    dissipation_w: Optional[float], theta_ja_c_per_w: float = 60.0, ambient_c: float = 25.0
) -> PowerCalculation:
    value = None
    level = "unknown"
    if dissipation_w is not None:
        rise = dissipation_w * theta_ja_c_per_w
        value = ambient_c + rise
        if value >= 125:
            level = "high (junction may exceed common 125 °C max)"
        elif value >= 85:
            level = "moderate"
        else:
            level = "low"
    return PowerCalculation(
        name="Estimated junction temperature / thermal risk",
        value=value,
        unit="°C",
        formula="T_j = T_ambient + P × θ_JA",
        inputs={"dissipation_w": dissipation_w, "theta_ja": theta_ja_c_per_w, "ambient_c": ambient_c},
        assumptions=[
            f"θ_JA assumed {theta_ja_c_per_w:g} °C/W (package/board dependent; often optimistic).",
            f"Ambient assumed {ambient_c:g} °C.",
            f"Risk level: {level}.",
        ],
        missing=[] if dissipation_w is not None else ["dissipation"],
        uncertainty="θ_JA varies strongly with copper area and airflow; use the datasheet value and verify.",
    )


def battery_runtime(
    capacity_mah: Optional[float], total_current_ma: Optional[float], usable_fraction: float = 0.8
) -> PowerCalculation:
    value = None
    missing = []
    if capacity_mah is None:
        missing.append("battery capacity (mAh)")
    if total_current_ma is None or total_current_ma == 0:
        missing.append("total current draw")
    if not missing:
        value = (capacity_mah * usable_fraction) / total_current_ma
    return PowerCalculation(
        name="Approximate battery runtime",
        value=value,
        unit="h",
        formula="t = (Capacity × usable_fraction) / I_total",
        inputs={"capacity_mah": capacity_mah, "total_current_ma": total_current_ma},
        assumptions=[
            f"Usable capacity fraction assumed {usable_fraction:g} (derating for cutoff/aging).",
            "Constant average current assumed; ignores sleep/active duty cycling.",
        ],
        missing=missing,
        uncertainty="Real runtime depends on duty cycle, temperature, and battery chemistry.",
    )
