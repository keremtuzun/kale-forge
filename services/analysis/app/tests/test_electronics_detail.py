"""The control system is modelled as devices and wires, not as labelled bricks.

The bar these tests hold is the FRC control-system diagram: if a run is on the diagram and the
robot has both ends of it, the run has to exist in the geometry, at the gauge the channel's
breaker calls for.
"""
from __future__ import annotations

import pytest

from app.services.electronics_detail import device_bodies, harness_cables, wire_od
from app.services.frc_parts import ELECTRONICS
from app.services.robot_spec import build_robot_spec


@pytest.fixture(scope="module")
def robot() -> dict:
    return build_robot_spec(
        "27 inch swerve robot, over-bumper intake, turreted shooter, climber, pneumatic deploy",
        use_model=False, season="2026-rebuilt")


@pytest.fixture(scope="module")
def electrical(robot) -> dict:
    return next(a for a in robot["cad"]["assemblies"] if a["id"] == "electrical")


def _names(assembly: dict) -> list[str]:
    return [f["n"] for f in assembly["features"]]


# ── device detail ───────────────────────────────────────────────────────────
def test_devices_are_built_from_parts_not_one_box(electrical) -> None:
    """A device used to be a single extruded envelope. Now it has internals."""
    components = [f for f in electrical["features"] if f["t"] == "component"]
    assert len(components) > 60, "the control system should be dozens of bodies, not a dozen"


def test_the_rio_has_its_real_ports(electrical) -> None:
    names = " | ".join(_names(electrical))
    for port in ("DIO/PWM header", "analog header", "CAN terminal", "RSL port",
                 "ethernet", "USB host", "power connector"):
        assert f"roboRIO 2.0 {port}" in names, port


def test_the_distributor_has_one_breaker_per_channel() -> None:
    parts = device_bodies("pdh", "PDH", [0.0, 1.0, 0.0], {"channels": 20})
    breakers = [p for p in parts if "ch breaker" in p["n"]]
    assert len(breakers) == 20
    assert any("main lug red" in p["n"] for p in parts)
    assert any("CAN terminal" in p["n"] for p in parts)


def test_device_sub_parts_declare_their_parent(electrical) -> None:
    """Without `allow`, every connector reads as a component buried inside its own case."""
    subs = [f for f in electrical["features"]
            if f["t"] == "component" and f["n"].endswith(("terminal", "LED", "case"))]
    assert subs
    for feature in subs:
        if feature["n"].endswith("case"):
            continue
        assert feature.get("allow"), feature["n"]


def test_every_device_geometry_matches_its_catalog_envelope() -> None:
    for key in ("rio", "pdh", "battery", "radio", "rsl", "ph", "compressor", "main_breaker"):
        parts = device_bodies(key, ELECTRONICS[key]["name"], [0.0, 5.0, 0.0], {})
        case = next(p for p in parts if p["n"].endswith(("case", "body", "motor", "base")))
        envelope = ELECTRONICS[key]["envelope_in"]
        # The case is drawn at the catalog size (or, for the multi-body devices, inside it).
        assert case["size"][0] <= envelope[0] + 0.01, key
        assert case["size"][2] <= envelope[1] + 0.01, key


def test_an_unknown_device_still_gets_one_honest_box() -> None:
    parts = device_bodies("vrm", "VRM", [0.0, 1.0, 0.0], {})
    assert parts and parts[0]["n"] == "VRM case"


# ── the harness ─────────────────────────────────────────────────────────────
def test_the_battery_loop_is_six_awg(electrical) -> None:
    cables = {f["n"]: f for f in electrical["features"] if f["t"] == "cable"}
    for name in ("battery + to SB50", "SB50 to main breaker", "main breaker to distributor"):
        assert name in cables, name
        assert cables[name]["gauge"] == "6 AWG"


def test_the_can_bus_is_daisy_chained(electrical) -> None:
    can = [f for f in electrical["features"]
           if f["t"] == "cable" and f.get("role") == "can"]
    assert len(can) >= 3, "PDH, roboRIO and at least one more device sit on the bus"
    assert all(f["gauge"] == "20 AWG" for f in can)


def test_channel_wires_use_the_gauge_their_breaker_calls_for(robot, electrical) -> None:
    """A 40 A drive channel gets 10 AWG. This is the diagram's wire legend, enforced."""
    by_channel = {a["channel"]: a for a in robot["electrical"]["assignments"]}
    runs = [f for f in electrical["features"]
            if f["t"] == "cable" and f["n"].startswith("ch")]
    assert runs, "high-current channels should be wired to their motors"
    checked = 0
    for feature in runs:
        channel = int(feature["n"][2:].split()[0])
        expected = by_channel.get(channel)
        if not expected:
            continue
        assert feature["gauge"] == f"{expected['wire_awg']} AWG", feature["n"]
        checked += 1
    assert checked >= 4


def test_the_rsl_and_radio_are_fed(electrical) -> None:
    names = " | ".join(_names(electrical))
    assert "roboRIO to RSL" in names
    assert "radio power" in names
    assert "distributor to roboRIO" in names


def test_pneumatics_are_wired_when_the_robot_has_them(electrical) -> None:
    names = " | ".join(_names(electrical))
    assert "distributor to pneumatic hub" in names
    assert "hub to compressor" in names


def test_a_heavier_gauge_is_a_fatter_wire() -> None:
    assert wire_od(6) > wire_od(10) > wire_od(18) > wire_od(22)


def test_no_harness_without_the_devices_at_both_ends() -> None:
    """Half a control system draws half a harness, not dangling wires."""
    assert harness_cables({}, [], {}) == []
    only_battery = harness_cables({"battery": [0.0, 1.0, 0.0]}, [], {})
    assert only_battery == [], "a battery with nothing to connect to gets no leads"


# ── it has to survive the audits ────────────────────────────────────────────
def test_the_control_system_does_not_float(robot) -> None:
    integrity = robot["cad"]["integrity"]
    assert [f for f in integrity["floating"] if f.startswith("electrical/")] == []
    assert integrity["ok"] is True


def test_the_detailed_electronics_add_no_interference(robot) -> None:
    """Sub-parts sit on their own cases; none of that may read as a collision."""
    report = robot["cad"]["geometry"]
    electrical = [c for c in report["collisions"] + report["truncations"]
                  if "electrical/" in (c.get("a", "") + c.get("b", ""))]
    assert electrical == [], electrical
