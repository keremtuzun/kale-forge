"""COMP-008: LEDs without a series current-limiting resistor."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_led, is_resistor


class LedNoResistorRule(Rule):
    id = "COMP-008"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "LED without current-limiting resistor"
    description = "An LED driven from a voltage rail without series resistance draws uncontrolled current."
    default_severity = Severity.ERROR

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not is_led(comp):
                continue
            nets = [p.net for p in comp.pins if p.net]
            if len(nets) < 2:
                continue
            # a series resistor shares one of the LED's nets
            has_series_r = any(
                other_ref != comp.reference
                and (other := ctx.graph.components.get(other_ref)) is not None
                and is_resistor(other)
                for net in nets
                for other_ref in ctx.graph.components_on_net(net)
            )
            if has_series_r:
                continue
            net_objs = [ctx.graph.nets.get(n) for n in nets]
            rail_count = sum(1 for n in net_objs if n is not None and (n.is_power or n.is_ground))
            if rail_count >= 2:
                confidence, detail = 0.9, "both LED pins connect directly to supply rails"
            elif rail_count == 1:
                confidence, detail = 0.6, "one LED pin is on a supply rail and no series resistor exists on either net"
            else:
                continue  # both nets are signals; could be a driver with internal limiting — stay quiet
            findings.append(
                self.finding(
                    description=(
                        f"LED {comp.reference} ({comp.value or 'LED'}) on nets "
                        f"{' / '.join(nets)} has no series current-limiting resistor: {detail}."
                    ),
                    affected_components=[comp.reference],
                    affected_nets=nets,
                    evidence=[
                        Evidence(kind="component", reference=comp.reference, description=detail),
                        Evidence(kind="net", reference=nets[0],
                                 description="no resistor found on either LED net"),
                    ],
                    suggested_fix=(
                        "Add a series resistor sized as R = (V_supply − V_f) / I_led "
                        "(e.g. ~330 Ω for 5 V, 2 V V_f, 10 mA), or use a current-regulated driver."
                    ),
                    confidence=confidence,
                )
            )
        return findings
