"""PWR-006: vias on power nets with small drills (current bottlenecks)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class SmallPowerViasRule(Rule):
    id = "PWR-006"
    version = "1.0.0"
    category = RuleCategory.POWER
    title = "High-current net on small vias"
    description = "Small single vias on power nets add resistance and heat; power transitions need bigger or stitched vias."
    default_severity = Severity.WARNING

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None and bool(project.board.vias)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        min_drill = float(ctx.threshold("power_min_via_drill_mm", 0.3))
        power_names = {n.name for n in ctx.graph.power_nets()}
        offenders: dict[str, list[float]] = {}
        for via in project.board.vias:
            if via.net in power_names and 0 < via.drill_mm < min_drill:
                offenders.setdefault(via.net, []).append(via.drill_mm)
        return [
            self.finding(
                description=(
                    f"Power net '{net}' uses {len(drills)} via(s) with drill "
                    f"{min(drills):g} mm (< power_min_via_drill_mm={min_drill:g} mm)."
                ),
                affected_nets=[net],
                evidence=[
                    Evidence(kind="board",
                             description=f"via drills {sorted(set(drills))} mm on power net; threshold {min_drill:g} mm; "
                                         "prefer multiple stitching vias for high current"),
                ],
                suggested_fix="Use larger vias or multiple parallel vias for power transitions.",
                confidence=0.7,
            )
            for net, drills in sorted(offenders.items())
        ]
