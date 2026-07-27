"""Parser for modern KiCad (v6/7/8) .kicad_sch schematic files.

Extracts symbols (with absolute pin positions), wires, junctions, labels, power symbols,
and no-connects, then reconstructs nets by geometric connectivity:

- wire endpoints connect the two ends of the segment;
- a junction connects every wire passing through it;
- a pin / label / power-symbol pin / wire-endpoint connects to a wire if it sits on the
  segment (within tolerance) — plain crossings without a junction do NOT connect;
- net names resolve by priority: power symbol > global label > hierarchical label >
  local label > generated "Net-(REF-PadN)".

Coordinate convention: schematic sheet Y grows downward; symbol-library pin coordinates
have Y growing upward, so a lib pin (px, py) sits at instance + rot(mirror(px, -py)).
"""
from __future__ import annotations

import math
from typing import Optional

from pydantic import BaseModel, Field

from app.models.normalized import (
    Component,
    LabelRef,
    ParserWarning,
    Pin,
    PinType,
    Position,
    PowerSymbol,
)
from app.parsers.sexpr import SExpr, find_all, find_first, first_atom, is_node, parse_sexpr

TOLERANCE_MM = 0.01

_PIN_TYPE_MAP = {
    "input": PinType.INPUT,
    "output": PinType.OUTPUT,
    "bidirectional": PinType.BIDIRECTIONAL,
    "tri_state": PinType.TRI_STATE,
    "passive": PinType.PASSIVE,
    "power_in": PinType.POWER_IN,
    "power_out": PinType.POWER_OUT,
    "open_collector": PinType.OPEN_COLLECTOR,
    "open_emitter": PinType.OPEN_EMITTER,
    "no_connect": PinType.NO_CONNECT,
    "free": PinType.FREE,
    "unspecified": PinType.UNSPECIFIED,
}


class LibPin(BaseModel):
    number: str
    name: str = ""
    type: PinType = PinType.UNSPECIFIED
    x: float = 0.0
    y: float = 0.0
    unit: int = 0  # 0 = common to all units


class PlacedPin(BaseModel):
    number: str
    name: str = ""
    type: PinType = PinType.UNSPECIFIED
    x: float = 0.0
    y: float = 0.0


class PlacedSymbol(BaseModel):
    lib_id: str
    reference: str = ""
    value: str = ""
    footprint: str = ""
    mpn: Optional[str] = None
    fields: dict[str, str] = Field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    mirror: str = ""  # "", "x", "y"
    unit: int = 1
    dnp: bool = False
    in_bom: bool = True
    is_power: bool = False
    pins: list[PlacedPin] = Field(default_factory=list)


