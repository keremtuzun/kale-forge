"""An open request gets several real designs built and the best one kept.

The properties that matter: the alternatives are genuinely different machines, they are scored
on measurements taken from real builds, buildability outranks performance, and a request that
already named a dimension is never second-guessed.
"""
from __future__ import annotations

import pytest

from app.services.edit_explorer import candidates, explore, score
from app.services.robot_spec import build_robot_spec, plan_and_resolve

BASE = ("27 inch swerve robot with a dual roller over-bumper intake, spindexer, "
        "hooded shooter, elevator, climber")


@pytest.fixture(scope="module")
def base_spec() -> dict:
    return build_robot_spec(BASE, use_model=False, season="2026-rebuilt")


def _explore(edit: str) -> dict:
    _, plan = plan_and_resolve(f"{BASE}\n\nRevision request: {edit}", season="2026-rebuilt")
    return plan


# ── the candidates ───────────────────────────────────────────────────────────
def test_the_alternatives_are_different_machines(base_spec) -> None:
    options = candidates(base_spec, ["intake"])
    assert len(options) >= 2
    assert len({tuple(o["clauses"]) for o in options}) == len(options), "no duplicates"
    assert all(o["rationale"] for o in options), "each option says why it exists"


def test_no_alternatives_for_a_subsystem_the_robot_does_not_have() -> None:
    spec = build_robot_spec("27 inch swerve drivebase only", use_model=False,
                            season="2026-rebuilt")
    assert candidates(spec, ["intake", "shooter", "elevator"]) == []


# ── scoring ──────────────────────────────────────────────────────────────────
def test_an_unbuildable_option_loses_to_a_buildable_one(base_spec) -> None:
    """Buildability outranks performance, or the tool recommends things nobody can make."""
    good = {"intake": {"width_in": 24.0, "roller_count": 2},
            "geometry_status": {"blocking": 0}, "cad": {"integrity": {"ok": True}}}
    fast_but_broken = {"intake": {"width_in": 40.0, "roller_count": 4},
                       "geometry_status": {"blocking": 9}, "cad": {"integrity": {"ok": False}}}
    assert (score(good, "intake", "IMPROVE")["total"]
            > score(fast_but_broken, "intake", "IMPROVE")["total"])


def test_a_rule_violation_is_disqualifying(base_spec) -> None:
    legal = {"elevator": {"stages": 2, "max_height_in": 40.0},
             "geometry_status": {"blocking": 0}, "cad": {"integrity": {"ok": True}}}
    illegal = dict(legal, rule_report={"export_blocked": True})
    assert score(legal, "elevator", "IMPROVE")["total"] > score(illegal, "elevator",
                                                                "IMPROVE")["total"]


def test_speed_is_scored_on_recovery_not_muzzle_velocity() -> None:
    """"Faster" on a shooter means cycles: the wheel has to be back up before the next piece."""
    punchy = {"shooter": {"exit_velocity_fps": 60.0, "spinup_time_s": 1.8},
              "geometry_status": {"blocking": 0}, "cad": {"integrity": {"ok": True}}}
    snappy = {"shooter": {"exit_velocity_fps": 38.0, "spinup_time_s": 0.9},
              "geometry_status": {"blocking": 0}, "cad": {"integrity": {"ok": True}}}
    assert (score(snappy, "shooter", "INCREASE_SPEED")["total"]
            > score(punchy, "shooter", "INCREASE_SPEED")["total"])
    # ...and for a plain improve, raw performance is allowed to win again.
    assert (score(punchy, "shooter", "IMPROVE")["total"]
            > score(snappy, "shooter", "IMPROVE")["total"])


# ── when it runs ─────────────────────────────────────────────────────────────
def test_an_aggressive_open_request_explores() -> None:
    plan = _explore("make the intake much better")
    study = plan.get("exploration")
    assert study and study["explored"] >= 2
    assert study["chosen"] and study["why"]
    assert study["resolved_text"] in {o["text"] for o in study["options"]}


def test_the_winner_is_the_highest_scoring_option() -> None:
    study = _explore("make the shooter much faster")["exploration"]
    assert study["options"][0]["name"] == study["chosen"]
    assert study["options"] == sorted(study["options"], key=lambda o: -o["score"])


def test_a_moderate_request_does_not_pay_for_exploration() -> None:
    """Three extra builds is not the right price for "improve the intake"."""
    assert _explore("improve the intake").get("exploration") is None


def test_a_request_naming_a_dimension_is_never_second_guessed() -> None:
    assert _explore("make the intake 30 in wide").get("exploration") is None


def test_the_chosen_option_is_what_gets_built() -> None:
    resolved, plan = plan_and_resolve(
        f"{BASE}\n\nRevision request: make the intake much better", season="2026-rebuilt")
    # The explorer picks the architecture; the iterator then sizes it, so the final text is
    # the iterator's. What must hold is that the winning architecture is what got built.
    study = plan["exploration"]
    assert study["chosen"]
    after = build_robot_spec(resolved, use_model=False, season="2026-rebuilt")
    base = build_robot_spec(BASE, use_model=False, season="2026-rebuilt")
    assert (after["intake"]["width_in"] > base["intake"]["width_in"]
            or after["intake"]["roller_count"] > base["intake"]["roller_count"])


def test_exploration_never_blocks_a_build(base_spec) -> None:
    """It is an optimisation. A candidate that will not compile just loses."""
    def explodes(_text: str) -> dict:
        raise RuntimeError("compiler said no")

    plan = {"intent": "IMPROVE", "strength": "aggressive", "affected_subsystems": ["intake"]}
    assert explore(BASE, plan, season="2026-rebuilt", build=explodes, spec=base_spec) is None
