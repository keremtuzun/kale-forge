"""Regression cases for the 2026-08-06 production hardening pass.

Every test here maps to a defect that was reproduced against the deployed site. They are
written against the *site bundle* (apps/site), which is the code Vercel actually runs.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[4] / "apps" / "site"
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))

from app.services.cad_contract import geometry_envelope, season_rule_report  # noqa: E402
from app.services.design_gate import gate, measurements  # noqa: E402
from app.services.frc_featurescript import build_featurescript  # noqa: E402
from app.services.frc_season import SEASONS  # noqa: E402
from app.services.robot_spec import DesignNotBuildable, build_robot_spec  # noqa: E402

R2026 = SEASONS["2026-rebuilt"]["rules"]


def build(prompt: str, season: str = "2026-rebuilt"):
    return build_robot_spec(prompt, use_model=False, season=season)


# ── defect 3: impossible dimensions were silently built ──────────────────────
@pytest.mark.parametrize("prompt", [
    "4 meter elevator robot",
    "make the elvator 4 meteres",          # misspelled, still a 4 m elevator
    "robot with a 157 inch lift",
    "elevator robot 400 cm tall",
])
def test_over_height_requests_are_rejected_not_clipped(prompt):
    with pytest.raises(DesignNotBuildable) as err:
        build(prompt)
    g = err.value.gate
    assert g["state"] == "rejected"
    assert g["violations"], "a rejection must say which rule was broken"
    assert g["violations"][0]["rule"] == R2026["max_height_rule"] == "R107"
    assert "30" in g["violations"][0]["message"]


def test_metric_and_imperial_are_both_understood():
    by_raw = {m["raw"]: m["inches"] for m in measurements("4 m tall, 27 inch frame, 30cm arm")}
    assert round(by_raw["4 m"]) == 157
    assert by_raw["27 inch"] == 27.0
    assert round(by_raw["30cm"], 1) == 11.8


# ── defect 4: negative / "only" requirements were ignored ────────────────────
def test_chassis_only_builds_a_chassis():
    spec = build("no mechanisms, only a 27 inch chassis")
    assert spec["scope"] == "chassis"
    assert spec["include_drivetrain"] is False
    assert spec["include_electrical"] is False
    ids = {a["id"] for a in spec["cad"]["assemblies"]}
    assert ids == {"chassis"}, f"expected chassis only, got {sorted(ids)}"
    for mech in ("intake", "hopper", "shooter", "elevator", "manipulator", "climber"):
        assert not (spec.get(mech) or {}).get("included"), f"{mech} should be excluded"
    # It used to emit ~121 parts including swerve modules and a control system.
    assert spec["cad"]["feature_total"] < 60


def test_chassis_only_featurescript_disables_mechanisms():
    source = build_featurescript(spec := build("no mechanisms, only a 27 inch chassis"),
                                 spec.get("name", "x"))
    assert "doMechanisms : false" in source
    assert "doDrivetrain : false" in source
    assert "doElectrical : false" in source


def test_explicit_negative_removes_only_that_mechanism():
    spec = build("27x27 swerve robot with an intake but no climber")
    assert (spec.get("intake") or {}).get("included")
    assert not (spec.get("climber") or {}).get("included")


# ── defect 5: a vague prompt generated hundreds of parts ─────────────────────
@pytest.mark.parametrize("prompt", ["robot", "a robot", "  robot  ", "build me something"])
def test_vague_prompts_ask_before_building(prompt):
    with pytest.raises(DesignNotBuildable) as err:
        build(prompt)
    g = err.value.gate
    assert g["state"] == "clarification_required"
    assert len(g["questions"]) >= 3
    assert {q["id"] for q in g["questions"]} >= {"season", "drivetrain", "mechanisms"}


def test_a_specific_prompt_still_builds():
    spec = build("27x27 swerve robot with intake and shooter")
    assert spec["cad"]["feature_total"] > 100


# ── defect: contradictions were silently resolved ────────────────────────────
@pytest.mark.parametrize("prompt", [
    "tank drive and swerve drive robot",
    "only chassis with a shooter",
    "no elevator, add a two-stage elevator",
])
def test_contradictions_are_surfaced(prompt):
    with pytest.raises(DesignNotBuildable) as err:
        build(prompt)
    assert err.value.gate["state"] == "clarification_required"
    assert err.value.gate["contradictions"]


# ── phase 6: rules enforced against real geometry, not constants ─────────────
def test_envelope_is_measured_from_generated_solids():
    spec = build("27x27 swerve robot with intake and shooter")
    env = spec["rule_report"]["envelope"]
    assert env["measured"] and env["bodies"] > 50
    # Height is measured from the drive-wheel contact plane, not the lowest solid.
    assert env["ground_y_in"] < 0
    assert 10 < env["height_in"] < float(R2026["max_height_in"]) + 0.01


@pytest.mark.parametrize("prompt", [
    "27x27 swerve robot with intake and shooter",
    "everything robot: intake, hopper, shooter, elevator, arm, climber",
    "two-segment arm with wrist and compliant gripper",
    "west coast drive robot with a hooded shooter and a hopper",
])
def test_generated_designs_obey_2026_limits(prompt):
    report = build(prompt)["rule_report"]
    assert report["ok"], [c["detail"] for c in report["failed"]]
    assert report["export_blocked"] is False


def test_nothing_is_modelled_below_the_floor():
    for prompt in ("everything robot: intake, hopper, shooter, elevator, arm, climber",
                   "two-segment arm with wrist and compliant gripper"):
        env = build(prompt)["rule_report"]["envelope"]
        assert env["below_floor"] == [], env["below_floor"]


def test_over_tall_geometry_blocks_export():
    """A design that reaches above the cap must fail hard, not be re-labelled."""
    spec = build("27x27 swerve robot with intake and shooter")
    tower = dict(spec["cad"]["assemblies"][0]["features"][0])
    tower.update({"n": "illegal mast", "at": [0, 40.0, 0], "sec": [2.0, 1.0], "len": 20.0})
    spec["cad"]["assemblies"][0]["features"].append(tower)
    report = season_rule_report(spec)
    assert report["ok"] is False and report["export_blocked"] is True
    failed = [c for c in report["failed"] if c["check"] == "total height"]
    assert failed and "R107" in failed[0]["rule"]
    assert "exceeding" in failed[0]["detail"]


def test_each_season_uses_its_own_limits():
    """Guards against one year's numbers leaking into another."""
    for key, season in SEASONS.items():
        if not season.get("selectable"):
            continue
        spec = build_robot_spec("27x27 swerve robot with intake", use_model=False, season=key)
        limits = spec["season"]["limits"]
        # Not every season has every limit (2026's always-on height cap is new), so compare
        # the whole published block rather than assuming a key exists.
        assert limits == season["rules"], f"{key} limits drifted from the published rules"
        for shared in ("perimeter_in", "weight_lb"):
            assert limits.get(shared) == season["rules"].get(shared)


