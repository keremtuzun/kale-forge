"""CONN-001: component pins with no net connection."""
from __future__ import annotations

from app.models.normalized import NormalizedProject, PinType
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_power_symbol_component


class UnconnectedPinsRule(Rule):
    id = "CONN-001"
    version = "1.0.0"
    category = RuleCategory.CONNECTIVITY
    title = "Unconnected component pins"
    description = "Pins without any net connection are usually wiring omissions."
    default_severity = Severity.WARNING
    source = "standard engineering practice"

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or is_power_symbol_component(comp):
                continue
            open_pins = [
                p for p in comp.pins
                if p.net is None and p.type != PinType.NO_CONNECT
                and ctx.graph.net_of(comp.reference, p.number) is None
            ]
            if not open_pins:
                continue
            pin_list = ", ".join(f"{comp.reference}.{p.number}" for p in open_pins)
            findings.append(
                self.finding(
                    description=(
                        f"{comp.reference} ({comp.value or comp.lib_id}) has "
                        f"{len(open_pins)} unconnected pin(s): {pin_list}. Pins that are "
                        "intentionally unused should carry an explicit no-connect marker."
                    ),
                    affected_components=[comp.reference],
                    evidence=[
                        Evidence(kind="pin", reference=f"{comp.reference}.{p.number}",
                                 description=f"pin {p.number} ({p.name or 'unnamed'}, {p.type.value}) has no net")
                        for p in open_pins
                    ],
                    suggested_fix="Wire the pin to its intended net, or place a no-connect flag if unused.",
                    confidence=1.0,
                )
            )
        return findings
