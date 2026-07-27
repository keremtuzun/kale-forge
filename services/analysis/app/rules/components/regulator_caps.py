"""COMP-011: voltage regulators missing input or output capacitors."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_capacitor, is_regulator

_IN_NAMES = {"vin", "vi", "in", "input", "vcc", "vdd"}
_OUT_NAMES = {"vout", "vo", "out", "output"}


def regulator_io_nets(comp, ctx) -> tuple[str | None, str | None]:
    """(input net, output net) by pin names, falling back to net-voltage ordering."""
    vin = vout = None
    for p in comp.pins:
        if not p.net:
            continue
        name = p.name.strip().lower()
        if name in _IN_NAMES and vin is None:
            vin = p.net
        elif name in _OUT_NAMES and vout is None:
            vout = p.net
    if vin is None or vout is None:
        power_nets = [
            (p.net, ctx.graph.net_voltage(p.net))
            for p in comp.pins
            if p.net and (n := ctx.graph.nets.get(p.net)) is not None and n.is_power
        ]
        with_v = [(net, v) for net, v in power_nets if v is not None]
        with_v.sort(key=lambda t: -t[1])
        if len(with_v) >= 2:
            vin = vin or with_v[0][0]
            vout = vout or with_v[-1][0]
    return vin, vout


class RegulatorCapsRule(Rule):
    id = "COMP-011"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "Regulator missing input/output capacitors"
    description = "Linear and switching regulators require input and output capacitance for stability."
    default_severity = Severity.ERROR
    source = "regulator datasheet application circuits (generic requirement)"

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for comp in project.components:
            if comp.dnp or not is_regulator(comp):
                continue
            vin, vout = regulator_io_nets(comp, ctx)
            for side, net in (("input", vin), ("output", vout)):
                if net is None:
                    continue
                has_cap = any(
                    ref != comp.reference
                    and (other := ctx.graph.components.get(ref)) is not None
                    and is_capacitor(other)
                    for ref in ctx.graph.components_on_net(net)
                )
                if has_cap:
                    continue
                findings.append(
                    self.finding(
                        title=f"Regulator missing {side} capacitor",
                        description=(
                            f"Regulator {comp.reference} ({comp.value or comp.lib_id}) has no capacitor "
                            f"on its {side} net '{net}'. Most regulators oscillate or overshoot without it."
                        ),
                        affected_components=[comp.reference],
                        affected_nets=[net],
                        evidence=[
                            Evidence(kind="net", reference=net, description=f"no capacitor attached to {side} net"),
                            Evidence(kind="component", reference=comp.reference,
                                     description="regulator identified by part-name heuristic"),
                        ],
                        suggested_fix=(
                            f"Add the datasheet-recommended {side} capacitor close to the pin "
                            "(commonly 1–10 µF plus 100 nF; check the specific regulator's requirements)."
                        ),
                        confidence=0.8,
                    )
                )
        return findings
