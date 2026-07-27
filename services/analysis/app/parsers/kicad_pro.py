"""Parser for .kicad_pro project files (JSON). Extracts design rules and net classes."""
from __future__ import annotations

import json
from typing import Any


def parse_project_file(text: str) -> dict[str, Any]:
    """Returns {"design_rules": {...}, "net_classes": [...], "meta": {...}}; tolerant of
    missing keys and invalid JSON (returns empty result with an 'error' key)."""
    out: dict[str, Any] = {"design_rules": {}, "net_classes": [], "meta": {}}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        out["error"] = f"invalid JSON in .kicad_pro: {exc}"
        return out
    if not isinstance(doc, dict):
        out["error"] = ".kicad_pro root is not an object"
        return out

    board = doc.get("board", {}) if isinstance(doc.get("board"), dict) else {}
    rules = board.get("design_settings", {}).get("rules", {}) if isinstance(board.get("design_settings"), dict) else {}
    mapping = {
        "min_track_width": "min_trace_width_mm",
        "min_via_diameter": "min_via_diameter_mm",
        "min_clearance": "min_clearance_mm",
        "min_copper_edge_clearance": "min_copper_edge_clearance_mm",
    }
    for src, dst in mapping.items():
        value = rules.get(src)
        if isinstance(value, (int, float)):
            out["design_rules"][dst] = float(value)

    net_settings = doc.get("net_settings", {})
    classes = net_settings.get("classes", []) if isinstance(net_settings, dict) else []
    for cls in classes:
        if isinstance(cls, dict):
            out["net_classes"].append(
                {
                    "name": cls.get("name", ""),
                    "track_width": cls.get("track_width"),
                    "via_diameter": cls.get("via_diameter"),
                    "clearance": cls.get("clearance"),
                }
            )

    meta = doc.get("meta", {})
    if isinstance(meta, dict):
        out["meta"] = {"filename": meta.get("filename", ""), "version": meta.get("version")}
    return out
