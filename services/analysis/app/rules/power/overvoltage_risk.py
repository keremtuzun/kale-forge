"""PWR-002: net voltage exceeds a component's specified maximum (spec data required)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity


class OvervoltageRiskRule(Rule):
    id = "PWR-002"
    version = "1.0.0"
    category = RuleCategory.POWER
    title = "Possible overvoltage on component"
    description = "A connected net's voltage exceeds the component's specified absolute maximum."
    default_severity = Severity.ERROR
    source = "component specifications provided in the component database"

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        specs: dict = ctx.config.get("component_specs", {})
        if not specs:
            return []  # no spec data — never invent limits
        findings: list[RuleFinding] = []
        for comp in project.components:
            spec = specs.get(comp.reference)
            if not spec:
                continue
            max_v = spec.get("max_voltage_v")
            if max_v is None:
                continue
            provenance = spec.get("provenance", "user-provided")
            for pin in comp.pins:
                if not pin.net:
                    continue
                v = ctx.graph.net_voltage(pin.net)
                if v is None or abs(v) <= float(max_v):
                    continue
                findings.append(
                    self.finding(
                        description=(
                            f"{comp.reference} pin {pin.number} connects to '{pin.net}' at {v:g} V, "
                            f"above its specified maximum of {max_v:g} V ({provenance} spec)."
                        ),
                        affected_components=[comp.reference],
                        affected_nets=[pin.net],
                        evidence=[
                            Evidence(kind="spec", reference=comp.reference,
                                     description=f"max_voltage_v = {max_v:g} V (provenance: {provenance})"),
                            Evidence(kind="net", reference=pin.net,
                                     description=f"net voltage {v:g} V inferred from net name"),
                        ],
                        suggested_fix="Level-shift or re-rail the signal, or use a part rated for the voltage.",
                        confidence=0.8 if provenance == "verified" else 0.6,
                    )
                )
        return findings
