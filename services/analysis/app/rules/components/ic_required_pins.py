"""COMP-006: ICs with unconnected power or input pins (not marked no-connect)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject, PinType
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_ic


class IcRequiredPinsRule(Rule):
    id = "COMP-006"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "IC with unconnected required pins"
    description = "Unconnected supply/input pins on an IC usually mean the part cannot function."
    default_severity = Severity.ERROR

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not is_ic(comp):
                continue
            missing = [
                p for p in comp.pins
                if p.net is None
                and ctx.graph.net_of(comp.reference, p.number) is None
                and p.type in (PinType.POWER_IN, PinType.INPUT)
            ]
            if not missing:
                continue
            power_missing = [p for p in missing if p.type == PinType.POWER_IN]
            findings.append(
                self.finding(
                    severity=Severity.ERROR if power_missing else Severity.WARNING,
                    description=(
                        f"IC {comp.reference} ({comp.value or comp.lib_id}) has unconnected required "
                        f"pins: {', '.join(f'{p.number} ({p.name or p.type.value})' for p in missing)}."
                    ),
                    affected_components=[comp.reference],
                    evidence=[
                        Evidence(kind="pin", reference=f"{comp.reference}.{p.number}",
                                 description=f"{p.type.value} pin '{p.name or p.number}' unconnected")
                        for p in missing
                    ],
                    suggested_fix=(
                        "Connect supply pins to their rails and give every input a defined level; "
                        "mark genuinely unused pins with explicit no-connect flags."
                    ),
                    confidence=0.9,
                )
            )
        return findings
