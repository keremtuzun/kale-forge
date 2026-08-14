"""Settings for the self-contained Vercel Design Studio.

Deliberately NOT the backend's config: that one is pydantic, and this function deploys with
the standard library only. Copying the backend file here once took the whole live site down
with FUNCTION_INVOCATION_FAILED — the import crashed before the handler ever ran. This module
carries exactly the fields the bundled `app.services` tree reads, nothing more.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    inference_url: str = ""
    inference_timeout_seconds: float = 30.0
    # Ask the model to PROPOSE one subsystem's geometry; the CAD contract decides whether it
    # ships. Off by default — a proposal is minutes of CPU generation, far past a serverless
    # budget, so this is for environments that own their inference box.
    model_geometry: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings(
        inference_url=os.environ.get("INFERENCE_URL", "").rstrip("/"),
        inference_timeout_seconds=float(os.environ.get("INFERENCE_TIMEOUT_SECONDS", "30")),
        model_geometry=os.environ.get("MODEL_GEOMETRY", "false").lower() in {"1", "true", "yes"},
    )
