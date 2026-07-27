"""Evaluation suite tests: metrics math, citation scoring, benchmark loading, end-to-end run."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_ROOT = REPO_ROOT / "services" / "evaluation"
sys.path.insert(0, str(EVAL_ROOT))

from kale_eval.cases import BENCHMARK_CATEGORIES, load_cases  # noqa: E402
from kale_eval.configs import EvalConfig  # noqa: E402
from kale_eval.metrics import MetricsAccumulator, brier_score, prf  # noqa: E402
from kale_eval.runner import _citations_valid, run_config  # noqa: E402

BENCH_DIR = EVAL_ROOT / "benchmarks"


@pytest.fixture(scope="module", autouse=True)
def _ensure_benchmarks():
    if not any(BENCH_DIR.glob("*.jsonl")):
        subprocess.run([sys.executable, str(EVAL_ROOT / "scripts" / "generate_benchmarks.py")],
                       check=True, capture_output=True)


class TestMetrics:
    def test_prf_known(self):
        m = prf(tp=8, fp=2, fn=2)
        assert m["precision"] == pytest.approx(0.8)
        assert m["recall"] == pytest.approx(0.8)
        assert m["f1"] == pytest.approx(0.8)

    def test_brier(self):
        # perfect calibration: confident and correct, unconfident and wrong
        assert brier_score([(1.0, True), (0.0, False)]) == pytest.approx(0.0)
        assert brier_score([(1.0, False)]) == pytest.approx(1.0)

    def test_accumulator_detection(self):
        acc = MetricsAccumulator()
        acc.add_detection("led_resistor", {"COMP-008"}, {"COMP-008", "EXTRA"})
        m = acc.category_metrics("led_resistor")
        assert m["tp"] == 1 and m["fp"] == 1 and m["fn"] == 0


class TestCitationScoring:
    def test_unsupported_citation_detected(self):
        review = {"confirmed_findings": [{"title": "x", "components": ["U9"], "nets": []}]}
        ok, unsupported = _citations_valid(review, {"U1"}, {"GND"})
        assert unsupported and not ok

    def test_valid_citation(self):
        review = {"confirmed_findings": [{"title": "x", "components": ["U1"], "nets": ["GND"]}]}
        ok, unsupported = _citations_valid(review, {"U1"}, {"GND"})
        assert ok and not unsupported


def test_all_benchmarks_load_and_cover_categories():
    cases = load_cases(BENCH_DIR)
    assert len(cases) >= 24
    categories = {c.category for c in cases}
    for cat in BENCHMARK_CATEGORIES:
        assert cat in categories, f"missing benchmark category: {cat}"


def test_rules_only_run_produces_metrics():
    cases = load_cases(BENCH_DIR)
    cfg = EvalConfig.named("rules-only")
    acc, case_reports = run_config(cfg, cases)
    assert len(case_reports) == len(cases)
    overall = acc.overall()
    assert "f1" in overall and "json_validity" in overall
    # rules-only should detect the LED-resistor positive case
    led_reports = [r for r in case_reports if r["id"] == "led_resistor_pos"]
    assert led_reports and "COMP-008" in led_reports[0]["predicted"]


def test_offline_model_run_completes():
    """base config hits inference; unreachable -> offline-stub, run still completes."""
    cases = load_cases(BENCH_DIR)[:4]
    cfg = EvalConfig.named("base", inference_url="http://127.0.0.1:9")
    acc, reports = run_config(cfg, cases)
    assert len(reports) == 4
