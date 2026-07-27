"""Rule-engine tests: registry discovery + firing/non-firing cases for each category."""
from __future__ import annotations

import pytest

from app.models.normalized import Board, Component, Pin, PinType, Point, Position, Trace, Via, Zone
from app.rules.base import Severity
from app.rules.registry import discover_rules, run_rules
from app.tests.factories import comp, ctx_for, make_project


class TestRegistry:
    def test_discovers_all_42_rules(self):
        rules = discover_rules(force=True)
        ids = [r.id for r in rules]
        assert len(ids) == len(set(ids)), "duplicate rule ids"
        by_prefix = {
            "CONN": [i for i in ids if i.startswith("CONN")],
            "COMP": [i for i in ids if i.startswith("COMP")],
            "PWR": [i for i in ids if i.startswith("PWR")],
            "PCB": [i for i in ids if i.startswith("PCB")],
        }
        assert len(by_prefix["CONN"]) == 9
        assert len(by_prefix["COMP"]) == 12
        assert len(by_prefix["PWR"]) == 9
        assert len(by_prefix["PCB"]) == 12

    def test_run_rules_sorted_by_severity(self):
        project = make_project(
            [comp("D1", "LED", lib_id="Device:LED"), comp("U1", "MCU", pins=[("1", PinType.POWER_IN)])],
            {"+5V": [("D1", "1"), ("U1", "1")], "GND": [("D1", "2")]},
        )
        findings = run_rules(project)
        severities = [f.severity for f in findings]
        order = {Severity.CRITICAL: 0, Severity.ERROR: 1, Severity.WARNING: 2, Severity.INFO: 3}
        assert severities == sorted(severities, key=lambda s: order[s])


def rule_ids(findings):
    return {f.rule_id for f in findings}


