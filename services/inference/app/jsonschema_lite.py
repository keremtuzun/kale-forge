"""Dependency-free strict JSON-schema validation for constrained model output.

This intentionally implements the closed subset used by Kale's generation contracts.  It is
strict enough to reject invented keys, enum near-misses, non-finite dimensions, oversized
arrays and duplicate identifiers before any value reaches a CAD compiler.
"""
from __future__ import annotations

import math
import re
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
    if isinstance(instance, float) and not math.isfinite(instance):
        errors.append(f"{path}: number must be finite")
        return errors
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not allowed")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: must be < {schema['exclusiveMaximum']}")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is too long")
        if "pattern" in schema and re.fullmatch(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match required pattern")
    if isinstance(instance, dict) and "properties" in schema:
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}.{key}: required property missing")
        for key, subschema in schema["properties"].items():
            if key in instance and isinstance(subschema, dict):
                errors.extend(_validate(instance[key], subschema, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            for key in instance.keys() - schema["properties"].keys():
                errors.append(f"{path}.{key}: additional property is not allowed")
    if isinstance(instance, list) and isinstance(schema.get("items"), dict):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            import json
            for item in instance:
                marker = json.dumps(item, sort_keys=True, separators=(",", ":"))
                if marker in seen:
                    errors.append(f"{path}: duplicate array item")
                    break
                seen.add(marker)
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
