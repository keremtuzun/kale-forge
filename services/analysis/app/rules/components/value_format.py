"""COMP-003: unparseable or stylistically inconsistent R/C values."""
from __future__ import annotations

import re

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_capacitor, is_resistor, parse_passive_value

_RKM_RE = re.compile(r"^\d+[RrKkMm]\d+$")
_DECIMAL_RE = re.compile(r"^\d+(\.\d+)?\s*[pnuµmkKMG]?[RΩFf]?$")


class ValueFormatRule(Rule):
    id = "COMP-003"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "Inconsistent or unparseable passive values"
    description = "Mixed value styles ('4k7' vs '4.7k') and unparseable values invite BOM errors."
    default_severity = Severity.INFO

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        rkm_users: list[str] = []
        decimal_users: list[str] = []
        for comp in project.components:
            if comp.dnp or not (is_resistor(comp) or is_capacitor(comp)):
                continue
            value = comp.value.strip()
            if not value or value.lower() in {"r", "c"}:
                continue  # COMP-001 territory
            if parse_passive_value(value) is None:
                findings.append(
                    self.finding(
                        description=f"{comp.reference} value '{value}' could not be parsed as a standard R/C value.",
                        affected_components=[comp.reference],
                        evidence=[Evidence(kind="component", reference=comp.reference,
                                           description=f"unparseable value '{value}'")],
                        suggested_fix="Use a standard notation like '10k', '4k7', or '100nF'.",
                        confidence=0.8,
                    )
                )
                continue
            if _RKM_RE.match(value):
                rkm_users.append(comp.reference)
            elif _DECIMAL_RE.match(value):
                decimal_users.append(comp.reference)
        if rkm_users and decimal_users:
            findings.append(
                self.finding(
                    title="Mixed value notation styles",
                    description=(
                        f"The project mixes RKM style ({', '.join(rkm_users[:5])}) and decimal style "
                        f"({', '.join(decimal_users[:5])}) values. Pick one convention."
                    ),
                    affected_components=(rkm_users + decimal_users)[:10],
                    evidence=[
                        Evidence(kind="component", description=f"RKM style: {', '.join(rkm_users[:10])}"),
                        Evidence(kind="component", description=f"decimal style: {', '.join(decimal_users[:10])}"),
                    ],
                    suggested_fix="Normalize all passive values to one notation.",
                    confidence=0.6,
                )
            )
        return findings
