"""PWR-004: capacitor voltage rating too close to rail voltage (spec data required)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_capacitor


class VoltageRatingMarginRule(Rule):
    id = "PWR-004"
    version = "1.0.0"
    category = RuleCategory.POWER
    title = "Insufficient voltage rating margin"
    description = "Capacitors (especially ceramics/electrolytics) need derating headroom above the rail voltage."
    default_severity = Severity.WARNING
    source = "standard derating practice (spec data from component database)"

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        specs: dict = ctx.config.get("component_specs", {})
        if not specs:
            return []
        derating = float(ctx.threshold("derating_factor", 1.25))
        findings: list[RuleFinding] = []
        for comp in project.components:
            if not is_capacitor(comp):
                continue
            spec = specs.get(comp.reference)
            if not spec:
                continue
            rating = spec.get("voltage_rating_v") or spec.get("max_voltage_v")
            if rating is None:
                continue
            provenance = spec.get("provenance", "user-provided")
            for pin in comp.pins:
                if not pin.net:
                    continue
                v = ctx.graph.net_voltage(pin.net)
                if v is None or v <= 0:
                    continue
                required = v * derating
                if float(rating) >= required:
                    continue
                findings.append(
                    self.finding(
                        description=(
                            f"Capacitor {comp.reference} is rated {rating:g} V but sits on '{pin.net}' "
                            f"at {v:g} V; with a ×{derating:g} derating factor it should be rated "
                            f"≥ {required:g} V."
                        ),
                        affected_components=[comp.reference],
                        affected_nets=[pin.net],
                        evidence=[
                            Evidence(kind="calculation",
                                     description=f"required ≥ V_rail × derating = {v:g} × {derating:g} = {required:g} V "
                                                 "(config 'derating_factor')"),
                            Evidence(kind="spec", reference=comp.reference,
                                     description=f"voltage rating {rating:g} V (provenance: {provenance})"),
                        ],
                        suggested_fix="Use a higher-voltage-rated capacitor (and mind ceramic DC-bias derating).",
                        confidence=0.7,
                    )
                )
                break  # one finding per capacitor
        return findings
