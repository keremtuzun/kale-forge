"""Phase 9 regressions: tooth outlines, wrapped belts, measured meshes, honest fidelity.

The original defects: gears/sprockets/pulleys shipped as smooth discs in the STEP,
belts and chains as straight bars ending in mid-air, gear centres eyeballed so far
apart the teeth could never touch, and no statement anywhere of how real any of it
was. These tests pin the repairs at the data layer (enrichment + validation), the
outline math the STEP worker extrudes, and the copy that describes it all.
"""
from __future__ import annotations

import math
from pathlib import Path

from app.services.cad_contract import fidelity_report, transmission_report
from app.services.robot_spec import build_robot_spec
from app.services.transmission_geom import (
    BELT_THICKNESS_IN,
    CHAIN_THICKNESS_IN,
    GEAR_ADDENDUM_F,
    GEAR_DEDENDUM_F,
    belt_loop,
    gear_outline,
    pulley_outline,
    resolve_wrap,
    sprocket_outline,
    transmission_issues,
)

REPO = Path(__file__).resolve().parents[4]
SITE = REPO / "apps" / "site"

PROMPTS = ["fuel cycler with elevator and vision", "swerve robot with arm intake",
           "defense robot with climber", "shooter robot with hopper",
           "tank drive robot with intake and shooter", "robot with elevator and gripper"]
SEASONS = ["2026-rebuilt", "2025-reefscape", "offseason"]


def _radii(pts):
    return [math.hypot(x, y) for x, y in pts]


# ── outlines ──────────────────────────────────────────────────────────────────

def test_gear_outline_has_teeth_at_the_true_pitch_diameter():
    teeth, pd = 40, 2.0
    pts = gear_outline(teeth, pd)
    assert len(pts) == teeth * 7            # 7 vertices per tooth, one closed loop
    radii = _radii(pts)
    r = pd / 2
    assert max(radii) > r * 1.05            # tips stand proud of the pitch circle
    assert min(radii) < r * 0.95            # roots cut below it — this is not a disc
    assert abs(max(radii) - r * (1 + GEAR_ADDENDUM_F)) < 1e-6
    assert abs(min(radii) - r * (1 - GEAR_DEDENDUM_F)) < 1e-6


def test_sprocket_and_pulley_outlines_are_toothed_not_smooth():
    for pts, r in ((sprocket_outline(22, 22 * 0.25 / math.pi), 22 * 0.25 / math.pi / 2),
                   (pulley_outline(18, 18 * 5 / math.pi / 25.4), 18 * 5 / math.pi / 25.4 / 2)):
        radii = _radii(pts)
        assert max(radii) - min(radii) > 0.02, "outline is a smooth circle"
        assert min(radii) > 0
        assert all(rr < r * 1.3 for rr in radii)


# ── belt loops ────────────────────────────────────────────────────────────────

def test_belt_loop_wraps_both_circles():
    out, inner = belt_loop(1.0, 0.5, 4.0, BELT_THICKNESS_IN)
    half = BELT_THICKNESS_IN / 2
    # points near circle 1 sit at r1±half, points near circle 2 at r2±half
    far = [p for p in out if p[0] < 0.5]
    assert far, "no wrap around the first circle"
    for x, y in far:
        assert abs(math.hypot(x, y) - (1.0 + half)) < 1e-6
    near2 = [p for p in inner if p[0] > 3.4]
    assert near2
    for x, y in near2:
        assert abs(math.hypot(x - 4.0, y) - (0.5 - half)) < 1e-6


def test_belt_loop_refuses_degenerate_geometry():
    assert belt_loop(1.0, 0.5, 0.0, 0.12) is None          # coincident centres
    assert belt_loop(2.0, 0.5, 1.0, 0.12) is None          # one circle inside the other


def test_equal_pulleys_give_parallel_runs():
    out, _ = belt_loop(0.75, 0.75, 3.0, 0.12)
    # with r1 == r2 the tangent angle is 90°: tangent points sit straight above/below
    xs = [p for p in out if abs(p[0]) < 1e-6]
    assert xs, "tangent points not vertical for equal radii"


