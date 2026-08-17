"""An edit rebuilds what it has to and leaves the rest alone — and says which was which.

Two properties are locked down here. Repeated builds of an identical design must be free, or
one edit pays for the same robot three times. And an edit must not quietly redesign a
subsystem it was not aiming at; being re-stationed to make room is fine, being rewritten is
not, and the difference has to survive.
"""
from __future__ import annotations

import time

import pytest

from app.services.component_graph import (
    SUBSYSTEMS, build_signature, dirty_set, drift_report, subsystem_fingerprints)
from app.services.robot_spec import build_robot_spec, clear_build_cache, plan_and_resolve

BASE = ("27 inch swerve robot with a dual roller over-bumper intake, spindexer, "
        "hooded shooter, elevator, climber")


@pytest.fixture(scope="module")
def base_spec() -> dict:
    return build_robot_spec(BASE, use_model=False, season="2026-rebuilt")


def _apply(edit: str) -> tuple[dict, dict]:
    resolved, plan = plan_and_resolve(f"{BASE}\n\nRevision request: {edit}",
                                      season="2026-rebuilt")
    return build_robot_spec(resolved, use_model=False, season="2026-rebuilt"), plan


# ── the contract ─────────────────────────────────────────────────────────────
def test_a_subsystem_edit_may_not_touch_the_whole_robot() -> None:
    contract = dirty_set({"affected_subsystems": ["intake"], "scope": "subsystem"})
    assert contract["must_change"] == ["intake"]
    assert "hopper" in contract["may_change"], "the intake hands over to the hopper"
    assert "climber" in contract["must_not_change"]
    assert "manipulator" in contract["must_not_change"]


def test_an_architecture_edit_is_allowed_everything() -> None:
    contract = dirty_set({"affected_subsystems": ["shooter"], "scope": "architecture"})
    assert contract["must_not_change"] == []


def test_an_unscoped_edit_claims_nothing_it_cannot_justify() -> None:
    """No named target means no honest 'must not change' list."""
    assert dirty_set({})["must_not_change"] == []


# ── what actually moved ──────────────────────────────────────────────────────
def test_an_identical_rebuild_moves_nothing(base_spec) -> None:
    again = build_robot_spec(BASE, use_model=False, season="2026-rebuilt")
    report = drift_report(base_spec, again)
    assert report["changed"] == []
    assert len(report["preserved"]) == len(SUBSYSTEMS)


def test_an_intake_edit_does_not_redesign_the_climber(base_spec) -> None:
    """The headline property: unrelated subsystems must not be rewritten."""
    after, plan = _apply("make the intake much better")
    report = drift_report(base_spec, after, plan)
    assert "intake" in report["redesigned"]
    assert "climber" not in report["redesigned"]
    assert "manipulator" not in report["redesigned"]
    assert report["unintended"] == [], report["unintended"]


def test_being_re_stationed_is_not_counted_as_drift(base_spec) -> None:
    """The placement pass moves neighbours to make room. That is its job, not a fault."""
    after, plan = _apply("make the intake much better")
    report = drift_report(base_spec, after, plan)
    # Whatever was re-stationed must NOT also appear as redesigned — the two are disjoint.
    assert not (set(report["restationed"]) & set(report["redesigned"]))


def test_the_report_notices_an_edit_that_did_nothing(base_spec) -> None:
    """An objective whose subsystem never changed is a failed edit, and it should show."""
    report = drift_report(base_spec, base_spec,
                          {"affected_subsystems": ["intake"], "scope": "subsystem"})
    assert report["objective_untouched"] == ["intake"]


def test_fingerprints_see_a_moved_assembly(base_spec) -> None:
    """Parameters alone would call a re-stationed mechanism unchanged."""
    moved = build_robot_spec(BASE, use_model=False, season="2026-rebuilt")
    climber = next(a for a in moved["cad"]["assemblies"] if a["id"] == "climber")
    climber["origin"] = [climber["origin"][0] + 6.0, climber["origin"][1], climber["origin"][2]]
    assert (subsystem_fingerprints(base_spec)["climber"]
            != subsystem_fingerprints(moved)["climber"])


# ── build reuse ──────────────────────────────────────────────────────────────
def test_the_signature_separates_designs_that_differ() -> None:
    a = build_signature(BASE, "2026-rebuilt", False, "", True)
    assert a == build_signature(BASE, "2026-rebuilt", False, "", True)
    assert a != build_signature(BASE + " with a turret", "2026-rebuilt", False, "", True)
    assert a != build_signature(BASE, "2025-reefscape", False, "", True)
    assert a != build_signature(BASE, "2026-rebuilt", False, "", False)


def test_a_repeated_build_is_reused_not_recomputed() -> None:
    clear_build_cache()
    prompt = BASE + " and a second climber hook"
    start = time.perf_counter()
    first = build_robot_spec(prompt, use_model=False, season="2026-rebuilt")
    cold = time.perf_counter() - start
    start = time.perf_counter()
    second = build_robot_spec(prompt, use_model=False, season="2026-rebuilt")
    warm = time.perf_counter() - start
    assert first["cad"]["feature_total"] == second["cad"]["feature_total"]
    # Generous bound: the point is orders of magnitude, not a benchmark.
    assert warm < max(cold * 0.5, 0.5), (cold, warm)


def test_a_cached_spec_cannot_be_corrupted_by_its_caller() -> None:
    clear_build_cache()
    prompt = BASE + " and a wrist"
    first = build_robot_spec(prompt, use_model=False, season="2026-rebuilt")
    first["frame"]["width_in"] = 999
    first["cad"]["assemblies"].clear()
    second = build_robot_spec(prompt, use_model=False, season="2026-rebuilt")
    assert second["frame"]["width_in"] != 999
    assert second["cad"]["assemblies"]
