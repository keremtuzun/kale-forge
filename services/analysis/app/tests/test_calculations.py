"""Power-tree and estimate tests."""
from __future__ import annotations

import pytest

from app.calculations.estimates import (
    battery_runtime,
    linear_regulator_dissipation,
    rail_current,
    thermal_risk,
)
from app.calculations.power_tree import build_power_tree
from app.models.normalized import PinType
from app.tests.factories import comp, make_project


class TestEstimates:
    def test_rail_current_sum_and_formula(self):
        calc = rail_current("+3V3", {"U1": 20.0, "U2": 10.0})
        assert calc.value == pytest.approx(30.0)
        assert "Σ" in calc.formula
        assert calc.assumptions and calc.uncertainty

    def test_missing_inputs_yield_none(self):
        calc = linear_regulator_dissipation(None, 3.3, 100.0)
        assert calc.value is None
        assert "input voltage" in calc.missing

    def test_dissipation_math(self):
        calc = linear_regulator_dissipation(5.0, 3.3, 300.0)
        assert calc.value == pytest.approx((5.0 - 3.3) * 0.3)
        assert calc.assumptions  # never bare

    def test_thermal_levels(self):
        calc = thermal_risk(2.0, theta_ja_c_per_w=60.0)
        assert calc.value == pytest.approx(25 + 120)
        assert any("high" in a for a in calc.assumptions)

    def test_battery_runtime(self):
        calc = battery_runtime(2000.0, 100.0)
        assert calc.value == pytest.approx(2000 * 0.8 / 100)
        assert calc.uncertainty


class TestPowerTree:
    def _usb_reg_project(self):
        usb = comp("J1", "USB-C", pins=[("1", PinType.POWER_OUT)], lib_id="Connector:USB_C")
        reg = comp("U1", "AMS1117-3.3",
                   pins=[("1", PinType.POWER_IN), ("2", PinType.POWER_OUT), ("3", PinType.POWER_IN)])
        reg.pins[0].name = "VIN"
        reg.pins[1].name = "VOUT"
        reg.pins[2].name = "GND"
        mcu = comp("U2", "STM32", pins=[("1", PinType.POWER_IN), ("2", PinType.POWER_IN)])
        mcu.pins[0].name = "VDD"
        mcu.pins[1].name = "GND"
        led = comp("D1", "LED", lib_id="Device:LED")
        return make_project(
            [usb, reg, mcu, led],
            {
                "+5V": [("J1", "1"), ("U1", "1")],
                "+3V3": [("U1", "2"), ("U2", "1"), ("D1", "1")],
                "GND": [("U1", "3"), ("U2", "2"), ("D1", "2")],
            },
        )

    def test_tree_shape(self):
        tree = build_power_tree(self._usb_reg_project())
        kinds = {n.kind for n in tree.nodes}
        assert {"source", "regulator", "rail", "load", "ground"} <= kinds
        reg = next(n for n in tree.nodes if n.kind == "regulator")
        assert reg.voltage_v == pytest.approx(3.3)
        # regulator has an edge from +5V rail and to +3V3 rail
        edge_pairs = {tuple(e) for e in tree.edges}
        assert ("rail:+5V", reg.id) in edge_pairs
        assert (reg.id, "rail:+3V3") in edge_pairs

    def test_rail_totals_with_override(self):
        tree = build_power_tree(self._usb_reg_project(), current_overrides={"U2": 50.0})
        rail = next(r for r in tree.rails if r.net == "+3V3")
        # U2 override 50 + LED default 10 = 60
        assert rail.total_current_ma == pytest.approx(60.0)
        u2 = next(n for n in tree.nodes if n.id == "load:U2")
        assert u2.current_provenance == "user-provided"

    def test_calculations_have_provenance(self):
        tree = build_power_tree(self._usb_reg_project())
        assert tree.calculations
        for calc in tree.calculations:
            assert calc.formula
            # every calc exposes assumptions or missing data and an uncertainty note
            assert calc.assumptions or calc.missing
            assert calc.uncertainty
        assert tree.assumptions  # tree-level assumptions present
