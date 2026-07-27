"""Normalized circuit representation — the single internal format all parsers emit
and all rules, calculations, retrieval, and AI prompts consume.

This module is a shared contract (see docs/architecture.md). Extend it carefully;
do not fork per-module variants.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class PinType(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"
    TRI_STATE = "tri_state"
    PASSIVE = "passive"
    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    OPEN_COLLECTOR = "open_collector"
    OPEN_EMITTER = "open_emitter"
    NO_CONNECT = "no_connect"
    FREE = "free"
    UNSPECIFIED = "unspecified"


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0


class Pin(BaseModel):
    number: str
    name: str = ""
    type: PinType = PinType.UNSPECIFIED
    net: Optional[str] = None  # None = unconnected


class Component(BaseModel):
    reference: str
    value: str = ""
    footprint: str = ""
    mpn: Optional[str] = None
    lib_id: str = ""
    dnp: bool = False  # "do not populate"
    in_bom: bool = True
    position: Optional[Position] = None
    layer: Optional[str] = None  # for PCB-placed parts: F.Cu / B.Cu
    pins: list[Pin] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)  # extra schematic fields
    source_file: str = ""

    def pin(self, number: str) -> Optional[Pin]:
        for p in self.pins:
            if p.number == number:
                return p
        return None


class NetPin(BaseModel):
    component: str  # component reference, e.g. "R1"
    pin: str  # pin number, e.g. "1"


class Net(BaseModel):
    name: str
    pins: list[NetPin] = Field(default_factory=list)
    is_power: bool = False
    is_ground: bool = False
    inferred_voltage: Optional[float] = None
    net_class: Optional[str] = None


class Layer(BaseModel):
    name: str
    kind: str = ""  # signal | power | mixed | user


class Point(BaseModel):
    x: float
    y: float


class Trace(BaseModel):
    net: Optional[str] = None
    layer: str = ""
    width_mm: float = 0.0
    start: Point
    end: Point


class Via(BaseModel):
    net: Optional[str] = None
    position: Optional[Point] = None
    diameter_mm: float = 0.0
    drill_mm: float = 0.0


class Zone(BaseModel):
    net: Optional[str] = None
    layer: str = ""
    filled: bool = False
    area_mm2: Optional[float] = None


class DesignRules(BaseModel):
    min_trace_width_mm: Optional[float] = None
    min_via_diameter_mm: Optional[float] = None
    min_clearance_mm: Optional[float] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Board(BaseModel):
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    outline: list[Point] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    traces: list[Trace] = Field(default_factory=list)
    vias: list[Via] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    design_rules: DesignRules = Field(default_factory=DesignRules)


class MeshBounds(BaseModel):
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def size(self) -> tuple[float, float, float]:
        return (self.max_x - self.min_x, self.max_y - self.min_y, self.max_z - self.min_z)


class Mesh(BaseModel):
    filename: str = ""
    units: str = "unspecified"
    vertices: int = 0
    texture_coordinates: int = 0
    normals: int = 0
    faces: int = 0
    triangles: int = 0
    lines: int = 0
    points: int = 0
    objects: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    material_libraries: list[str] = Field(default_factory=list)
    bounds: Optional[MeshBounds] = None
    boundary_edges: int = 0
    nonmanifold_edges: int = 0
    degenerate_faces: int = 0
    invalid_vertices: int = 0
    invalid_faces: int = 0
    topology_truncated: bool = False


class PowerSymbol(BaseModel):
    name: str  # e.g. "+3V3", "GND"
    net: Optional[str] = None
    position: Optional[Position] = None
    source_file: str = ""


class LabelRef(BaseModel):
    name: str
    net: Optional[str] = None
    position: Optional[Position] = None
    source_file: str = ""


class DifferentialPair(BaseModel):
    name: str  # base name, e.g. "USB_D"
    positive_net: str
    negative_net: str


class ParserWarning(BaseModel):
    file: str = ""
    message: str
    line: Optional[int] = None


class ProjectMeta(BaseModel):
    name: str = ""
    source_format: str = "kicad"  # kicad | spice | mixed
    kicad_version: Optional[str] = None
    files: list[str] = Field(default_factory=list)
    parser_warnings: list[ParserWarning] = Field(default_factory=list)


class NormalizedProject(BaseModel):
    project: ProjectMeta = Field(default_factory=ProjectMeta)
    components: list[Component] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    board: Optional[Board] = None
    mesh: Optional[Mesh] = None
    power_symbols: list[PowerSymbol] = Field(default_factory=list)
    global_labels: list[LabelRef] = Field(default_factory=list)
    hierarchical_labels: list[LabelRef] = Field(default_factory=list)
    differential_pairs: list[DifferentialPair] = Field(default_factory=list)
    unconnected_pins: list[NetPin] = Field(default_factory=list)

    def get_component(self, reference: str) -> Optional[Component]:
        for c in self.components:
            if c.reference == reference:
                return c
        return None

    def get_net(self, name: str) -> Optional[Net]:
        for n in self.nets:
            if n.name == name:
                return n
        return None

    def component_references(self) -> set[str]:
        return {c.reference for c in self.components}

    def net_names(self) -> set[str]:
        return {n.name for n in self.nets}
