"""An edit is an engineering objective, and it has to actually move the design.

The two failures these lock down are the ones users hit: a vague objective that changed
nothing while the studio reported success, and a follow-up ("a little more") with no referent.
"""
from __future__ import annotations

import pytest

from app.services.edit_planner import edit_summary, plan_edit
from app.services.robot_spec import build_robot_spec, plan_and_resolve

BASE = ("27 inch swerve robot with a dual roller over-bumper intake, spindexer, "
        "hooded shooter, elevator, climber")


def _edit(text: str, *, previous: list[str] | None = None) -> str:
    chain = list(previous or []) + [text]
    return BASE + "".join(f"\n\nRevision request: {c}" for c in chain)


@pytest.fixture(scope="module")
def base_spec() -> dict:
    return build_robot_spec(BASE, use_model=False, season="2026-rebuilt")


# ── classification ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(("text", "intent", "strength"), [
    ("make this slightly wider", "RESIZE", "minimal"),
    ("improve the intake", "IMPROVE", "moderate"),
    ("make the intake much better", "IMPROVE", "aggressive"),
    ("completely redesign the intake", "REDESIGN", "redesign"),
    ("the shooter jams", "INCREASE_RELIABILITY", "moderate"),
    ("make the robot faster at scoring", "INCREASE_SPEED", "moderate"),
    ("this is too heavy", "LIGHTEN", "moderate"),
])
def test_intent_and_strength_are_read_from_the_request(text, intent, strength) -> None:
    plan = plan_edit(text)
    assert plan["intent"] == intent
    assert plan["strength"] == strength


@pytest.mark.parametrize(("text", "scope"), [
    ("make this wheel 4 inches", "parameter"),
    ("improve the intake", "subsystem"),
    ("make the intake feed more reliably into the shooter", "multiSubsystem"),
    ("completely redesign the intake", "architecture"),
])
def test_scope_is_not_forced_down_to_parameter(text, scope) -> None:
    assert plan_edit(text)["scope"] == scope


def test_the_affected_subsystem_is_identified() -> None:
    plan = plan_edit("the shooter jams when the hopper feeds it")
    assert set(plan["affected_subsystems"]) >= {"shooter", "hopper"}


# ── the objective becomes changes the engine can make ────────────────────────
def test_a_vague_objective_is_expanded_into_real_parameters(base_spec) -> None:
    plan = plan_edit("make the intake much better", spec=base_spec)
    assert plan["changes"], "an aggressive improve must name concrete changes"
    assert plan["resolved_text"] != plan["objective"]
    assert any("intake width" in c for c in plan["changes"])


def test_a_stronger_request_moves_more_than_a_weaker_one(base_spec) -> None:
    mild = plan_edit("improve the intake", spec=base_spec)
    hard = plan_edit("make the intake much better", spec=base_spec)
    assert len(hard["changes"]) >= len(mild["changes"])


def test_an_explicit_dimension_is_passed_through_untouched(base_spec) -> None:
    """A user who asked for 30 in gets 30 in, not an interpretation of it."""
    plan = plan_edit("make the intake 30 in wide", spec=base_spec)
    assert plan["changes"] == []
    assert plan["resolved_text"] == "make the intake 30 in wide"


def test_a_vague_edit_actually_changes_the_robot(base_spec) -> None:
    """The headline regression: this used to rebuild an identical robot."""
    resolved, plan = plan_and_resolve(_edit("make the intake much better"),
                                      season="2026-rebuilt")
    after = build_robot_spec(resolved, use_model=False, season="2026-rebuilt")
    summary = edit_summary(base_spec, after)
    assert summary["applied"], summary
    # Improved on SOME axis, and worse on none. Asserting the width specifically was too
    # narrow a claim: the iterator measures each attempt and, when widening the intake breaks
    # the geometry, it earns the objective with a third roller instead. Which lever moves is
    # the engine's decision; that one moved and nothing went backwards is the contract.
    assert (after["intake"]["width_in"] > base_spec["intake"]["width_in"]
            or after["intake"]["roller_count"] > base_spec["intake"]["roller_count"])
    assert after["intake"]["width_in"] >= base_spec["intake"]["width_in"]
    assert after["intake"]["roller_count"] >= base_spec["intake"]["roller_count"]


