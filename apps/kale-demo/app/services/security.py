"""Stdlib-only shim of the fencing helpers used by the corpus generator.

Byte-identical to services/analysis/app/services/security.py for FENCE_BEGIN/FENCE_END and
fence_user_content, so training inputs match inference exactly. The real module also carries
the auth/DB dependencies (FastAPI + SQLAlchemy); importing those here would defeat the point.
"""
from __future__ import annotations

FENCE_BEGIN = "<<<KALE_PROJECT_DATA_BEGIN>>>"
FENCE_END = "<<<KALE_PROJECT_DATA_END>>>"


def fence_user_content(text: str) -> str:
    cleaned = text.replace(FENCE_BEGIN, "").replace(FENCE_END, "")
    return f"{FENCE_BEGIN}\n{cleaned}\n{FENCE_END}"
