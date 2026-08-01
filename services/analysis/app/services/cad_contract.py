"""Closed CAD contract between the design model and deterministic Onshape compiler."""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

ALLOWED_FEATURES = frozenset({
    "actuator", "bearing", "belt", "bevel", "bolts", "brake", "cable",
    "chain_track", "component", "drum", "envelope", "gear", "gearbox", "gusset",
    "hardstop", "hood", "hook", "motor", "pawl", "plate", "polycarb", "pulley",
    "rope", "sensor", "shaft", "slide", "sprocket", "standoff", "tensioner",
    "tube", "wheel",
})
CATALOG_SECTIONS = frozenset({
    (2.0, 1.0), (1.0, 1.0), (2.0, 2.0), (1.5, 1.5), (1.5, 0.5),
})


class CadContractError(ValueError):
    """Raised when generated CAD cannot safely enter the compiler."""

    def __init__(self, errors: list[str]):
        super().__init__("CAD contract rejected: " + "; ".join(errors[:12]))
        self.errors = errors


def normalize_cad(cad: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with stable unique assembly/part names before compilation.

    Repeated physical parts remain separate editable bodies.  Their display names receive a
    deterministic numeric suffix instead of being flattened into one repeated feature.
    """
    normalized = deepcopy(cad)
    assembly_ids: dict[str, int] = {}
    for assembly in normalized.get("assemblies") or []:
        original_id = str(assembly.get("id") or "assembly")
        assembly_ids[original_id] = assembly_ids.get(original_id, 0) + 1
        if assembly_ids[original_id] > 1:
            assembly["id"] = f"{original_id}-{assembly_ids[original_id]}"
        names: dict[str, int] = {}
        for feature in assembly.get("features") or []:
            original_name = str(feature.get("n") or feature.get("t") or "part").strip()
            key = original_name.casefold()
            names[key] = names.get(key, 0) + 1
            if names[key] > 1:
                feature["n"] = f"{original_name} {names[key]}"
    return normalized


def _numbers(value: Any):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _numbers(item)


def validate_cad(cad: dict[str, Any]) -> list[str]:
    """Validate names, vocabulary, dimensions, stock, pitch math and bounded repetition."""
    errors: list[str] = []
    assemblies = cad.get("assemblies")
    if not isinstance(assemblies, list) or not assemblies:
        return ["$.assemblies: at least one assembly is required"]
    assembly_ids: set[str] = set()
    global_part_ids: set[str] = set()
    for ai, assembly in enumerate(assemblies):
        ap = f"$.assemblies[{ai}]"
        aid = assembly.get("id")
        if not isinstance(aid, str) or not aid:
            errors.append(f"{ap}.id: non-empty id required")
            continue
        if aid in assembly_ids:
            errors.append(f"{ap}.id: duplicate assembly id {aid!r}")
        assembly_ids.add(aid)
        features = assembly.get("features")
        if not isinstance(features, list):
            errors.append(f"{ap}.features: array required")
            continue
        local_names: set[str] = set()
        for fi, feature in enumerate(features):
            fp = f"{ap}.features[{fi}]"
            if not isinstance(feature, dict):
                errors.append(f"{fp}: object required")
                continue
            kind, name = feature.get("t"), feature.get("n")
            if kind not in ALLOWED_FEATURES:
                errors.append(f"{fp}.t: illegal feature {kind!r}")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{fp}.n: non-empty part name required")
            else:
                key = name.strip().casefold()
                if key in local_names:
                    errors.append(f"{fp}.n: duplicate part name {name!r} in {aid}")
                local_names.add(key)
                part_id = f"{aid}/{key}"
                if part_id in global_part_ids:
                    errors.append(f"{fp}.n: duplicate stable part id {part_id!r}")
                global_part_ids.add(part_id)
            at = feature.get("at")
            if not isinstance(at, list) or len(at) != 3:
                errors.append(f"{fp}.at: exactly three coordinates required")
            for number in _numbers({k: v for k, v in feature.items()
                                    if k not in {"rot", "at", "to", "rep"}}):
                if not math.isfinite(number):
                    errors.append(f"{fp}: dimensions must be finite")
                    break
                if number < 0:
                    errors.append(f"{fp}: dimensions cannot be negative")
                    break
                if number > 1000:
                    errors.append(f"{fp}: dimension exceeds compiler safety bound")
                    break
            if kind == "tube":
                sec = feature.get("sec")
                if not isinstance(sec, list) or len(sec) != 2 or tuple(map(float, sec)) not in CATALOG_SECTIONS:
                    errors.append(f"{fp}.sec: non-catalog tube section {sec!r}")
                if float(feature.get("len", 0) or 0) <= 0:
                    errors.append(f"{fp}.len: tube length must be positive")
            if kind == "pulley":
                teeth = feature.get("teeth")
                pitch = feature.get("pitch_mm")
                pd = feature.get("pd")
                if pitch != 5.0:
                    errors.append(f"{fp}.pitch_mm: only HTD 5 mm is allowed")
                if isinstance(teeth, int) and isinstance(pd, (int, float)):
                    expected = teeth * 5.0 / math.pi / 25.4
                    if abs(float(pd) - expected) > 0.011:
                        errors.append(f"{fp}.pd: inconsistent with tooth count and pitch")
            rep = feature.get("rep")
            if rep:
                count = rep.get("n") if isinstance(rep, dict) else None
                step = rep.get("step") if isinstance(rep, dict) else None
                if not isinstance(count, int) or not 1 <= count <= 64:
                    errors.append(f"{fp}.rep.n: repetition must be bounded to 1..64")
                if count and count > 1 and (not isinstance(step, list) or len(step) != 3
                                            or not any(float(x) != 0 for x in step)):
                    errors.append(f"{fp}.rep.step: repeated parts need a non-zero 3D step")
    return errors


def require_valid_cad(cad: dict[str, Any]) -> dict[str, Any]:
    errors = validate_cad(cad)
    if errors:
        raise CadContractError(errors)
    return cad


def editable_manifest(cad: dict[str, Any]) -> dict[str, Any]:
    """Stable, deterministic mapping used by Onshape publishing and later edits."""
    parts: list[dict[str, Any]] = []
    mates: list[dict[str, Any]] = []
    for assembly in cad["assemblies"]:
        aid = assembly["id"]
        for index, feature in enumerate(assembly["features"]):
            dimensions = {key: value for key, value in feature.items()
                          if key not in {"t", "n", "at", "rot", "to", "rep", "mat", "kind", "key"}}
            parts.append({"id": f"{aid}:part:{index + 1}", "assembly_id": aid,
                          "name": feature["n"], "feature_type": feature["t"],
                          "dimensions": dimensions})
        for index, mate in enumerate(assembly.get("mates") or []):
            mates.append({"id": f"{aid}:mate:{index + 1}", "assembly_id": aid,
                          "definition": mate})
    return {"version": "kale-editable-1.0", "flattened": False,
            "parts": parts, "mates": mates}


def compact_design_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The small parametric contract retained beside deterministically compiled geometry."""
    mechanisms = []
    previous = "drivetrain"
    architecture_fields = {
        "intake": "type", "hopper": "type", "shooter": "type",
        "elevator": "architecture", "arm": "type", "climber": "type",
    }
    for kind in ("intake", "hopper", "shooter", "elevator", "arm", "climber"):
        block = spec.get("manipulator" if kind == "arm" else kind) or {}
        if not block.get("included"):
            continue
        item = {"id": kind, "kind": kind,
                "architecture": block.get(architecture_fields[kind], "deterministic-default"),
                "depends_on": [previous]}
        if kind == "elevator":
            item["stages"] = block.get("stages", 1)
        mechanisms.append(item)
        previous = kind
    frame = spec.get("frame") or {}
    drivetrain = spec.get("drivetrain") or {}
    return {
        "schema_version": "kale-parametric-1.0",
        "units": "in",
        "frame": {"width_in": frame.get("width_in"), "length_in": frame.get("length_in"),
                  "stock_section": frame.get("tube", "2x1x0.100 in")},
        "drivetrain": {"id": "drivetrain", "type": drivetrain.get("type"),
                       "wheel_diameter_in": drivetrain.get("wheel_diameter_in"),
                       "drive_ratio": drivetrain.get("drive_ratio")},
        "mechanisms": mechanisms,
        "compiler": "kale-cad-deterministic-1.0",
    }


def validate_parametric_design(design: dict[str, Any]) -> list[str]:
    """Reject duplicate nodes, dangling dependencies and dependency cycles."""
    errors: list[str] = []
    frame = design.get("frame") or {}
    for field in ("width_in", "length_in"):
        value = frame.get(field)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 18 <= value <= 40):
            errors.append(f"$.frame.{field}: must be a finite 18..40 inch dimension")
    nodes = [design.get("drivetrain") or {}, *(design.get("mechanisms") or [])]
    ids: set[str] = set()
    graph: dict[str, list[str]] = {}
    for index, node in enumerate(nodes):
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            errors.append(f"$.nodes[{index}].id: non-empty id required")
            continue
        if node_id in ids:
            errors.append(f"$.nodes[{index}].id: duplicate id {node_id!r}")
        ids.add(node_id)
        graph[node_id] = list(node.get("depends_on") or [])
    for node_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in ids:
                errors.append(f"$.nodes.{node_id}: unknown dependency {dependency!r}")
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node_id: str) -> None:
        if node_id in visiting:
            errors.append(f"$.nodes.{node_id}: dependency loop")
            return
        if node_id in visited: return
        visiting.add(node_id)
        for dependency in graph.get(node_id, []): visit(dependency)
        visiting.remove(node_id); visited.add(node_id)
    for node_id in graph: visit(node_id)
    return errors


def require_valid_parametric_design(design: dict[str, Any]) -> dict[str, Any]:
    errors = validate_parametric_design(design)
    if errors:
        raise CadContractError(errors)
    return design