# ── enrichment: every generated belt knows what it wraps ─────────────────────

def test_every_belt_in_every_build_is_stamped_with_wrap_radii():
    for season in SEASONS:
        for prompt in PROMPTS:
            spec = build_robot_spec(prompt, season=season)
            for asm in spec["cad"]["assemblies"]:
                for f in asm["features"]:
                    if f.get("t") != "belt":
                        continue
                    label = f"{season}/{prompt}: {asm['id']}/{f.get('n')}"
                    assert f.get("r1") is not None, f"unresolved belt start: {label}"
                    assert f.get("r2") is not None, f"unresolved belt end: {label}"
                    assert f.get("axis"), f"no wrap axis: {label}"
                    assert f.get("thick") in (BELT_THICKNESS_IN, CHAIN_THICKNESS_IN)


def test_chain_and_belt_get_their_own_thickness():
    feats = [
        {"t": "sprocket", "n": "a", "at": [0, 0, 0], "teeth": 18, "pd": 1.43},
        {"t": "sprocket", "n": "b", "at": [0, 4, 0], "teeth": 18, "pd": 1.43},
        {"t": "belt", "n": "run", "at": [0, 0, 0], "to": [0, 4, 0], "w": 0.3, "kind": "#25 chain"},
    ]
    r1, r2, axis = resolve_wrap(feats, feats[2])
    assert r1 == r2 == 1.43 / 2
    assert axis == (0.0, 1.0, 0.0)


# ── measured meshes ───────────────────────────────────────────────────────────

def _gear(name, teeth, at, dp=20):
    return {"t": "gear", "n": name, "at": at, "teeth": teeth, "dp": dp,
            "pd": teeth / dp, "rot": [0, 0, 90]}


def test_gear_gap_and_interference_are_both_reported():
    # pitch radii 0.9 + 0.6 = 1.5: meshing demands exactly 1.5 in
    bad_gap = [{"id": "m", "features": [_gear("a", 36, [0, 0, 0]), _gear("b", 24, [0, 1.8, 0])]}]
    overlap = [{"id": "m", "features": [_gear("a", 36, [0, 0, 0]), _gear("b", 24, [0, 1.2, 0])]}]
    exact = [{"id": "m", "features": [_gear("a", 36, [0, 0, 0]), _gear("b", 24, [0, 1.5, 0])]}]
    assert any(i["type"] == "gear_mesh" and "do not mesh" in i["detail"]
               for i in transmission_issues(bad_gap))
    assert any(i["type"] == "gear_mesh" and "interference" in i["detail"]
               for i in transmission_issues(overlap))
    assert transmission_issues(exact) == []


def test_distant_gears_and_stacked_stages_are_not_false_positives():
    separate = [{"id": "m", "features": [_gear("a", 36, [0, 0, 0]), _gear("b", 24, [0, 6.0, 0])]}]
    stacked = [{"id": "m", "features": [_gear("a", 36, [0, 0, 0]), _gear("b", 24, [1.0, 0.4, 0])]}]
    assert transmission_issues(separate) == []
    assert transmission_issues(stacked) == []       # 1.0 in apart along their shared axis


def test_belt_ending_in_mid_air_is_reported():
    asm = [{"id": "m", "features": [
        {"t": "pulley", "n": "drive", "at": [0, 0, 0], "teeth": 18, "pd": 1.2},
        {"t": "belt", "n": "lost run", "at": [0, 0, 0], "to": [0, 5, 0], "w": 0.3},
    ]}]
    issues = transmission_issues(asm)
    assert any(i["type"] == "belt_endpoint" and "nothing to wrap" in i["detail"] for i in issues)


def test_no_generated_design_has_transmission_issues():
    """The headline regression: the sweep that once found floating idlers, a 3.6 in
    gear inside its neighbours, one-sided elevator rigging and a feeder belt ending
    in air must stay clean for every prompt × season combination."""
    for season in SEASONS:
        for prompt in PROMPTS:
            spec = build_robot_spec(prompt, season=season)
            issues = spec["transmission"]["issues"]
            assert issues == [], f"{season}/{prompt}: {[i['detail'] for i in issues]}"


