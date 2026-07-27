"""PCB-012: differential pairs with mismatched routing (length/width/via count)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.pcb._geometry import seg_length


class DiffPairConsistencyRule(Rule):
    id = "PCB-012"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Differential pair inconsistency"
    description = "P/N legs of a differential pair should match in length, width, and via count."
    default_severity = Severity.WARNING

    def applies(self, project: NormalizedProject) -> bool:
        return (
            project.board is not None
            and bool(project.board.traces)
            and bool(project.differential_pairs)
        )

    def _net_stats(self, project: NormalizedProject, net: str) -> tuple[float, set[float], int]:
        length = 0.0
        widths: set[float] = set()
        for t in project.board.traces:
            if t.net == net:
                length += seg_length(t.start, t.end)
                if t.width_mm > 0:
                    widths.add(t.width_mm)
        vias = sum(1 for v in project.board.vias if v.net == net)
        return length, widths, vias

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        max_mismatch_pct = float(ctx.threshold("diff_pair_length_mismatch_pct", 10.0))
        findings: list[RuleFinding] = []
        for pair in project.differential_pairs:
            len_p, widths_p, vias_p = self._net_stats(project, pair.positive_net)
            len_n, widths_n, vias_n = self._net_stats(project, pair.negative_net)
            if len_p == 0 and len_n == 0:
                continue  # not routed yet
            problems: list[str] = []
            longest = max(len_p, len_n)
            if longest > 0:
                mismatch = abs(len_p - len_n) / longest * 100
                if mismatch > max_mismatch_pct:
                    problems.append(
                        f"length mismatch {mismatch:.0f}% ({len_p:.1f} vs {len_n:.1f} mm; "
                        f"threshold {max_mismatch_pct:g}%)"
                    )
            if widths_p and widths_n and widths_p != widths_n:
                problems.append(f"different trace widths ({sorted(widths_p)} vs {sorted(widths_n)} mm)")
            if abs(vias_p - vias_n) > 1:
                problems.append(f"via count differs ({vias_p} vs {vias_n})")
            if not problems:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Differential pair '{pair.name}' ({pair.positive_net}/{pair.negative_net}): "
                        + "; ".join(problems) + "."
                    ),
                    affected_nets=[pair.positive_net, pair.negative_net],
                    evidence=[Evidence(kind="board", description=p) for p in problems],
                    suggested_fix="Route the pair coupled with matched lengths, widths, and via transitions.",
                    confidence=0.5,
                )
            )
        return findings
