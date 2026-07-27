"""Shared analysis helpers: net graph, power/ground heuristics, component classification,
and passive-value parsing. Used by all rule categories and the power-tree builder.

Heuristics here return best-effort classifications; rules that rely on them must set
confidence < 1.0 and cite the heuristic in evidence.
"""
from __future__ import annotations

import re
from typing import Optional

from app.models.normalized import Component, Net, NetPin, NormalizedProject, Pin, PinType

# --- net-name heuristics -----------------------------------------------------------------

GROUND_RE = re.compile(
    r"^(GND[A-Z0-9_]*|AGND|DGND|PGND|VSS[A-Z0-9_]*|GROUND|0|EARTH|CHASSIS)$", re.IGNORECASE
)

POWER_RE = re.compile(
    r"^(\+?-?\d+(?:V|V\d+|\.\d+V)\w*|VCC\w*|VDD\w*|VBAT\w*|VBUS|VIN\w*|VOUT\w*|VSYS|VREG|"
    r"V\+|V-|\+\d+V\d*|-\d+V\d*|PWR\w*|VPP|AVCC|AVDD|USB_?5V|\d+V\d+)$",
    re.IGNORECASE,
)

_VOLTAGE_PATTERNS = [
    (re.compile(r"^([+-]?)(\d+)V(\d+)", re.IGNORECASE), lambda m: float(f"{m.group(2)}.{m.group(3)}") * (-1 if m.group(1) == "-" else 1)),
    (re.compile(r"^([+-]?)(\d+(?:\.\d+)?)\s*V", re.IGNORECASE), lambda m: float(m.group(2)) * (-1 if m.group(1) == "-" else 1)),
    (re.compile(r"^V?([+-]?)(\d+)V(\d+)$", re.IGNORECASE), lambda m: float(f"{m.group(2)}.{m.group(3)}") * (-1 if m.group(1) == "-" else 1)),
]

_KNOWN_RAIL_VOLTAGES = {
    "VBUS": 5.0,
    "USB_5V": 5.0,
    "USB5V": 5.0,
}


def is_ground_name(name: str) -> bool:
    return bool(GROUND_RE.match(name.strip()))


def is_power_name(name: str) -> bool:
    if is_ground_name(name):
        return False
    return bool(POWER_RE.match(name.strip()))


def infer_voltage(net_name: str) -> Optional[float]:
    """Best-effort voltage from a net name: '+3V3' -> 3.3, '5V' -> 5.0, '-12V' -> -12.0.
    Returns None when the name encodes no voltage. Heuristic — callers must treat this
    as inferred, never verified."""
    name = net_name.strip().upper()
    if name in _KNOWN_RAIL_VOLTAGES:
        return _KNOWN_RAIL_VOLTAGES[name]
    stripped = name.lstrip("+")
    for pattern, extract in _VOLTAGE_PATTERNS:
        m = pattern.match(stripped if not name.startswith("-") else name)
        if m:
            try:
                return extract(m)
            except (ValueError, IndexError):
                continue
    return None


# --- passive-value parsing ---------------------------------------------------------------

_VALUE_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*([pnuµmkMG]?)\s*([RΩFHf]?|ohm|Ohm|OHM)?\s*(\d*)\s*$"
)
_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "M": 1e6, "G": 1e9}


def parse_passive_value(value: str) -> Optional[float]:
    """Parse '10k', '4.7u', '100nF', '4R7', '2k2' into a base-unit float.
    Returns None when unparseable. Unit-agnostic (ohms for R, farads for C, henries for L)."""
    if not value:
        return None
    v = value.strip()
    # RKM style: 4R7 = 4.7, 2k2 = 2200
    rkm = re.match(r"^(\d+)([RrKkMmuUnNpP])(\d+)$", v)
    if rkm:
        mult = {"r": 1.0, "k": 1e3, "m": 1e6, "u": 1e-6, "n": 1e-9, "p": 1e-12}[rkm.group(2).lower()]
        return float(f"{rkm.group(1)}.{rkm.group(3)}") * mult
    m = _VALUE_RE.match(v.replace(",", "."))
    if not m:
        return None
    base = float(m.group(1))
    prefix = m.group(2)
    # 'm' vs 'M' matters; normalize k/K
    if prefix == "K":
        prefix = "k"
    return base * _SI.get(prefix, 1.0)


# --- component classification ------------------------------------------------------------

_REF_PREFIX_RE = re.compile(r"^([A-Za-z]+)")


def ref_prefix(reference: str) -> str:
    m = _REF_PREFIX_RE.match(reference)
    return m.group(1).upper() if m else ""


def is_resistor(c: Component) -> bool:
    return ref_prefix(c.reference) == "R" or ":R_" in c.lib_id or c.lib_id.lower().startswith("device:r")


def is_capacitor(c: Component) -> bool:
    return ref_prefix(c.reference) == "C" or c.lib_id.lower().startswith("device:c")


