"""CONN-003: multiple push-pull outputs driving the same net."""
from __future__ import annotations

from app.models.normalized import NormalizedProject, PinType
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class ConflictingOutputsRule(Rule):
    id = "CONN-003"
    version = "1.0.0"
    category = RuleCategory.CONNECTIVITY
    title = "Conflicting outputs on one net"
    description = "Two push-pull outputs on the same net can fight and cause damage or undefined levels."
    default_severity = Severity.ERROR

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for net in project.nets:
            drivers: list[str] = []
            for np in net.pins:
                comp = ctx.graph.components.get(np.component)
                pin = comp.pin(np.pin) if comp else None
                if pin is not None and pin.type == PinType.OUTPUT:
                    drivers.append(f"{np.component}.{np.pin}")
            if len(drivers) > 1:
                findings.append(
                    self.finding(
                        description=(
                            f"Net '{net.name}' is driven by {len(drivers)} push-pull outputs "
                            f"({', '.join(drivers)}). Unless this is intentional (and it rarely "
                            "is for plain outputs), the drivers will conflict."
                        ),
                        affected_components=sorted({d.split('.')[0] for d in drivers}),
                        affected_nets=[net.name],
                        evidence=[
                            Evidence(kind="pin", reference=d, description="push-pull output driver")
                            for d in drivers
                        ],
                        suggested_fix=(
                            "Use open-drain/tri-state outputs with a pull-up, add an output "
                            "multiplexer, or remove the extra driver."
                        ),
                        confidence=0.9,
                    )
                )
        return findings