class TestConnectivity:
    def test_conn001_unconnected_pin(self):
        from app.rules.connectivity.unconnected_pins import UnconnectedPinsRule

        project = make_project([comp("R1", "10k")], {"A": [("R1", "1")]})
        findings = UnconnectedPinsRule().check(project, ctx_for(project))
        assert len(findings) == 1
        assert findings[0].affected_components == ["R1"]

    def test_conn001_respects_no_connect(self):
        from app.rules.connectivity.unconnected_pins import UnconnectedPinsRule

        c = comp("U1", "IC", pins=[("1", PinType.PASSIVE), ("2", PinType.NO_CONNECT)])
        project = make_project([c], {"A": [("U1", "1")]})
        assert UnconnectedPinsRule().check(project, ctx_for(project)) == []

    def test_conn003_conflicting_outputs(self):
        from app.rules.connectivity.conflicting_outputs import ConflictingOutputsRule

        a = comp("U1", "IC", pins=[("1", PinType.OUTPUT)])
        b = comp("U2", "IC", pins=[("1", PinType.OUTPUT)])
        project = make_project([a, b], {"X": [("U1", "1"), ("U2", "1")]})
        findings = ConflictingOutputsRule().check(project, ctx_for(project))
        assert len(findings) == 1
        assert set(findings[0].affected_components) == {"U1", "U2"}

    def test_conn004_floating_input(self):
        from app.rules.connectivity.floating_inputs import FloatingInputsRule

        a = comp("U1", "IC", pins=[("1", PinType.INPUT)])
        b = comp("U2", "IC", pins=[("1", PinType.INPUT)])
        project = make_project([a, b], {"X": [("U1", "1"), ("U2", "1")]})
        findings = FloatingInputsRule().check(project, ctx_for(project))
        assert rule_ids(findings) == {"CONN-004"}
        # add a driver -> no finding
        c = comp("U3", "IC", pins=[("1", PinType.OUTPUT)])
        project2 = make_project([a, b, c], {"X": [("U1", "1"), ("U2", "1"), ("U3", "1")]})
        assert FloatingInputsRule().check(project2, ctx_for(project2)) == []

    def test_conn005_single_pin_net(self):
        from app.rules.connectivity.single_pin_nets import SinglePinNetsRule

        project = make_project([comp("R1")], {"LONELY": [("R1", "1")], "GND": [("R1", "2")]})
        findings = SinglePinNetsRule().check(project, ctx_for(project))
        assert {f.affected_nets[0] for f in findings} == {"LONELY", "GND"}

    def test_conn006_missing_ground(self):
        from app.rules.connectivity.missing_ground import MissingGroundRule

        project = make_project([comp("R1"), comp("R2")], {"+5V": [("R1", "1"), ("R2", "1")]})
        findings = MissingGroundRule().check(project, ctx_for(project))
        assert len(findings) == 1 and findings[0].severity == Severity.CRITICAL
        grounded = make_project([comp("R1"), comp("R2")], {"GND": [("R1", "1"), ("R2", "1")]})
        assert MissingGroundRule().check(grounded, ctx_for(grounded)) == []

    def test_conn007_split_grounds(self):
        from app.rules.connectivity.split_ground_domains import SplitGroundDomainsRule

        project = make_project(
            [comp("R1"), comp("R2")], {"GND": [("R1", "1")], "AGND": [("R2", "1")]}
        )
        findings = SplitGroundDomainsRule().check(project, ctx_for(project))
        assert len(findings) == 1
        bridged = make_project([comp("R1"), comp("R2"), comp("R3")],
                               {"GND": [("R1", "1"), ("R3", "1")], "AGND": [("R2", "1"), ("R3", "2")]})
        assert SplitGroundDomainsRule().check(bridged, ctx_for(bridged)) == []

    def test_conn008_duplicate_refs(self):
        from app.rules.connectivity.duplicate_references import DuplicateReferencesRule

        project = make_project([comp("R1", "10k"), comp("R1", "22k")], {})
        findings = DuplicateReferencesRule().check(project, ctx_for(project))
        assert len(findings) == 1 and findings[0].affected_components == ["R1"]

    def test_conn009_islands(self):
        from app.rules.connectivity.isolated_islands import IsolatedIslandsRule

        project = make_project(
            [comp("R1"), comp("R2"), comp("R3"), comp("R4")],
            {"A": [("R1", "1"), ("R2", "1")], "B": [("R3", "1"), ("R4", "1")]},
        )
        findings = IsolatedIslandsRule().check(project, ctx_for(project))
        assert len(findings) == 1


