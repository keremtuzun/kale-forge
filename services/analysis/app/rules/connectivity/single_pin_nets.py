"""CONN-005: nets with exactly one connected pin (isolated / stub nets)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class SinglePinNetsRule(Rule):
    id = "CONN-005"
    version = "1.0.0"
    category = RuleCategory.CONNECTIVITY
    title = "Net with only one connected pin"
    description = "A one-pin net carries no signal anywhere — usually a mislabeled or incomplete connection."
    default_severity = Severity.WARNING

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for net in project.nets:
            if len(net.pins) != 1:
                continue
            np = net.pins[0]
            findings.append(
                self.finding(
                    description=(
                        f"Net '{net.name}' connects only {np.component}.{np.pin}. "
                        "Check for a typo in a label or a missing wire."
                    ),
                    affected_components=[np.component],
                    affected_nets=[net.name],
                    evidence=[
                        Evidence(kind="net", reference=net.name, description="net has exactly one pin"),
                        Evidence(kind="pin", reference=f"{np.component}.{np.pin}", description="only member"),
                    ],
                    suggested_fix="Connect the intended second endpoint or remove the stray label.",
                    confidence=1.0,
                )
            )
        return findings