def test_2026_limits_are_the_published_ones():
    assert R2026["weight_lb"] == 115.0
    assert R2026["perimeter_in"] == 110.0
    assert R2026["max_height_in"] == 30.0
    assert R2026["extension_in"] == 12.0


# ── defect 6: the design name ignored the request ────────────────────────────
def test_chassis_request_is_not_named_after_an_intake_robot():
    spec = build("no mechanisms, only a 27 inch chassis")
    assert "intake" not in spec["name"].lower()
    assert "chassis" in spec["name"].lower()


# ── defect 7 / phase 3: untrusted text must never become markup ──────────────
XSS = [
    '<img src=x onerror=alert(1)>',
    '<svg onload=alert(1)>',
    '<a href="javascript:alert(1)">open</a>',
    '</script><script>alert(1)</script>',
]


@pytest.mark.parametrize("payload", XSS)
def test_xss_payloads_survive_as_inert_data(payload):
    """The prompt is echoed into the spec; it must arrive as data, never as markup.

    The page escapes every value at the boundary (escapeDeep) before it reaches innerHTML;
    this asserts the server does not pre-render it into HTML and that JSON round-trips it.
    """
    spec = build(f"27x27 swerve robot with intake {payload}")
    # The payload must survive as an ordinary JSON *string value* — data, not markup. The
    # server never renders it into HTML; the page escapes every value at the boundary
    # (escapeDeep) before it can reach innerHTML, so it displays as visible inert text.
    assert isinstance(spec["intent"], str)
    assert payload in json.loads(json.dumps(spec))["intent"]
    # And the served page must not contain the payload anywhere in its own markup.
    assert payload not in _load_studio().PAGE


