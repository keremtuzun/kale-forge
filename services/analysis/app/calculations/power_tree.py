"""Power-tree construction: identify sources, regulators, rails, loads, and grounds, then
build a source→regulator→rail→load hierarchy with rail current totals and derived estimates.

Current values are estimates (inferred defaults or user overrides), never measurements —
each carries provenance and every calculation exposes its formula and assumptions."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.calculations.estimates import (
    DEFAULT_CURRENT_MA,
    PowerCalculation,
    battery_runtime,
    efficiency,
    linear_regulator_dissipation,
    rail_current,
    thermal_risk,
)
from app.models.normalized import NormalizedProject
from app.rules.components.regulator_caps import regulator_io_nets
from app.rules.helpers import (
    NetGraph,
    is_connector,
    is_ic,
    is_led,
    is_regulator,
    is_relay,
    ref_prefix,
)


class PowerNode(BaseModel):
    id: str
    kind: str  # source | regulator | rail | load | ground
    name: str
    voltage_v: Optional[float] = None
    voltage_provenance: str = "missing"  # inferred | user-provided | extracted | missing
    current_ma: Optional[float] = None
    current_provenance: str = "missing"
    component_refs: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)


class RailSummary(BaseModel):
    net: str
    voltage_v: Optional[float] = None
    total_current_ma: Optional[float] = None
    load_refs: list[str] = Field(default_factory=list)


class PowerTree(BaseModel):
    nodes: list[PowerNode] = Field(default_factory=list)
    edges: list[list[str]] = Field(default_factory=list)  # [parent_id, child_id]
    rails: list[RailSummary] = Field(default_factory=list)
    calculations: list[PowerCalculation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def _load_class(comp) -> str:
    if is_regulator(comp):
        return "regulator"
    if is_relay(comp):
        return "relay"
    if is_led(comp):
        return "led"
    if is_ic(comp):
        text = f"{comp.value} {comp.lib_id}".lower()
        if any(k in text for k in ("opamp", "op-amp", "lm358", "tl07", "mcp6")):
            return "opamp"
        if any(k in text for k in ("mcu", "stm32", "atmega", "attiny", "esp", "rp2040", "pic", "nrf")):
            return "mcu"
        return "ic"
    if ref_prefix(comp.reference) in {"U"}:
        return "ic"
    return "default"


def _estimate_current(comp, overrides: dict[str, float]) -> tuple[Optional[float], str]:
    if comp.reference in overrides:
        return overrides[comp.reference], "user-provided"
    cls = _load_class(comp)
    if cls in ("regulator", "default") and cls == "default":
        return None, "missing"
    return DEFAULT_CURRENT_MA.get(cls, DEFAULT_CURRENT_MA["default"]), "inferred"


def build_power_tree(
    project: NormalizedProject, current_overrides: Optional[dict[str, float]] = None
) -> PowerTree:
    overrides = current_overrides or {}
    graph = NetGraph(project)
    tree = PowerTree()
    tree.assumptions = [
        "Default per-component currents are inferred class typicals (MCU 20 mA, IC 10 mA, "
        "op-amp 5 mA, LED 10 mA, relay coil 70 mA, regulator quiescent 5 mA).",
        "Voltages are inferred from net names unless overridden.",
        "This tree is an approximation to guide review, not a verified power budget.",
    ]

    node_by_id: dict[str, PowerNode] = {}

    def add_node(node: PowerNode) -> None:
        node_by_id[node.id] = node
        tree.nodes.append(node)

    # ground nodes
    for gnd in graph.ground_nets():
        add_node(PowerNode(id=f"gnd:{gnd.name}", kind="ground", name=gnd.name, voltage_v=0.0,
                           voltage_provenance="inferred"))

    # rails
    rail_nodes: dict[str, PowerNode] = {}
    for rail in graph.power_nets():
        v = graph.net_voltage(rail.name)
        node = PowerNode(
            id=f"rail:{rail.name}", kind="rail", name=rail.name,
            voltage_v=v, voltage_provenance="inferred" if v is not None else "missing",
        )
        rail_nodes[rail.name] = node
        add_node(node)

    # sources (connectors / batteries feeding a power net) and regulators
    for comp in project.components:
        comp_nets = graph.nets_of_component(comp.reference)
        power_nets_here = [n for n in comp_nets if n in rail_nodes]
        if is_regulator(comp):
            vin_net, vout_net = regulator_io_nets(comp, __ctx(graph))
            cur, prov = _estimate_current(comp, overrides)
            reg = PowerNode(
                id=f"reg:{comp.reference}", kind="regulator",
                name=f"{comp.reference} ({comp.value or comp.lib_id})",
                voltage_v=graph.net_voltage(vout_net) if vout_net else None,
                voltage_provenance="inferred" if vout_net and graph.net_voltage(vout_net) is not None else "missing",
                current_ma=cur, current_provenance=prov, component_refs=[comp.reference],
            )
            add_node(reg)
            if vin_net and vin_net in rail_nodes:
                tree.edges.append([rail_nodes[vin_net].id, reg.id])
                rail_nodes[vin_net].children.append(reg.id)
            if vout_net and vout_net in rail_nodes:
                tree.edges.append([reg.id, rail_nodes[vout_net].id])
                reg.children.append(rail_nodes[vout_net].id)
        elif (is_connector(comp) or ref_prefix(comp.reference) in {"BT", "B", "V"}) and power_nets_here:
            src = PowerNode(
                id=f"src:{comp.reference}", kind="source",
                name=f"{comp.reference} ({comp.value or comp.lib_id or 'source'})",
                component_refs=[comp.reference],
            )
            # source voltage = highest connected rail voltage
            volts = [graph.net_voltage(n) for n in power_nets_here if graph.net_voltage(n) is not None]
            if volts:
                src.voltage_v = max(volts)
                src.voltage_provenance = "inferred"
            add_node(src)
            for net in power_nets_here:
                tree.edges.append([src.id, rail_nodes[net].id])
                src.children.append(rail_nodes[net].id)

    # loads on each rail + rail current totals
    for rail_name, rail_node in rail_nodes.items():
        load_currents: dict[str, float] = {}
        load_refs: list[str] = []
        for ref in sorted(graph.components_on_net(rail_name)):
            comp = graph.components.get(ref)
            if comp is None or is_regulator(comp) or is_connector(comp):
                continue
            if ref_prefix(ref) in {"BT", "B"}:
                continue
            cls = _load_class(comp)
            if cls in ("default",) and not is_ic(comp):
                continue  # passives don't draw modeled current
            cur, prov = _estimate_current(comp, overrides)
            load_id = f"load:{ref}"
            if load_id not in node_by_id:
                add_node(PowerNode(
                    id=load_id, kind="load", name=f"{ref} ({comp.value or comp.lib_id or cls})",
                    voltage_v=rail_node.voltage_v, voltage_provenance=rail_node.voltage_provenance,
                    current_ma=cur, current_provenance=prov, component_refs=[ref],
                ))
            tree.edges.append([rail_node.id, load_id])
            rail_node.children.append(load_id)
            load_refs.append(ref)
            if cur is not None:
                load_currents[ref] = cur
        calc = rail_current(rail_name, load_currents)
        tree.calculations.append(calc)
        rail_node.current_ma = calc.value
        rail_node.current_provenance = "inferred" if load_currents else "missing"
        tree.rails.append(RailSummary(
            net=rail_name, voltage_v=rail_node.voltage_v,
            total_current_ma=calc.value, load_refs=load_refs,
        ))

    # per-regulator dissipation / thermal, efficiency
    for node in list(tree.nodes):
        if node.kind != "regulator":
            continue
        comp = graph.components.get(node.component_refs[0]) if node.component_refs else None
        if comp is None:
            continue
        vin_net, vout_net = regulator_io_nets(comp, __ctx(graph))
        v_in = graph.net_voltage(vin_net) if vin_net else None
        v_out = graph.net_voltage(vout_net) if vout_net else None
        out_rail = tree_rail_current(tree, vout_net) if vout_net else None
        p_diss = linear_regulator_dissipation(v_in, v_out, out_rail)
        p_diss.name = f"{comp.reference} dissipation"
        tree.calculations.append(p_diss)
        tree.calculations.append(_named(thermal_risk(p_diss.value), f"{comp.reference} thermal"))
        if v_in and v_out and out_rail:
            p_out = v_out * out_rail / 1000.0
            p_in = v_in * out_rail / 1000.0
            tree.calculations.append(_named(efficiency(p_out, p_in), f"{comp.reference} efficiency"))

    # battery runtime if a battery + total current exist
    battery_refs = [c.reference for c in project.components if ref_prefix(c.reference) in {"BT", "B"}]
    total_current = sum(r.total_current_ma for r in tree.rails if r.total_current_ma) or None
    if battery_refs:
        cap = overrides.get(f"{battery_refs[0]}:capacity_mah")
        tree.calculations.append(battery_runtime(cap, total_current))
        if cap is None:
            tree.warnings.append(
                f"Battery {battery_refs[0]} present but no capacity provided; set it to estimate runtime."
            )

    if not rail_nodes:
        tree.warnings.append("No power rails identified — check net naming or power symbols.")
    return tree


def tree_rail_current(tree: PowerTree, net: Optional[str]) -> Optional[float]:
    for rail in tree.rails:
        if rail.net == net:
            return rail.total_current_ma
    return None


def _named(calc: PowerCalculation, name: str) -> PowerCalculation:
    calc.name = name + " — " + calc.name
    return calc


class __Ctx:
    """Minimal RuleContext shim so regulator_io_nets (which expects ctx.graph) works here."""

    def __init__(self, graph: NetGraph):
        self.graph = graph


def __ctx(graph: NetGraph) -> "__Ctx":
    return __Ctx(graph)
