"""PCB-005: placed components outside the board outline."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.pcb._geometry import board_bbox, inside


class ComponentsOutsideBoardRule(Rule):
    id = "PCB-005"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Component outside board outline"
    description = "Parts placed outside the outline will not exist on the manufactured board."
    default_severity = Severity.ERROR

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None and bool(project.board.outline)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        bbox = board_bbox(project.board)
        if bbox is None:
            return []
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.position is None or comp.layer is None:
                continue  # only PCB-placed components
            if inside(comp.position.x, comp.position.y, bbox, margin=0.0):
                continue
            findings.append(
                self.finding(
                    description=(
                        f"{comp.reference} is placed at ({comp.position.x:g}, {comp.position.y:g}), "
                        f"outside the board outline bbox {bbox}."
                    ),
                    affected_components=[comp.reference],
                    evidence=[Evidence(kind="board", reference=comp.reference,
                                       description=f"position outside outline bbox {bbox}")],
                    suggested_fix="Move the component inside the board outline.",
                    confidence=0.95,
                )
            )
        return findings