def is_inductor(c: Component) -> bool:
    return ref_prefix(c.reference) == "L" or c.lib_id.lower().startswith("device:l")


def is_led(c: Component) -> bool:
    text = f"{c.lib_id} {c.value}".upper()
    return "LED" in text and ref_prefix(c.reference) in {"D", "LED"} or ref_prefix(c.reference) == "LED"


def is_diode(c: Component) -> bool:
    return ref_prefix(c.reference) == "D" and not is_led(c)


def is_transistor(c: Component) -> bool:
    return ref_prefix(c.reference) == "Q"


def is_connector(c: Component) -> bool:
    return ref_prefix(c.reference) in {"J", "P", "CN", "X", "CON"} or "conn" in c.lib_id.lower()


def is_ic(c: Component) -> bool:
    return ref_prefix(c.reference) == "U"


def is_relay(c: Component) -> bool:
    return ref_prefix(c.reference) in {"K", "RLY"} or "relay" in c.lib_id.lower() or "relay" in c.value.lower()


_REGULATOR_RE = re.compile(
    r"(78\d\d|79\d\d|LM317|LM1117|AMS1117|MCP170\d|AP211\d|XC6206|HT75\d\d|TLV757|TPS7|LD111\d|"
    r"L78|LD29|NCP11|AZ1117|RT9080|ME6211|SPX3819|LP298\d|MIC52|TC1262|REG\d|LDO|BUCK|BOOST|"
    r"TPS5|TPS6|MP15|MP2\d|LM25\d\d|LM2596|MC34063|XL40|AP63)",
    re.IGNORECASE,
)


def is_regulator(c: Component) -> bool:
    text = f"{c.lib_id} {c.value} {c.mpn or ''}"
    return bool(_REGULATOR_RE.search(text))


def is_power_symbol_component(c: Component) -> bool:
    return ref_prefix(c.reference) == "PWR" or c.lib_id.lower().startswith("power:")


# --- net graph -----------------------------------------------------------------------------


class NetGraph:
    """Indexes a NormalizedProject for O(1) lookups used across rules and power analysis."""

    def __init__(self, project: NormalizedProject):
        self.project = project
        self.components: dict[str, Component] = {c.reference: c for c in project.components}
        self.nets: dict[str, Net] = {n.name: n for n in project.nets}
        # net name -> list[(component ref, pin number)]
        self.net_pins: dict[str, list[NetPin]] = {n.name: list(n.pins) for n in project.nets}
        # (ref, pin number) -> net name
        self.pin_net: dict[tuple[str, str], str] = {}
        for net in project.nets:
            for np in net.pins:
                self.pin_net[(np.component, np.pin)] = net.name
        # also index from component pin data (schematic side)
        for c in project.components:
            for p in c.pins:
                if p.net and (c.reference, p.number) not in self.pin_net:
                    self.pin_net[(c.reference, p.number)] = p.net

    # --- lookups ---
    def net_of(self, reference: str, pin_number: str) -> Optional[str]:
        return self.pin_net.get((reference, pin_number))

    def nets_of_component(self, reference: str) -> set[str]:
        c = self.components.get(reference)
        if not c:
            return set()
        nets = {p.net for p in c.pins if p.net}
        nets |= {net for (ref, _pin), net in self.pin_net.items() if ref == reference}
        return nets

    def components_on_net(self, net_name: str) -> set[str]:
        return {np.component for np in self.net_pins.get(net_name, [])}

    def pins_on_net(self, net_name: str) -> list[NetPin]:
        return self.net_pins.get(net_name, [])

    def neighbors(self, reference: str) -> set[str]:
        out: set[str] = set()
        for net in self.nets_of_component(reference):
            out |= self.components_on_net(net)
        out.discard(reference)
        return out

    # --- classifications ---
    def ground_nets(self) -> list[Net]:
        return [n for n in self.project.nets if n.is_ground or is_ground_name(n.name)]

    def power_nets(self) -> list[Net]:
        return [n for n in self.project.nets if (n.is_power or is_power_name(n.name)) and not is_ground_name(n.name)]

    def net_voltage(self, net_name: str) -> Optional[float]:
        net = self.nets.get(net_name)
        if net and net.inferred_voltage is not None:
            return net.inferred_voltage
        return infer_voltage(net_name)

    def unconnected_pins(self) -> list[tuple[Component, Pin]]:
        out = []
        for c in self.project.components:
            for p in c.pins:
                if p.type == PinType.NO_CONNECT:
                    continue
                if not p.net and (c.reference, p.number) not in self.pin_net:
                    out.append((c, p))
        return out

    # --- passive two-pin helper ---
    def other_net(self, c: Component, this_net: str) -> Optional[str]:
        """For a two-pin part, the net on the other pin."""
        nets = [p.net for p in c.pins if p.net]
        for n in nets:
            if n != this_net:
                return n
        return None
