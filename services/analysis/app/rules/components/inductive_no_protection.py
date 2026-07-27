"""COMP-010: transistor-driven inductive loads without clamping protection."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_diode, is_relay, is_transistor, ref_prefix


class InductiveNoProtectionRule(Rule):
    id = "COMP-010"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "Inductive load without clamp protection"
    description = "Motors/solenoids switched by transistors need a freewheel diode, TVS, or snubber."
    default_severity = Severity.WARNING

    def _is_inductive_load(self, comp) -> bool:
        if is_relay(comp):
            return False  # COMP-009 owns relays
        text = f"{comp.value} {comp.lib_id}".lower()
        return (
            ref_prefix(comp.reference) in {"M", "MOT"}
            or "motor" in text
            or "solenoid" in text
            or "buzzer" in text
        )

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not self._is_inductive_load(comp):
                continue
            nets = [p.net for p in comp.pins if p.net]
            if not nets:
                continue
            driven = any(
                (other := ctx.graph.components.get(ref)) is not None and is_transistor(other)
                for net in nets
                for ref in ctx.graph.components_on_net(net)
            )
            if not driven:
                continue
            protected = any(
                (other := ctx.graph.components.get(ref)) is not None and is_diode(other)
                for net in nets
                for ref in ctx.graph.components_on_net(net)
            )
            if protected:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Inductive load {comp.reference} ({comp.value or comp.lib_id}) is switched by a "
                        f"transistor but no diode/clamp was found on its nets ({', '.join(nets)})."
                    ),
                    affected_components=[comp.reference],
                    affected_nets=nets,
                    evidence=[
                        Evidence(kind="component", reference=comp.reference,
                                 description="inductive-load heuristic matched; transistor driver present; no diode on load nets"),
                    ],
                    suggested_fix="Add a freewheel diode across the load (or TVS/snubber sized for the switching energy).",
                    confidence=0.6,
                )
            )
        return findings