# ── fidelity is stated, never assumed ────────────────────────────────────────

def test_fidelity_report_never_summarises_by_the_best_part():
    spec = build_robot_spec("swerve robot with arm intake", season="2026-rebuilt")
    fid = spec["fidelity"]
    assert fid["levels"]["detailed"] > 0
    assert fid["levels"]["envelope"] > 0            # motors and gearboxes exist
    assert fid["overall"] == "mixed"                # so the one-word answer is not "detailed"
    assert "envelopes" in fid["statement"]


def test_single_level_cad_reports_that_level():
    assert fidelity_report({"assemblies": [{"features": [
        {"t": "motor", "n": "m", "at": [0, 0, 0]}]}]})["overall"] == "envelope"
    assert fidelity_report({"assemblies": []})["overall"] == "layout"


def test_transmission_report_is_attached_and_clean():
    spec = build_robot_spec("robot with elevator and gripper", season="2026-rebuilt")
    assert spec["transmission"]["ok"] is True
    assert "pitch radii" in spec["transmission"]["checked"]


# ── renderers and copy stay in step with the data ────────────────────────────

def test_viewer_wraps_belts_and_keeps_a_visible_fallback():
    js = (SITE / "studio-viewer.js").read_text(encoding="utf-8")
    assert "f.r1 && f.r2" in js                     # wrapped-loop branch keyed on enrichment
    assert "makeBasis" in js                        # oriented into the pulley plane
    assert "never silent" in js                     # unresolved endpoints stay visible


def test_step_worker_extrudes_teeth_and_loops():
    src = (REPO / "services" / "inference" / "step_worker.py").read_text(encoding="utf-8")
    assert "gear_outline" in src and "sprocket_outline" in src and "pulley_outline" in src
    assert "belt_loop" in src
    assert "belt" not in src.split("SKIP = {")[1].split("}")[0], "belts are skipped again"
    assert "catalog envelope" in src                # envelope parts are named honestly


def test_overstated_copy_is_gone():
    home = (SITE / "index.html").read_text(encoding="utf-8")
    assert "Zero flattened CAD" not in home
    assert "0 flattened bodies" not in home
    studio = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    assert "never a claim beyond what was modelled" in studio
    for phrase in ("Production-ready", "production-ready", "Native assemblies"):
        assert phrase not in studio and phrase not in home


# ── the team's course build (reference videos, 2026-08-09) ───────────────────
def test_chassis_and_bumper_follow_the_course_build():
    """The reference videos (Golden Horn 8159 Onshape course) build the chassis as
    50x25x2 mm punched tube (O5 holes, 25 mm pitch) joined by 3 mm hole-matched
    gussets, and sweep the bumper as ONE continuous ring with rounded corners.
    The generator must produce that construction, not approximations of it."""
    spec = build_robot_spec("swerve robot with arm intake", season="2026-rebuilt")
    chassis = spec["cad"]["assemblies"][0]
    rails = [f for f in chassis["features"] if f["t"] == "tube" and "rail" in f["n"]]
    assert rails and all(f["wall"] == 0.079 for f in rails)          # 2 mm wall
    assert all(f["bolt_pitch"] == 1.0 for f in rails)                # 25 mm hole rows
    assert all("50x25" in (f.get("note") or "") for f in rails)
    gussets = [f for f in chassis["features"] if f["n"] == "corner gusset"]
    assert gussets and all(f["th"] == 0.118 for f in gussets)        # 3 mm 6061
    corners = [f for f in chassis["features"] if f["t"] == "noodle_corner"]
    assert len(corners) == 8                                         # 2 levels x 4 corners
    noodle_off = {round(f["bend"], 3) for f in corners}
    assert len(noodle_off) == 1                                      # one ring radius
    # straight noodles end exactly at the tangent points of the corner bends
    frame = spec["frame"]
    front = [f for f in chassis["features"]
             if f["t"] == "noodle" and f["n"].startswith("front bumper")]
    assert front and all(abs(f["len"] - frame["width_in"]) < 1e-6 for f in front)
