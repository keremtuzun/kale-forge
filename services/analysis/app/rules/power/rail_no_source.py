"""PWR-007: power-named nets with no identifiable source feeding them."""
from __future__ import annotations

from app.models.normalized import NormalizedProject, PinType
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_connector, is_regulator, ref_prefix


class RailNoSourceRule(Rule):
    id = "PWR-007"
    version = "1.0.0"
    category = RuleCategory.POWER
    title = "Power rail with no source"
    description = "A rail that nothing feeds will simply be dead at power-up."
    default_severity = Severity.ERROR

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for net in ctx.graph.power_nets():
            members = ctx.graph.components_on_net(net.name)
            if not members:
                continue
            has_source = False
            for ref in members:
                comp = ctx.graph.components.get(ref)
                if comp is None:
                    continue
                if is_regulator(comp) or is_connector(comp) or ref_prefix(ref) in {"BT", "B", "V"}:
                    has_source = True
                    break
                for pin in comp.pins:
                    if pin.net == net.name and pin.type == PinType.POWER_OUT:
                        has_source = True
                        break
                if has_source:
                    break
            if has_source:
                continue
            findings.append(
                self.finding(
                    description=(
                        f"Power net '{net.name}' connects {len(members)} components "
                        f"({', '.join(sorted(members)[:8])}) but no regulator output, connector, "
                        "battery, or supply pin feeds it."
                    ),
                    affected_nets=[net.name],
                    affected_components=sorted(members)[:10],
                    evidence=[
                        Evidence(kind="net", reference=net.name,
                                 description="no source-type component (regulator/connector/battery/power_out pin) on net"),
                    ],
                    suggested_fix="Connect the rail to its supply (regulator output, connector, or battery).",
                    confidence=0.85,
                )
            )
        return findings
