"""Stdlib-only shim of the fencing helpers the bundled robot_spec needs.

Byte-identical to services/analysis/app/services/security.py for FENCE_BEGIN/FENCE_END and
fence_user_content, so the prompt this site sends matches the one the adapter was trained on
exactly. The real module also carries the auth/DB dependencies (FastAPI + SQLAlchemy), which
do not exist in the Vercel function: the full copy that used to sit here would have raised
ImportError the first time the model path ran.

Same shim, same reason, as apps/kale-demo/app/services/security.py.
"""
from __future__ import annotations

FENCE_BEGIN = "<<<KALE_PROJECT_DATA_BEGIN>>>"
FENCE_END = "<<<KALE_PROJECT_DATA_END>>>"


def fence_user_content(text: str) -> str:
    cleaned = text.replace(FENCE_BEGIN, "").replace(FENCE_END, "")
    return f"{FENCE_BEGIN}\n{cleaned}\n{FENCE_END}"
