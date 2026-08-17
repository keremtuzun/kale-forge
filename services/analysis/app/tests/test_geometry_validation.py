"""Broken assemblies are caught; real mechanical interfaces are not.

Both halves matter equally. A checker that reports every bolted joint is turned off within a
week, and one that reports nothing lets a turret ship with its head inside the hopper. Each
fault case below is a minimal tree with exactly one thing wrong; each interface case is a
minimal tree with exactly one thing that LOOKS wrong and is not.
"""
from __future__ import annotations

import pytest

from app.services.geometry_repair import repair_geometry
from app.services.geometry_validation import (
    geometry_status, validate_geometry, TRUNCATION_FRACTION)
from app.services.robot_spec import build_robot_spec


def _cad(*assemblies: dict) -> dict:
    return {"units": "in", "assemblies": list(assemblies)}


def _asm(aid: str, features: list[dict], origin=(0.0, 0.0, 0.0), **extra) -> dict:
    return {"id": aid, "name": aid.title(), "kind": "mechanism", "mates": [],
            "origin": list(origin), "features": features, **extra}


def _tube(name, at, length=10.0, sec=(2.0, 1.0), rot=None, **kw):
    f = {"t": "tube", "n": name, "at": list(at), "sec": list(sec), "len": length, "wall": 0.1}
    if rot:
        f["rot"] = list(rot)
    f.update(kw)
    return f


def _part(kind, name, at, **kw):
    return {"t": kind, "n": name, "at": list(at), **kw}


# ── faults that must be caught ───────────────────────────────────────────────
def test_wheel_buried_in_a_chassis_rail_is_reported_as_truncated() -> None:
    """Half a wheel inside a rail is the 'cut off in the middle' fault, not a near miss."""
    cad = _cad(
        _asm("chassis", [_tube("front rail", (0, 0, 0), length=20.0, sec=(3.0, 3.0),
                               rot=[0, 90, 0])]),
        _asm("drivetrain", [_part("wheel", "drive wheel", (0, 0, 0), dia=1.6, len=1.0)]),
    )
    report = validate_geometry(cad)
    assert report["truncation_count"] >= 1
    hit = report["truncations"][0]
    assert "drive wheel" in hit["part"] and "front rail" in hit["inside"]
    assert hit["buried_fraction"] >= TRUNCATION_FRACTION
    assert report["valid"] is False


def test_motor_inside_a_structural_member_is_reported() -> None:
    cad = _cad(
        _asm("chassis", [_tube("left rail", (0, 0, 0), length=20.0, sec=(2.0, 2.0),
                               rot=[0, 90, 0])]),
        _asm("shooter", [_part("motor", "flywheel motor", (0, 0, 0), dia=2.4, len=4.0)]),
    )
    assert validate_geometry(cad)["valid"] is False


def test_two_frame_tubes_in_the_same_place_are_reported() -> None:
    """A duplicate solid is not a joint: one of them is entirely inside the other."""
    cad = _cad(
        _asm("chassis", [_tube("rail a", (0, 0, 0), length=20.0, rot=[0, 90, 0])]),
        _asm("subframe", [_tube("rail b", (0, 0, 0), length=20.0, rot=[0, 90, 0])]),
    )
    report = validate_geometry(cad)
    assert report["collision_count"] + report["truncation_count"] >= 1
    assert report["valid"] is False


def test_a_turret_that_clears_at_zero_and_strikes_at_one_eighty_is_caught() -> None:
    """The fault that started this: fine in the pose it is drawn in, through the tower later.

    Nothing here overlaps as drawn — the head is at +Z and the tower is at −Z — so a static
    audit passes it. Sampling the rotation is the only thing that finds it.
    """
    cad = _cad(
        _asm("chassis", [_tube("deck", (0, 0, 0), length=24.0, rot=[0, 90, 0])]),
        _asm("elevator", [_tube("tower", (0, 14.0, -9.0), length=24.0, sec=(2.0, 2.0))]),
        _asm("shooter",
             [_part("plate", "turret base", (0, 1.0, 0), size=[8.0, 0.25, 8.0]),
              _part("plate", "shooter head", (0, 14.0, 9.0), size=[6.0, 8.0, 6.0])],
             origin=(0.0, 0.0, 0.0), sweep={"x": 0.0, "z": 0.0, "from_y": 1.45}),
    )
    static = validate_geometry(cad, samples=1)
    assert static["motion_collision_count"] == 0, "the drawn pose really is clear"
    report = validate_geometry(cad)
    assert report["motion_collision_count"] >= 1
    hit = report["motion_collisions"][0]
    assert "shooter head" in (hit["a"] + hit["b"])
    assert "turret rotation" in hit["pose"]
    assert report["valid"] is False


