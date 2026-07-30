"""Minimal stub so the bundled robot_spec imports cleanly in the self-contained demo.
The demo always calls build_robot_spec(..., use_model=False), so the inference settings
here are never actually used — they exist only to satisfy the top-level import."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class _Settings:
    inference_url: str = ""
    inference_timeout_seconds: float = 2.0
    admin_token: str = ""


def get_settings() -> _Settings:
    return _Settings()