def test_onshape_url_allowlist():
    spec_mod = _load_studio()
    ok = spec_mod._safe_onshape_url
    assert ok("https://cad.onshape.com/documents/a/w/b/e/c")
    for bad in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>",
                "http://cad.onshape.com/x", "https://evil.example/x",
                "https://user:pw@cad.onshape.com/x", "https://cad.onshape.com.evil.com/x", ""):
        assert not ok(bad), bad


def _load_studio():
    """Import api/studio.py by path (it is not a package)."""
    os.environ.setdefault("KALE_DEV", "1")
    spec = importlib.util.spec_from_file_location("kale_studio", SITE / "api" / "studio.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── phase 2 / 4: transport security ──────────────────────────────────────────
def test_no_wildcard_cors_anywhere_in_the_handler():
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    # No CORS header is ever *emitted*. Prose in a comment explaining the old bug is fine;
    # what matters is that no executable line sets the header.
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "Access-Control-Allow-Origin" not in code
    assert "Access-Control-Allow-Origin" not in _load_studio()._SECURITY_HEADERS


def test_inline_scripts_carry_a_nonce_matching_the_policy():
    """The page's three inline scripts must run under a nonce, not 'unsafe-inline'.

    Without this the strict policy would silently break the whole studio; with
    'unsafe-inline' it would also permit an injected inline handler.
    """
    mod = _load_studio()
    assert mod.PAGE.count("__CSP_NONCE__") == 3
    nonce = mod._new_nonce()
    served = mod.PAGE.replace("__CSP_NONCE__", nonce)
    assert "__CSP_NONCE__" not in served
    assert served.count(f'nonce="{nonce}"') == 3
    assert f"'nonce-{nonce}'" in mod._csp(nonce)
    # every inline <script> in the served page is nonced
    import re as _re
    for tag in _re.findall(r"<script\b[^>]*>", served):
        if " src=" in tag:
            continue
        assert 'nonce="' in tag, tag


def test_security_headers_include_a_restrictive_csp():
    mod = _load_studio()
    csp = mod._csp("TESTNONCE")
    assert "default-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("style-src")[0]   # not allowed for scripts
    assert "'unsafe-eval'" not in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    for header in ("Referrer-Policy", "Permissions-Policy", "Strict-Transport-Security"):
        assert header in mod._SECURITY_HEADERS


def test_prompt_is_never_placed_in_a_url_by_the_client():
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    for legacy in ("studio?json=1&prompt=", "studio?fs=1&prompt=", "studio?step=1&prompt="):
        assert legacy not in source, f"{legacy} still constructed client-side"
    assert "encodeURIComponent(currentPrompt)" not in source


def test_logging_never_receives_prompt_or_credentials():
    mod = _load_studio()
    captured: list[str] = []
    import builtins
    real_print = builtins.print
    builtins.print = lambda *a, **k: captured.append(" ".join(str(x) for x in a))
    try:
        mod._log("test.event", requestId="abc", prompt="SECRET-PROMPT",
                 access_key="AK-SECRET", secret_key="SK-SECRET", source="fs source",
                 parts=12)
    finally:
        builtins.print = real_print
    blob = " ".join(captured)
    assert "abc" in blob and "12" in blob
    for leak in ("SECRET-PROMPT", "AK-SECRET", "SK-SECRET", "fs source"):
        assert leak not in blob, f"{leak} leaked into logs"


def test_rate_limiter_eventually_returns_false():
    mod = _load_studio()
    key = "test-client"
    allowed = sum(1 for _ in range(mod.RATE_LIMIT_PER_MIN + 5) if mod._rate_ok(key))
    assert allowed == mod.RATE_LIMIT_PER_MIN


# ── input limits ─────────────────────────────────────────────────────────────
def test_prompt_length_ceiling_is_defined():
    mod = _load_studio()
    assert 0 < mod.MAX_PROMPT_CHARS <= 10000
    assert 0 < mod.MAX_BODY_BYTES <= 1_000_000


@pytest.mark.parametrize("prompt", ["", "x", "  "])
def test_degenerate_prompts_do_not_build(prompt):
    with pytest.raises(DesignNotBuildable):
        build(prompt)


def test_very_long_prompt_is_still_bounded():
    spec = build("27x27 swerve robot with intake and shooter " + ("very " * 800))
    assert spec["cad"]["feature_total"] < 400


@pytest.mark.parametrize("prompt", [
    "27x27 swerve robot with intake \U0001f680\U0001f916",
    "27x27 swerve robot with intake مرحبا",
    "27x27 swerve robot with intake ünïcödé",
])
def test_unicode_prompts_build_and_produce_valid_identifiers(prompt):
    spec = build(prompt)
    source = build_featurescript(spec, spec.get("name", "x"))
    # FeatureScript identifiers must stay ASCII-safe regardless of the prompt.
    for line in source.splitlines():
        if line.strip().startswith("annotation") or " id : " in line:
            assert all(ord(ch) < 128 for ch in line), line[:80]


# ── persistence, lifecycle and accessibility (phases 7, 8, 12, 14) ───────────
def test_revisions_reference_a_design_id_not_a_growing_prompt():
    """The original defect: every edit re-sent the whole prompt history, so a
    design's requests grew without bound. Now the client sends a design id and
    the chain lives (and is capped) server-side."""
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    assert "_resolve_design" in source
    assert "designId" in source
    assert "expected_revision" in source          # optimistic concurrency reaches the client
    assert "DESIGN_NOT_FOUND" in source
    # malformed ids are rejected before any network call is made with them
    assert "DESIGN_ID_INVALID" in source


def test_store_writes_happen_server_side_after_validation():
    """The review's critical findings: the client used to write revisions to the
    store BEFORE validation (a rejected edit persisted), restores flipped the
    store before the rebuild, and one transient store error permanently degraded
    revision semantics. Now every write is a post-build commit in the function."""
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    assert "_CommitFailed" in source                      # build first, write second
    assert "commit_revision" in source and "commit_restore" in source
    assert "storeAvailable" not in source                 # no permanent degradation latch
    assert "async function storeRead" in source           # client only reads the store
    assert "storeCall(" not in source                     # the old write path is gone


def test_exports_reference_the_design_on_screen():
    """Exports used to send currentPrompt, which after a stored revision held only
    the edit text — the export was a different robot from the one displayed."""
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    assert "const fsBody=()=> designId" in source
    assert "baseRevision: revisionNumber" in source
    assert "REVISION_CONFLICT" in source                  # a moved chain 409s, never silently differs


def test_operations_do_not_share_one_abort_controller():
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    for scope in ("'export-fs'", "'export-step'", "'onshape'"):
        assert scope in source, f"missing per-operation scope {scope}"


def test_onshape_keys_are_wiped_from_the_dom():
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    assert source.count("$('#os-access').value=''") >= 2  # after use AND on close


def test_seasons_fail_closed():
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    assert "__seasonsFailed" in source
    assert "off-season (no rule checks)" in source        # rule-free is explicit, never inferred


def test_generation_lifecycle_is_a_state_machine_with_cancel():
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    for state in ("'generating'", "'succeeded'", "'failed'", "'cancelled'"):
        assert state in source, f"missing lifecycle state {state}"
    assert "abortInflight" in source
    assert 'id="cancel"' in source
    # cancelling must invalidate the in-flight response, not just hide the spinner
    assert "op.ticket++" in source


def test_dialogs_and_inputs_carry_accessible_semantics():
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    assert source.count('role="dialog"') >= 2
    assert source.count('aria-modal="true"') >= 2
    assert 'aria-live="polite"' in source
    assert "trapFocus" in source and "__returnFocus" in source
    for label_for in ('for="q"', 'for="season"', 'for="revision-q"'):
        assert label_for in source, f"input missing label {label_for}"


def test_refusals_render_their_questions_as_a_list():
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    assert "showQuestions" in source
    assert "q.examples" in source or "(q && q.examples" in source


def test_public_season_list_is_cacheable_and_designs_are_not():
    source = (SITE / "api" / "studio.py").read_text(encoding="utf-8")
    assert "public, max-age=3600" in source
    assert "no-store, private" in source


def test_season_rules_carry_their_source():
    from app.services.frc_season import SEASONS
    for key in ("2026-rebuilt", "2025-reefscape"):
        src = SEASONS[key]["rules"].get("source") or {}
        assert src.get("document") and src.get("url") and src.get("transcribed"), (
            f"{key} rules have no provenance")


def test_viewer_pauses_when_hidden_and_respects_reduced_motion():
    viewer = (SITE / "studio-viewer.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in viewer
    assert "visibilitychange" in viewer
    # the highlight clone leak: every deselect must release its cloned material
    assert "highlightMat.dispose()" in viewer
