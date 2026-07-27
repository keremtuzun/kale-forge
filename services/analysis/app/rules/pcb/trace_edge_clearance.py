"""PCB-001: traces too close to the board edge."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.pcb._geometry import board_bbox, edge_distance


class TraceEdgeClearanceRule(Rule):
    id = "PCB-001"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Trace too close to board edge"
    description = "Copper near the routed edge risks exposure, shorts, and breakout damage."
    default_severity = Severity.WARNING
    source = "common fab guidance (~0.3 mm copper-to-edge minimum)"

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None and bool(project.board.traces) and bool(project.board.outline)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        clearance = float(ctx.threshold("edge_clearance_mm", 0.3))
        bbox = board_bbox(project.board)
        if bbox is None:
            return []
        offenders: dict[str, float] = {}
        for trace in project.board.traces:
            for pt in (trace.start, trace.end):
                d = edge_distance(pt.x, pt.y, bbox) - trace.width_mm / 2
                if d < clearance:
                    key = trace.net or "(unnamed)"
                    offenders[key] = min(offenders.get(key, 1e9), d)
        return [
            self.finding(
                description=(
                    f"Trace on net '{net}' comes within {max(d, 0):.2f} mm of the board edge "
                    f"(threshold edge_clearance_mm={clearance:g})."
                ),
                affected_nets=[net] if net != "(unnamed)" else [],
                evidence=[Evidence(kind="board",
                                   description=f"copper-to-edge distance {max(d, 0):.2f} mm < {clearance:g} mm")],
                suggested_fix="Pull the trace inside the edge clearance or adjust the outline.",
                confidence=0.9,
            )
            for net, d in sorted(offenders.items())
        ]
