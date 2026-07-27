"""CONN-006: design has components but no ground net at all."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class MissingGroundRule(Rule):
    id = "CONN-006"
    version = "1.0.0"
    category = RuleCategory.CONNECTIVITY
    title = "No ground net found"
    description = "Every powered circuit needs a return path; no ground-like net was identified."
    default_severity = Severity.CRITICAL

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        if len(project.components) < 2:
            return []
        if ctx.graph.ground_nets():
            return []
        return [
            self.finding(
                description=(
                    f"The project has {len(project.components)} components but no net named like a "
                    "ground (GND/AGND/DGND/VSS/0). Either the ground is missing or it uses an "
                    "unconventional name this check cannot recognize."
                ),
                evidence=[
                    Evidence(
                        kind="net",
                        description=(
                            "no net matched ground naming patterns; nets present: "
                            + ", ".join(sorted(n.name for n in project.nets)[:20])
                        ),
                    )
                ],
                suggested_fix="Add the ground connection, or rename the return net to a standard ground name.",
                confidence=0.8,
            )
        ]
