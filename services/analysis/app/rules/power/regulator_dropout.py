"""PWR-001: regulator input voltage below required output + dropout."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.components.regulator_caps import regulator_io_nets
from app.rules.helpers import is_regulator


class RegulatorDropoutRule(Rule):
    id = "PWR-001"
    version = "1.0.0"
    category = RuleCategory.POWER
    title = "Regulator input below required output"
    description = "A linear regulator needs V_in ≥ V_out + dropout to regulate at all."
    default_severity = Severity.ERROR

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        dropout = float(ctx.threshold("regulator_dropout_v", 1.2))
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not is_regulator(comp):
                continue
            vin_net, vout_net = regulator_io_nets(comp, ctx)
            if not vin_net or not vout_net or vin_net == vout_net:
                continue
            v_in = ctx.graph.net_voltage(vin_net)
            v_out = ctx.graph.net_voltage(vout_net)
            if v_in is None or v_out is None:
                continue  # never guess missing voltages
            if v_in >= v_out + dropout:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Regulator {comp.reference} ({comp.value or comp.lib_id}): input '{vin_net}' is "
                        f"{v_in:g} V but output '{vout_net}' is {v_out:g} V — requires "
                        f"V_in ≥ V_out + dropout ({v_out:g} + {dropout:g} = {v_out + dropout:g} V)."
                    ),
                    affected_components=[comp.reference],
                    affected_nets=[vin_net, vout_net],
                    evidence=[
                        Evidence(kind="calculation", description=(
                            f"check: V_in ≥ V_out + V_dropout → {v_in:g} < {v_out:g} + {dropout:g}; "
                            f"dropout threshold from config 'regulator_dropout_v' (default 1.2 V, "
                            "typical for classic linear regulators; LDOs may need less — verify against the datasheet)"
                        )),
                        Evidence(kind="net", reference=vin_net, description=f"inferred {v_in:g} V from net name"),
                        Evidence(kind="net", reference=vout_net, description=f"inferred {v_out:g} V from net name"),
                    ],
                    suggested_fix=(
                        "Raise the input voltage, use a low-dropout (LDO) or buck-boost regulator, "
                        "or correct the net naming if the voltages are mislabeled."
                    ),
                    confidence=0.8,
                )
            )
        return findings
