"""Evaluation runner: for each case, run the rule engine and/or the self-hosted model,
assemble the final AIReview, and score against expectations. Runs fully offline (falls back
to schema-default 'offline-stub' when the inference service is unreachable)."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import httpx

ANALYSIS_ROOT = Path(__file__).resolve().parents[3] / "services" / "analysis"
if str(ANALYSIS_ROOT) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_ROOT))

from app.models.normalized import NormalizedProject  # noqa: E402
from app.rules.registry import run_rules  # noqa: E402

from kale_eval.cases import EvalCase  # noqa: E402
from kale_eval.configs import EvalConfig  # noqa: E402
from kale_eval.metrics import MetricsAccumulator  # noqa: E402

AI_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "confirmed_findings": {"type": "array"},
        "possible_findings": {"type": "array"},
        "recommendations": {"type": "array"},
        "questions_for_engineer": {"type": "array"},
        "evidence": {"type": "array"},
        "confidence": {"type": "number"},
        "limitations": {"type": "array"},
    },
    "required": ["summary", "confidence"],
}


def _valid_entities(project: NormalizedProject) -> tuple[set[str], set[str]]:
    return project.component_references(), project.net_names()


def _run_model(cfg: EvalConfig, case: EvalCase) -> tuple[dict | None, float, bool]:
    """Return (review_json, latency_ms, reachable). Offline -> schema-default stub."""
    payload = {"system": "eval", "user": str(case.project) + (case.question or ""),
               "json_schema": AI_REVIEW_SCHEMA, "max_tokens": 512}
    try:
        resp = httpx.post(f"{cfg.inference_url}/v1/generate", json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        return data.get("json"), data.get("latency_ms", 0.0), True
    except httpx.HTTPError:
        # offline-stub: schema-default empty review (as the inference stub would produce)
        return {"summary": "", "confirmed_findings": [], "possible_findings": [],
                "recommendations": [], "questions_for_engineer": [], "evidence": [],
                "confidence": 0.0, "limitations": ["offline-stub"]}, 0.0, False


def _predicted_ids(rule_findings, model_review, case) -> set[str]:
    ids: set[str] = set()
    for f in rule_findings:
        ids.add(f.rule_id)
    if model_review:
        for item in model_review.get("confirmed_findings", []) + model_review.get("possible_findings", []):
            if item.get("rule_id"):
                ids.add(item["rule_id"])
    return ids


def _citations_valid(model_review, valid_comps, valid_nets) -> tuple[bool, bool]:
    """Return (all_citations_exist, has_unsupported)."""
    if not model_review:
        return True, False
    unsupported = False
    for key in ("confirmed_findings", "possible_findings", "recommendations"):
        for item in model_review.get(key, []):
            for c in item.get("components", []):
                if c not in valid_comps:
                    unsupported = True
            for n in item.get("nets", []):
                if n not in valid_nets:
                    unsupported = True
    return (not unsupported), unsupported


def _forbidden_present(model_review, forbidden: list[str]) -> bool:
    if not model_review or not forbidden:
        return False
    text = str(model_review).lower()
    return any(re.search(pat.lower(), text) for pat in forbidden)


def _behavior_pass(model_review, rule_findings, case: EvalCase) -> bool:
    if not case.required_behaviors:
        return True
    ok = True
    for behavior in case.required_behaviors:
        if behavior == "asks_question":
            ok = ok and bool((model_review or {}).get("questions_for_engineer"))
        elif behavior == "refuses_certification":
            text = str(model_review or {}).lower()
            ok = ok and ("cannot certify" in text or "professional" in text or "not a compliance" in text
                         or (model_review or {}).get("confidence", 1.0) == 0.0)
        elif behavior == "reports_no_issues":
            ok = ok and not rule_findings and not (model_review or {}).get("confirmed_findings")
    return ok


def run_config(cfg: EvalConfig, cases: list[EvalCase]) -> tuple[MetricsAccumulator, list[dict]]:
    acc = MetricsAccumulator()
    case_reports: list[dict] = []
    for case in cases:
        project = NormalizedProject.model_validate(case.project)
        valid_comps, valid_nets = _valid_entities(project)
        rule_findings = run_rules(project) if cfg.use_rules else []
        model_review, latency, _reachable = (_run_model(cfg, case) if cfg.use_model else (None, 0.0, True))

        expected_ids = {ef.rule_id for ef in case.expected_findings if ef.rule_id}
        predicted_ids = _predicted_ids(rule_findings, model_review, case)
        # only score detection on cases that declare rule-id expectations
        if expected_ids or (cfg.use_rules and case.category in {
            "missing_decoupling", "led_resistor", "relay_flyback", "regulator_io",
            "floating_input", "incorrect_footprint",
        }):
            acc.add_detection(case.category, expected_ids, predicted_ids)

        json_valid = model_review is not None if cfg.use_model else True
        citation_ok, has_unsupported = _citations_valid(model_review, valid_comps, valid_nets)
        if _forbidden_present(model_review, case.forbidden_claims):
            has_unsupported = True
            citation_ok = False
        behavior_ok = _behavior_pass(model_review, rule_findings, case)
        confidence = (model_review or {}).get("confidence") if cfg.use_model else None
        correct = predicted_ids >= expected_ids if expected_ids else None

        acc.add_flags(case.category, json_valid=json_valid, citation_ok=citation_ok,
                      has_unsupported=has_unsupported, latency_ms=latency,
                      confidence=confidence, correct=correct, behavior_pass=behavior_ok)
        case_reports.append({
            "id": case.id, "category": case.category, "expected": sorted(expected_ids),
            "predicted": sorted(predicted_ids), "json_valid": json_valid,
            "citation_ok": citation_ok, "unsupported": has_unsupported, "behavior_pass": behavior_ok,
        })
    return acc, case_reports
