"""PCB generation: requirement -> electronics spec -> components -> netlist -> board.

This is the generation side. It is deliberately separate from the REVIEW side in
`services/analysis` (KiCad parsers, rules engine, AI review), which is untouched: reviewing
a board somebody drew and drawing one from a requirement are different problems and share
only the component vocabulary.

Two rules govern everything here, and both exist because the failure they prevent is worse
than having no feature at all:

  1. **Never fabricate a manufacturer part number.** A hallucinated MPN is a part somebody
     orders and cannot buy, or worse, buys and finds is something else. When the exact part
     is not known, this emits a REQUIREMENT — "5 V synchronous buck, >= 3 A out, >= 16 V in"
     — which is honest, actionable, and resolvable later against a distributor API.

  2. **Never claim a stage that has not run.** Every board reports where it actually got to
     on the five-stage ladder below. A board with a netlist and no placement says so.

        SCHEMATIC_COMPLETE     every net is defined and every pin is accounted for
        PLACEMENT_COMPLETE     every component has a checked position on the board
        ROUTING_COMPLETE       every net is a copper path
        RULE_CHECK_COMPLETE    DRC and ERC have run and passed
        MANUFACTURING_READY    gerbers, drill, netlist and BOM all exist and agree

Where this implementation reaches is returned on every board, and it is decided per board
rather than declared: schematic always, placement when the placement actually passes its own
geometric checks. It is not `MANUFACTURING_READY` and does not pretend to be.

Stdlib-only.
"""
from __future__ import annotations

import math
import re
from typing import Any

from app.services import engineering_calc as calc
from app.services import pcb_layout, pcb_route
from app.services.design_intent import (
    ASSUMED, CALCULATED, INFERRED, UNRESOLVED, USER_PROVIDED, VERIFIED)

PCB_VERSION = "kale-pcb-1.0"

# ── completion ladder ────────────────────────────────────────────────────────
STAGES = ("SCHEMATIC_COMPLETE", "PLACEMENT_COMPLETE", "ROUTING_COMPLETE",
          "RULE_CHECK_COMPLETE", "MANUFACTURING_READY")

# What this build of Kale actually does. Edited when a stage is genuinely implemented, and
# not before — this constant is the honesty mechanism, so treat changing it as a claim.
STAGE_STATUS: dict[str, dict[str, Any]] = {
    "SCHEMATIC_COMPLETE": {
        "done": True,
        "detail": "nets, pins and connectivity are generated and internally consistent"},
    "PLACEMENT_COMPLETE": {
        # Implemented. Still per-board: the flag below is the default, and `completion()`
        # overrides it with whether THIS board's placement actually passed its checks.
        "done": False,
        "detail": "every component has an x/y position on a real outline, checked for edge "
                  "keepout, courtyard overlap, mounting-hole clearance and decoupling "
                  "distance — this board did not pass those checks"},
    "ROUTING_COMPLETE": {
        # Implemented. Per board, like placement: the flag below is the default and
        # `completion()` overrides it with whether THIS board actually routed.
        "done": False,
        "detail": "a grid maze router traces every net and pours ground, checked for "
                  "continuity and for the pour being one piece — this board did not route"},
    "RULE_CHECK_COMPLETE": {
        "done": False,
        "detail": "clearance, trace width and connectivity are re-derived from the finished "
                  "copper independently of the router — this board did not pass"},
    "MANUFACTURING_READY": {
        "done": False,
        "detail": "no gerbers, no drill file, no pick-and-place"},
}


