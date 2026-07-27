"""Template circuits (as NormalizedProject) and fault mutators for synthetic data generation.

Imports the analysis app so ground-truth findings come from the real rule engine — the
synthetic data never hand-writes what the rules should say."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ANALYSIS_ROOT = Path(__file__).resolve().parents[3] / "services" / "analysis"
if str(ANALYSIS_ROOT) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_ROOT))

from app.models.normalized import (  # noqa: E402
    Component,
    Net,
    NetPin,
    NormalizedProject,
    Pin,
    PinType,
    ProjectMeta,
)
from app.rules import helpers  # noqa: E402


def _finalize(project: NormalizedProject) -> NormalizedProject:
    for net in project.nets:
        net.is_ground = helpers.is_ground_name(net.name)
        net.is_power = helpers.is_power_name(net.name)
        if net.is_power:
            net.inferred_voltage = helpers.infer_voltage(net.name)
    return project


def _mk(components, nets, name) -> NormalizedProject:
    project = NormalizedProject(project=ProjectMeta(name=name, source_format="synthetic"))
    project.components = components
    by_ref = {c.reference: c for c in components}
    for net_name, pins in nets.items():
        project.nets.append(Net(name=net_name, pins=[NetPin(component=r, pin=p) for r, p in pins]))
        for r, p in pins:
            comp = by_ref.get(r)
            if comp:
                pin = comp.pin(p)
                if pin:
                    pin.net = net_name
                else:
                    comp.pins.append(Pin(number=p, net=net_name))
    return _finalize(project)


def _c(ref, value="", pins=2, lib_id="", **kw):
    if isinstance(pins, int):
        pin_objs = [Pin(number=str(i), type=PinType.PASSIVE) for i in range(1, pins + 1)]
    else:
        pin_objs = [Pin(number=n, name=nm, type=t) for n, nm, t in pins]
    return Component(reference=ref, value=value, lib_id=lib_id, pins=pin_objs, **kw)


# --- template builders (return a clean, known-good baseline) --------------------------------


def valid_circuit() -> NormalizedProject:
    """A genuinely clean reference circuit (should produce zero rule findings): a connector
    feeding a bypassed resistive divider, everything footprinted, consistent value style."""
    return _mk(
        [
            _c("J1", "PWR_IN", pins=[("1", "V", PinType.POWER_OUT), ("2", "G", PinType.POWER_OUT)],
               lib_id="Connector:Conn_01x02", footprint="PinHeader_1x02"),
            _c("C1", "100nF", footprint="C_0603"),
            _c("R1", "10k", footprint="R_0603"),
            _c("R2", "10k", footprint="R_0603"),
        ],
        {"+5V": [("J1", "1"), ("C1", "1"), ("R1", "1")],
         "MID": [("R1", "2"), ("R2", "1")],
         "GND": [("J1", "2"), ("C1", "2"), ("R2", "2")]},
        "valid_circuit",
    )


def led_circuit() -> NormalizedProject:
    return _mk(
        [
            _c("BT1", "3V", lib_id="Device:Battery", footprint="BatteryHolder"),
            _c("R1", "330", footprint="R_0603"),
            _c("D1", "LED", lib_id="Device:LED", footprint="LED_0603"),
        ],
        {"+3V": [("BT1", "1"), ("R1", "1")], "LEDA": [("R1", "2"), ("D1", "1")],
         "GND": [("D1", "2"), ("BT1", "2")]},
        "led_circuit",
    )


def mcu_circuit() -> NormalizedProject:
    mcu = _c("U1", "ATmega328P", pins=[("7", "VCC", PinType.POWER_IN), ("8", "GND", PinType.POWER_IN),
                                       ("1", "RESET", PinType.INPUT)], lib_id="MCU:ATmega328P")
    return _mk(
        [mcu, _c("C1", "100nF", footprint="C_0603"), _c("R1", "10k", footprint="R_0603"),
         _c("U2", "AMS1117-3.3", pins=[("1", "GND", PinType.POWER_IN), ("2", "VOUT", PinType.POWER_OUT),
                                       ("3", "VIN", PinType.POWER_IN)])],
        {"+3V3": [("U1", "7"), ("C1", "1"), ("R1", "1"), ("U2", "2")],
         "+5V": [("U2", "3")],
         "RESET": [("U1", "1"), ("R1", "2")],
         "GND": [("U1", "8"), ("C1", "2"), ("U2", "1")]},
        "mcu_circuit",
    )


def regulator_circuit() -> NormalizedProject:
    reg = _c("U1", "AMS1117-3.3", pins=[("1", "GND", PinType.POWER_IN), ("2", "VOUT", PinType.POWER_OUT),
                                        ("3", "VIN", PinType.POWER_IN)])
    return _mk(
        [reg, _c("J1", "DC_IN", pins=[("1", "V", PinType.POWER_OUT)], lib_id="Connector:Barrel"),
         _c("C1", "10uF"), _c("C2", "10uF")],
        {"+5V": [("J1", "1"), ("U1", "3"), ("C1", "1")],
         "+3V3": [("U1", "2"), ("C2", "1")],
         "GND": [("U1", "1"), ("C1", "2"), ("C2", "2")]},
        "regulator_circuit",
    )


def relay_circuit() -> NormalizedProject:
    relay = _c("K1", "RELAY-5V", pins=[("1", "COIL", PinType.PASSIVE), ("2", "COIL", PinType.PASSIVE)],
               lib_id="Relay:G5V-1")
    q = _c("Q1", "2N7002", pins=[("1", "G", PinType.INPUT), ("2", "S", PinType.PASSIVE), ("3", "D", PinType.PASSIVE)])
    d = _c("D1", "1N4148", pins=[("1", "K", PinType.PASSIVE), ("2", "A", PinType.PASSIVE)], lib_id="Device:D")
    return _mk(
        [relay, q, d, _c("U1", "MCU", pins=[("1", "OUT", PinType.OUTPUT)])],
        {"+5V": [("K1", "1"), ("D1", "1")], "COIL_SW": [("K1", "2"), ("Q1", "3"), ("D1", "2")],
         "GATE": [("Q1", "1"), ("U1", "1")], "GND": [("Q1", "2")]},
        "relay_circuit",
    )


def i2c_circuit() -> NormalizedProject:
    mcu = _c("U1", "MCU", pins=[("1", "SDA", PinType.BIDIRECTIONAL), ("2", "SCL", PinType.BIDIRECTIONAL)])
    eeprom = _c("U2", "24LC256", pins=[("5", "SDA", PinType.BIDIRECTIONAL), ("6", "SCL", PinType.BIDIRECTIONAL)])
    return _mk(
        [mcu, eeprom, _c("R1", "4k7"), _c("R2", "4k7")],
        {"SDA": [("U1", "1"), ("U2", "5"), ("R1", "1")], "SCL": [("U1", "2"), ("U2", "6"), ("R2", "1")],
         "+3V3": [("R1", "2"), ("R2", "2")]},
        "i2c_circuit",
    )


TEMPLATES = {
    "valid": valid_circuit,
    "led": led_circuit,
    "mcu": mcu_circuit,
    "regulator": regulator_circuit,
    "relay": relay_circuit,
    "i2c": i2c_circuit,
}


# --- mutators (each returns a faulty copy + a human label of the injected defect) -----------


def _drop_component(project: NormalizedProject, ref: str) -> NormalizedProject:
    p = copy.deepcopy(project)
    p.components = [c for c in p.components if c.reference != ref]
    for net in p.nets:
        net.pins = [np for np in net.pins if np.component != ref]
    p.nets = [n for n in p.nets if n.pins]
    return p


def remove_led_resistor(project: NormalizedProject) -> NormalizedProject:
    # remove R1 and reconnect the LED anode straight to the rail
    p = _drop_component(project, "R1")
    for net in p.nets:
        if net.name == "+3V":
            net.pins.append(NetPin(component="D1", pin="1"))
    # rename LEDA net members to the rail
    p.nets = [n for n in p.nets if n.name != "LEDA"]
    led = p.get_component("D1")
    if led and led.pin("1"):
        led.pin("1").net = "+3V"
    return _finalize(p)


def remove_decoupling(project: NormalizedProject) -> NormalizedProject:
    return _finalize(_drop_component(project, "C1"))


def remove_flyback(project: NormalizedProject) -> NormalizedProject:
    return _finalize(_drop_component(project, "D1"))


def remove_regulator_cap(project: NormalizedProject) -> NormalizedProject:
    return _finalize(_drop_component(project, "C1"))


def drop_pullups(project: NormalizedProject) -> NormalizedProject:
    p = _drop_component(project, "R1")
    return _finalize(_drop_component(p, "R2"))


def float_input(project: NormalizedProject) -> NormalizedProject:
    # disconnect the reset pull-up so RESET floats
    p = _drop_component(project, "R1")
    return _finalize(p)


MUTATORS = {
    "led": [("remove_led_resistor", remove_led_resistor)],
    "mcu": [("remove_decoupling", remove_decoupling), ("float_input", float_input)],
    "regulator": [("remove_regulator_cap", remove_regulator_cap)],
    "relay": [("remove_flyback", remove_flyback)],
    "i2c": [("drop_pullups", drop_pullups)],
}
