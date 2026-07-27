"""PCB-003: populated board with no copper zones at all."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class NoCopperZonesRule(Rule):
    id = "PCB-003"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "No copper zones on board"
    description = "Boards of any density usually benefit from a ground pour for return paths and EMI."
    default_severity = Severity.INFO

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        min_components = int(ctx.threshold("zone_component_count", 10))
        placed = [c for c in project.components if c.position is not None]
        if project.board.zones or len(placed) < min_components:
            return []
        return [
            self.finding(
                description=(
                    f"The board places {len(placed)} components but defines no copper zones. "
                    "A ground plane/pour is strongly recommended."
                ),
                evidence=[Evidence(kind="board",
                                   description=f"0 zones, {len(placed)} placed components "
                                               f"(threshold zone_component_count={min_components})")],
                suggested_fix="Add a ground zone (and power zones where useful) on appropriate layers.",
                confidence=0.7,
            )
        ]
