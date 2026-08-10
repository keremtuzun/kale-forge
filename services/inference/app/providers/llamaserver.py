"""llama.cpp provider that talks to a separate `llama-server` process over HTTP.

This exists because the in-process binding does not survive contact with production. Running
llama.cpp inside the API worker (`local_llamacpp`) means a fault in the C++ takes the whole
FastAPI process with it, and v18 produced three of those in one afternoon — two `GGML_ASSERT`
aborts in the repack kernel and one SIGSEGV. Capping threads made them rarer, not absent, and
no amount of supervision changes the fact that the API disappears with the model.

Out-of-process, the same fault is survivable: `llama-server` dies alone, this provider raises
`ProviderUnavailable`, the API answers normally, and the Design Studio falls back to
deterministic synthesis until the server is back. The API never goes down for a model crash.

Two things come free with the split:

* **Prompt caching.** llama-server keeps the KV cache between requests, so the 818-token
  system-and-season preamble is processed once. Measured on the same prompt: 63.9 s cold,
  16.5 s warm with 817 cached tokens.
* **Restart without reload.** Restarting the API no longer reloads 2.5 GB of weights.

Constrained decoding uses llama.cpp's own JSON-schema grammar, so the schema is enforced at
the sampler exactly as it was in-process — this is a transport change, not a contract change.
"""
from __future__ import annotations

import json
import time

import httpx

from app.jsonschema_lite import validate
from app.providers.base import GenerateRequest, GenerateResult, Provider, ProviderUnavailable


class LlamaServerProvider(Provider):
    name = "local_llamaserver"

    def __init__(self, base_url: str, model_version: str = "dev", timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.model_version = model_version
        self.timeout = timeout

    def load(self) -> None:
        """Nothing to load here; the weights live in the server process."""

    def _chat(self, messages: list[dict], req: GenerateRequest,
              schema: dict | None, temperature: float) -> tuple[str, dict]:
        payload: dict = {
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": temperature,
        }
        if schema is not None:
            # llama.cpp compiles this into a GBNF grammar and constrains sampling to it.
            payload["response_format"] = {"type": "json_object", "schema": schema}
        try:
            response = httpx.post(f"{self.base_url}/chat/completions", json=payload,
                                  timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # The whole point of this provider: a dead model server is a reported outage, not
            # a dead API. The caller turns this into a deterministic design.
            raise ProviderUnavailable(f"llama-server unreachable at {self.base_url}: {exc}") from exc
        data = response.json()
        return data["choices"][0]["message"]["content"], data.get("usage", {})

    def generate(self, req: GenerateRequest) -> GenerateResult:
        started = time.monotonic()
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})

        text, usage = self._chat(messages, req, req.json_schema, req.temperature)
        warnings: list[str] = []
        json_obj = None

        if req.json_schema is not None:
            json_obj, warnings = self._parse_and_validate(text, req, messages)

        return GenerateResult(
            text=text, json=json_obj, model_version=self.model_version, provider=self.name,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=(time.monotonic() - started) * 1000, warnings=warnings,
        )

    def _parse_and_validate(self, text: str, req: GenerateRequest,
                            messages: list[dict]) -> tuple[dict | None, list[str]]:
        """Same fail-closed shape as the in-process provider: one repair try, then give up.

        `validate` returns a list of errors, so an empty list means valid.
        """
        warnings: list[str] = []
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = None
        if obj is not None and not validate(obj, req.json_schema):
            return obj, warnings

        warnings.append("first generation was not schema-valid JSON; retried once")
        repair = messages + [{
            "role": "user",
            "content": "Your previous reply was not valid JSON for the schema. "
                       "Reply with ONLY the JSON object.",
        }]
        text2, _ = self._chat(repair, req, req.json_schema, 0.0)
        try:
            obj2 = json.loads(text2)
            if not validate(obj2, req.json_schema):
                return obj2, warnings
        except json.JSONDecodeError:
            pass
        warnings.append("repair attempt also failed; returning null JSON")
        return None, warnings
