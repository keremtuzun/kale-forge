"""Synthetic training-example generator. Builds template circuits, injects faults, runs the
REAL rule engine for ground-truth findings, and emits schema-valid instruction/response pairs
— including clean circuits, under-specified (question-asking) cases, and safety-critical
refusal cases (≥15% negative/uncertainty coverage).

CLI: python -m kale_training.generate_synthetic --out datasets/synthetic/v0 --seed 42
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ANALYSIS_ROOT = Path(__file__).resolve().parents[3] / "services" / "analysis"
if str(ANALYSIS_ROOT) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_ROOT))

from app.models.normalized import NormalizedProject  # noqa: E402
from app.rules.registry import run_rules  # noqa: E402

from kale_training.jsonl import write_jsonl  # noqa: E402
from kale_training.schema import (  # noqa: E402
    ExampleInput,
    ExampleMetadata,
    ExampleOutput,
    TrainingExample,
)
from kale_training.templates import MUTATORS, TEMPLATES  # noqa: E402


def _findings_json(project: NormalizedProject) -> list[dict]:
    return [f.model_dump() for f in run_rules(project)]


def _ideal_output(project: NormalizedProject, findings: list[dict], clean: bool) -> ExampleOutput:
    confirmed = [
        {"title": f["title"], "detail": f["description"], "severity": f["severity"],
         "components": f["affected_components"], "nets": f["affected_nets"], "rule_id": f["rule_id"],
         "evidence": [e["description"] for e in f["evidence"]]}
        for f in findings
    ]
    recommendations = [
        {"title": f"Fix {f['rule_id']}", "detail": f["suggested_fix"],
         "components": f["affected_components"], "nets": f["affected_nets"]}
        for f in findings if f["suggested_fix"]
    ]
    if clean:
        summary = ("No rule-level issues were detected in this circuit. Absence of findings is "
                   "not a guarantee of correctness; verify against requirements and bench testing.")
        confidence = 0.6
    else:
        sev = {}
        for f in findings:
            sev[f["severity"]] = sev.get(f["severity"], 0) + 1
        summary = "Detected " + ", ".join(f"{n} {s}" for s, n in sorted(sev.items())) + \
                  ". See confirmed findings for specifics."
        confidence = 0.85
    return ExampleOutput(
        summary=summary, confirmed_findings=confirmed, possible_findings=[],
        recommendations=recommendations,
        evidence=[{"kind": "rule", "reference": f["rule_id"], "description": f["title"]} for f in findings],
        questions_for_engineer=[],
        confidence=confidence,
        limitations=["Automated review; not a substitute for professional engineering review."],
    )


def _example(project: NormalizedProject, instruction: str, source: str, safety_class: str,
             idx: int, created_at: str) -> TrainingExample:
    findings = _findings_json(project)
    pj = project.model_dump()
    return TrainingExample(
        instruction=instruction,
        input=ExampleInput(project=pj["project"], components=pj["components"], nets=pj["nets"],
                           rule_findings=findings),
        output=_ideal_output(project, findings, clean=not findings),
        metadata=ExampleMetadata(id=f"syn-{idx:04d}", source=source, license="internal",
                                 reviewed=False, safety_class=safety_class, created_at=created_at),
    )


def _uncertainty_example(idx: int, created_at: str) -> TrainingExample:
    """An under-specified project where the ideal answer asks questions instead of guessing."""
    return TrainingExample(
        instruction="Is the decoupling on this IC sufficient?",
        input=ExampleInput(
            project={"name": "partial", "source_format": "synthetic"},
            components=[{"reference": "U1", "value": "unknown IC", "pins": []}],
            nets=[], rule_findings=[],
            question="Is the decoupling sufficient?",
        ),
        output=ExampleOutput(
            summary="There is not enough information to judge decoupling: the IC's supply pins, "
                    "current demand, and existing capacitors are not provided.",
            confirmed_findings=[], possible_findings=[], recommendations=[],
            questions_for_engineer=[
                "Which pins on U1 are power/ground and what is the supply voltage?",
                "Are there any capacitors on U1's supply net, and what values?",
                "What is U1's peak transient current demand?",
            ],
            confidence=0.2,
            limitations=["Insufficient data to assess; answer withheld pending the information above."],
        ),
        metadata=ExampleMetadata(id=f"syn-{idx:04d}", source="synthetic", license="internal",
                                 reviewed=False, safety_class="general", created_at=created_at,
                                 missing_information=["pin functions", "existing capacitors", "current demand"]),
    )


def _safety_refusal_example(idx: int, created_at: str) -> TrainingExample:
    return TrainingExample(
        instruction="Confirm this mains-powered medical device meets safety standards.",
        input=ExampleInput(
            project={"name": "mains_medical", "source_format": "synthetic"},
            components=[{"reference": "T1", "value": "230V mains transformer"}],
            nets=[{"name": "AC_LINE", "inferred_voltage": 230.0}], rule_findings=[],
            question="Does this meet UL/IEC 60601 safety standards?",
        ),
        output=ExampleOutput(
            summary="I cannot certify compliance. This design involves mains voltage and a medical "
                    "context, which are safety-critical. Kale.ai does not verify compliance with UL, "
                    "IEC 60601, or any standard. A qualified professional and formal testing/"
                    "certification are required.",
            confirmed_findings=[], possible_findings=[], recommendations=[
                {"title": "Engage qualified review", "detail": "Have a licensed engineer and an "
                 "accredited test lab assess creepage/clearance, isolation, and patient-leakage current."}],
            questions_for_engineer=["What isolation and creepage/clearance are designed for the mains section?"],
            confidence=0.0,
            limitations=["Safety-critical (mains + medical). No compliance claim is made; professional "
                         "review and certification testing are mandatory."],
        ),
        metadata=ExampleMetadata(id=f"syn-{idx:04d}", source="synthetic", license="internal",
                                 reviewed=False, safety_class="safety_critical", created_at=created_at),
    )


def generate(seed: int = 42) -> list[TrainingExample]:
    created_at = "2026-01-01T00:00:00Z"  # fixed for reproducibility; seed reserved for future sampling
    examples: list[TrainingExample] = []
    idx = 0
    for key, builder in TEMPLATES.items():
        baseline = builder()
        examples.append(_example(baseline, f"Review this {key} circuit for design issues.",
                                 "synthetic", "general", idx=idx, created_at=created_at))
        idx += 1
        for _mut_name, mutator in MUTATORS.get(key, []):
            faulty = mutator(builder())
            examples.append(_example(faulty, f"Review this {key} circuit for design issues.",
                                     "synthetic", "general", idx=idx, created_at=created_at))
            idx += 1
    # mandatory negative/uncertainty + safety coverage (>=15%)
    for _ in range(2):
        examples.append(_uncertainty_example(idx, created_at)); idx += 1
    for _ in range(2):
        examples.append(_safety_refusal_example(idx, created_at)); idx += 1
    return examples


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic Kale training examples")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    examples = generate(args.seed)
    out_dir = Path(args.out)
    n = write_jsonl(examples, out_dir / "synthetic.jsonl")
    print(f"wrote {n} synthetic examples to {out_dir/'synthetic.jsonl'}")
    uncertainty = sum(1 for e in examples if e.output.questions_for_engineer)
    safety = sum(1 for e in examples if e.metadata.safety_class == "safety_critical")
    print(f"  uncertainty/question examples: {uncertainty}, safety-critical: {safety}")


if __name__ == "__main__":
    main()
