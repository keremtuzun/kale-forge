"""PCB-002: vias too close to the board edge."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.pcb._geometry import board_bbox, edge_distance


class ViaEdgeClearanceRule(Rule):
    id = "PCB-002"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Via too close to board edge"
    description = "Vias near the edge can be torn open during routing/breakout."
    default_severity = Severity.WARNING
    source = "common fab guidance (~0.3 mm copper-to-edge minimum)"

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None and bool(project.board.vias) and bool(project.board.outline)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        clearance = float(ctx.threshold("edge_clearance_mm", 0.3))
        bbox = board_bbox(project.board)
        if bbox is None:
            return []
        findings: list[RuleFinding] = []
        for via in project.board.vias:
            if via.position is None:
                continue
            d = edge_distance(via.position.x, via.position.y, bbox) - via.diameter_mm / 2
            if d >= clearance:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Via on net '{via.net or '?'}' at ({via.position.x:g}, {via.position.y:g}) is "
                        f"{max(d, 0):.2f} mm from the board edge (threshold {clearance:g} mm)."
                    ),
                    affected_nets=[via.net] if via.net else [],
                    evidence=[Evidence(kind="board",
                                       description=f"via edge distance {max(d, 0):.2f} mm < edge_clearance_mm={clearance:g}")],
                    suggested_fix="Move the via inward from the board edge.",
                    confidence=0.9,
                )
            )
        return findings
