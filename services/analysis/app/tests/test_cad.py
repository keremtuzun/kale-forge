"""The CAD tree has to be geometry, not decoration.

These tests check the properties a consumer relies on: every feature is positioned, every
structural member is a section you can order, derived dimensions follow from their inputs,
and the tree tracks the spec rather than drifting from it.
"""
from __future__ import annotations

import math

import pytest

from app.services.frc_cad import CAD_VERSION, build_cad, cut_list
from app.services.frc_parts import STRUCTURE
from app.services.robot_spec import build_robot_spec

STOCK_SECTIONS = {
    tuple(STRUCTURE[key]["section_in"]) for key in
    ("tube_2x1", "tube_1x1", "tube_2x2", "tube_1_5x1_5", "tube_1x1_thin", "tube_1_5x0_5")
}

PROMPTS = [
    "28x28 REEFSCAPE robot with a 3 stage belt-rigged cascade tower, a dual-roller "
    "over-bumper intake, a turreted dual flywheel hooded shooter, a double-jointed arm "
    "and a deep climb",
    "just an MK4i swerve module on a Kraken X60",
    "30x30 west-coast drivebase on six Krakens with a single flywheel backspin shooter",
    "rookie 2026 robot, ground-to-feeder tunnel intake, two-stage continuous elevator",
    "26x26 robot with a horizontal series-roller intake and a four-bar linkage arm, no climber",
]


@pytest.fixture(scope="module", params=PROMPTS)
def spec(request):
    return build_robot_spec(request.param, use_model=False)


def test_every_feature_is_positioned_and_named(spec):
    for assembly in spec["cad"]["assemblies"]:
        assert assembly["features"], f"{assembly['id']} has no features"
        for feature in assembly["features"]:
            assert feature.get("n"), f"unnamed feature in {assembly['id']}"
            at = feature["at"]
            assert len(at) == 3 and all(isinstance(v, (int, float)) for v in at)


def test_structural_members_are_orderable_stock(spec):
    """A section nobody sells is a section nobody can cut."""
    for assembly in spec["cad"]["assemblies"]:
        for feature in assembly["features"]:
            if feature["t"] != "tube":
                continue
            section = tuple(feature["sec"])
            assert section in STOCK_SECTIONS, f"{feature['n']}: {section} is not stock"
            assert feature["len"] > 0


def test_gear_pitch_diameter_follows_from_tooth_count(spec):
    for assembly in spec["cad"]["assemblies"]:
        for feature in assembly["features"]:
            if feature["t"] == "gear":
                assert feature["pd"] == pytest.approx(feature["teeth"] / feature["dp"], abs=1e-3)
            if feature["t"] == "pulley":
                expected = feature["teeth"] * feature["pitch_mm"] / math.pi / 25.4
                assert feature["pd"] == pytest.approx(expected, abs=1e-3)


def test_bearings_bore_smaller_than_outside_diameter(spec):
    for assembly in spec["cad"]["assemblies"]:
        for feature in assembly["features"]:
            if feature["t"] == "bearing":
                assert 0 < feature["bore"] < feature["od"]


def test_assemblies_track_the_spec(spec):
    """A subsystem that is not included must not appear, and one that is must."""
    ids = {assembly["id"] for assembly in spec["cad"]["assemblies"]}
    assert "chassis" in ids
    for key, asm_id in (("intake", "intake"), ("shooter", "shooter"), ("elevator", "elevator"),
                        ("manipulator", "manipulator"), ("climber", "climber")):
        assert (asm_id in ids) is bool(spec[key]["included"]), f"{asm_id} does not match the spec"


def test_links_carry_both_endpoints(spec):
    for assembly in spec["cad"]["assemblies"]:
        for feature in assembly["features"]:
            if feature["t"] in ("belt", "cable", "rope"):
                assert len(feature["to"]) == 3


def test_cut_list_covers_every_tube(spec):
    tubes = sum(1 for assembly in spec["cad"]["assemblies"]
                for feature in assembly["features"] if feature["t"] == "tube")
    assert sum(row["qty"] for row in cut_list(spec["cad"])) == tubes


def test_cad_is_deterministic():
    """Same prompt, same geometry — a design has to be reproducible to be reviewable."""
    prompt = "28x28 swerve robot with a coaxial slapdown intake and a cascade elevator"
    first = build_robot_spec(prompt, use_model=False)["cad"]
    second = build_robot_spec(prompt, use_model=False)["cad"]
    assert first == second


def test_build_cad_is_pure(spec):
    """Building the tree twice off one spec must not accumulate state."""
    again = build_cad(spec)
    assert again["feature_total"] == spec["cad"]["feature_total"]
    assert again["version"] == CAD_VERSION


def test_cut_list_is_entirely_orderable(spec):
    """Every member comes off the nesting ladder, so nothing should be flagged fabricated.

    This is the test that would have caught `cut_list` keeping a stale stock set after the
    ladder was added: the geometry was right and the cut list still told teams to source a
    section it had itself just stopped producing.
    """
    fabricated = [row for row in cut_list(spec["cad"]) if not row["stock"]]
    assert not fabricated, f"not orderable: {[r['section_in'] for r in fabricated]}"
