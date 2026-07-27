"""vLLM provider (production tier). Talks to a vLLM/TGI OpenAI-compatible server running on
KALE-CONTROLLED infrastructure (KALE_VLLM_URL). This is NOT an external commercial API — it is
Kale Forge's own GPU inference process, self-hosted. Structured output uses vLLM guided_json."""
from __future__ import annotations

import time

import httpx

from app.providers.base import GenerateRequest, GenerateResult, Provider, ProviderUnavailable


class VLLMProvider(Provider):
    name = "local_vllm"

    def __init__(self, base_url: str, model_version: str = "prod", model_name: str = "kale-review"):
        self.base_url = base_url.rstrip("/")
        self.model_version = model_version
        self.model_name = model_name

    def generate(self, req: GenerateRequest) -> GenerateResult:
        started = time.monotonic()
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})
        payload = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if req.json_schema is not None:
            # vLLM guided decoding — forces schema-valid JSON at the sampler
            payload["guided_json"] = req.json_schema
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = httpx.post(f"{self.base_url}/chat/completions", json=payload, timeout=120.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"self-hosted vLLM server unreachable: {exc}") from exc
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        json_obj = None
        warnings: list[str] = []
        if req.json_schema is not None:
            import json as _json

            try:
                json_obj = _json.loads(text)
            except _json.JSONDecodeError:
                warnings.append("guided_json output was not parseable")
        return GenerateResult(
            text=text, json=json_obj, model_version=self.model_version, provider=self.name,
            prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=(time.monotonic() - started) * 1000, warnings=warnings,
        )
