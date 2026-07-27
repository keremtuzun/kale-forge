"""PCB-007: silkscreen text positioned over pads (approximate; needs parsed silk data)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class SilkscreenOverPadsRule(Rule):
    id = "PCB-007"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "Silkscreen over pads"
    description = "Silkscreen printed on pads gets clipped or contaminates solder joints."
    default_severity = Severity.INFO

    def applies(self, project: NormalizedProject) -> bool:
        board = project.board
        return board is not None and bool(getattr(board, "silk_texts", None) or self._extra(project))

    @staticmethod
    def _extra(project: NormalizedProject):
        # silk/pad geometry travels via board extras (set by the analysis service when the
        # PCB parser output is attached); absent for schematic-only projects
        return project.board.design_rules.extra.get("silk_pad_data") if project.board else None

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        data = self._extra(project)
        if not data:
            return []
        findings: list[RuleFinding] = []
        pads = data.get("pads", [])  # [{ref, number, x, y, w, h}]
        for silk in data.get("silk_texts", []):  # [{ref, text, x, y}]
            for pad in pads:
                if abs(silk["x"] - pad["x"]) <= pad["w"] / 2 and abs(silk["y"] - pad["y"]) <= pad["h"] / 2:
                    findings.append(
                        self.finding(
                            description=(
                                f"Silkscreen text '{silk.get('text', '?')}' of {silk.get('ref', '?')} sits over "
                                f"pad {pad.get('ref', '?')}.{pad.get('number', '?')} (position-only approximation)."
                            ),
                            affected_components=[r for r in {silk.get("ref"), pad.get("ref")} if r],
                            evidence=[Evidence(kind="board",
                                               description="silk text anchor inside pad bounding box (text extents not modeled)")],
                            suggested_fix="Move the silkscreen text off exposed copper.",
                            confidence=0.4,
                        )
                    )
                    break
        return findings
