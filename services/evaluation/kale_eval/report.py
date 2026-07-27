"""Write evaluation reports (JSON + markdown) and compare two runs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from kale_eval.cases import EvalCase
from kale_eval.configs import EvalConfig
from kale_eval.metrics import MetricsAccumulator

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_OUT_DIR = REPO_ROOT / "models" / "evaluations"


def dataset_hash(cases: list[EvalCase]) -> str:
    payload = "\n".join(sorted(c.model_dump_json() for c in cases))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_report(cfg: EvalConfig, acc: MetricsAccumulator, case_reports: list[dict],
                 cases: list[EvalCase], timestamp: str) -> dict[str, Any]:
    per_category = {cat: acc.category_metrics(cat) for cat in acc.categories()}
    return {
        "config": cfg.name,
        "model_version": cfg.model_version,
        "timestamp": timestamp,
        "dataset_hash": dataset_hash(cases),
        "case_count": len(cases),
        "overall": acc.overall(),
        "per_category": per_category,
        "cases": case_reports,
    }


def write_report(report: dict, timestamp: str) -> Path:
    EVAL_OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = EVAL_OUT_DIR / f"{timestamp}-{report['config']}.json"
    path.write_text(json.dumps(report, indent=2))
    (EVAL_OUT_DIR / f"{timestamp}-{report['config']}.md").write_text(to_markdown(report))
    return path


def to_markdown(report: dict) -> str:
    lines = [f"# Evaluation — {report['config']}", "",
             f"- Timestamp: {report['timestamp']}",
             f"- Model version: {report.get('model_version')}",
             f"- Cases: {report['case_count']} (dataset {report['dataset_hash']})", "",
             "## Overall", ""]
    o = report["overall"]
    lines += [f"- F1: **{o['f1']}** (P {o['precision']} / R {o['recall']})",
              f"- JSON validity: {o['json_validity']}",
              f"- Citation accuracy: {o['citation_accuracy']}",
              f"- Unsupported-claim rate: {o['unsupported_claim_rate']}",
              f"- Behavior pass rate: {o['behavior_pass_rate']}",
              f"- Brier (calibration): {o['brier']}",
              f"- Latency p50/p95: {o['latency_p50']}/{o['latency_p95']} ms", "",
              "## Per category", "",
              "| Category | F1 | P | R | JSON | Citations | Unsupported | Behavior |",
              "|---|---|---|---|---|---|---|---|"]
    for cat, m in sorted(report["per_category"].items()):
        lines.append(f"| {cat} | {m['f1']} | {m['precision']} | {m['recall']} | "
                     f"{m['json_validity']} | {m['citation_accuracy']} | "
                     f"{m['unsupported_claim_rate']} | {m['behavior_pass_rate']} |")
    return "\n".join(lines) + "\n"


def compare(run_a: dict, run_b: dict) -> str:
    """Markdown delta table powering base-vs-fine-tuned comparisons."""
    lines = [f"# Comparison: {run_a['config']} → {run_b['config']}", "",
             "| Metric | " + f"{run_a['config']} | {run_b['config']} | Δ |", "|---|---|---|---|"]
    for key in ["f1", "precision", "recall", "json_validity", "citation_accuracy",
                "unsupported_claim_rate", "behavior_pass_rate", "brier"]:
        a = run_a["overall"].get(key, 0)
        b = run_b["overall"].get(key, 0)
        lines.append(f"| {key} | {a} | {b} | {round(b - a, 4):+} |")
    lines += ["", "## Per-category F1 delta", "",
              "| Category | " + f"{run_a['config']} | {run_b['config']} | Δ |", "|---|---|---|---|"]
    cats = sorted(set(run_a["per_category"]) | set(run_b["per_category"]))
    for cat in cats:
        a = run_a["per_category"].get(cat, {}).get("f1", 0)
        b = run_b["per_category"].get(cat, {}).get("f1", 0)
        lines.append(f"| {cat} | {a} | {b} | {round(b - a, 4):+} |")
    return "\n".join(lines) + "\n"