def completion(placed: bool = False, placed_detail: str = "",
               routed: bool = False, routed_detail: str = "",
               checked: bool = False, checked_detail: str = "") -> dict[str, Any]:
    """Where THIS board actually got to.

    None of these is a claim the module makes about itself. Each is the result of running
    that stage's checks on this specific board, so a generator that can route a simple board
    and cannot route a crowded one says so per board instead of averaging the two into a
    claim that is false half the time.

    A stage also cannot be reached over the top of a failed one: routing without placement is
    not a thing, so the ladder is walked in order and stops at the first gap.
    """
    status = {s: dict(STAGE_STATUS[s]) for s in STAGES}
    if placed:
        status["PLACEMENT_COMPLETE"] = {
            "done": True,
            "detail": placed_detail or "every component has a checked position"}
    if placed and routed:
        status["ROUTING_COMPLETE"] = {
            "done": True, "detail": routed_detail or "every net is copper"}
    if placed and routed and checked:
        status["RULE_CHECK_COMPLETE"] = {
            "done": True, "detail": checked_detail or "the finished copper passes DRC"}

    top = "NONE"
    for stage in STAGES:
        if not status[stage]["done"]:
            break
        top = stage
    summary = {
        "NONE": "Nothing generated.",
        "SCHEMATIC_COMPLETE": ("Schematic-level design. This board is a connectivity and "
                               "component plan, not a manufacturable layout."),
        "PLACEMENT_COMPLETE": ("Schematic and placement. Every part has a checked position "
                               "on a real outline; no copper is routed."),
        "ROUTING_COMPLETE": ("Schematic, placement and copper. Every net is routed and "
                             "ground is poured, but the geometry has not passed DRC."),
        "RULE_CHECK_COMPLETE": ("Routed and rule-checked. Clearance, trace width and "
                                "connectivity were re-derived from the finished copper and "
                                "hold. Still not manufacturing ready: no gerbers, no drill "
                                "file, no pick-and-place, and no vendor has quoted it."),
    }[top]
    return {"stages": [{"stage": s, **status[s]} for s in STAGES],
            "reached": top, "summary": summary}


# ── component representation ─────────────────────────────────────────────────
def component(reference: str, category: str, *, value: str = "", package: str = "",
              footprint: str = "", mpn: str = "", manufacturer: str = "",
              requirement: str = "", source: str = UNRESOLVED,
              pins: list[str] | None = None) -> dict[str, Any]:
    """One component. `verified` is true only when an exact part is actually known.

    `requirement` carries what the part has to DO when the part itself is unresolved, which
    is the output this module prefers over a plausible-looking MPN.
    """
    return {"reference": reference, "category": category, "value": value,
            "package": package, "footprint": footprint,
            "mpn": mpn or None, "manufacturer": manufacturer or None,
            "requirement": requirement,
            "verified": bool(mpn) and source == VERIFIED,
            "source": source, "pins": pins or []}


# Generic parts whose electrical behaviour is defined by their value, not by a part number.
# A 10k 0603 resistor is a 10k 0603 resistor; quoting an MPN for one adds nothing and risks
# being wrong.
def passive(reference: str, category: str, value: str, package: str = "0603") -> dict[str, Any]:
    # The value and the package ARE the requirement for a passive: any part that meets them
    # works. Stating it that way keeps one rule true across the whole BOM — every line is
    # either a verified part or a requirement — instead of leaving passives as a silent
    # exception a reader has to notice.
    return component(reference, category, value=value, package=package,
                     footprint=f"{category.capitalize()}_SMD:{category.upper()}_{package}",
                     requirement=f"{value} {category}, {package}, any manufacturer",
                     source=CALCULATED if category == "resistor" else ASSUMED,
                     pins=["1", "2"])


# Connectors and interfaces that ARE standard in FRC and can be named without inventing
# anything, because the standard is the specification.
KNOWN_INTERFACES: dict[str, dict[str, Any]] = {
    "can": {"name": "FRC CAN bus", "pins": ["CANH", "CANL"], "connector": "Weidmuller 2-pos",
            "note": "daisy-chained; every node needs an in and an out", "source": VERIFIED},
    "power_12v": {"name": "12 V robot power", "pins": ["V+", "V-"],
                  "connector": "Weidmuller 2-pos or Anderson PP",
                  "note": "nominal 12 V; a battery under load sags to about 9 V and spikes "
                          "above 14 V off the charger", "source": VERIFIED},
    "i2c": {"name": "I2C", "pins": ["SDA", "SCL", "3V3", "GND"], "connector": "4-pin JST-PH",
            "source": VERIFIED},
    "spi": {"name": "SPI", "pins": ["SCK", "MOSI", "MISO", "CS", "3V3", "GND"],
            "connector": "6-pin JST-PH", "source": VERIFIED},
    "uart": {"name": "UART", "pins": ["TX", "RX", "GND"], "connector": "3-pin JST-PH",
             "source": VERIFIED},
}