def test_widening_the_intake_does_not_shrink_the_frame(base_spec) -> None:
    """"26 in wide intake" is read by the FRAME selector; the phrasing has to dodge it."""
    resolved, _ = plan_and_resolve(_edit("make the intake much better"), season="2026-rebuilt")
    after = build_robot_spec(resolved, use_model=False, season="2026-rebuilt")
    assert after["frame"]["width_in"] == base_spec["frame"]["width_in"]


# ── conversational memory ────────────────────────────────────────────────────
def test_a_follow_up_refers_to_the_previous_edit() -> None:
    resolved, plan = plan_and_resolve(
        _edit("a little more", previous=["make the intake 30 in wide"]), season="2026-rebuilt")
    assert "33" in plan["resolved_text"], plan["resolved_text"]
    assert plan["notes"]


def test_an_absolute_relative_follow_up_is_resolved() -> None:
    _, plan = plan_and_resolve(
        _edit("actually make it 2 in smaller than that",
              previous=["make the intake 30 in wide"]), season="2026-rebuilt")
    assert "28" in plan["resolved_text"], plan["resolved_text"]


def test_a_follow_up_with_no_history_is_left_alone() -> None:
    plan = plan_edit("a little more", previous=[])
    assert plan["resolved_text"] == "a little more"


# ── feedback ─────────────────────────────────────────────────────────────────
def test_a_no_op_edit_is_reported_as_a_no_op(base_spec) -> None:
    summary = edit_summary(base_spec, base_spec)
    assert summary["applied"] is False
    assert "did not move the design" in summary["note"]


def test_the_summary_names_what_changed(base_spec) -> None:
    resolved, _ = plan_and_resolve(_edit("make the intake much better"), season="2026-rebuilt")
    after = build_robot_spec(resolved, use_model=False, season="2026-rebuilt")
    summary = edit_summary(base_spec, after)
    assert any(c.startswith("intake ") for c in summary["changed"]), summary["changed"]
    assert summary["validation"]["status"] in {"valid", "invalid"}


def test_every_edit_carries_success_criteria() -> None:
    plan = plan_edit("improve the intake")
    assert plan["success_criteria"]
    assert any("interference" in c for c in plan["success_criteria"])


def test_an_empty_edit_is_harmless() -> None:
    plan = plan_edit("")
    assert plan["resolved_text"] == "" and plan["changes"] == []


# ── the wheel selector reads both word orders ───────────────────────────────
@pytest.mark.parametrize("phrasing", [
    "make the wheels 3 inches",
    "3 inch wheels",
    "wheels at 3 in",
    "make the wheels 3 in",
])
def test_a_wheel_edit_lands_whichever_way_it_is_phrased(phrasing) -> None:
    """Only "3 inch wheels" used to match, so the natural phrasing changed nothing at all."""
    spec = build_robot_spec(
        "27x27 west coast robot with a hooded shooter"
        f"\n\nRevision request: {phrasing}", use_model=False, season="2026-rebuilt")
    assert spec["drivetrain"]["wheel_diameter_in"] == 3.0, phrasing


@pytest.mark.parametrize("prompt", [
    "swerve robot with compliant wheels and a 27 in frame",
    "west coast robot with colson wheels, 30 in long frame",
    "robot with wheels; 28 in wide",
])
def test_a_frame_dimension_near_the_word_wheels_is_not_a_wheel_size(prompt) -> None:
    """The reverse pattern has to be tight: a loose gap reads "wheels and a 27 in frame"
    as a 27 in wheel, which is how a robot ends up on monster-truck tyres."""
    spec = build_robot_spec(prompt, use_model=False, season="2026-rebuilt")
    assert spec["drivetrain"]["wheel_diameter_in"] <= 8.0, prompt
