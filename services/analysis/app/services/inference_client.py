"""HTTP client for the Kale Forge self-hosted inference service. This is the ONLY path to a
model, and it points exclusively at Kale-controlled infrastructure (INFERENCE_URL).
No external commercial AI provider is ever contacted."""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx
from pydantic import BaseModel


class InferenceUnavailable(RuntimeError):
    pass


def _auth_headers() -> dict[str, str]:
    """Bearer for the cloud inference box. Read from the environment, not the constructor,
    so `robot_spec.py` stays byte-identical between the site bundle and the backend."""
    token = os.environ.get("INFERENCE_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


class InferenceResult(BaseModel):
    text: str
    json: Optional[dict[str, Any]] = None
    model_version: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    warnings: list[str] = []


class InferenceClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(
        self,
        system: str,
        user: str,
        json_schema: Optional[dict] = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> InferenceResult:
        payload = {
            "system": system,
            "user": user,
            "json_schema": json_schema,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        started = time.monotonic()
        try:
            resp = httpx.post(f"{self.base_url}/v1/generate", json=payload,
                              headers=_auth_headers(), timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise InferenceUnavailable(f"inference service unreachable: {exc}") from exc
        if resp.status_code >= 500:
            raise InferenceUnavailable(f"inference service error {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise InferenceUnavailable(f"inference request rejected {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        data.setdefault("latency_ms", (time.monotonic() - started) * 1000)
        return InferenceResult(**data)

    def health(self) -> dict[str, Any]:
        try:
            resp = httpx.get(f"{self.base_url}/health", headers=_auth_headers(), timeout=5.0)
            return resp.json()
        except httpx.HTTPError as exc:
            raise InferenceUnavailable(str(exc)) from exc
