"""COMP-009: relay coils without flyback (freewheeling) diode protection."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_diode, is_relay


class RelayNoFlybackRule(Rule):
    id = "COMP-009"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "Relay coil without flyback diode"
    description = (
        "Switching an unprotected relay coil generates a large inductive voltage spike that can "
        "destroy the driving transistor."
    )
    default_severity = Severity.ERROR

    def _coil_nets(self, comp, ctx: RuleContext) -> list[str]:
        # prefer pins named like coil terminals; else use the first two connected pins
        named = [p.net for p in comp.pins
                 if p.net and p.name.strip().lower() in {"coil", "a1", "a2", "+", "-", "1", "2"}]
        if len(named) >= 2:
            return named[:2]
        nets = [p.net for p in comp.pins if p.net]
        return nets[:2]

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not is_relay(comp):
                continue
            coil = self._coil_nets(comp, ctx)
            if len(coil) < 2:
                continue
            protected = any(
                ref != comp.reference
                and (other := ctx.graph.components.get(ref)) is not None
                and is_diode(other)
                and set(coil) <= {p.net for p in other.pins if p.net}
                for ref in ctx.graph.components_on_net(coil[0])
            )
            if protected:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Relay {comp.reference} ({comp.value or comp.lib_id}) coil nets "
                        f"{coil[0]} / {coil[1]} have no anti-parallel diode across them."
                    ),
                    affected_components=[comp.reference],
                    affected_nets=coil,
                    evidence=[
                        Evidence(kind="component", reference=comp.reference,
                                 description="no diode found spanning both coil nets"),
                    ],
                    suggested_fix=(
                        "Add a flyback diode (e.g. 1N4007/1N4148 per coil rating) across the coil, "
                        "cathode to the supply side."
                    ),
                    confidence=0.8,
                )
            )
        return findings
