"""The CAD tree has to be geometry, not decoration.

These tests check the properties a consumer relies on: every feature is positioned, every
structural member is a section you can order, derived dimensions follow from their inputs,
and the tree tracks the spec rather than drifting from it.
"""
from __future__ import annotations

import math

import pytest

from app.services.frc_cad import (CAD_VERSION, LADDER_HEAVY, LADDER_LIGHT, build_cad,
                                  cut_list)
from app.services.frc_parts import STRUCTURE
from app.services.robot_spec import (ARM_TYPES, CLIMBER_TYPES, ELEVATOR_TYPES, INTAKE_TYPES,
                                     SHOOTER_TYPES, build_robot_spec)

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


MECHANISM_TYPES = [(kind, name) for kind, names in
                   (("intake", INTAKE_TYPES), ("shooter", SHOOTER_TYPES), ("arm", ARM_TYPES),
                    ("climber", CLIMBER_TYPES), ("elevator", ELEVATOR_TYPES))
                   for name in names]


@pytest.mark.parametrize("kind,mechanism", MECHANISM_TYPES)
def test_every_mechanism_type_is_built_from_stock(kind, mechanism):
    """PROMPTS does not reach every mechanism the spec can name, and the gap hid a bug.

    A stacked-barrel shooter is the only thing that builds a barrel side rail, and no prompt
    in PROMPTS builds one — so a hardcoded 0.8x0.6 rail sat behind the orderability test
    while it passed. Walking the enums is what closes that gap; the corpus already walks
    them, so anything the enums reach is something the model gets trained on.
    """
    spec = build_robot_spec(f"28x28 robot with a {mechanism}", use_model=False)
    for assembly in spec["cad"]["assemblies"]:
        for feature in assembly["features"]:
            if feature["t"] == "tube":
                assert tuple(feature["sec"]) in STOCK_SECTIONS, (
                    f"{kind} '{mechanism}' → {feature['n']}: "
                    f"{tuple(feature['sec'])} is not stock")


@pytest.mark.parametrize("name,ladder", [("heavy", LADDER_HEAVY), ("light", LADDER_LIGHT)])
def test_nesting_ladders_actually_nest(name, ladder):
    """A stage that will not pass through the tube outboard of it is not a telescoping stage.

    Checking the outside dimension against the *bore* of the section before it is the whole
    point of a ladder — a list of stock sections that happen to get smaller is not one.
    """
    for (outer, outer_wall), (inner, _) in zip(ladder, ladder[1:]):
        bore = (outer[0] - 2 * outer_wall, outer[1] - 2 * outer_wall)
        assert inner[0] < bore[0] and inner[1] < bore[1], (
            f"{name} ladder: {inner} does not fit the "
            f"{bore[0]:g}x{bore[1]:g} bore of {outer[0]:g}x{outer[1]:g}")


def test_cut_list_is_entirely_orderable(spec):
    """Every member comes off the nesting ladder, so every cut-list row is orderable.

    Checked against the catalog rather than against a flag the cut list sets on itself:
    `cut_list` no longer carries a stock/fabricated split, and a split it computed for its
    own rows could only ever agree with itself anyway.
    """
    unstocked = [row for row in cut_list(spec["cad"])
                 if tuple(row["section_in"]) not in STOCK_SECTIONS]
    assert not unstocked, f"not orderable: {[r['section_in'] for r in unstocked]}"