def test_an_intake_that_collides_only_mid_deploy_is_caught() -> None:
    """Clear stowed and clear deployed, through the bumper at 45°."""
    cad = _cad(
        _asm("chassis", [_tube("bumper rail", (0, 6.0, -12.0), length=24.0, sec=(2.0, 2.0),
                               rot=[0, 90, 0])]),
        _asm("intake",
             [_part("plate", "pivot tower", (0, 0.5, 0), size=[3.0, 1.0, 3.0]),
              _part("plate", "intake arm", (0, 0.0, -14.0), size=[3.0, 0.5, 6.0])],
             articulation={"type": "pivot", "axis": "x", "at": [0, 0, 0],
                           "deg": [0.0, 90.0], "home": 0.0, "static": ["pivot tower"]}),
    )
    report = validate_geometry(cad)
    assert report["motion_collision_count"] >= 1
    assert "deploy arc" in report["motion_collisions"][0]["pose"]


def test_a_part_below_the_carpet_is_out_of_bounds() -> None:
    cad = _cad(
        _asm("drivetrain", [_part("wheel", "drive wheel left", (-10, 2.0, 0), dia=4.0, len=2.0),
                            _part("wheel", "drive wheel right", (10, 2.0, 0), dia=4.0, len=2.0)]),
        _asm("manipulator", [_part("plate", "gripper", (0, -4.0, 0), size=[3.0, 1.0, 3.0])]),
    )
    report = validate_geometry(cad)
    assert any(i["limit"] == "floor" and "gripper" in i["part"]
               for i in report["out_of_bounds"])


def test_a_component_far_outside_the_robot_is_reported_against_limits() -> None:
    cad = _cad(
        _asm("drivetrain", [_part("wheel", "drive wheel left", (-10, 2.0, 0), dia=4.0, len=2.0)]),
        _asm("climber", [_part("plate", "hook", (0, 90.0, 0), size=[2.0, 2.0, 2.0])]),
    )
    report = validate_geometry(cad, limits={"max_height_in": 48.0})
    assert any(i["limit"] == "height" for i in report["out_of_bounds"])


# ── interfaces that must NOT be reported ────────────────────────────────────
@pytest.mark.parametrize(("kind_a", "kind_b", "name_a", "name_b"), [
    ("shaft", "bearing", "flywheel shaft", "flywheel bearing"),
    ("shaft", "pulley", "feeder shaft", "feeder pulley"),
    ("motor", "gearbox", "intake motor", "intake gearbox"),
    ("gear", "gear", "driving gear", "driven gear"),
    ("bearing", "bearing", "inner race", "bearing block"),
    ("shaft", "wheel", "roller shaft", "compliant roller"),
])
def test_mating_interfaces_are_accepted(kind_a, kind_b, name_a, name_b) -> None:
    """Concentric by construction. Reporting these is how a checker gets switched off."""
    cad = _cad(_asm("shooter", [
        _part(kind_a, name_a, (0, 0, 0), dia=0.5, len=8.0, size=[0.5, 0.5, 8.0]),
        _part(kind_b, name_b, (0, 0, 0), dia=2.0, len=1.0, size=[2.0, 2.0, 1.0]),
    ]))
    report = validate_geometry(cad)
    assert report["collision_count"] == 0, report["collisions"]
    assert report["truncation_count"] == 0, report["truncations"]


