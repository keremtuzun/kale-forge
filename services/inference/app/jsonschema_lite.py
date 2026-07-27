"""Minimal JSON-schema validation (required + type checks) and a schema-default synthesizer.

The full `jsonschema` package is intentionally not a dependency; we only need enough to
validate model output structure and to synthesize a valid instance for the stub provider."""
from __future__ import annotations

from typing import Any

_PY_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def validate(instance: Any, schema: dict) -> list[str]:
    """Return a list of human-readable errors ([] if valid). Supports type, required,
    properties, and items — enough for our structured-output contracts."""
    return _validate(instance, schema, "$")


def _validate(instance: Any, schema: dict, path: str) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        py = tuple(_PY_TYPES[t] for t in types if t in _PY_TYPES)
        # bool is a subclass of int; guard integer/number checks
        if py:
            ok = isinstance(instance, py)
            if "boolean" not in types and isinstance(instance, bool) and ("integer" in types or "number" in types):
                ok = False
            if not ok:
                errors.append(f"{path}: expected {expected}, got {type(instance).__name__}")
                return errors
    if isinstance(instance, dict) and "properties" in schema:
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}.{key}: required property missing")
        for key, subschema in schema["properties"].items():
            if key in instance and isinstance(subschema, dict):
                errors.extend(_validate(instance[key], subschema, f"{path}.{key}"))
    if isinstance(instance, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(instance):
            errors.extend(_validate(item, schema["items"], f"{path}[{i}]"))
    return errors


def synthesize(schema: dict) -> Any:
    """Build a schema-valid default instance (empty-ish). Respects 'default' when present."""
    if "default" in schema:
        return schema["default"]
    t = schema.get("type")
    if isinstance(t, list):
        t = t[0]
    if t == "object" or "properties" in schema:
        obj: dict[str, Any] = {}
        for key, sub in schema.get("properties", {}).items():
            obj[key] = synthesize(sub) if isinstance(sub, dict) else None
        return obj
    if t == "array":
        return []
    if t == "string":
        return ""
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return False
    if t == "null":
        return None
    # unknown/anyOf: best-effort empty object
    return {}
