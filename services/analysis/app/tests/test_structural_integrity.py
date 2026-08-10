"""Nothing floats: every body touches structure, every assembly reaches the chassis.

The deterministic compiler must produce fully-connected robots for ANY prompt. These cases
sweep the mechanism matrix (each mechanism alone and in combination, both drivetrains, all
seasons, several frames); the audit itself lives in cad_contract.structural_report and runs
against conservative world-space boxes built from the shared geometry conventions.
"""
from __future__ import annotations

import pytest

from app.services.cad_contract import structural_errors, structural_report
from app.services.robot_spec import build_robot_spec

CASES = [
    ("27 inch REBUILT robot on MK4i swerve, dual-roller over-bumper intake into a spindexer, "
     "turreted hooded shooter, telescoping climber to L3", "2026-rebuilt"),
    ("28 inch REEFSCAPE robot, three-stage cascade elevator to L4, coaxial slapdown intake, "
     "wristed carriage arm, deep-cage climb", "2025-reefscape"),
    ("MK5i swerve on Krakens at R2 with a fast dual-roller over-bumper intake and a deep-cage "
     "climber", "offseason"),
    ("west coast drive robot with a hooded shooter and a hopper", "2026-rebuilt"),
    ("compact 24 inch robot, single stage elevator, claw arm with wrist", "2025-reefscape"),
    ("turreted double-flywheel shooter with a 12 inch barrel, spindexer hopper, climber",
     "2026-rebuilt"),
    ("west coast drivetrain, arm with two segments and compliant gripper, climber", "offseason"),
    ("swerve drivebase only", "2026-rebuilt"),
    ("everything robot: intake, hopper, shooter, elevator, arm, climber", "2026-rebuilt"),
    ("tank drive with 8 motors and a kicker-fed hooded shooter", "2025-reefscape"),
    ("30x26 robot on west coast drive with a rope-rigged elevator", "offseason"),
]


@pytest.mark.parametrize(("prompt", "season"), CASES, ids=[c[0][:44] for c in CASES])
def test_nothing_floats(prompt: str, season: str) -> None:
    spec = build_robot_spec(prompt, use_model=False, season=season)
    integrity = spec["cad"]["integrity"]
    assert integrity["floating"] == [], integrity["floating"]
    assert integrity["unreached_assemblies"] == [], integrity["unreached_assemblies"]
    assert integrity["ok"] is True
    # The report is real, not decorative: a robot has hundreds of bodies and far more contacts.
    assert integrity["bodies"] > 80
    assert integrity["contacts"] > integrity["bodies"]


def test_report_ships_with_the_tree() -> None:
    spec = build_robot_spec("27 inch swerve robot with a hooded shooter", use_model=False,
                            season="2026-rebuilt")
    report = spec["cad"]["integrity"]
    assert report["version"] == "kale-integrity-1.0"
    assert report["tolerance_in"] == pytest.approx(0.08)
    # Recomputing from the shipped tree agrees with what shipped.
    fresh = structural_report(spec["cad"])
    assert fresh["floating"] == report["floating"]
    assert fresh["ok"] is report["ok"]


def test_floating_body_is_named() -> None:
    """The audit catches a part hung in air, and names it assembly/part."""
    spec = build_robot_spec("swerve drivebase only", use_model=False, season="2026-rebuilt")
    cad = spec["cad"]
    cad["assemblies"][0]["features"].append(
        {"t": "plate", "n": "orphan bracket", "at": [0.0, 40.0, 0.0],
         "size": [2.0, 0.19, 2.0]})
    errors = structural_errors(cad)
    assert any("orphan bracket" in error for error in errors)


def test_unreached_assembly_is_named() -> None:
    """An assembly with no contact path back to the chassis is flagged as unreached."""
    spec = build_robot_spec("swerve drivebase only", use_model=False, season="2026-rebuilt")
    cad = spec["cad"]
    cad["assemblies"].append({
        "id": "skyhook", "name": "Skyhook", "kind": "mechanism",
        "origin": [0.0, 60.0, 0.0], "mates": [], "note": "",
        "features": [
            {"t": "plate", "n": "sky plate", "at": [0.0, 0.0, 0.0], "size": [4.0, 0.25, 4.0]},
            {"t": "motor", "n": "sky motor", "at": [0.0, 1.0, 0.0], "dia": 2.4, "len": 2.8},
        ]})
    report = structural_report(cad)
    assert "skyhook" in report["unreached_assemblies"]
    assert report["ok"] is False