class TestComponents:
    def test_comp001_missing_value(self):
        from app.rules.components.missing_value import MissingValueRule

        project = make_project([comp("R1", ""), comp("R2", "10k")], {})
        findings = MissingValueRule().check(project, ctx_for(project))
        assert [f.affected_components[0] for f in findings] == ["R1"]

    def test_comp002_missing_footprint(self):
        from app.rules.components.missing_footprint import MissingFootprintRule

        project = make_project([comp("R1", "10k"), comp("R2", "1k", footprint="R_0603")], {})
        findings = MissingFootprintRule().check(project, ctx_for(project))
        assert [f.affected_components[0] for f in findings] == ["R1"]

    def test_comp003_mixed_styles(self):
        from app.rules.components.value_format import ValueFormatRule

        project = make_project([comp("R1", "4k7"), comp("R2", "4.7k"), comp("R3", "garbage##")], {})
        findings = ValueFormatRule().check(project, ctx_for(project))
        titles = {f.title for f in findings}
        assert "Mixed value notation styles" in titles
        assert any("R3" in f.affected_components for f in findings)

    def test_comp008_led_rail_to_rail(self):
        from app.rules.components.led_no_resistor import LedNoResistorRule

        led = comp("D1", "LED", lib_id="Device:LED")
        project = make_project([led], {"+5V": [("D1", "1")], "GND": [("D1", "2")]})
        findings = LedNoResistorRule().check(project, ctx_for(project))
        assert len(findings) == 1 and findings[0].confidence == pytest.approx(0.9)

    def test_comp008_with_series_resistor_quiet(self):
        from app.rules.components.led_no_resistor import LedNoResistorRule

        led = comp("D1", "LED", lib_id="Device:LED")
        r = comp("R1", "330")
        project = make_project(
            [led, r], {"+5V": [("R1", "1")], "LEDA": [("R1", "2"), ("D1", "1")], "GND": [("D1", "2")]}
        )
        assert LedNoResistorRule().check(project, ctx_for(project)) == []

    def test_comp009_relay_flyback(self):
        from app.rules.components.relay_no_flyback import RelayNoFlybackRule

        relay = comp("K1", "RELAY-5V", lib_id="Relay:G5V-1")
        project = make_project([relay], {"+5V": [("K1", "1")], "SW": [("K1", "2")]})
        findings = RelayNoFlybackRule().check(project, ctx_for(project))
        assert len(findings) == 1
        diode = comp("D1", "1N4148", lib_id="Device:D")
        protected = make_project(
            [relay, diode], {"+5V": [("K1", "1"), ("D1", "2")], "SW": [("K1", "2"), ("D1", "1")]}
        )
        assert RelayNoFlybackRule().check(protected, ctx_for(protected)) == []

    def test_comp011_regulator_caps(self):
        from app.rules.components.regulator_caps import RegulatorCapsRule

        reg = comp("U1", "AMS1117-3.3", pins=[("1", PinType.POWER_IN), ("2", PinType.POWER_OUT), ("3", PinType.POWER_IN)])
        reg.pins[0].name = "VIN"
        reg.pins[1].name = "VOUT"
        reg.pins[2].name = "GND"
        project = make_project(
            [reg], {"+5V": [("U1", "1")], "+3V3": [("U1", "2")], "GND": [("U1", "3")]}
        )
        findings = RegulatorCapsRule().check(project, ctx_for(project))
        assert len(findings) == 2  # missing input and output caps
        cap_in, cap_out = comp("C1", "10uF"), comp("C2", "10uF")
        good = make_project(
            [reg, cap_in, cap_out],
            {"+5V": [("U1", "1"), ("C1", "1")], "+3V3": [("U1", "2"), ("C2", "1")],
             "GND": [("U1", "3"), ("C1", "2"), ("C2", "2")]},
        )
        assert RegulatorCapsRule().check(good, ctx_for(good)) == []

    def test_comp012_decoupling(self):
        from app.rules.components.decoupling_near_ic import DecouplingNearIcRule

        mcu = comp("U1", "ATTINY85", pins=[("8", PinType.POWER_IN), ("4", PinType.POWER_IN)])
        mcu.pins[0].name = "VCC"
        mcu.pins[1].name = "GND"
        project = make_project([mcu], {"+5V": [("U1", "8")], "GND": [("U1", "4")]})
        findings = DecouplingNearIcRule().check(project, ctx_for(project))
        assert len(findings) == 1 and "+5V" in findings[0].affected_nets
        cap = comp("C1", "100nF")
        good = make_project(
            [mcu, cap], {"+5V": [("U1", "8"), ("C1", "1")], "GND": [("U1", "4"), ("C1", "2")]}
        )
        assert DecouplingNearIcRule().check(good, ctx_for(good)) == []

    def test_comp007_i2c_pullups(self):
        from app.rules.components.missing_pullups import MissingPullupsRule

        mcu = comp("U1", "MCU", pins=[("1", PinType.BIDIRECTIONAL), ("2", PinType.BIDIRECTIONAL)])
        mcu.pins[0].name = "SDA"
        mcu.pins[1].name = "SCL"
        eeprom = comp("U2", "EEPROM", pins=[("1", PinType.BIDIRECTIONAL), ("2", PinType.INPUT)])
        eeprom.pins[0].name = "SDA"
        eeprom.pins[1].name = "SCL"
        project = make_project(
            [mcu, eeprom], {"SDA": [("U1", "1"), ("U2", "1")], "SCL": [("U1", "2"), ("U2", "2")]}
        )
        findings = MissingPullupsRule().check(project, ctx_for(project))
        assert len(findings) == 2


