"""CONN-002: power-input pins on nets with no identifiable power source."""
from __future__ import annotations

from app.models.normalized import NormalizedProject, PinType
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_power_name


class UnpoweredPowerPinsRule(Rule):
    id = "CONN-002"
    version = "1.0.0"
    category = RuleCategory.CONNECTIVITY
    title = "Power input pin without a power source"
    description = "A power_in pin should connect to a power rail or a supply output."
    default_severity = Severity.ERROR

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        power_symbol_nets = {ps.net for ps in project.power_symbols if ps.net}
        for comp in project.components:
            if comp.dnp:
                continue
            for pin in comp.pins:
                if pin.type != PinType.POWER_IN or pin.net is None:
                    continue
                net = pin.net
                if is_power_name(net) or net in power_symbol_nets:
                    continue
                if ctx.graph.nets.get(net) and ctx.graph.nets[net].is_ground:
                    continue  # ground pins are power_in in KiCad symbols; handled elsewhere
                has_source = any(
                    (other := ctx.graph.components.get(np.component)) is not None
                    and (op := other.pin(np.pin)) is not None
                    and op.type == PinType.POWER_OUT
                    for np in ctx.graph.pins_on_net(net)
                    if np.component != comp.reference
                )
                if not has_source:
                    findings.append(
                        self.finding(
                            description=(
                                f"Power input pin {comp.reference}.{pin.number} "
                                f"({pin.name or 'unnamed'}) is on net '{net}', which has no "
                                "power symbol, power-named rail, or supply output pin."
                            ),
                            affected_components=[comp.reference],
                            affected_nets=[net],
                            evidence=[
                                Evidence(kind="pin", reference=f"{comp.reference}.{pin.number}",
                                         description=f"power_in pin on net '{net}'"),
                                Evidence(kind="net", reference=net,
                                         description="net has no identifiable power source"),
                            ],
                            suggested_fix="Connect the pin to the intended supply rail, or add the missing regulator/source.",
                            confidence=0.9,
                        )
                    )
        return findings
