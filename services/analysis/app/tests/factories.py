"""Shared test factories: build NormalizedProject fixtures in code."""
from __future__ import annotations

from app.models.normalized import (
    Component,
    Net,
    NetPin,
    NormalizedProject,
    Pin,
    PinType,
    PowerSymbol,
    ProjectMeta,
)
from app.rules.base import RuleContext
from app.rules.helpers import NetGraph


def make_project(
    components: list[Component],
    nets: dict[str, list[tuple[str, str]]],
    power_symbols: list[str] | None = None,
    name: str = "test",
) -> NormalizedProject:
    """nets: net name -> [(ref, pin_number)]. Pin nets are back-filled onto components."""
    project = NormalizedProject(project=ProjectMeta(name=name))
    project.components = components
    by_ref = {c.reference: c for c in components}
    for net_name, pins in nets.items():
        net = Net(name=net_name, pins=[NetPin(component=r, pin=p) for r, p in pins])
        project.nets.append(net)
        for ref, pin_num in pins:
            comp = by_ref.get(ref)
            if comp is None:
                continue
            pin = comp.pin(pin_num)
            if pin is None:
                comp.pins.append(Pin(number=pin_num, net=net_name))
            else:
                pin.net = net_name
    for ps in power_symbols or []:
        project.power_symbols.append(PowerSymbol(name=ps, net=ps))
    # classify like the normalizer does
    from app.rules import helpers

    for net in project.nets:
        net.is_ground = helpers.is_ground_name(net.name)
        net.is_power = helpers.is_power_name(net.name)
        if net.is_power:
            net.inferred_voltage = helpers.infer_voltage(net.name)
    return project


def comp(
    reference: str,
    value: str = "",
    pins: list[tuple[str, PinType]] | int = 2,
    lib_id: str = "",
    footprint: str = "",
    **kwargs,
) -> Component:
    """comp("R1", "10k") or comp("U1", "MCU", pins=[("1", PinType.POWER_IN), ...])"""
    if isinstance(pins, int):
        pin_objs = [Pin(number=str(i), type=PinType.PASSIVE) for i in range(1, pins + 1)]
    else:
        pin_objs = [Pin(number=n, type=t) for n, t in pins]
    return Component(
        reference=reference, value=value, lib_id=lib_id, footprint=footprint, pins=pin_objs, **kwargs
    )


def ctx_for(project: NormalizedProject, config: dict | None = None) -> RuleContext:
    ctx = RuleContext(config=config or {})
    ctx.graph = NetGraph(project)
    return ctx
