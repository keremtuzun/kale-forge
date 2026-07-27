"""COMP-012: IC power pins without any decoupling capacitor on their rail (schematic level).
PCB proximity is checked separately by PCB-011."""
from __future__ import annotations

from app.models.normalized import NormalizedProject, PinType
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_capacitor, is_ic


class DecouplingNearIcRule(Rule):
    id = "COMP-012"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "IC without decoupling capacitor"
    description = "Every IC supply pin needs local decoupling to ground; missing caps cause resets and noise."
    default_severity = Severity.WARNING

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not is_ic(comp):
                continue
            uncovered: list[tuple[str, str]] = []  # (pin, net)
            for pin in comp.pins:
                if pin.type != PinType.POWER_IN or not pin.net:
                    continue
                net = ctx.graph.nets.get(pin.net)
                if net is None or net.is_ground:
                    continue
                covered = False
                for ref in ctx.graph.components_on_net(pin.net):
                    other = ctx.graph.components.get(ref)
                    if other is None or not is_capacitor(other) or other.dnp:
                        continue
                    other_side = ctx.graph.other_net(other, pin.net)
                    other_net = ctx.graph.nets.get(other_side) if other_side else None
                    if other_net is not None and other_net.is_ground:
                        covered = True
                        break
                if not covered:
                    uncovered.append((pin.number, pin.net))
            if not uncovered:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"IC {comp.reference} ({comp.value or comp.lib_id}) has supply pins without any "
                        f"decoupling capacitor to ground: "
                        + ", ".join(f"pin {p} ({n})" for p, n in uncovered)
                    ),
                    affected_components=[comp.reference],
                    affected_nets=sorted({n for _, n in uncovered}),
                    evidence=[
                        Evidence(kind="pin", reference=f"{comp.reference}.{p}",
                                 description=f"supply pin on '{n}' with no cap-to-ground on that rail")
                        for p, n in uncovered
                    ],
                    suggested_fix=(
                        "Add a 100 nF ceramic per supply pin (plus bulk capacitance per rail), placed "
                        "close to the pin during layout."
                    ),
                    confidence=0.85,
                )
            )
        return findings
