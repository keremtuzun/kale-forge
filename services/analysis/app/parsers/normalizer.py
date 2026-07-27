"""Merge parser outputs into the canonical NormalizedProject.

Precedence: schematic is authoritative for components/nets; a netlist substitutes when no
schematic exists; the PCB contributes board data and placements; .kicad_pro contributes
design rules; BOM enriches MPN/fields without overwriting schematic values. Original files
are never modified; every anomaly becomes a parser warning, not a crash.
"""
from __future__ import annotations

import re
from collections import defaultdict

from app.models.normalized import (
    Component,
    DifferentialPair,
    Net,
    NetPin,
    NormalizedProject,
    ParserWarning,
    Position,
    ProjectMeta,
)
from app.parsers import bom as bom_parser
from app.parsers import kicad_pcb, kicad_pro, kicad_sch, obj, spice
from app.rules import helpers

_DIFF_SUFFIXES = [("_P", "_N"), ("_p", "_n"), ("+", "-"), ("P", "N")]


def normalize_project(files: list[tuple[str, bytes]], project_name: str = "") -> NormalizedProject:
    project = NormalizedProject(project=ProjectMeta(name=project_name))
    warnings: list[ParserWarning] = []

    schematics: list[kicad_sch.SchematicData] = []
    pcbs: list[kicad_pcb.PcbData] = []
    netlists: list[spice.NetlistData] = []
    bom_rows: list[bom_parser.BomRow] = []
    design_rules: dict = {}
    meshes: list[obj.ObjData] = []

    for filename, payload in files:
        project.project.files.append(filename)
        lower = filename.lower()
        try:
            text = payload.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover — decode with replace should not raise
            warnings.append(ParserWarning(file=filename, message="undecodable file"))
            continue
        try:
            if lower.endswith(".kicad_sch"):
                sch = kicad_sch.parse_schematic(text, filename)
                schematics.append(sch)
                warnings.extend(sch.warnings)
            elif lower.endswith(".kicad_pcb"):
                pcb = kicad_pcb.parse_pcb(text, filename)
                pcbs.append(pcb)
                warnings.extend(pcb.warnings)
            elif lower.endswith(".kicad_pro"):
                pro = kicad_pro.parse_project_file(text)
                if "error" in pro:
                    warnings.append(ParserWarning(file=filename, message=pro["error"]))
                design_rules.update(pro.get("design_rules", {}))
            elif lower.endswith((".net", ".cir", ".spice")):
                nl = spice.parse_netlist(text, filename)
                netlists.append(nl)
                warnings.extend(nl.warnings)
            elif lower.endswith(".csv"):
                rows = bom_parser.parse_bom_csv(text)
                if not rows:
                    warnings.append(ParserWarning(file=filename, message="BOM has no usable Reference column"))
                bom_rows.extend(rows)
            elif lower.endswith(".obj"):
                parsed_mesh = obj.parse_obj(text, filename)
                meshes.append(parsed_mesh)
                warnings.extend(parsed_mesh.warnings)
            elif lower.endswith(".mtl"):
                pass  # preserved alongside the OBJ; material names are read from the OBJ itself
            elif lower.endswith(".pdf"):
                pass  # datasheets are stored and indexed elsewhere; recorded in files list
            else:
                warnings.append(ParserWarning(file=filename, message="unsupported file type ignored"))
        except Exception as exc:  # noqa: BLE001 — parsing failures are logged, never fatal
            warnings.append(ParserWarning(file=filename, message=f"parse failed: {exc}"))

    # --- components + nets: schematic first, netlist fallback -----------------------------
    net_pins: dict[str, list[NetPin]] = defaultdict(list)
    if schematics:
        project.project.source_format = "kicad"
        for sch in schematics:
            if sch.kicad_version and not project.project.kicad_version:
                project.project.kicad_version = sch.kicad_version
            for comp in sch.components:
                if project.get_component(comp.reference) is not None and not comp.reference.startswith("UNREF"):
                    # duplicate refs across sheets are kept — the connectivity rule flags them
                    pass
                project.components.append(comp)
            project.power_symbols.extend(sch.power_symbols)
            project.global_labels.extend(sch.global_labels)
            project.hierarchical_labels.extend(sch.hierarchical_labels)
            for net_name, pins in sch.net_pins.items():
                for ref, pin_num in pins:
                    net_pins[net_name].append(NetPin(component=ref, pin=pin_num))
        if netlists:
            project.project.source_format = "mixed"
    elif netlists:
        project.project.source_format = "spice"
        for nl in netlists:
            project.components.extend(nl.components)
            for net in nl.nets:
                net_pins[net.name].extend(net.pins)

    if meshes:
        project.mesh = meshes[0].mesh
        if len(meshes) > 1:
            warnings.append(ParserWarning(file=meshes[0].mesh.filename, message="multiple OBJ files; analyzing the first mesh"))
        project.project.source_format = "mixed" if (schematics or pcbs or netlists) else "obj"

    for name, pins in net_pins.items():
        project.nets.append(Net(name=name, pins=pins))

    # --- board -----------------------------------------------------------------------------
    if pcbs:
        pcb = pcbs[0]
        if len(pcbs) > 1:
            warnings.append(ParserWarning(file=pcb.filename, message="multiple .kicad_pcb files; using first"))
        project.board = pcb.board
        for key, value in design_rules.items():
            if hasattr(project.board.design_rules, key) and getattr(project.board.design_rules, key) is None:
                setattr(project.board.design_rules, key, value)
        # placements onto schematic components; PCB-only footprints become components
        for fp in pcb.footprints:
            comp = project.get_component(fp.reference) if fp.reference else None
            if comp is not None:
                comp.layer = fp.layer
                if comp.position is None or not schematics:
                    comp.position = Position(x=fp.x, y=fp.y, rotation=fp.rotation)
                else:
                    comp.position = Position(x=fp.x, y=fp.y, rotation=fp.rotation)
                if not comp.footprint:
                    comp.footprint = fp.footprint_id
            elif fp.reference and not schematics and not netlists:
                board_comp = Component(
                    reference=fp.reference,
                    footprint=fp.footprint_id,
                    position=Position(x=fp.x, y=fp.y, rotation=fp.rotation),
                    layer=fp.layer,
                    source_file=pcb.filename,
                )
                project.components.append(board_comp)
        # pad nets can fill gaps when no schematic/netlist named them
        if not net_pins:
            for fp in pcb.footprints:
                for pad in fp.pads:
                    if pad.net and fp.reference:
                        existing = project.get_net(pad.net)
                        if existing is None:
                            existing = Net(name=pad.net)
                            project.nets.append(existing)
                        existing.pins.append(NetPin(component=fp.reference, pin=pad.number))

    # --- BOM enrichment (never overwrites schematic data) ----------------------------------
    for row in bom_rows:
        comp = project.get_component(row.reference)
        if comp is None:
            continue
        if row.mpn and not comp.mpn:
            comp.mpn = row.mpn
        if row.footprint and not comp.footprint:
            comp.footprint = row.footprint
        if row.value and not comp.value:
            comp.value = row.value
        for key, value in {"manufacturer": row.manufacturer, "description": row.description}.items():
            if value and key not in comp.fields:
                comp.fields[key] = value
        for key, value in row.fields.items():
            comp.fields.setdefault(f"bom:{key}", value)

    # --- net classification ----------------------------------------------------------------
    for net in project.nets:
        net.is_ground = helpers.is_ground_name(net.name)
        net.is_power = helpers.is_power_name(net.name)
        if net.is_power:
            net.inferred_voltage = helpers.infer_voltage(net.name)

    # --- unconnected pins ------------------------------------------------------------------
    connected: set[tuple[str, str]] = set()
    for net in project.nets:
        for np in net.pins:
            connected.add((np.component, np.pin))
    for comp in project.components:
        for pin in comp.pins:
            if pin.net is None and (comp.reference, pin.number) not in connected:
                if pin.type.value != "no_connect":
                    project.unconnected_pins.append(NetPin(component=comp.reference, pin=pin.number))

    # --- differential pairs ----------------------------------------------------------------
    net_names = {n.name for n in project.nets}
    seen_pairs: set[str] = set()
    for name in sorted(net_names):
        for pos_suffix, neg_suffix in _DIFF_SUFFIXES:
            if not name.endswith(pos_suffix) or len(name) <= len(pos_suffix):
                continue
            base = name[: -len(pos_suffix)]
            partner = base + neg_suffix
            if partner not in net_names or not base or base in seen_pairs:
                continue
            # bare P/N suffixes need a separator ("USB_P") to avoid false pairs like SIGP/SIGN
            if pos_suffix == "P" and not re.search(r"[_\-]$", base):
                continue
            seen_pairs.add(base)
            project.differential_pairs.append(
                DifferentialPair(name=base.rstrip("_-"), positive_net=name, negative_net=partner)
            )
            break

    project.project.parser_warnings = warnings
    return project
