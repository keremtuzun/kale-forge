"""PCB-004: ground zones that are defined but not filled."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_ground_name


class UnfilledGroundZoneRule(Rule):
    id = "PCB-004"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Unfilled ground plane"
    description = "A ground zone with no fill provides no copper — the plane exists only on screen."
    default_severity = Severity.WARNING

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None and bool(project.board.zones)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for zone in project.board.zones:
            if not zone.net or not is_ground_name(zone.net) or zone.filled:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Ground zone on net '{zone.net}' (layer {zone.layer or '?'}) has no filled "
                        "polygon — refill zones before exporting fabrication files."
                    ),
                    affected_nets=[zone.net],
                    evidence=[Evidence(kind="board",
                                       description=f"zone on {zone.layer or '?'} lacks filled_polygon data")],
                    suggested_fix="Refill all zones (KiCad: 'B' hotkey) and re-run DRC before export.",
                    confidence=0.9,
                )
            )
        return findings