def test_bolts_through_a_plate_are_never_reported() -> None:
    cad = _cad(_asm("chassis", [
        _part("plate", "bellypan", (0, 0, 0), size=[20.0, 0.19, 20.0]),
        _part("bolts", "pan bolt row", (0, 0, 0), dia=0.19, len=0.75),
    ]))
    assert validate_geometry(cad)["valid"] is True


def test_a_declared_nesting_is_accepted() -> None:
    """`allow` authorises one interface; without it the same tree is a truncation."""
    inner = _tube("climb stage 1", (0, 6.0, 0), length=18.0, sec=(1.5, 1.5), rot=[90, 0, 0])
    outer = _tube("climber tower", (0, 6.0, 0), length=20.0, sec=(2.0, 2.0), rot=[90, 0, 0])
    without = validate_geometry(_cad(_asm("climber", [outer, dict(inner)])))
    assert without["truncation_count"] >= 1

    inner["allow"] = ["climber tower"]
    with_allow = validate_geometry(_cad(_asm("climber", [outer, inner])))
    assert with_allow["truncation_count"] == 0
    assert with_allow["collision_count"] == 0


def test_declared_assembly_pairs_are_accepted() -> None:
    hopper = _asm("hopper", [_part("plate", "hopper wall", (0, 4.0, 0), size=[8.0, 8.0, 8.0])])
    intake = _asm("intake", [_part("wheel", "handoff roller", (0, 4.0, 0), dia=3.0, len=6.0)])
    assert validate_geometry(_cad(hopper, intake))["collision_count"] == 0


def test_a_butted_tube_joint_is_not_a_collision() -> None:
    """An end butted into a face overlaps by the section depth. That is a joint."""
    cad = _cad(_asm("elevator", [
        _tube("left upright", (-8.0, 12.0, 0), length=24.0, sec=(2.0, 1.0), rot=[90, 0, 0]),
        _tube("bottom tie", (0, 1.0, 0), length=16.0, sec=(1.0, 1.0), rot=[0, 90, 0]),
    ]))
    report = validate_geometry(cad)
    assert report["collision_count"] == 0, report["collisions"]


# ── repair ──────────────────────────────────────────────────────────────────
def test_repair_limits_a_turret_instead_of_leaving_it_broken() -> None:
    """The turret gets hard stops at the angles it actually clears — and keeps its parts."""
    cad = _cad(
        _asm("chassis", [_tube("deck", (0, 0, 0), length=24.0, rot=[0, 90, 0])]),
        _asm("elevator", [_tube("tower", (0, 14.0, -9.0), length=24.0, sec=(2.0, 2.0))]),
        _asm("shooter",
             [_part("plate", "turret base", (0, 1.0, 0), size=[8.0, 0.25, 8.0]),
              _part("plate", "shooter head", (0, 14.0, 9.0), size=[6.0, 8.0, 6.0])],
             sweep={"x": 0.0, "z": 0.0, "from_y": 1.45}),
    )
    before = {a["id"]: len(a["features"]) for a in cad["assemblies"]}
    repaired, status = repair_geometry(cad)
    assert status["passes"] >= 1
    assert status["issues_after"] < status["issues_before"]
    # Nothing was deleted to make the problem go away.
    assert {a["id"]: len(a["features"]) for a in repaired["assemblies"]} == before
    shooter = next(a for a in repaired["assemblies"] if a["id"] == "shooter")
    limited = shooter.get("sweep", {}).get("deg")
    moved = any(r["pass"] == "separate" for r in status["repairs"])
    assert limited is not None or moved, status["repairs"]


def test_repair_never_removes_features() -> None:
    spec = build_robot_spec(
        "27 inch REBUILT robot on MK4i swerve, dual-roller over-bumper intake into a "
        "spindexer, turreted hooded shooter, telescoping climber to L3",
        use_model=False, season="2026-rebuilt")
    before = sorted((a["id"], len(a["features"])) for a in spec["cad"]["assemblies"])
    repaired, status = repair_geometry(spec["cad"])
    assert sorted((a["id"], len(a["features"])) for a in repaired["assemblies"]) == before
    assert status["issues_after"] <= status["issues_before"]


