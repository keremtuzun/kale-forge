"""HTTP client for the Kale Forge self-hosted inference service, stdlib only.

This mirrors the interface of `services/analysis/app/services/inference_client.py` so the
bundled `robot_spec.py` stays byte-identical to the backend copy, but implements it on
`urllib.request` instead of httpx + pydantic. The Vercel function ships with no
`requirements.txt`; keeping it dependency-free avoids adding a compiled dependency to a cold
start that already has a hard timeout to fit inside.

`test_site_bundle_parity.py` in the analysis suite enforces that this is the *only* reason the
two trees differ, so the divergence cannot quietly spread to the modules that must match.

Like the backend client, this points exclusively at Kale-controlled infrastructure
(INFERENCE_URL). No external commercial AI provider is ever contacted.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional


class InferenceUnavailable(RuntimeError):
    pass


def _auth_headers() -> dict[str, str]:
    """Bearer for the cloud inference box. Read from the environment, not the constructor,
    so `robot_spec.py` stays byte-identical between the site bundle and the backend."""
    token = os.environ.get("INFERENCE_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


@dataclass
class InferenceResult:
    text: str = ""
    json: Optional[dict[str, Any]] = None
    model_version: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)


class InferenceClient:
    def __init__(self, base_url: str, timeout: float = 6.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    def generate(
        self,
        system: str,
        user: str,
        json_schema: Optional[dict] = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> InferenceResult:
        if not self.base_url:
            raise InferenceUnavailable("no inference service configured")
        payload = json.dumps({
            "system": system,
            "user": user,
            "json_schema": json_schema,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }).encode()
        request = urllib.request.Request(
            f"{self.base_url}/v1/generate", data=payload,
            headers={"Content-Type": "application/json", **_auth_headers()}, method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode("utf-8", "replace")
            raise InferenceUnavailable(
                f"inference request rejected {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            # ValueError covers a non-JSON body; every one of these means "no usable model
            # answer", and the caller's job is to fall back rather than surface an error.
            raise InferenceUnavailable(f"inference service unreachable: {exc}") from exc
        if not isinstance(body, dict):
            raise InferenceUnavailable("inference service returned a non-object response")
        body.setdefault("latency_ms", (time.monotonic() - started) * 1000)
        known = {f for f in InferenceResult.__dataclass_fields__}
        return InferenceResult(**{k: v for k, v in body.items() if k in known})

    def health(self) -> dict[str, Any]:
        if not self.base_url:
            raise InferenceUnavailable("no inference service configured")
        try:
            request = urllib.request.Request(f"{self.base_url}/health", headers=_auth_headers())
            with urllib.request.urlopen(request, timeout=5.0) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise InferenceUnavailable(str(exc)) from exc
