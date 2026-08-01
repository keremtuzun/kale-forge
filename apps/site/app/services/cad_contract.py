"""Strict, deterministic CAD boundary used by the Vercel Design Studio."""
from __future__ import annotations
from copy import deepcopy
import math
from typing import Any

ALLOWED_FEATURES = frozenset("actuator bearing belt bevel bolts brake cable chain_track component drum envelope gear gearbox gusset hardstop hood hook motor pawl plate polycarb pulley rope sensor shaft slide sprocket standoff tensioner tube wheel".split())
CATALOG_SECTIONS = {(2.0, 1.0), (1.0, 1.0), (2.0, 2.0), (1.5, 1.5), (1.5, 0.5)}

class CadContractError(ValueError):
    pass

def normalize_cad(cad: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(cad)
    seen_assemblies: dict[str, int] = {}
    for assembly in out.get("assemblies") or []:
        aid = str(assembly.get("id") or "assembly")
        seen_assemblies[aid] = seen_assemblies.get(aid, 0) + 1
        if seen_assemblies[aid] > 1:
            assembly["id"] = f"{aid}-{seen_assemblies[aid]}"
        names: dict[str, int] = {}
        for feature in assembly.get("features") or []:
            name = str(feature.get("n") or feature.get("t") or "part").strip()
            key = name.casefold(); names[key] = names.get(key, 0) + 1
            if names[key] > 1: feature["n"] = f"{name} {names[key]}"
    return out

def validate_cad(cad: dict[str, Any]) -> list[str]:
    errors: list[str] = []; assembly_ids: set[str] = set()
    for ai, assembly in enumerate(cad.get("assemblies") or []):
        aid = assembly.get("id"); path = f"assemblies[{ai}]"
        if not aid or aid in assembly_ids: errors.append(f"{path}: duplicate or missing assembly id")
        assembly_ids.add(aid); names: set[str] = set()
        for fi, feature in enumerate(assembly.get("features") or []):
            fp = f"{path}.features[{fi}]"; kind = feature.get("t")
            name = str(feature.get("n") or "").strip().casefold()
            if kind not in ALLOWED_FEATURES: errors.append(f"{fp}: illegal feature {kind!r}")
            if not name or name in names: errors.append(f"{fp}: duplicate or missing part name")
            names.add(name)
            if not isinstance(feature.get("at"), list) or len(feature["at"]) != 3:
                errors.append(f"{fp}: bad position")
            if kind == "tube" and tuple(map(float, feature.get("sec") or [])) not in CATALOG_SECTIONS:
                errors.append(f"{fp}: illegal stock section")
            for key in ("len", "dia", "w", "th", "bore", "od", "pd", "face"):
                value = feature.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                          or not math.isfinite(value) or value <= 0 or value > 1000):
                    errors.append(f"{fp}.{key}: bad dimension")
            if kind == "pulley" and feature.get("pitch_mm") != 5.0:
                errors.append(f"{fp}: illegal pitch")
    return errors or ([] if cad.get("assemblies") else ["assemblies: at least one required"])

def require_valid_cad(cad: dict[str, Any]) -> dict[str, Any]:
    errors = validate_cad(cad)
    if errors: raise CadContractError("CAD contract rejected: " + "; ".join(errors[:12]))
    return cad

def editable_manifest(cad: dict[str, Any]) -> dict[str, Any]:
    parts=[]; mates=[]
    for assembly in cad["assemblies"]:
        aid=assembly["id"]
        for i, feature in enumerate(assembly["features"]):
            dims={k:v for k,v in feature.items() if k not in {"t","n","at","rot","to","rep","mat","kind","key"}}
            parts.append({"id":f"{aid}:part:{i+1}","assembly_id":aid,"name":feature["n"],"feature_type":feature["t"],"dimensions":dims})
        for i, mate in enumerate(assembly.get("mates") or []):
            mates.append({"id":f"{aid}:mate:{i+1}","assembly_id":aid,"definition":mate})
    return {"version":"kale-editable-1.0","flattened":False,"parts":parts,"mates":mates}

def compact_design_spec(spec: dict[str, Any]) -> dict[str, Any]:
    blocks=[]
    for kind,field in (("intake","type"),("hopper","type"),("shooter","type"),("elevator","architecture"),("arm","type"),("climber","type")):
        block=spec.get("manipulator" if kind=="arm" else kind) or {}
        if block.get("included"): blocks.append({"id":kind,"kind":kind,"architecture":block.get(field)})
    f=spec.get("frame") or {}; d=spec.get("drivetrain") or {}
    return {"schema_version":"kale-parametric-1.0","units":"in","frame":{"width_in":f.get("width_in"),"length_in":f.get("length_in"),"stock_section":f.get("tube")},"drivetrain":{"id":"drivetrain","type":d.get("type"),"wheel_diameter_in":d.get("wheel_diameter_in"),"drive_ratio":d.get("drive_ratio")},"mechanisms":blocks,"compiler":"kale-cad-deterministic-1.0"}
