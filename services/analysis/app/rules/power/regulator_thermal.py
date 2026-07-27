"""PWR-009: linear-regulator dissipation estimate above threshold (estimate data required)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.components.regulator_caps import regulator_io_nets
from app.rules.helpers import is_regulator


class RegulatorThermalRule(Rule):
    id = "PWR-009"
    version = "1.0.0"
    category = RuleCategory.POWER
    title = "Regulator thermal risk"
    description = "Estimated linear dissipation P = (V_in − V_out) × I_load exceeds the configured budget."
    default_severity = Severity.WARNING
    source = "linear-regulator dissipation formula; current estimates are user-provided/inferred"

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        estimates: dict[str, float] = ctx.config.get("current_estimates_ma", {})
        if not estimates:
            return []
        max_p = float(ctx.threshold("regulator_max_dissipation_w", 1.0))
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not is_regulator(comp):
                continue
            vin_net, vout_net = regulator_io_nets(comp, ctx)
            if not vin_net or not vout_net:
                continue
            v_in = ctx.graph.net_voltage(vin_net)
            v_out = ctx.graph.net_voltage(vout_net)
            if v_in is None or v_out is None or v_in <= v_out:
                continue
            load_ma = sum(
                estimates[ref]
                for ref in ctx.graph.components_on_net(vout_net)
                if ref != comp.reference and ref in estimates
            )
            if load_ma <= 0:
                continue
            p = (v_in - v_out) * (load_ma / 1000.0)
            if p <= max_p:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Regulator {comp.reference} ({comp.value or comp.lib_id}) would dissipate "
                        f"≈{p:.2f} W as a linear regulator ({vin_net}→{vout_net} at ≈{load_ma:g} mA), "
                        f"above the {max_p:g} W budget."
                    ),
                    affected_components=[comp.reference],
                    affected_nets=[vin_net, vout_net],
                    evidence=[
                        Evidence(kind="calculation", description=(
                            f"P = (V_in − V_out) × I = ({v_in:g} − {v_out:g}) × {load_ma / 1000.0:g} A = {p:.2f} W. "
                            f"Assumptions: linear topology; I from summed load estimates ({load_ma:g} mA, "
                            "user-provided/inferred — not measured); threshold 'regulator_max_dissipation_w' "
                            f"= {max_p:g} W. Uncertainty: actual load and topology unverified."
                        )),
                    ],
                    suggested_fix=(
                        "Use a switching pre-regulator or buck converter, add heatsinking/copper area, "
                        "or reduce the input-output differential."
                    ),
                    confidence=0.6,
                )
            )
        return findings
