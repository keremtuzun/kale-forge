"""Netlist parsers: SPICE (.cir/.spice and SPICE-style .net) and KiCad S-expression
netlists (.net files starting with "(export"). Netlists are parsed as data only —
no simulator is ever invoked."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.normalized import Component, Net, NetPin, ParserWarning, Pin, PinType
from app.parsers.sexpr import find_all, find_first, first_atom, is_node, parse_sexpr

# element letter -> (expected node count or None for variable, category)
_SPICE_ELEMENTS = {
    "R": 2, "C": 2, "L": 2, "D": 2, "V": 2, "I": 2,
    "Q": 3, "J": 3, "M": 4, "K": 0, "X": None, "U": None,
}


class NetlistData(BaseModel):
    filename: str = ""
    source_format: str = "spice"  # spice | kicad_netlist
    components: list[Component] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    subcircuits: list[str] = Field(default_factory=list)
    warnings: list[ParserWarning] = Field(default_factory=list)


def parse_netlist(text: str, filename: str = "") -> NetlistData:
    if text.lstrip().startswith("(export"):
        return _parse_kicad_netlist(text, filename)
    return _parse_spice(text, filename)


# --- KiCad s-expression netlist ------------------------------------------------------------


def _parse_kicad_netlist(text: str, filename: str) -> NetlistData:
    data = NetlistData(filename=filename, source_format="kicad_netlist")
    root = parse_sexpr(text)
    if not is_node(root, "export"):
        data.warnings.append(ParserWarning(file=filename, message="not a KiCad netlist export"))
        return data

    components_node = find_first(root, "components")
    comps: dict[str, Component] = {}
    if components_node is not None:
        for comp_node in find_all(components_node, "comp"):
            ref = str(first_atom(find_first(comp_node, "ref"), ""))
            if not ref:
                continue
            comps[ref] = Component(
                reference=ref,
                value=str(first_atom(find_first(comp_node, "value"), "")),
                footprint=str(first_atom(find_first(comp_node, "footprint"), "")),
                source_file=filename,
            )

    nets_node = find_first(root, "nets")
    if nets_node is not None:
        for net_node in find_all(nets_node, "net"):
            name = str(first_atom(find_first(net_node, "name"), ""))
            net = Net(name=name)
            for node in find_all(net_node, "node"):
                ref = str(first_atom(find_first(node, "ref"), ""))
                pin_num = str(first_atom(find_first(node, "pin"), ""))
                net.pins.append(NetPin(component=ref, pin=pin_num))
                comp = comps.get(ref)
                if comp is not None:
                    existing = comp.pin(pin_num)
                    if existing is None:
                        comp.pins.append(Pin(number=pin_num, net=name))
                    else:
                        existing.net = name
            data.nets.append(net)

    data.components = list(comps.values())
    return data


# --- SPICE ---------------------------------------------------------------------------------


def _spice_logical_lines(text: str) -> list[tuple[int, str]]:
    """Join '+' continuations; strip comments; return (line_number, content)."""
    out: list[tuple[int, str]] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("*"):
            continue
        if line.lstrip().startswith("+") and out:
            prev_idx, prev = out[-1]
            out[-1] = (prev_idx, prev + " " + line.lstrip()[1:].strip())
        else:
            out.append((idx, line.strip()))
    return out


def _norm_net(node: str) -> str:
    return "GND" if node == "0" else node


def _parse_spice(text: str, filename: str) -> NetlistData:
    data = NetlistData(filename=filename, source_format="spice")
    nets: dict[str, Net] = {}
    in_subckt = 0

    def net_for(name: str) -> Net:
        if name not in nets:
            is_ground = name == "GND"
            nets[name] = Net(name=name, is_ground=is_ground)
        return nets[name]

    lines = _spice_logical_lines(text)
    for line_no, line in lines:
        lower = line.lower()
        if lower.startswith(".subckt"):
            parts = line.split()
            if len(parts) > 1:
                data.subcircuits.append(parts[1])
            in_subckt += 1
            continue
        if lower.startswith(".ends"):
            in_subckt = max(0, in_subckt - 1)
            continue
        if in_subckt:
            continue  # subcircuit internals are out of scope for the top-level netlist
        if lower.startswith("."):
            continue  # .model/.include/.tran/.end etc. — directives, never executed
        tokens = line.split()
        if not tokens:
            continue
        ref = tokens[0]
        letter = ref[0].upper()
        if letter not in _SPICE_ELEMENTS:
            data.warnings.append(
                ParserWarning(file=filename, line=line_no, message=f"unknown SPICE element '{ref}'")
            )
            continue
        node_count = _SPICE_ELEMENTS[letter]
        if letter == "X":
            # X<name> node1 ... nodeN subcktname
            nodes = tokens[1:-1]
            value = tokens[-1] if len(tokens) > 1 else ""
        elif node_count is None or node_count == 0:
            continue  # K (coupling) etc. carry no direct nodes
        else:
            nodes = tokens[1 : 1 + node_count]
            rest = tokens[1 + node_count :]
            value = rest[0] if rest else ""
        if len(nodes) < 2 and letter != "X":
            data.warnings.append(
                ParserWarning(file=filename, line=line_no, message=f"too few nodes for '{ref}'")
            )
            continue
        comp = Component(reference=ref, value=value, source_file=filename)
        for pos, node in enumerate(nodes, start=1):
            net_name = _norm_net(node)
            comp.pins.append(Pin(number=str(pos), type=PinType.PASSIVE, net=net_name))
            net_for(net_name).pins.append(NetPin(component=ref, pin=str(pos)))
        data.components.append(comp)

    data.nets = list(nets.values())
    return data