class TestPower:
    def _regulator(self):
        reg = comp("U1", "LM7805", pins=[("1", PinType.POWER_IN), ("2", PinType.POWER_IN), ("3", PinType.POWER_OUT)])
        reg.pins[0].name = "VI"
        reg.pins[1].name = "GND"
        reg.pins[2].name = "VO"
        return reg

    def test_pwr001_dropout(self):
        from app.rules.power.regulator_dropout import RegulatorDropoutRule

        reg = self._regulator()
        project = make_project(
            [reg], {"+5V": [("U1", "1")], "GND": [("U1", "2")], "+5V0_OUT": [("U1", "3")]}
        )
        # +5V in, 7805 wants 5V out -> vin < vout + dropout — but +5V0_OUT parses as 5.0
        findings = RegulatorDropoutRule().check(project, ctx_for(project))
        assert len(findings) == 1
        ok = make_project(
            [self._regulator()], {"+12V": [("U1", "1")], "GND": [("U1", "2")], "+5V": [("U1", "3")]}
        )
        assert RegulatorDropoutRule().check(ok, ctx_for(ok)) == []

    def test_pwr002_overvoltage_requires_specs(self):
        from app.rules.power.overvoltage_risk import OvervoltageRiskRule

        c = comp("U1", "SENSOR", pins=[("1", PinType.POWER_IN)])
        project = make_project([c], {"+12V": [("U1", "1")]})
        assert OvervoltageRiskRule().check(project, ctx_for(project)) == []  # no specs -> silent
        ctx = ctx_for(project, {"component_specs": {"U1": {"max_voltage_v": 3.6, "provenance": "verified"}}})
        findings = OvervoltageRiskRule().check(project, ctx)
        assert len(findings) == 1 and findings[0].severity == Severity.ERROR

    def test_pwr003_rail_no_cap(self):
        from app.rules.power.rail_no_bulk_cap import RailNoBulkCapRule

        project = make_project(
            [comp("U1", "A", pins=[("1", PinType.POWER_IN)]), comp("U2", "B", pins=[("1", PinType.POWER_IN)])],
            {"+3V3": [("U1", "1"), ("U2", "1")]},
        )
        findings = RailNoBulkCapRule().check(project, ctx_for(project))
        assert len(findings) == 1

    def test_pwr007_rail_no_source(self):
        from app.rules.power.rail_no_source import RailNoSourceRule

        project = make_project(
            [comp("U1", "MCU", pins=[("1", PinType.POWER_IN)]), comp("U2", "SENSOR", pins=[("1", PinType.POWER_IN)])],
            {"+3V3": [("U1", "1"), ("U2", "1")]},
        )
        findings = RailNoSourceRule().check(project, ctx_for(project))
        assert len(findings) == 1
        with_reg = make_project(
            [comp("U1", "MCU", pins=[("1", PinType.POWER_IN)]), comp("U3", "AMS1117-3.3")],
            {"+3V3": [("U1", "1"), ("U3", "2")]},
        )
        assert RailNoSourceRule().check(with_reg, ctx_for(with_reg)) == []

    def test_pwr009_thermal(self):
        from app.rules.power.regulator_thermal import RegulatorThermalRule

        reg = self._regulator()
        mcu = comp("U2", "MCU", pins=[("1", PinType.POWER_IN)])
        project = make_project(
            [reg, mcu], {"+12V": [("U1", "1")], "GND": [("U1", "2")], "+5V": [("U1", "3"), ("U2", "1")]}
        )
        ctx = ctx_for(project, {"current_estimates_ma": {"U2": 300.0}})
        findings = RegulatorThermalRule().check(project, ctx)
        assert len(findings) == 1  # (12-5)*0.3 = 2.1 W > 1 W
        assert "2.10 W" in findings[0].description or "2.1" in findings[0].description


