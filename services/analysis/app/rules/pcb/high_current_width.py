"""PCB-010: power traces thinner than an IPC-2221-style requirement for their estimated
current (requires current estimates)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


def required_width_mm(current_a: float, temp_rise_c: float, copper_mm: float) -> float:
    """IPC-2221 external-layer approximation: I = k · ΔT^0.44 · A^0.725 with k = 0.048,
    A in mil²; solved for width. This is a guideline approximation, not a thermal model."""
    if current_a <= 0:
        return 0.0
    k, b, c = 0.048, 0.44, 0.725
    area_mil2 = (current_a / (k * temp_rise_c**b)) ** (1 / c)
    thickness_mil = copper_mm / 0.0254
    width_mil = area_mil2 / thickness_mil
    return width_mil * 0.0254


class HighCurrentWidthRule(Rule):
    id = "PCB-010"
    version = "1.0.0"
    category = RuleCategory.PCB
    title = "High-current trace below required width"
    description = "Estimated rail current needs more copper than the routed trace provides."
    default_severity = Severity.WARNING
    source = "IPC-2221 external-layer current-capacity approximation"

    def applies(self, project: NormalizedProject) -> bool:
        return project.board is not None and bool(project.board.traces)

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        net_currents: dict[str, float] = ctx.config.get("net_current_ma", {})
        if not net_currents:
            return []  # no estimates — skip rather than guess
        temp_rise = float(ctx.threshold("temp_rise_c", 10.0))
        copper = float(ctx.threshold("copper_thickness_mm", 0.035))
        findings: list[RuleFinding] = []
        for net_name, current_ma in sorted(net_currents.items()):
            current_a = current_ma / 1000.0
            need = required_width_mm(current_a, temp_rise, copper)
            widths = [t.width_mm for t in project.board.traces if t.net == net_name and t.width_mm > 0]
            if not widths:
                continue
            narrowest = min(widths)
            if narrowest >= need:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Net '{net_name}' carries an estimated {current_ma:g} mA but its narrowest "
                        f"trace is {narrowest:g} mm; the IPC-2221 approximation suggests ≥ {need:.2f} mm."
                    ),
                    affected_nets=[net_name],
                    evidence=[
                        Evidence(kind="calculation", description=(
                            f"I = k·ΔT^0.44·A^0.725 (k=0.048, external layer) solved for width: "
                            f"I={current_a:g} A, ΔT={temp_rise:g} °C, copper {copper:g} mm (1 oz ≈ 0.035 mm) "
                            f"→ required ≈ {need:.2f} mm. Assumptions: external layer, estimated current "
                            "(not measured), guideline formula — not a thermal simulation."
                        )),
                    ],
                    suggested_fix="Widen the trace, use a polygon pour, or parallel layers for this rail.",
                    confidence=0.6,
                )
            )
        return findings
