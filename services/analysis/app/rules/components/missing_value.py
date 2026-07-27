"""COMP-001: components with missing or placeholder values."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_capacitor, is_ic, is_inductor, is_resistor

_PLACEHOLDERS = {"", "~", "r", "c", "l", "u", "val", "value", "?", "tbd"}


class MissingValueRule(Rule):
    id = "COMP-001"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "Missing component value"
    description = "Passives and ICs need real values/part names for BOM and review."
    default_severity = Severity.WARNING

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not comp.in_bom:
                continue
            if not (is_resistor(comp) or is_capacitor(comp) or is_inductor(comp) or is_ic(comp)):
                continue
            if comp.value.strip().lower() in _PLACEHOLDERS:
                findings.append(
                    self.finding(
                        description=f"{comp.reference} has no usable value ('{comp.value or ''}').",
                        affected_components=[comp.reference],
                        evidence=[Evidence(kind="component", reference=comp.reference,
                                           description=f"value field is '{comp.value}'")],
                        suggested_fix="Set the real value (e.g. '10k', '100nF') or part name.",
                        confidence=1.0,
                    )
                )
        return findings
