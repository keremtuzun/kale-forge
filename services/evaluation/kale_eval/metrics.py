"""Evaluation metrics: detection P/R/F1, citation accuracy, unsupported-claim rate,
confidence calibration (Brier), latency percentiles. Per-category and overall aggregation."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn}


def detection_metrics(expected_ids: set[str], predicted_ids: set[str]) -> dict[str, Any]:
    tp = len(expected_ids & predicted_ids)
    fp = len(predicted_ids - expected_ids)
    fn = len(expected_ids - predicted_ids)
    m = prf(tp, fp, fn)
    total_neg = fp + tp  # simplistic; FPR/FNR computed at aggregate over cases
    m["fpr"] = round(fp / (fp + tp), 4) if (fp + tp) else 0.0
    m["fnr"] = round(fn / (fn + tp), 4) if (fn + tp) else 0.0
    return m


def brier_score(pairs: list[tuple[float, bool]]) -> float:
    """pairs of (confidence, was_correct). Lower is better-calibrated."""
    if not pairs:
        return 0.0
    return round(sum((conf - (1.0 if correct else 0.0)) ** 2 for conf, correct in pairs) / len(pairs), 4)


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0}
    s = sorted(values)
    def pct(p: float) -> float:
        return round(s[min(len(s) - 1, int(p * len(s)))], 2)
    return {"p50": pct(0.5), "p95": pct(0.95)}


class MetricsAccumulator:
    """Aggregate case-level outcomes into per-category and overall metrics."""

    def __init__(self) -> None:
        self.tp: dict[str, int] = defaultdict(int)
        self.fp: dict[str, int] = defaultdict(int)
        self.fn: dict[str, int] = defaultdict(int)
        self.json_valid: dict[str, list[bool]] = defaultdict(list)
        self.citation_ok: dict[str, list[bool]] = defaultdict(list)
        self.unsupported: dict[str, list[bool]] = defaultdict(list)
        self.calibration: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        self.latencies: dict[str, list[float]] = defaultdict(list)
        self.behavior_pass: dict[str, list[bool]] = defaultdict(list)

    def add_detection(self, category: str, expected: set[str], predicted: set[str]) -> None:
        self.tp[category] += len(expected & predicted)
        self.fp[category] += len(predicted - expected)
        self.fn[category] += len(expected - predicted)

    def add_flags(self, category: str, *, json_valid: bool, citation_ok: bool,
                  has_unsupported: bool, latency_ms: float = 0.0,
                  confidence: float | None = None, correct: bool | None = None,
                  behavior_pass: bool | None = None) -> None:
        self.json_valid[category].append(json_valid)
        self.citation_ok[category].append(citation_ok)
        self.unsupported[category].append(has_unsupported)
        if latency_ms:
            self.latencies[category].append(latency_ms)
        if confidence is not None and correct is not None:
            self.calibration[category].append((confidence, correct))
        if behavior_pass is not None:
            self.behavior_pass[category].append(behavior_pass)

    def _rate(self, d: dict[str, list[bool]], category: str) -> float:
        vals = d.get(category, [])
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    def category_metrics(self, category: str) -> dict[str, Any]:
        m = prf(self.tp[category], self.fp[category], self.fn[category])
        m["json_validity"] = self._rate(self.json_valid, category)
        m["citation_accuracy"] = self._rate(self.citation_ok, category)
        m["unsupported_claim_rate"] = self._rate(self.unsupported, category)
        m["behavior_pass_rate"] = self._rate(self.behavior_pass, category)
        m["brier"] = brier_score(self.calibration.get(category, []))
        m.update({f"latency_{k}": v for k, v in percentiles(self.latencies.get(category, [])).items()})
        return m

    def categories(self) -> list[str]:
        keys = set(self.tp) | set(self.json_valid)
        return sorted(keys)

    def overall(self) -> dict[str, Any]:
        tp = sum(self.tp.values())
        fp = sum(self.fp.values())
        fn = sum(self.fn.values())
        m = prf(tp, fp, fn)
        all_json = [v for lst in self.json_valid.values() for v in lst]
        all_cit = [v for lst in self.citation_ok.values() for v in lst]
        all_uns = [v for lst in self.unsupported.values() for v in lst]
        all_beh = [v for lst in self.behavior_pass.values() for v in lst]
        all_cal = [p for lst in self.calibration.values() for p in lst]
        all_lat = [v for lst in self.latencies.values() for v in lst]
        m["json_validity"] = round(sum(all_json) / len(all_json), 4) if all_json else 0.0
        m["citation_accuracy"] = round(sum(all_cit) / len(all_cit), 4) if all_cit else 0.0
        m["unsupported_claim_rate"] = round(sum(all_uns) / len(all_uns), 4) if all_uns else 0.0
        m["behavior_pass_rate"] = round(sum(all_beh) / len(all_beh), 4) if all_beh else 0.0
        m["brier"] = brier_score(all_cal)
        m.update({f"latency_{k}": v for k, v in percentiles(all_lat).items()})
        return m
