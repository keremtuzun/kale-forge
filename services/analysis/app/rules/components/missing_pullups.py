"""COMP-007: likely missing pull-up/pull-down resistors (I2C buses, control pins)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity
from app.rules.helpers import is_resistor

_I2C_NAMES = {"sda", "scl", "sda1", "scl1", "sda2", "scl2", "i2c_sda", "i2c_scl"}
_CONTROL_NAMES = {"~reset", "reset", "nrst", "~rst", "rst", "en", "enable", "cs", "~cs", "ncs", "ss", "~ss"}


class MissingPullupsRule(Rule):
    id = "COMP-007"
    version = "1.0.0"
    category = RuleCategory.COMPONENTS
    title = "Likely missing pull-up/pull-down resistor"
    description = "I2C lines require pull-ups; reset/enable/chip-select pins usually need a defined idle level."
    default_severity = Severity.WARNING
    source = "I2C-bus specification (open-drain bus requires pull-ups); standard engineering practice"

    def _net_has_resistor_to_rail(self, net_name: str, ctx: RuleContext) -> bool:
        for ref in ctx.graph.components_on_net(net_name):
            comp = ctx.graph.components.get(ref)
            if comp is None or not is_resistor(comp):
                continue
            other = ctx.graph.other_net(comp, net_name)
            if other is None:
                continue
            other_net = ctx.graph.nets.get(other)
            if other_net is not None and (other_net.is_power or other_net.is_ground):
                return True
        return False

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for net in project.nets:
            lname = net.name.lower()
            pin_names = set()
            for np in net.pins:
                comp = ctx.graph.components.get(np.component)
                pin = comp.pin(np.pin) if comp else None
                if pin is not None and pin.name:
                    pin_names.add(pin.name.lower())
            is_i2c = lname in _I2C_NAMES or bool(pin_names & _I2C_NAMES)
            is_control = bool(pin_names & _CONTROL_NAMES) or lname in _CONTROL_NAMES
            if not (is_i2c or is_control):
                continue
            if self._net_has_resistor_to_rail(net.name, ctx):
                continue
            findings.append(
                self.finding(
                    severity=Severity.WARNING if is_i2c else Severity.INFO,
                    title="Missing I2C pull-ups" if is_i2c else "Control pin without defined idle level",
                    description=(
                        f"Net '{net.name}' looks like an "
                        + ("I2C line (open-drain bus) but has no pull-up resistor to a supply rail."
                           if is_i2c else
                           "IC control line (reset/enable/chip-select) with no pull resistor setting its idle state.")
                    ),
                    affected_nets=[net.name],
                    affected_components=sorted({np.component for np in net.pins}),
                    evidence=[
                        Evidence(kind="net", reference=net.name,
                                 description=f"matched by name/pin-name heuristic ({', '.join(sorted(pin_names)[:5]) or lname}); "
                                             "no resistor to a rail found on this net"),
                    ],
                    suggested_fix=(
                        "Add pull-up resistors (typically 2.2k–10k to the bus supply)." if is_i2c
                        else "Add a pull-up/pull-down (typically 10k) so the pin idles in a safe state."
                    ),
                    confidence=0.7 if is_i2c else 0.5,
                )
            )
        return findings
