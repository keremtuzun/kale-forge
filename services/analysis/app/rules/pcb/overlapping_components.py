"""PCB-006: same-layer components placed suspiciously close (approximate overlap check)."""
from __future__ import annotations

from itertools import combinations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class OverlappingComponentsRule(Rule):
    id = "PCB-006"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Possibly overlapping components"
    description = "Footprint centers closer than the configured spacing suggest colliding courtyards."
    default_severity = Severity.WARNING

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        min_dist = float(ctx.threshold("overlap_distance_mm", 1.0))
        placed = [c for c in project.components if c.position is not None and c.layer is not None]
        findings: list[RuleFinding] = []
        for a, b in combinations(placed, 2):
            if a.layer != b.layer:
                continue
            d = ((a.position.x - b.position.x) ** 2 + (a.position.y - b.position.y) ** 2) ** 0.5
            if d >= min_dist:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"{a.reference} and {b.reference} centers are {d:.2f} mm apart on {a.layer} "
                        f"(threshold overlap_distance_mm={min_dist:g}). This is a center-distance "
                        "approximation — courtyard data is not evaluated."
                    ),
                    affected_components=[a.reference, b.reference],
                    evidence=[Evidence(kind="board",
                                       description=f"center distance {d:.2f} mm < {min_dist:g} mm (approximation)")],
                    suggested_fix="Check courtyards in the layout tool and separate the parts.",
                    confidence=0.5,
                )
            )
        return findings