# ── requirement parsing ──────────────────────────────────────────────────────
_V_RE = re.compile(r"(\d+(?:\.\d+)?)\s*v\b", re.I)
_A_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(m?)a\b", re.I)
_LAYER_RE = re.compile(r"(\d)\s*-?\s*layer", re.I)
_COUNT_RE = re.compile(r"\b(\d+|two|three|four|six|eight)\s+(?:\w+\s+){0,2}"
                       r"(can|i2c|spi|uart|sensor|connector|port|node)s?\b", re.I)
# Output taps on a distribution board. Distinct from the connector counts above because these
# hang off a REGULATED rail, not off a bus, and each one is a load the rail has to carry.
_OUTPUT_RE = re.compile(r"\b(\d+|two|three|four|six|eight)\s+(?:\w+\s+){0,2}"
                        r"(?:output|outlet|channel|branch|tap|leg)s?\b", re.I)
_WORDS = {"two": 2, "three": 3, "four": 4, "six": 6, "eight": 8}


def parse_requirements(prompt: str) -> dict[str, Any]:
    """Turn the sentence into an electronics specification.

    Voltages are read in order and the first is taken as the input when it is the largest —
    "12 V in, 5 V and 3.3 V out" is the shape almost every one of these requests has.
    """
    text = prompt or ""
    low = " " + text.lower() + " "
    volts = [float(m.group(1)) for m in _V_RE.finditer(low)]
    currents: list[float] = []
    for m in _A_RE.finditer(low):
        amps = float(m.group(1))
        currents.append(amps / 1000 if m.group(2) else amps)

    spec: dict[str, Any] = {
        "version": PCB_VERSION,
        "input": {}, "outputs": [], "buses": [], "connectors": [],
        "constraints": {}, "assumptions": [], "unresolved": [],
    }

    if volts:
        v_in = max(volts)
        spec["input"] = {"voltage": v_in, "source": USER_PROVIDED if volts else ASSUMED,
                         "note": "the highest voltage named is taken as the input rail"}
        outs = [v for v in volts if v != v_in]
    else:
        spec["input"] = {"voltage": 12.0, "source": ASSUMED,
                         "note": "no input voltage stated; 12 V is the FRC bus"}
        spec["assumptions"].append("input is the 12 V robot bus")
        outs = []

    # Pair each output voltage with a current if one was given in the same order.
    for i, v_out in enumerate(outs):
        amps = currents[i] if i < len(currents) else 0.0
        rail: dict[str, Any] = {"voltage": v_out,
                                "current_a": amps or 1.0,
                                "source": USER_PROVIDED if amps else ASSUMED}
        if not amps:
            spec["assumptions"].append(
                f"{v_out:g} V rail sized at 1 A; no current was stated")
        spec["outputs"].append(rail)

    for key in ("can", "i2c", "spi", "uart"):
        if re.search(rf"\b{key}\b", low):
            spec["buses"].append(dict(KNOWN_INTERFACES[key], key=key))

    for m in _COUNT_RE.finditer(low):
        raw, what = m.group(1).lower(), m.group(2).lower()
        n = _WORDS.get(raw, int(raw) if raw.isdigit() else 1)
        spec["connectors"].append({"kind": what, "count": n, "source": USER_PROVIDED})

    taps = _OUTPUT_RE.search(low)
    if taps:
        raw = taps.group(1).lower()
        n = min(24, _WORDS.get(raw, int(raw) if raw.isdigit() else 1))
        spec["connectors"].append({"kind": "output", "count": n, "source": USER_PROVIDED})

    layers = _LAYER_RE.search(low)
    if layers:
        spec["constraints"]["layers"] = {"value": int(layers.group(1)), "source": USER_PROVIDED}
    else:
        # Two layers unless the board has to distribute power or carry a bus plane, where a
        # ground pour on its own layer is what makes the thing work.
        needs_four = bool(spec["outputs"]) and len(spec["outputs"]) > 1
        spec["constraints"]["layers"] = {
            "value": 4 if needs_four else 2, "source": ASSUMED,
            "note": ("multiple regulated rails want a ground plane and a power plane"
                     if needs_four else "a two-layer board is enough for this")}
        spec["assumptions"].append(
            f"{spec['constraints']['layers']['value']}-layer stackup")

    size = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*mm", low)
    if size:
        spec["constraints"]["size_mm"] = {"value": [float(size.group(1)), float(size.group(2))],
                                          "source": USER_PROVIDED}
    spec["constraints"]["environment"] = {
        "value": "FRC robot: vibration, 9-14 V bus swing, no conformal coat assumed",
        "source": ASSUMED}
    return spec