class TestPcb:
    def _board_project(self):
        r1 = comp("R1", "10k")
        r1.position = Position(x=100, y=50)
        r1.layer = "F.Cu"
        project = make_project([r1], {"+5V": [("R1", "1")], "GND": [("R1", "2")]})
        project.board = Board(
            outline=[Point(x=90, y=40), Point(x=120, y=40), Point(x=120, y=60), Point(x=90, y=60)],
            width_mm=30,
            height_mm=20,
            traces=[
                Trace(net="+5V", layer="F.Cu", width_mm=0.1,
                      start=Point(x=90.05, y=41), end=Point(x=95, y=41)),
                Trace(net="GND", layer="F.Cu", width_mm=0.5,
                      start=Point(x=100, y=50), end=Point(x=110, y=50)),
            ],
            vias=[Via(net="+5V", position=Point(x=91, y=41), diameter_mm=0.3, drill_mm=0.15)],
            zones=[Zone(net="GND", layer="B.Cu", filled=False)],
        )
        return project

    def test_pcb_rules_skip_without_board(self):
        from app.rules.pcb.trace_edge_clearance import TraceEdgeClearanceRule

        project = make_project([comp("R1")], {})
        assert TraceEdgeClearanceRule().applies(project) is False

    def test_pcb001_edge_clearance(self):
        from app.rules.pcb.trace_edge_clearance import TraceEdgeClearanceRule

        project = self._board_project()
        findings = TraceEdgeClearanceRule().check(project, ctx_for(project))
        assert any("+5V" in f.affected_nets for f in findings)

    def test_pcb004_unfilled_zone(self):
        from app.rules.pcb.unfilled_ground_zone import UnfilledGroundZoneRule

        project = self._board_project()
        findings = UnfilledGroundZoneRule().check(project, ctx_for(project))
        assert len(findings) == 1

    def test_pcb005_outside_board(self):
        from app.rules.pcb.components_outside_board import ComponentsOutsideBoardRule

        project = self._board_project()
        outside = comp("C9", "1uF")
        outside.position = Position(x=500, y=500)
        outside.layer = "F.Cu"
        project.components.append(outside)
        findings = ComponentsOutsideBoardRule().check(project, ctx_for(project))
        assert [f.affected_components[0] for f in findings] == ["C9"]

    def test_pcb008_min_width_uses_design_rules(self):
        from app.rules.pcb.min_trace_width import MinTraceWidthRule

        project = self._board_project()
        project.board.design_rules.min_trace_width_mm = 0.2
        findings = MinTraceWidthRule().check(project, ctx_for(project))
        assert any("+5V" in f.affected_nets for f in findings)
        assert all("GND" not in f.affected_nets for f in findings)

    def test_pcb009_min_via(self):
        from app.rules.pcb.min_via_size import MinViaSizeRule

        project = self._board_project()
        findings = MinViaSizeRule().check(project, ctx_for(project))
        assert len(findings) == 1

    def test_pcb010_high_current_needs_estimates(self):
        from app.rules.pcb.high_current_width import HighCurrentWidthRule, required_width_mm

        project = self._board_project()
        assert HighCurrentWidthRule().check(project, ctx_for(project)) == []
        ctx = ctx_for(project, {"net_current_ma": {"+5V": 2000.0}})
        findings = HighCurrentWidthRule().check(project, ctx)
        assert len(findings) == 1
        assert required_width_mm(2.0, 10.0, 0.035) > 0.5  # 2 A needs a wide trace

    def test_pcb011_decoupling_distance(self):
        from app.rules.pcb.decoupling_distance import DecouplingDistanceRule

        ic = comp("U1", "MCU", pins=[("1", PinType.POWER_IN)])
        ic.pins[0].name = "VCC"
        ic.position = Position(x=100, y=50)
        ic.layer = "F.Cu"
        cap = comp("C1", "100nF")
        cap.position = Position(x=150, y=50)
        cap.layer = "F.Cu"
        project = make_project([ic, cap], {"+3V3": [("U1", "1"), ("C1", "1")], "GND": [("C1", "2")]})
        project.board = Board(outline=[Point(x=0, y=0), Point(x=200, y=100)])
        findings = DecouplingDistanceRule().check(project, ctx_for(project))
        assert len(findings) == 1 and "50.0 mm" in findings[0].description
