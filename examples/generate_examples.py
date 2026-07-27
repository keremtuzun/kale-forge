"""Generate the 8 labeled example projects. Each example's normalized.json is the real
internal format; expected_findings.json contains only findings that genuinely fire under the
real rule engine; ideal_review.json validates against the AIReview contract. A SPICE .cir
source is emitted for provenance (full pin-type semantics live in normalized.json).

Run: python examples/generate_examples.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ANALYSIS = REPO / "services" / "analysis"
TRAINING = REPO / "services" / "training"
for p in (str(ANALYSIS), str(TRAINING)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.models.ai_review import AIReview, ReviewItem, validate_ai_review  # noqa: E402
from app.models.normalized import NormalizedProject  # noqa: E402
from app.rules.base import Evidence  # noqa: E402
from app.rules.registry import engine_version, run_rules  # noqa: E402

from kale_training import templates as T  # noqa: E402

EXAMPLES_DIR = REPO / "examples"


def _spice_from_project(project: NormalizedProject) -> str:
    """A best-effort SPICE representation for provenance."""
    lines = [f"* {project.project.name} — Kale.ai example (provenance only; see normalized.json)"]
    for comp in project.components:
        nodes = " ".join((p.net or "0").replace("GND", "0") for p in comp.pins) or "n1 n2"
        lines.append(f"{comp.reference} {nodes} {comp.value or comp.lib_id or 'X'}")
    lines.append(".end")
    return "\n".join(lines) + "\n"


def _ideal_review(project: NormalizedProject, findings, primary_rule: str | None,
                  summary: str, extra_limits: list[str] | None = None) -> AIReview:
    confirmed = [
        ReviewItem(title=f.title, detail=f.description, severity=f.severity,
                   components=list(f.affected_components), nets=list(f.affected_nets),
                   rule_id=f.rule_id, evidence=[e.description for e in f.evidence])
        for f in findings
    ]
    recs = [
        ReviewItem(title=f"Fix for {f.rule_id}", detail=f.suggested_fix,
                   components=list(f.affected_components), nets=list(f.affected_nets), rule_id=f.rule_id)
        for f in findings if f.suggested_fix
    ]
    evidence = [Evidence(kind="rule", reference=f.rule_id, description=f.title) for f in findings]
    review = AIReview(
        summary=summary, confirmed_findings=confirmed, possible_findings=[], recommendations=recs,
        questions_for_engineer=[], evidence=evidence,
        confidence=0.85 if findings else 0.6,
        limitations=(extra_limits or []) + [
            "Automated review; not a substitute for professional electrical engineering review.",
        ],
    )
    cleaned, _warnings = validate_ai_review(review, project, findings)
    return cleaned


def _wrong_footprint_example() -> tuple[NormalizedProject, str]:
    """An 8-pin IC whose footprint is missing — detectable via COMP-002. True footprint/
    package mismatch needs component-spec data (documented in the README)."""
    project = T.mcu_circuit()
    project.project.name = "wrong_footprint"
    ic = project.get_component("U1")
    ic.footprint = ""  # simulate an unassigned/incorrect footprint
    return project, "COMP-002"


def _rail_no_source_example() -> tuple[NormalizedProject, str]:
    project = T.mcu_circuit()
    project.project.name = "rail_no_source"
    # remove the regulator so +5V has no source feeding it
    project = T._drop_component(project, "U2")
    project = T._finalize(project)
    return project, "PWR-007"


EXAMPLES = [
    ("01-led-no-resistor", "led_resistor", "power", lambda: (T.remove_led_resistor(T.led_circuit()), "COMP-008"),
     "This LED circuit drives an LED directly between a supply rail and ground with no series "
     "current-limiting resistor, so the LED current is uncontrolled."),
    ("02-mcu-no-decoupling", "missing_decoupling", "general",
     lambda: (T.remove_decoupling(T.mcu_circuit()), "COMP-012"),
     "The microcontroller has a supply pin with no local decoupling capacitor to ground, "
     "risking brownout resets and switching noise."),
    ("03-regulator-bad-caps", "regulator_io", "power",
     lambda: (T.remove_regulator_cap(T.regulator_circuit()), "COMP-011"),
     "The voltage regulator is missing a required capacitor, which can cause instability or "
     "oscillation."),
    ("04-relay-no-flyback", "relay_flyback", "power",
     lambda: (T.remove_flyback(T.relay_circuit()), "COMP-009"),
     "The relay coil is switched by a transistor but has no flyback diode; the inductive spike "
     "at turn-off can destroy the driver."),
    ("05-floating-input", "floating_input", "general",
     lambda: (T.float_input(T.mcu_circuit()), "CONN-004"),
     "A digital input is left without a defined logic level after its pull resistor is removed, "
     "so it floats."),
    ("06-wrong-footprint", "incorrect_footprint", "general", _wrong_footprint_example,
     "An IC has no/incorrect footprint assigned. Detecting a true package↔footprint mismatch "
     "requires component-spec data; this example is detected via the missing-footprint rule."),
    ("07-rail-no-source", "power_tree_explanation", "power", _rail_no_source_example,
     "A power rail has loads but nothing feeding it (the regulator that would source it is "
     "absent), so the rail would be dead at power-up."),
    ("08-valid-circuit", "circuit_purpose", "general", lambda: (T.valid_circuit(), None),
     "A simple, correctly-formed resistive divider with input bypassing. No rule-level issues "
     "are expected."),
]


def main() -> None:
    for slug, category, safety, builder, description in EXAMPLES:
        project, primary_rule = builder()
        project = T._finalize(project)
        findings = run_rules(project)
        # expected findings = the intended defect (subset of actual), else all (valid circuit)
        if primary_rule:
            expected = [f for f in findings if f.rule_id == primary_rule]
            assert expected, f"{slug}: intended rule {primary_rule} did not fire! actual: {[f.rule_id for f in findings]}"
        else:
            expected = [f for f in findings if f.severity.value in ("info",)]  # valid: only info at most

        d = EXAMPLES_DIR / slug
        (d / "source").mkdir(parents=True, exist_ok=True)
        (d / "source" / "circuit.cir").write_text(_spice_from_project(project))
        (d / "normalized.json").write_text(json.dumps(project.model_dump(), indent=2, default=str))
        (d / "expected_findings.json").write_text(json.dumps([
            {"rule_id": f.rule_id, "severity": f.severity.value,
             "affected_components": f.affected_components, "affected_nets": f.affected_nets}
            for f in expected
        ], indent=2))
        summary = (f"{description} " + (
            f"The rule engine confirms {len(findings)} finding(s)." if findings
            else "The rule engine reports no issues; this is not a guarantee of correctness."))
        review = _ideal_review(project, findings, primary_rule, summary,
                               extra_limits=(["Elevated-risk (power) design — have a professional review it."]
                                             if safety == "power" else None))
        (d / "ideal_review.json").write_text(review.model_dump_json(indent=2))
        (d / "labels.json").write_text(json.dumps({
            "category": category, "safety_class": ("power" if safety == "power" else "general"),
            "primary_rule": primary_rule, "rule_engine_version": engine_version(),
        }, indent=2))
        (d / "README.md").write_text(
            f"# {slug}\n\n{description}\n\n"
            f"**Category:** {category} · **Safety class:** {safety}\n\n"
            f"**Intended finding:** `{primary_rule or 'none (valid circuit)'}`\n\n"
            f"**Rule findings that fire ({len(findings)}):** "
            f"{', '.join(sorted({f.rule_id for f in findings})) or 'none'}\n\n"
            "## Files\n"
            "- `source/circuit.cir` — SPICE representation (provenance; full pin-type "
            "semantics are in `normalized.json`)\n"
            "- `normalized.json` — Kale.ai normalized circuit (the internal contract)\n"
            "- `expected_findings.json` — findings the rule engine is expected to produce\n"
            "- `ideal_review.json` — the ideal Kale model response (AIReview schema)\n"
            "- `labels.json` — evaluation labels\n"
        )
        print(f"{slug}: {len(findings)} findings, expected={[f.rule_id for f in expected]}")


if __name__ == "__main__":
    main()