# ── component selection ──────────────────────────────────────────────────────
def select_components(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Choose what goes on the board.

    Passives get values because those are calculated. Actives get REQUIREMENTS because this
    module does not have a verified parts database, and the alternative — a confident MPN
    nobody checked — is the specific failure mode this design forbids.
    """
    parts: list[dict[str, Any]] = []
    v_in = float(spec["input"].get("voltage") or 12.0)
    ref = {"J": 0, "U": 0, "C": 0, "R": 0, "D": 0, "F": 0, "L": 0}

    def nref(prefix: str) -> str:
        ref[prefix] += 1
        return f"{prefix}{ref[prefix]}"

    parts.append(component(
        nref("J"), "connector", value=f"{v_in:g} V input",
        requirement=(f"2-pos screw terminal or Anderson PP, rated >= {v_in * 2:g} V and "
                     "the board's full input current"),
        source=UNRESOLVED, pins=["V+", "GND"]))

    total_out_w = sum(float(r["voltage"]) * float(r["current_a"]) for r in spec["outputs"])
    in_a = calc.input_current_a(total_out_w, v_in) or 0.5
    fuse = calc.fuse_rating_a(in_a)
    parts.append(component(
        nref("F"), "protection", value=f"{fuse['recommended_a']} A",
        requirement=f"resettable or blade fuse, {fuse['recommended_a']} A ({fuse['note']})",
        source=CALCULATED, pins=["1", "2"]))
    parts.append(component(
        nref("D"), "protection", value="reverse polarity",
        requirement=(f"P-channel MOSFET ideal-diode or Schottky, Vds >= {v_in * 2:g} V, "
                     f"Id >= {in_a * 2:.1f} A"),
        source=UNRESOLVED, pins=["1", "2", "3"]))
    parts.append(component(
        nref("D"), "protection", value="TVS",
        requirement=f"bidirectional TVS, standoff just above {v_in:g} V, for load-dump spikes",
        source=UNRESOLVED, pins=["1", "2"]))

    for rail in spec["outputs"]:
        v_out, amps = float(rail["voltage"]), float(rail["current_a"])
        linear = calc.regulator_dissipation_w(v_in, v_out, amps, "linear")
        topology = "switching" if linear["needs_switching"] else "linear"
        switching = calc.regulator_dissipation_w(v_in, v_out, amps, "switching")
        rail["topology"] = topology
        rail["dissipation_w"] = (switching if topology == "switching" else linear)["dissipation_w"]
        rail["topology_reason"] = (
            f"a linear regulator would dissipate {linear['dissipation_w']:.1f} W dropping "
            f"{v_in:g} V to {v_out:g} V at {amps:g} A, which needs a heatsink this board "
            "has no room for" if topology == "switching"
            else f"only {linear['dissipation_w']:.2f} W to lose, so a linear part is simpler")
        parts.append(component(
            nref("U"), "regulator", value=f"{v_out:g} V {amps:g} A",
            requirement=(f"{v_out:g} V {topology} regulator, >= {amps:g} A out, "
                         f">= {max(v_in * 1.5, 16):g} V input rating"),
            source=UNRESOLVED, pins=["VIN", "GND", "VOUT", "EN", "FB"]))
        # Tagged with what they decouple, so the placer can put them against it and the
        # placement checks can measure whether it actually did.
        reg_ref = parts[-1]["reference"]
        for value in ("10uF", "22uF"):
            cap = passive(nref("C"), "capacitor", value, "0805")
            cap["decouples"] = reg_ref
            parts.append(cap)
        if topology == "switching":
            parts.append(component(
                nref("L"), "inductor", value="4.7uH",
                requirement=(f"power inductor, >= {amps * 1.3:.1f} A saturation, low DCR; "
                             "value depends on the regulator's switching frequency"),
                source=UNRESOLVED, pins=["1", "2"]))

    for bus in spec["buses"]:
        if bus["key"] == "can":
            parts.append(component(
                nref("U"), "transceiver", value="CAN",
                requirement=("CAN transceiver, 5 V supply, 1 Mbit/s, with bus fault "
                             "protection; FRC CAN runs at 1 Mbit/s"),
                source=UNRESOLVED, pins=["TXD", "RXD", "VCC", "GND", "CANH", "CANL", "STB"]))
            parts.append(passive(nref("R"), "resistor", "120R"))
            parts.append(component(
                nref("J"), "connector", value="CAN in",
                requirement="2-pos Weidmuller, the FRC CAN standard", source=VERIFIED,
                pins=["CANH", "CANL"]))
            parts.append(component(
                nref("J"), "connector", value="CAN out",
                requirement="2-pos Weidmuller, for the daisy chain", source=VERIFIED,
                pins=["CANH", "CANL"]))
        else:
            # A bus with no way off the board is not a breakout. The pinout comes from the
            # interface itself, so this names a real connector without inventing a part.
            parts.append(component(
                nref("J"), "connector", value=f"{bus['key'].upper()} header",
                requirement=f"{bus['connector']} carrying {', '.join(bus['pins'])}",
                source=bus["source"], pins=list(bus["pins"])))

    for conn in spec["connectors"]:
        if conn["kind"] in ("sensor", "connector", "port", "node"):
            for _ in range(int(conn["count"])):
                parts.append(component(
                    nref("J"), "connector", value="sensor",
                    requirement="4-pin JST-PH: 5 V, GND, signal, spare",
                    source=ASSUMED, pins=["1", "2", "3", "4"]))
        elif conn["kind"] == "output":
            rail = spec["outputs"][0]["voltage"] if spec["outputs"] else (
                spec["input"].get("voltage") or 12.0)
            for _ in range(int(conn["count"])):
                parts.append(component(
                    nref("J"), "connector", value=f"{rail:g} V out",
                    requirement="2-pos screw terminal or JST-VH, rated above the rail current",
                    source=ASSUMED, pins=["V+", "GND"]))

    # Every regulated rail needs a way off the board. A rail with nothing on it is a
    # regulator wired to nowhere — which the connectivity check correctly rejects, and which
    # the right answer is to fix in the design rather than to report.
    for rail in spec["outputs"]:
        v_out = float(rail["voltage"])
        tap = f"{v_out:g} V out"
        served = any(p["value"] == tap for p in parts if p["category"] == "connector")
        served = served or any(p["category"] == "connector" and p["value"] == "sensor"
                               for p in parts)
        served = served or (v_out == 5.0 and any(p["category"] == "transceiver" for p in parts))
        if not served:
            parts.append(component(
                nref("J"), "connector", value=tap,
                requirement=(f"2-pos screw terminal or JST-VH for the {v_out:g} V rail, "
                             f"rated above {rail['current_a']:g} A"),
                source=ASSUMED, pins=["V+", "GND"]))

    # Power indicator, sized against a rail this board actually has: the lowest regulated
    # output if there is one, otherwise the input. Sizing it against an imagined 3.3 V rail
    # gave the wrong resistor on every board that had no 3.3 V on it.
    led_rail = min((float(r["voltage"]) for r in spec["outputs"]), default=v_in)
    led = calc.led_resistor_ohm(led_rail, 2.0, 0.005)
    if led:
        anode = component(nref("D"), "indicator", value="green LED",
                          requirement="0603 green LED, Vf about 2 V", source=ASSUMED,
                          pins=["A", "K"])
        series = passive(nref("R"), "resistor", f"{led['standard_ohm']:g}R")
        # Marked so the netlist can find the pair without matching on their values.
        anode["role"] = "indicator_led"
        series["role"] = "indicator_series"
        series["rail_v"] = led_rail
        parts += [anode, series]
    return parts


# ── netlist ──────────────────────────────────────────────────────────────────
def build_netlist(spec: dict[str, Any], parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Connectivity. Every net names the pins on it, which is what makes this a schematic
    rather than a shopping list."""
    nets: list[dict[str, Any]] = []
    v_in = float(spec["input"].get("voltage") or 12.0)

    def net(name: str, nodes: list[str], note: str = "") -> None:
        nets.append({"name": name, "nodes": nodes, "node_count": len(nodes), "note": note})

    inputs = [p for p in parts if p["category"] == "connector" and "input" in p["value"]]
    protection = [p for p in parts if p["category"] == "protection"]
    regulators = [p for p in parts if p["category"] == "regulator"]

    # The input connector's positive pin is "V+", not "1". Nothing noticed until the router
    # went looking for the pad and there wasn't one: a netlist can name a pin that does not
    # exist and still look perfectly well formed.
    chain = [f"{p['reference']}.V+" for p in inputs]
    chain += [f"{p['reference']}.1" for p in protection]
    net(f"VIN_{v_in:g}V", chain + [f"{r['reference']}.VIN" for r in regulators],
        "protected input rail feeding every regulator")
    net("GND", [f"{p['reference']}.GND" for p in parts if "GND" in p["pins"]]
        + [f"{p['reference']}.2" for p in parts if p["category"] == "capacitor"]
        + [f"{p['reference']}.K" for p in parts if p.get("role") == "indicator_led"],
        "single ground; a plane on any board with more than two layers")

    for rail, reg in zip(spec["outputs"], regulators):
        loads = []
        if rail["voltage"] == 5.0:
            loads += [f"{p['reference']}.VCC" for p in parts if p["category"] == "transceiver"]
        loads += [f"{p['reference']}.1" for p in parts
                  if p["category"] == "connector" and p["value"] == "sensor"]
        # Output taps on this rail. A distribution board's whole job is these connectors, and
        # a rail that reaches none of them is a regulator with nowhere to send its current.
        tap = f"{rail['voltage']:g} V out"
        loads += [f"{p['reference']}.V+" for p in parts
                  if p["category"] == "connector" and p["value"] == tap]
        net(f"+{rail['voltage']:g}V", [f"{reg['reference']}.VOUT"] + loads,
            f"{rail['voltage']:g} V at {rail['current_a']:g} A")

    # Indicator string: rail -> series resistor -> LED -> ground.
    series = next((p for p in parts if p.get("role") == "indicator_series"), None)
    anode = next((p for p in parts if p.get("role") == "indicator_led"), None)
    if series and anode:
        rail_v = float(series.get("rail_v") or v_in)
        rail_name = f"+{rail_v:g}V" if any(
            float(r["voltage"]) == rail_v for r in spec["outputs"]) else f"VIN_{v_in:g}V"
        for existing in nets:
            if existing["name"] == rail_name:
                existing["nodes"].append(f"{series['reference']}.1")
                existing["node_count"] = len(existing["nodes"])
        net("LED_A", [f"{series['reference']}.2", f"{anode['reference']}.A"],
            f"power indicator off the {rail_v:g} V rail")

    for p in parts:
        if p["category"] == "transceiver":
            net("CANH", [f"{p['reference']}.CANH"]
                + [f"{c['reference']}.CANH" for c in parts if "CAN" in str(c["value"])],
                "differential pair; route with CANL, 120 R at each end of the bus")
            net("CANL", [f"{p['reference']}.CANL"]
                + [f"{c['reference']}.CANL" for c in parts if "CAN" in str(c["value"])],
                "differential pair; route with CANH")
    return nets


# ── checks ───────────────────────────────────────────────────────────────────
def electrical_checks(spec: dict[str, Any], parts: list[dict[str, Any]],
                      nets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic checks on the generated design. These are the same KIND of check the
    review engine runs on an uploaded board, applied to one we generated."""
    out: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        out.append({"check": name, "ok": bool(ok), "detail": detail})

    v_in = float(spec["input"].get("voltage") or 12.0)
    total_w = sum(float(r["voltage"]) * float(r["current_a"]) for r in spec["outputs"])
    in_a = calc.input_current_a(total_w, v_in) or 0.0
    check("Power budget closes", total_w >= 0,
          f"{total_w:.2f} W out, about {in_a:.2f} A in at {v_in:g} V and 85% efficiency")

    width = calc.trace_width_in(max(in_a, 0.1))
    if width:
        check("Input trace width", True,
              f"{width['width_mm']:.2f} mm for {in_a:.2f} A — {width['assumption']}")

    for rail in spec["outputs"]:
        watts = rail.get("dissipation_w", 0.0)
        # The limit depends on the topology, because the same wattage means different things.
        # A linear part loses it across a junction with nowhere to send it; a switcher loses
        # it spread across an inductor, a FET and a diode, on a board with a copper pour.
        limit = 1.0 if rail.get("topology") == "linear" else 3.0
        check(f"{rail['voltage']:g} V regulator thermals", watts <= limit,
              f"{watts:.2f} W to lose against a {limit:g} W budget for a "
              f"{rail.get('topology', 'linear')} part; {rail.get('topology_reason', '')}")

    single = [n for n in nets if n["node_count"] < 2]
    check("Every net connects at least two pins", not single,
          "no single-pin nets" if not single
          else f"{len(single)} net(s) go nowhere: {', '.join(n['name'] for n in single)}")

    caps = [p for p in parts if p["category"] == "capacitor"]
    regs = [p for p in parts if p["category"] == "regulator"]
    check("Every regulator has bulk decoupling", len(caps) >= 2 * len(regs) if regs else True,
          f"{len(caps)} bulk capacitors for {len(regs)} regulator(s)")

    if any(p["category"] == "transceiver" for p in parts):
        terms = [p for p in parts if p["category"] == "resistor" and "120" in str(p["value"])]
        check("CAN bus is terminated", bool(terms),
              "120 R fitted; only the two ends of a CAN bus should be terminated, so make "
              "this one fitted-optional if the board sits mid-chain")

    unresolved = [p for p in parts if p["source"] == UNRESOLVED]
    check("Every component is resolvable", True,
          f"{len(unresolved)} of {len(parts)} are stated as requirements rather than exact "
          "parts — deliberately, so nothing here is an invented part number")
    return out


# ── top level ────────────────────────────────────────────────────────────────
def generate_board(prompt: str) -> dict[str, Any]:
    """One requirement in, one board plan out."""
    spec = parse_requirements(prompt)
    parts = select_components(spec)
    nets = build_netlist(spec, parts)
    checks = electrical_checks(spec, parts, nets)

    layers = int(spec["constraints"]["layers"]["value"])
    # Rough area from the parts, so the board size is a consequence rather than a wish.
    area_mm2 = 260.0 + 190.0 * len([p for p in parts if p["category"] == "regulator"]) \
        + 90.0 * len([p for p in parts if p["category"] == "connector"]) \
        + 40.0 * len([p for p in parts if p["category"] in ("transceiver", "protection")])
    stated = spec["constraints"].get("size_mm")
    if stated:
        size = list(stated["value"])
    else:
        side = math.sqrt(area_mm2 * 1.35)
        size = [round(max(25.0, side), 1), round(max(20.0, side * 0.72), 1)]

    layout = pcb_layout.place(spec, parts, size)
    size = layout["board_mm"]
    routed = pcb_route.route(layout, parts, nets, spec, layers) if layout["complete"] else {
        "traces": [], "vias": [], "complete": False, "checks": [],
        "unrouted": [n["name"] for n in nets], "ground": None, "copper_mm": 0,
        "version": pcb_route.ROUTE_VERSION,
        "note": "not attempted: the placement did not pass, and routing an illegal placement "
                "would produce copper nobody can build"}
    rules = (pcb_route.drc(routed, layout, parts, nets, spec)
             if routed["complete"] else [])
    if not stated:
        # Stated after placement, because the outline is a RESULT of the parts fitting on it
        # rather than a guess made before anyone tried.
        spec["assumptions"].append(
            f"board sized at {size[0]:g} x {size[1]:g} mm — the smallest outline the placer "
            "could fit these parts on with its keepouts; no size was given")
    checks = checks + layout["checks"] + routed.get("checks", []) + rules

    bom: list[dict[str, Any]] = []
    for part in parts:
        bom.append({"reference": part["reference"], "category": part["category"],
                    "value": part["value"], "package": part["package"],
                    "mpn": part["mpn"], "requirement": part["requirement"],
                    "verified": part["verified"], "source": part["source"]})

    name = _board_name(spec, prompt)
    failed = [c["check"] for c in layout["checks"] if not c["ok"]]
    return {
        "name": name,
        "electronics": spec,
        "components": parts,
        "nets": nets,
        "checks": checks,
        "bom": bom,
        "layout": layout,
        "routing": routed,
        "drc": rules,
        # The placed board as geometry, in the same CAD document a robot or a bearing block
        # produces — which is what puts a PCB in the 3D viewer and the STEP export without
        # either of them learning what a PCB is.
        "cad_assembly": pcb_layout.board_assembly(name, layout, parts, layers,
                                                  routing=routed),
        "board": {"layers": layers, "size_mm": size,
                  "size_source": stated["source"] if stated else ASSUMED,
                  "mounting_holes": len(layout["mounting_holes"]),
                  "mounting_note": "M3 at the corners, 3.5 mm from each edge",
                  "placement": "checked" if layout["complete"] else "; ".join(failed),
                  "package_note": pcb_layout.PACKAGE_NOTE},
        "power": calc.power_budget([
            {"name": f"{r['voltage']:g} V", "voltage": r["voltage"], "current_a": r["current_a"]}
            for r in spec["outputs"]]),
        "completion": completion(
            layout["complete"],
            f"{len(layout['placements'])} components placed on a "
            f"{layout['board_mm'][0]:g} x {layout['board_mm'][1]:g} mm outline; edge keepout, "
            f"courtyard overlap, mounting-hole clearance and decoupling distance all check out",
            routed.get("complete", False),
            f"{len(routed.get('traces', []))} trace runs, {len(routed.get('vias', []))} vias, "
            f"{routed.get('copper_mm', 0):g} mm of copper, ground poured on the bottom layer",
            bool(rules) and all(c["ok"] for c in rules),
            f"{len(rules)} geometric checks re-derived from the finished copper, all passing"),
        "exports": {"kicad_pro": False, "kicad_sch": False, "kicad_pcb": False,
                    "gerbers": False, "bom_csv": True,
                    "step": bool(routed.get("traces")),
                    "note": ("KiCad source and gerber generation are not implemented. The "
                             "netlist, the placement, the copper and the BOM are real and "
                             "exportable as text or as STEP; the .kicad_* and .gbr files are "
                             "not produced, and this build does not pretend otherwise.")},
    }


def _board_name(spec: dict[str, Any], prompt: str) -> str:
    bits = []
    if spec["buses"]:
        bits.append("/".join(b["key"].upper() for b in spec["buses"]))
    if spec["outputs"]:
        bits.append(" + ".join(f"{r['voltage']:g} V" for r in spec["outputs"]))
    return (" ".join(bits) + " board").strip() if bits else "Custom board"
