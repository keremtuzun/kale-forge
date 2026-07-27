"""COMP-004: polarized components whose pins do not indicate polarity."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity

_POLARIZED_LIB_HINTS = ("cp", "c_polarized", "c_elec", "d_", ":d", "led", "battery", "electrolytic", "tantalum")
_POLARITY_MARKERS = {"+", "-", "a", "k", "anode", "cathode", "pos", "neg"}


class PolarityUnclearRule(Rule):
    id = "COMP-004"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "Polarized component with unclear polarity"
    description = "Electrolytic/tantalum caps, diodes, and batteries need unambiguous polarity markings."
    default_severity = Severity.WARNING

    def _is_polarized(self, lib_id: str, value: str, fields: dict[str, str]) -> bool:
        text = f"{lib_id} {value} {' '.join(fields.values())}".lower()
        return any(hint in text for hint in _POLARIZED_LIB_HINTS)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or len(comp.pins) != 2:
                continue
            if not self._is_polarized(comp.lib_id, comp.value, comp.fields):
                continue
            names = {p.name.strip().lower() for p in comp.pins}
            if names & _POLARITY_MARKERS:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"{comp.reference} ({comp.value or comp.lib_id}) looks polarized but its pin "
                        f"names ({', '.join(sorted(n or '?' for n in names))}) do not mark +/− or A/K."
                    ),
                    affected_components=[comp.reference],
                    evidence=[Evidence(kind="component", reference=comp.reference,
                                       description="polarized part heuristic matched; no polarity-marked pins")],
                    suggested_fix="Use a symbol with explicit polarity markings and verify orientation.",
                    confidence=0.6,
                )
            )
        return findings
