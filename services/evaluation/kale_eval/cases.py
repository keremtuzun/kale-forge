"""Evaluation case model and loader. Cases are small self-contained circuits with expected
findings, required behaviors, and forbidden claims."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

BENCHMARK_CATEGORIES = [
    "missing_decoupling", "led_resistor", "relay_flyback", "regulator_io", "floating_input",
    "incorrect_footprint", "power_tree_explanation", "circuit_purpose", "hallucination_resistance",
    "uncertainty_reporting", "evidence_citation", "structured_output", "safety_refusal",
]


class ExpectedFinding(BaseModel):
    rule_id: Optional[str] = None
    issue_id: Optional[str] = None
    must_cite_components: list[str] = Field(default_factory=list)
    must_cite_nets: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    id: str
    category: str
    project: dict[str, Any]
    question: Optional[str] = None
    expected_findings: list[ExpectedFinding] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    required_behaviors: list[str] = Field(default_factory=list)
    notes: str = ""


def load_cases(benchmarks_dir: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in sorted(Path(benchmarks_dir).glob("*.jsonl")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                cases.append(EvalCase.model_validate_json(line))
    return cases