def test_repair_leaves_a_clean_design_untouched() -> None:
    spec = build_robot_spec("swerve drivebase only", use_model=False, season="2026-rebuilt")
    repaired, status = repair_geometry(spec["cad"])
    assert status["status"] == "valid"
    assert status["passes"] == 0
    assert status["repairs"] == []


# ── the report ships, and the gate reads it ─────────────────────────────────
def test_every_design_ships_a_geometry_report() -> None:
    spec = build_robot_spec("28 inch robot with a hooded shooter and a hopper",
                            use_model=False, season="2026-rebuilt")
    report = spec["cad"]["geometry"]
    assert report["version"] == "kale-geometry-2.0"
    assert report["bodies"] > 80
    assert "collisions" in report and "motion_envelopes" in report
    status = spec["geometry_status"]
    assert status["status"] in {"valid", "invalid"}
    assert status["exportable"] is (status["status"] == "valid")


def test_a_turret_design_publishes_its_motion_envelope() -> None:
    spec = build_robot_spec("turreted hooded shooter with a spindexer hopper",
                            use_model=False, season="2026-rebuilt")
    envelopes = spec["cad"]["geometry"]["motion_envelopes"]
    turret = next((e for e in envelopes if e["kind"] == "turret"), None)
    assert turret is not None
    assert turret["samples"] >= 11 and turret["bodies"] > 5


def test_the_export_gate_blocks_cut_off_geometry() -> None:
    cad = _cad(
        _asm("chassis", [_tube("front rail", (0, 0, 0), length=20.0, sec=(3.0, 3.0),
                               rot=[0, 90, 0])]),
        _asm("drivetrain", [_part("wheel", "drive wheel", (0, 0, 0), dia=1.6, len=1.0)]),
    )
    status = geometry_status(cad)
    assert status["exportable"] is False
    assert "truncations" in status["blocking_classes"]


# ── the bumper is not negotiable space ──────────────────────────────────────
def test_a_part_inside_the_bumper_is_caught_at_any_depth() -> None:
    """The general 0.60 in tolerance is wrong here and this is why.

    A thin plate embedded in the bumper overlaps deepest along its OWN thickness, so it never
    reaches the mechanism-packaging threshold — which is how a pivot tower sat 0.7 in inside
    the front bumper while the audit called the robot clean.
    """
    cad = _cad(
        _asm("chassis", [_part("plate", "front bumper plywood", (0, 2.5, -14.0),
                               size=[24.0, 5.0, 0.75])]),
        # Poking in by well under the general tolerance, which is the whole point.
        _asm("intake", [_part("plate", "pivot tower left", (-8.0, 3.0, -12.5),
                              size=[0.19, 4.0, 2.6])]),
    )
    report = validate_geometry(cad)
    assert report["bumper_intrusion_count"] >= 1
    hit = report["bumper_intrusions"][0]
    assert "pivot tower left" in hit["b"] and "bumper" in hit["a"]
    # Shallow enough that the mechanism-packaging threshold would have let it through.
    assert hit["penetration_in"] < 0.60
    assert report["collision_count"] == 0


def test_clearing_the_bumper_passes() -> None:
    cad = _cad(
        _asm("chassis", [_part("plate", "front bumper plywood", (0, 2.5, -14.0),
                               size=[24.0, 5.0, 0.75])]),
        _asm("intake", [_part("plate", "pivot tower left", (-8.0, 3.0, -12.0),
                              size=[0.19, 4.0, 2.6])]),
    )
    assert validate_geometry(cad)["bumper_intrusion_count"] == 0


def test_the_bumper_is_not_reported_against_its_own_chassis() -> None:
    """Hangers and brackets bolt to the rails; reporting that is reporting the mounting."""
    cad = _cad(_asm("chassis", [
        _part("plate", "front bumper plywood", (0, 2.5, -14.0), size=[24.0, 5.0, 0.75]),
        _part("plate", "front bumper hanger", (0, 2.5, -13.6), size=[3.0, 2.0, 1.5]),
    ]))
    assert validate_geometry(cad)["bumper_intrusion_count"] == 0
