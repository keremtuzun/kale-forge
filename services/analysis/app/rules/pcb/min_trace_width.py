"""PCB-008: traces below the design-rule (or configured) minimum width."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class MinTraceWidthRule(Rule):
    id = "PCB-008"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Trace below minimum width"
    description = "Traces under the fab/design minimum may etch open or fail DRC at the fab."
    default_severity = Severity.ERROR

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None and bool(project.board.traces)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        rule_min = project.board.design_rules.min_trace_width_mm
        limit = rule_min if rule_min else float(ctx.threshold("min_trace_width_mm", 0.15))
        limit_src = "board design rules" if rule_min else "config default min_trace_width_mm"
        offenders: dict[str, list[float]] = {}
        for trace in project.board.traces:
            if 0 < trace.width_mm < limit:
                offenders.setdefault(trace.net or "(unnamed)", []).append(trace.width_mm)
        return [
            self.finding(
                description=(
                    f"Net '{net}' has {len(widths)} segment(s) below the minimum trace width: "
                    f"narrowest {min(widths):g} mm < {limit:g} mm ({limit_src})."
                ),
                affected_nets=[net] if net != "(unnamed)" else [],
                evidence=[Evidence(kind="board",
                                   description=f"widths {sorted(set(widths))} mm; limit {limit:g} mm from {limit_src}")],
                suggested_fix="Widen the traces to at least the minimum width.",
                confidence=1.0,
            )
            for net, widths in sorted(offenders.items())
        ]
