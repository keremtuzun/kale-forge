"""Parser for modern KiCad .kicad_pcb board files: nets, footprints (with pads and
silkscreen text), traces, vias, zones, board outline, layers, and design rules."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.normalized import (
    Board,
    DesignRules,
    Layer,
    ParserWarning,
    Point,
    Position,
    Trace,
    Via,
    Zone,
)
from app.parsers.sexpr import SExpr, find_all, find_first, first_atom, is_node, parse_sexpr


class PcbPad(BaseModel):
    number: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    net: Optional[str] = None


class SilkText(BaseModel):
    text: str
    x: float
    y: float
    layer: str


class PcbFootprint(BaseModel):
    reference: str = ""
    footprint_id: str = ""
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    layer: str = "F.Cu"
    pads: list[PcbPad] = Field(default_factory=list)
    silk_texts: list[SilkText] = Field(default_factory=list)


class PcbData(BaseModel):
    filename: str = ""
    nets: dict[int, str] = Field(default_factory=dict)
    footprints: list[PcbFootprint] = Field(default_factory=list)
    board: Board = Field(default_factory=Board)
    warnings: list[ParserWarning] = Field(default_factory=list)


def _net_name(node: SExpr, table: dict[int, str]) -> Optional[str]:
    net = find_first(node, "net")
    if net is None or len(net) < 2:
        return None
    if len(net) >= 3 and isinstance(net[2], str):
        return net[2] or None
    try:
        return table.get(int(net[1])) or None
    except (TypeError, ValueError):
        return None


def parse_pcb(text: str, filename: str = "") -> PcbData:
    data = PcbData(filename=filename)
    root = parse_sexpr(text)
    if not is_node(root, "kicad_pcb"):
        data.warnings.append(ParserWarning(file=filename, message="not a kicad_pcb document"))
        return data

    for net_node in find_all(root, "net"):
        if len(net_node) >= 3:
            try:
                data.nets[int(net_node[1])] = str(net_node[2])
            except (TypeError, ValueError):
                continue

    layers_node = find_first(root, "layers")
    if layers_node is not None:
        for entry in layers_node[1:]:
            if isinstance(entry, list) and len(entry) >= 3:
                data.board.layers.append(Layer(name=str(entry[1]), kind=str(entry[2])))

    setup = find_first(root, "setup")
    dr = data.board.design_rules
    if setup is not None:
        for key, attr in (
            ("min_track_width", "min_trace_width_mm"),
            ("min_via_diameter", "min_via_diameter_mm"),
            ("min_clearance", "min_clearance_mm"),
        ):
            node = find_first(setup, key)
            if node is not None:
                try:
                    setattr(dr, attr, float(first_atom(node, 0)))
                except (TypeError, ValueError):
                    pass

    # traces
    for seg in find_all(root, "segment"):
        start, end = find_first(seg, "start"), find_first(seg, "end")
        width = find_first(seg, "width")
        layer = find_first(seg, "layer")
        if start is None or end is None:
            continue
        data.board.traces.append(
            Trace(
                net=_net_name(seg, data.nets),
                layer=str(first_atom(layer, "")) if layer else "",
                width_mm=float(first_atom(width, 0)) if width else 0.0,
                start=Point(x=float(start[1]), y=float(start[2])),
                end=Point(x=float(end[1]), y=float(end[2])),
            )
        )

    # vias
    for via_node in find_all(root, "via"):
        at = find_first(via_node, "at")
        size = find_first(via_node, "size")
        drill = find_first(via_node, "drill")
        data.board.vias.append(
            Via(
                net=_net_name(via_node, data.nets),
                position=Point(x=float(at[1]), y=float(at[2])) if at else None,
                diameter_mm=float(first_atom(size, 0)) if size else 0.0,
                drill_mm=float(first_atom(drill, 0)) if drill else 0.0,
            )
        )

    # zones
    for zone_node in find_all(root, "zone"):
        net_name_node = find_first(zone_node, "net_name")
        layer_node = find_first(zone_node, "layer") or find_first(zone_node, "layers")
        data.board.zones.append(
            Zone(
                net=str(first_atom(net_name_node, "")) or _net_name(zone_node, data.nets),
                layer=str(first_atom(layer_node, "")) if layer_node else "",
                filled=find_first(zone_node, "filled_polygon") is not None,
            )
        )

    # board outline from Edge.Cuts graphics
    outline_pts: list[Point] = []
    for tag in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly"):
        for g in find_all(root, tag):
            layer = find_first(g, "layer")
            if layer is None or first_atom(layer, "") != "Edge.Cuts":
                continue
            for pt_tag in ("start", "end", "mid", "center"):
                pt = find_first(g, pt_tag)
                if pt is not None:
                    outline_pts.append(Point(x=float(pt[1]), y=float(pt[2])))
            pts = find_first(g, "pts")
            if pts is not None:
                for xy in find_all(pts, "xy"):
                    outline_pts.append(Point(x=float(xy[1]), y=float(xy[2])))
    if outline_pts:
        xs = [p.x for p in outline_pts]
        ys = [p.y for p in outline_pts]
        data.board.outline = outline_pts
        data.board.width_mm = round(max(xs) - min(xs), 3)
        data.board.height_mm = round(max(ys) - min(ys), 3)

    # footprints
    for fp_node in find_all(root, "footprint") + find_all(root, "module"):
        fp = PcbFootprint(footprint_id=str(first_atom(fp_node, "")))
        at = find_first(fp_node, "at")
        if at is not None:
            fp.x, fp.y = float(at[1]), float(at[2])
            if len(at) > 3:
                try:
                    fp.rotation = float(at[3])
                except (TypeError, ValueError):
                    pass
        layer = find_first(fp_node, "layer")
        if layer is not None:
            fp.layer = str(first_atom(layer, "F.Cu"))
        for prop in find_all(fp_node, "property"):
            if len(prop) >= 3 and str(prop[1]) == "Reference":
                fp.reference = str(prop[2])
        for fp_text in find_all(fp_node, "fp_text"):
            if len(fp_text) >= 3 and str(fp_text[1]) == "reference" and not fp.reference:
                fp.reference = str(fp_text[2])
            t_at = find_first(fp_text, "at")
            t_layer = find_first(fp_text, "layer")
            layer_name = str(first_atom(t_layer, "")) if t_layer else ""
            if t_at is not None and "Silk" in layer_name:
                fp.silk_texts.append(
                    SilkText(
                        text=str(fp_text[2]) if len(fp_text) >= 3 else "",
                        x=fp.x + float(t_at[1]),
                        y=fp.y + float(t_at[2]),
                        layer=layer_name,
                    )
                )
        for pad_node in find_all(fp_node, "pad"):
            p_at = find_first(pad_node, "at")
            p_size = find_first(pad_node, "size")
            fp.pads.append(
                PcbPad(
                    number=str(first_atom(pad_node, "")),
                    x=fp.x + (float(p_at[1]) if p_at else 0.0),
                    y=fp.y + (float(p_at[2]) if p_at else 0.0),
                    width=float(p_size[1]) if p_size and len(p_size) > 1 else 0.0,
                    height=float(p_size[2]) if p_size and len(p_size) > 2 else 0.0,
                    net=_net_name(pad_node, data.nets),
                )
            )
        data.footprints.append(fp)

    return data
