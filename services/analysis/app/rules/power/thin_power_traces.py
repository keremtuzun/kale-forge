"""PWR-005: power-net traces narrower than the configured minimum for power routing."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class ThinPowerTracesRule(Rule):
    id = "PWR-005"
    version = "1.0.0"
    category = RuleCategory.POWER
    title = "Suspiciously thin power trace"
    description = "Power distribution traces need more copper than signal traces."
    default_severity = Severity.WARNING

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None and bool(project.board.traces)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        min_w = float(ctx.threshold("power_min_trace_mm", 0.5))
        power_names = {n.name for n in ctx.graph.power_nets()}
        offenders: dict[str, list[float]] = {}
        for trace in project.board.traces:
            if trace.net in power_names and 0 < trace.width_mm < min_w:
                offenders.setdefault(trace.net, []).append(trace.width_mm)
        findings: list[RuleFinding] = []
        for net, widths in sorted(offenders.items()):
            findings.append(
                self.finding(
                    description=(
                        f"Power net '{net}' has {len(widths)} trace segment(s) as narrow as "
                        f"{min(widths):g} mm (configured power minimum: {min_w:g} mm)."
                    ),
                    affected_nets=[net],
                    evidence=[
                        Evidence(kind="board",
                                 description=f"segment widths {sorted(set(widths))} mm < power_min_trace_mm={min_w:g} "
                                             "(config threshold; actual requirement depends on current — see power analysis)"),
                    ],
                    suggested_fix="Widen power traces or pour a copper area; verify against expected rail current.",
                    confidence=0.7,
                )
            )
        return findings
