"""CONN-004: input pins with no driver on their net (floating inputs)."""
from __future__ import annotations

from app.models.normalized import NormalizedProject, PinType
from app.rules.base import Evidence, Rule, RuleCategory, RuleContext, RuleFinding, Severity

_DRIVER_TYPES = {
    PinType.OUTPUT,
    PinType.BIDIRECTIONAL,
    PinType.TRI_STATE,
    PinType.PASSIVE,
    PinType.POWER_OUT,
    PinType.POWER_IN,  # a rail connection pins the level
    PinType.OPEN_COLLECTOR,
    PinType.OPEN_EMITTER,
    PinType.UNSPECIFIED,
}


class FloatingInputsRule(Rule):
    id = "CONN-004"
    version = "1.0.0"
    category = RuleCategory.CONNECTIVITY
    title = "Floating digital input"
    description = (
        "CMOS inputs left floating pick up noise, oscillate, and raise supply current; every "
        "input needs a defined level."
    )
    default_severity = Severity.ERROR

    def check(self, project: NormalizedProject, ctx: RuleContext) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for net in project.nets:
            input_pins: list[str] = []
            has_driver = net.is_power or net.is_ground
            for np in net.pins:
                comp = ctx.graph.components.get(np.component)
                pin = comp.pin(np.pin) if comp else None
                if pin is None:
                    continue
                if pin.type == PinType.INPUT:
                    input_pins.append(f"{np.component}.{np.pin}")
                elif pin.type in _DRIVER_TYPES:
                    has_driver = True
            if input_pins and not has_driver:
                findings.append(
                    self.finding(
                        description=(
                            f"Net '{net.name}' connects only input pins "
                            f"({', '.join(input_pins)}) with nothing driving or biasing it — "
                            "these inputs float."
                        ),
                        affected_components=sorted({p.split('.')[0] for p in input_pins}),
                        affected_nets=[net.name],
                        evidence=[
                            Evidence(kind="pin", reference=p, description="input pin with no driver on net")
                            for p in input_pins
                        ],
                        suggested_fix="Drive the net from a source or tie it high/low through a resistor.",
                        confidence=0.9,
                    )
                )
        return findings
