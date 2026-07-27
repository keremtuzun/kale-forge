"""Validate the 8 labeled example projects against the real parser + rule engine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.ai_review import AIReview, validate_ai_review
from app.models.normalized import NormalizedProject
from app.rules.registry import run_rules

EXAMPLES_DIR = Path(__file__).resolve().parents[4] / "examples"
EXAMPLE_DIRS = sorted(d for d in EXAMPLES_DIR.glob("[0-9]*-*") if d.is_dir())


def test_examples_exist():
    assert len(EXAMPLE_DIRS) == 8, f"expected 8 example dirs, found {[d.name for d in EXAMPLE_DIRS]}"


@pytest.mark.parametrize("example_dir", EXAMPLE_DIRS, ids=lambda d: d.name)
def test_example(example_dir: Path):
    normalized = NormalizedProject.model_validate_json((example_dir / "normalized.json").read_text())
    # source exists and is non-empty
    assert (example_dir / "source" / "circuit.cir").read_text().strip()

    findings = run_rules(normalized)
    fired = {(f.rule_id, tuple(sorted(f.affected_components)), tuple(sorted(f.affected_nets))) for f in findings}

    expected = json.loads((example_dir / "expected_findings.json").read_text())
    for exp in expected:
        rid = exp["rule_id"]
        matching = [f for f in findings if f.rule_id == rid]
        assert matching, f"{example_dir.name}: expected rule {rid} did not fire"
        # the intended affected component/net must be covered by at least one matching finding
        exp_comps = set(exp["affected_components"])
        if exp_comps:
            assert any(exp_comps & set(f.affected_components) for f in matching), \
                f"{example_dir.name}: {rid} did not cite expected components {exp_comps}"

    # ideal review validates against the contract with zero dropped citations
    review = AIReview.model_validate_json((example_dir / "ideal_review.json").read_text())
    cleaned, warnings = validate_ai_review(review, normalized, findings)
    dropped = [w for w in warnings if "removed" in w or "dropped" in w]
    assert not dropped, f"{example_dir.name}: ideal review has invalid citations: {dropped}"

    labels = json.loads((example_dir / "labels.json").read_text())
    assert "category" in labels and "safety_class" in labels


def test_valid_circuit_is_clean():
    valid = next(d for d in EXAMPLE_DIRS if "valid" in d.name)
    normalized = NormalizedProject.model_validate_json((valid / "normalized.json").read_text())
    findings = run_rules(normalized)
    non_info = [f for f in findings if f.severity.value != "info"]
    assert not non_info, f"valid circuit should have no warnings/errors, got {[f.rule_id for f in non_info]}"