class Wire(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class SchLabel(BaseModel):
    text: str
    x: float
    y: float
    kind: str  # local | global | hierarchical


class SchematicData(BaseModel):
    filename: str = ""
    kicad_version: Optional[str] = None
    symbols: list[PlacedSymbol] = Field(default_factory=list)
    wires: list[Wire] = Field(default_factory=list)
    junctions: list[tuple[float, float]] = Field(default_factory=list)
    no_connects: list[tuple[float, float]] = Field(default_factory=list)
    labels: list[SchLabel] = Field(default_factory=list)
    warnings: list[ParserWarning] = Field(default_factory=list)
    # results of net building:
    components: list[Component] = Field(default_factory=list)
    power_symbols: list[PowerSymbol] = Field(default_factory=list)
    global_labels: list[LabelRef] = Field(default_factory=list)
    hierarchical_labels: list[LabelRef] = Field(default_factory=list)
    net_pins: dict[str, list[tuple[str, str]]] = Field(default_factory=dict)  # net -> [(ref, pin#)]


# --- lib symbol extraction -----------------------------------------------------------------


def _parse_lib_pins(lib_symbols_node: SExpr) -> tuple[dict[str, list[LibPin]], dict[str, str]]:
    """Returns (lib_id -> pins, lib_id -> extends-parent-name)."""
    pins_by_lib: dict[str, list[LibPin]] = {}
    extends: dict[str, str] = {}
    for sym in find_all(lib_symbols_node, "symbol"):
        lib_id = str(first_atom(sym, ""))
        if not isinstance(sym[1], str):
            lib_id = str(sym[1])
        ext = find_first(sym, "extends")
        if ext is not None:
            extends[lib_id] = str(first_atom(ext, ""))
        pins: list[LibPin] = []
        for sub in find_all(sym, "symbol"):
            sub_name = str(sub[1]) if len(sub) > 1 and isinstance(sub[1], str) else ""
            unit = 0
            parts = sub_name.rsplit("_", 2)
            if len(parts) == 3:
                try:
                    unit = int(parts[1])
                except ValueError:
                    unit = 0
            for pin_node in find_all(sub, "pin"):
                pins.append(_parse_lib_pin(pin_node, unit))
        # pins can also (rarely) sit directly on the top-level symbol
        for pin_node in find_all(sym, "pin"):
            pins.append(_parse_lib_pin(pin_node, 0))
        pins_by_lib[lib_id] = pins
    return pins_by_lib, extends


def _parse_lib_pin(pin_node: SExpr, unit: int) -> LibPin:
    etype = str(pin_node[1]) if len(pin_node) > 1 and isinstance(pin_node[1], str) else "unspecified"
    at = find_first(pin_node, "at")
    x = float(at[1]) if at and len(at) > 1 else 0.0
    y = float(at[2]) if at and len(at) > 2 else 0.0
    name_node = find_first(pin_node, "name")
    number_node = find_first(pin_node, "number")
    return LibPin(
        number=str(first_atom(number_node, "")),
        name=str(first_atom(name_node, "")),
        type=_PIN_TYPE_MAP.get(etype, PinType.UNSPECIFIED),
        x=x,
        y=y,
        unit=unit,
    )


def _resolve_extends(
    pins_by_lib: dict[str, list[LibPin]], extends: dict[str, str]
) -> dict[str, list[LibPin]]:
    resolved = dict(pins_by_lib)
    for lib_id, parent_name in extends.items():
        if resolved.get(lib_id):
            continue
        # parent name has no library prefix; reuse the child's prefix
        prefix = lib_id.split(":", 1)[0] if ":" in lib_id else ""
        candidates = [parent_name, f"{prefix}:{parent_name}" if prefix else parent_name]
        for cand in candidates:
            if resolved.get(cand):
                resolved[lib_id] = resolved[cand]
                break
    return resolved


# --- instance transform --------------------------------------------------------------------


def _transform_pin(px: float, py: float, sym: PlacedSymbol) -> tuple[float, float]:
    """Lib pin (Y-up) -> sheet coordinates (Y-down) for a placed symbol."""
    # into screen space
    x, y = px, -py
    # mirror (screen space)
    if sym.mirror == "x":
        y = -y
    elif sym.mirror == "y":
        x = -x
    # screen rotation: KiCad symbol angle is visually CCW; with Y-down that is a
    # clockwise mathematical rotation: (x, y) -> (x cosT + y sinT, -x sinT + y cosT)
    t = math.radians(sym.rotation % 360)
    cos_t, sin_t = round(math.cos(t), 9), round(math.sin(t), 9)
    rx = x * cos_t + y * sin_t
    ry = -x * sin_t + y * cos_t
    return (sym.x + rx, sym.y + ry)


# --- main parse ----------------------------------------------------------------------------


def parse_schematic(text: str, filename: str = "") -> SchematicData:
    data = SchematicData(filename=filename)
    root = parse_sexpr(text)
    if not is_node(root, "kicad_sch"):
        data.warnings.append(ParserWarning(file=filename, message="not a kicad_sch document"))
        return data
    version = find_first(root, "version")
    if version is not None:
        data.kicad_version = str(first_atom(version, ""))

    lib_node = find_first(root, "lib_symbols")
    pins_by_lib: dict[str, list[LibPin]] = {}
    if lib_node is not None:
        raw, extends = _parse_lib_pins(lib_node)
        pins_by_lib = _resolve_extends(raw, extends)

    for wire_node in find_all(root, "wire"):
        pts = find_first(wire_node, "pts")
        if pts is None:
            continue
        xys = find_all(pts, "xy")
        if len(xys) >= 2:
            data.wires.append(
                Wire(x1=float(xys[0][1]), y1=float(xys[0][2]), x2=float(xys[-1][1]), y2=float(xys[-1][2]))
            )

    for j in find_all(root, "junction"):
        at = find_first(j, "at")
        if at is not None:
            data.junctions.append((float(at[1]), float(at[2])))

    for nc in find_all(root, "no_connect"):
        at = find_first(nc, "at")
        if at is not None:
            data.no_connects.append((float(at[1]), float(at[2])))

    for tag, kind in (("label", "local"), ("global_label", "global"), ("hierarchical_label", "hierarchical")):
        for lab in find_all(root, tag):
            text_val = str(first_atom(lab, ""))
            at = find_first(lab, "at")
            if at is None:
                continue
            data.labels.append(SchLabel(text=text_val, x=float(at[1]), y=float(at[2]), kind=kind))

    for sym_node in find_all(root, "symbol"):
        placed = _parse_symbol_instance(sym_node, pins_by_lib, data, filename)
        if placed is not None:
            data.symbols.append(placed)

    _build_nets(data, filename)
    return data


def _parse_symbol_instance(
    sym_node: SExpr,
    pins_by_lib: dict[str, list[LibPin]],
    data: SchematicData,
    filename: str,
) -> Optional[PlacedSymbol]:
    lib_id = str(first_atom(find_first(sym_node, "lib_id"), ""))
    at = find_first(sym_node, "at")
    if at is None:
        return None
    sym = PlacedSymbol(
        lib_id=lib_id,
        x=float(at[1]),
        y=float(at[2]),
        rotation=float(at[3]) if len(at) > 3 else 0.0,
    )
    mirror = find_first(sym_node, "mirror")
    if mirror is not None:
        sym.mirror = str(first_atom(mirror, ""))
    unit = find_first(sym_node, "unit")
    if unit is not None:
        try:
            sym.unit = int(first_atom(unit, 1))
        except (TypeError, ValueError):
            sym.unit = 1
    dnp = find_first(sym_node, "dnp")
    sym.dnp = dnp is not None and first_atom(dnp, "no") == "yes"
    in_bom = find_first(sym_node, "in_bom")
    sym.in_bom = in_bom is None or first_atom(in_bom, "yes") == "yes"

    for prop in find_all(sym_node, "property"):
        if len(prop) < 3:
            continue
        key, value = str(prop[1]), str(prop[2])
        sym.fields[key] = value
        if key == "Reference":
            sym.reference = value
        elif key == "Value":
            sym.value = value
        elif key == "Footprint":
            sym.footprint = value
        elif key.upper() in {"MPN", "MANUFACTURER PART NUMBER", "PART NUMBER", "MFR. NO", "MANF#"}:
            sym.mpn = value

    sym.is_power = lib_id.lower().startswith("power:")

    lib_pins = pins_by_lib.get(lib_id, [])
    if not lib_pins:
        data.warnings.append(
            ParserWarning(file=filename, message=f"no library pins found for {lib_id} ({sym.reference})")
        )
    for lp in lib_pins:
        if lp.unit not in (0, sym.unit):
            continue
        px, py = _transform_pin(lp.x, lp.y, sym)
        sym.pins.append(PlacedPin(number=lp.number, name=lp.name, type=lp.type, x=px, y=py))
    return sym


# --- net building --------------------------------------------------------------------------


def _key(x: float, y: float) -> tuple[int, int]:
    return (round(x / TOLERANCE_MM), round(y / TOLERANCE_MM))


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(self, a: tuple[int, int]) -> tuple[int, int]:
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _on_segment(px: float, py: float, w: Wire) -> bool:
    """Point lies on segment (inclusive of endpoints) within tolerance."""
    minx, maxx = min(w.x1, w.x2) - TOLERANCE_MM, max(w.x1, w.x2) + TOLERANCE_MM
    miny, maxy = min(w.y1, w.y2) - TOLERANCE_MM, max(w.y1, w.y2) + TOLERANCE_MM
    if not (minx <= px <= maxx and miny <= py <= maxy):
        return False
    # cross product distance
    dx, dy = w.x2 - w.x1, w.y2 - w.y1
    length = math.hypot(dx, dy)
    if length < TOLERANCE_MM:
        return math.hypot(px - w.x1, py - w.y1) <= TOLERANCE_MM
    dist = abs(dy * (px - w.x1) - dx * (py - w.y1)) / length
    return dist <= TOLERANCE_MM


def _build_nets(data: SchematicData, filename: str) -> None:
    uf = _UnionFind()

    # wires connect their own endpoints
    for w in data.wires:
        uf.union(_key(w.x1, w.y1), _key(w.x2, w.y2))

    # attachment points that can merge onto wire segments
    attach_points: list[tuple[float, float]] = []
    attach_points.extend(data.junctions)
    for w in data.wires:
        attach_points.extend([(w.x1, w.y1), (w.x2, w.y2)])
    for sym in data.symbols:
        for p in sym.pins:
            attach_points.append((p.x, p.y))
    for lab in data.labels:
        attach_points.append((lab.x, lab.y))

    for ax, ay in attach_points:
        for w in data.wires:
            if _on_segment(ax, ay, w):
                uf.union(_key(ax, ay), _key(w.x1, w.y1))

    nc_keys = {_key(x, y) for (x, y) in data.no_connects}

    # group pins by net root
    groups: dict[tuple[int, int], list[tuple[PlacedSymbol, PlacedPin]]] = {}
    for sym in data.symbols:
        for p in sym.pins:
            root = uf.find(_key(p.x, p.y))
            groups.setdefault(root, []).append((sym, p))

    # candidate names per root
    names: dict[tuple[int, int], list[tuple[int, str]]] = {}  # root -> [(priority, name)]

    def add_name(x: float, y: float, priority: int, name: str) -> None:
        root = uf.find(_key(x, y))
        names.setdefault(root, []).append((priority, name))

    for sym in data.symbols:
        if sym.is_power and sym.pins:
            p = sym.pins[0]
            add_name(p.x, p.y, 0, sym.value or sym.lib_id.split(":", 1)[-1])
    for lab in data.labels:
        priority = {"global": 1, "hierarchical": 2, "local": 3}[lab.kind]
        add_name(lab.x, lab.y, priority, lab.text)

    # emit
    counter = 0
    for root, members in groups.items():
        real_pins = [(s, p) for (s, p) in members if not s.is_power]
        if not real_pins:
            continue
        candidates = sorted(names.get(root, []))
        has_nc = any(_key(p.x, p.y) in nc_keys for (_s, p) in members)
        if candidates:
            net_name = candidates[0][1]
        elif len(real_pins) == 1 and has_nc:
            net_name = None  # single pin explicitly marked no-connect
        else:
            s0, p0 = real_pins[0]
            net_name = f"Net-({s0.reference or 'X'}-Pad{p0.number})"
            counter += 1
        if net_name is not None:
            data.net_pins.setdefault(net_name, [])
            for s, p in real_pins:
                if s.reference:
                    data.net_pins[net_name].append((s.reference, p.number))

    # build Component / PowerSymbol / label outputs
    nc_marked: set[tuple[str, str]] = set()
    for sym in data.symbols:
        for p in sym.pins:
            if _key(p.x, p.y) in nc_keys:
                nc_marked.add((sym.reference, p.number))

    pin_to_net: dict[tuple[str, str], str] = {}
    for net_name, pins in data.net_pins.items():
        for ref, num in pins:
            pin_to_net[(ref, num)] = net_name

    for sym in data.symbols:
        if sym.is_power:
            data.power_symbols.append(
                PowerSymbol(
                    name=sym.value or sym.lib_id.split(":", 1)[-1],
                    net=sym.value or None,
                    position=Position(x=sym.x, y=sym.y, rotation=sym.rotation),
                    source_file=filename,
                )
            )
            continue
        comp = Component(
            reference=sym.reference or f"UNREF{len(data.components) + 1}",
            value=sym.value,
            footprint=sym.footprint,
            mpn=sym.mpn,
            lib_id=sym.lib_id,
            dnp=sym.dnp,
            in_bom=sym.in_bom,
            position=Position(x=sym.x, y=sym.y, rotation=sym.rotation),
            fields={k: v for k, v in sym.fields.items() if k not in {"Reference", "Value", "Footprint"}},
            source_file=filename,
        )
        for p in sym.pins:
            ptype = PinType.NO_CONNECT if (sym.reference, p.number) in nc_marked else p.type
            comp.pins.append(
                Pin(number=p.number, name=p.name, type=ptype, net=pin_to_net.get((sym.reference, p.number)))
            )
        data.components.append(comp)

    for lab in data.labels:
        target = LabelRef(
            name=lab.text,
            net=lab.text,
            position=Position(x=lab.x, y=lab.y),
            source_file=filename,
        )
        if lab.kind == "global":
            data.global_labels.append(target)
        elif lab.kind == "hierarchical":
            data.hierarchical_labels.append(target)
