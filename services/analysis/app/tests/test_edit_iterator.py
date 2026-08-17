"""An edit that only compiles is not a finished edit.

The loop measures each attempt on two separate numbers — does it serve the objective, and can
it be built — and picks the next attempt from which one failed. What these lock down is that
it never ships a regression and never claims success it did not earn.
"""
from __future__ import annotations

import pytest

from app.services.edit_iterator import MEANINGFUL_GAIN, _next_strength, _weaker, iterate
from app.services.robot_spec import build_robot_spec, plan_and_resolve

BASE = ("27 inch swerve robot with a dual roller over-bumper intake, spindexer, "
        "hooded shooter, elevator, climber")


@pytest.fixture(scope="module")
def base_spec() -> dict:
    return build_robot_spec(BASE, use_model=False, season="2026-rebuilt")


def _run(edit: str) -> tuple[dict, dict]:
    resolved, plan = plan_and_resolve(f"{BASE}\n\nRevision request: {edit}",
                                      season="2026-rebuilt")
    return build_robot_spec(resolved, use_model=False, season="2026-rebuilt"), plan


# ── the ladder ───────────────────────────────────────────────────────────────
def test_escalation_walks_up_and_stops_at_the_top() -> None:
    assert _next_strength("minimal") == "moderate"
    assert _next_strength("moderate") == "aggressive"
    assert _next_strength("aggressive") == "redesign"
    assert _next_strength("redesign") is None


def test_backing_off_never_goes_below_where_the_design_started(base_spec) -> None:
    """Scaling freely let the ladder overshoot the original and 'improve' a narrower intake."""
    width = base_spec["intake"]["width_in"]
    weaker = _weaker([f"{width * 1.2:g} in intake width"], 0.5, base_spec)
    value = float(weaker[0].split()[0])
    assert value >= width, weaker


def test_backing_off_keeps_the_architecture(base_spec) -> None:
    """A collision is evidence about size, not about whether a third roller was right."""
    weaker = _weaker(["26 in intake width", "three rollers"], 0.88, base_spec)
    assert "three rollers" in weaker


# ── the loop ─────────────────────────────────────────────────────────────────
def test_an_edit_iterates_and_records_every_attempt() -> None:
    _, plan = _run("make the intake much better")
    loop = plan["iteration"]
    assert loop["iterations"] >= 1
    assert all(a["verdict"] for a in loop["attempts"])
    assert all("performance" in a and "validity" in a for a in loop["attempts"])


def test_an_attempt_that_breaks_geometry_is_retried_smaller() -> None:
    _, plan = _run("make the intake much better")
    attempts = plan["iteration"]["attempts"]
    broke = [i for i, a in enumerate(attempts) if a["verdict"] == "broke geometry"]
    if broke and broke[0] + 1 < len(attempts):
        first, second = attempts[broke[0]], attempts[broke[0] + 1]
        assert second["validity"] >= first["validity"], "the retry must not break it further"


def test_the_loop_never_ships_a_worse_design(base_spec) -> None:
    """The headline property: no attempt may leave the objective worse than it started."""
    for edit in ("improve the intake", "make the intake much better"):
        after, _ = _run(edit)
        assert after["intake"]["width_in"] >= base_spec["intake"]["width_in"], edit
        assert after["intake"]["roller_count"] >= base_spec["intake"]["roller_count"], edit


def test_a_blocked_objective_is_reported_not_faked(base_spec) -> None:
    """If nothing can improve it without breaking it, say so and change nothing."""
    after, plan = _run("improve the intake")
    loop = plan["iteration"]
    if not loop["converged"]:
        assert "no improvement" in loop["outcome"] or "broke" in loop["outcome"]
        assert after["intake"]["width_in"] == base_spec["intake"]["width_in"]


def test_convergence_requires_a_meaningful_gain() -> None:
    _, plan = _run("make the intake much better")
    loop = plan["iteration"]
    if loop["converged"]:
        assert loop["gain"] >= MEANINGFUL_GAIN, loop["gain"]


def test_a_validity_regression_is_never_accepted(base_spec) -> None:
    after, _ = _run("make the intake much better")
    assert (after["geometry_status"]["blocking"]
            <= base_spec["geometry_status"]["blocking"]), "an edit must not add blocking faults"


# ── it stays out of the way ──────────────────────────────────────────────────
def test_an_explicit_dimension_is_not_iterated() -> None:
    _, plan = _run("make the intake 30 in wide")
    assert plan.get("iteration") is None


def test_iteration_never_blocks_a_build(base_spec) -> None:
    def explodes(_text: str) -> dict:
        raise RuntimeError("compiler said no")

    plan = {"intent": "IMPROVE", "strength": "moderate", "affected_subsystems": ["intake"],
            "changes": ["25 in intake width"]}
    assert iterate(BASE, plan, season="2026-rebuilt", build=explodes, before=base_spec) is None


def test_nothing_to_iterate_on_returns_none(base_spec) -> None:
    assert iterate(BASE, {"intent": "IMPROVE", "strength": "moderate",
                          "affected_subsystems": [], "changes": []},
                   season="2026-rebuilt", build=lambda t: base_spec,
                   before=base_spec) is None
