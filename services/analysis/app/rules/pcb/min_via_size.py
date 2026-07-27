"""PCB-009: vias below the design-rule (or configured) minimum size."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class MinViaSizeRule(Rule):
    id = "PCB-009"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Via below minimum size"
    description = "Vias below the fab minimum drill/diameter risk breakout and plating failures."
    default_severity = Severity.ERROR

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None and bool(project.board.vias)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        rule_min = project.board.design_rules.min_via_diameter_mm
        dia_limit = rule_min if rule_min else float(ctx.threshold("min_via_diameter_mm", 0.4))
        drill_limit = float(ctx.threshold("min_via_drill_mm", 0.2))
        findings: list[RuleFinding] = []
        for via in project.board.vias:
            problems = []
            if 0 < via.diameter_mm < dia_limit:
                problems.append(f"diameter {via.diameter_mm:g} mm < {dia_limit:g} mm")
            if 0 < via.drill_mm < drill_limit:
                problems.append(f"drill {via.drill_mm:g} mm < {drill_limit:g} mm")
            if not problems:
                continue
            pos = f"({via.position.x:g}, {via.position.y:g})" if via.position else "(?)"
            findings.append(
                self.finding(
                    description=f"Via at {pos} on net '{via.net or '?'}': {'; '.join(problems)}.",
                    affected_nets=[via.net] if via.net else [],
                    evidence=[Evidence(kind="board",
                                       description="; ".join(problems) + (
                                           " (limit from board design rules)" if rule_min else
                                           " (limit from config defaults)"))],
                    suggested_fix="Increase via size to meet the fab's minimums.",
                    confidence=1.0,
                )
            )
        return findings
