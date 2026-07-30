"""MLX provider: Apple-silicon local inference with LoRA adapters (host-native tier).

This is the provider that serves Kale's own fine-tuned design adapters on the Mac that
trains them. The adapters are MLX LoRA output, so this path loads them directly — the
llama.cpp provider would need a GGUF conversion and the transformers provider needs torch,
neither of which the MLX training stack provides. Runs locally-downloaded open weights on
the machine's GPU via mlx-lm; contacts no external service.

One rule inherited from the training side: never run this while an MLX training job is
running — they contend for the same GPU and both stall.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from app.jsonschema_lite import validate
from app.providers.base import GenerateRequest, GenerateResult, Provider, ProviderUnavailable


def _strip_fences(text: str) -> str:
    """Models occasionally fence JSON output; validation cares about the payload."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


class MLXProvider(Provider):
    name = "local_mlx"

    def __init__(self, model_path: str, adapter_path: str = "", model_version: str = "dev"):
        self.model_path = model_path
        self.adapter_path = adapter_path
        self.model_version = model_version
        self._model = None
        self._tokenizer = None
        # MLX's Metal stream is thread-affine: generation must run on the thread that loaded
        # the model, and FastAPI executes sync endpoints on arbitrary threadpool workers
        # (symptom otherwise: "There is no Stream(cpu, 0) in current thread"). One dedicated
        # worker owns the model for its whole life — which also serializes requests, all this
        # host's single GPU could take anyway.
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")

    def load(self) -> None:
        self._worker.submit(self._load_impl).result()

    def _load_impl(self) -> None:
        if self._model is not None:
            return
        try:
            from mlx_lm import load  # noqa: PLC0415
        except ImportError as exc:
            raise ProviderUnavailable(
                "mlx-lm is not installed; pip install mlx-lm"
            ) from exc
        if not self.model_path:
            raise ProviderUnavailable("KALE_MODEL_PATH is not set")
        adapter = self.adapter_path or None
        if adapter and not os.path.isdir(adapter):
            raise ProviderUnavailable(f"adapter directory not found: {adapter}")
        self._model, self._tokenizer = load(self.model_path, adapter_path=adapter)

    def _chat(self, messages: list[dict], max_tokens: int, temperature: float) -> tuple[str, int, int]:
        from mlx_lm import generate  # noqa: PLC0415
        from mlx_lm.sample_utils import make_sampler  # noqa: PLC0415

        prompt = self._tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        text = generate(self._model, self._tokenizer, prompt=prompt, max_tokens=max_tokens,
                        sampler=make_sampler(temp=max(temperature, 0.0)), verbose=False)
        prompt_tokens = len(prompt) if isinstance(prompt, list) else 0
        completion_tokens = len(self._tokenizer.encode(text)) if text else 0
        return text, prompt_tokens, completion_tokens

    def generate(self, req: GenerateRequest) -> GenerateResult:
        self.load()
        started = time.monotonic()
        # The whole request — first pass, parse, repair retry — runs as ONE job on the model's
        # own thread. Submitting the repair pass separately from inside the worker would
        # deadlock a single-thread executor.
        text, json_obj, warnings, prompt_tokens, completion_tokens = self._worker.submit(
            self._generate_impl, req).result()
        return GenerateResult(
            text=text, json=json_obj, model_version=self.model_version, provider=self.name,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            latency_ms=(time.monotonic() - started) * 1000, warnings=warnings,
        )

    def _generate_impl(self, req: GenerateRequest):
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})
        text, prompt_tokens, completion_tokens = self._chat(
            messages, req.max_tokens, req.temperature)
        warnings: list[str] = []
        json_obj = None
        if req.json_schema is not None:
            json_obj, warnings = self._parse_and_validate(text, req.json_schema,
                                                          messages, req)
        return text, json_obj, warnings, prompt_tokens, completion_tokens

    def _parse_and_validate(self, text: str, schema: dict, messages: list[dict],
                            req: GenerateRequest) -> tuple[dict | None, list[str]]:
        warnings: list[str] = []
        try:
            obj = json.loads(_strip_fences(text))
        except json.JSONDecodeError:
            obj = None
        if obj is not None and not validate(obj, schema):
            return obj, warnings
        # One repair retry at temperature 0, same shape as the llama.cpp provider.
        warnings.append("first generation was not schema-valid JSON; retried once")
        repair = messages + [{
            "role": "user",
            "content": "Your previous reply was not valid JSON for the schema. "
                       "Reply with ONLY the JSON object.",
        }]
        text2, _, _ = self._chat(repair, req.max_tokens, 0.0)
        try:
            obj2 = json.loads(_strip_fences(text2))
            if not validate(obj2, schema):
                return obj2, warnings
        except json.JSONDecodeError:
            pass
        warnings.append("repair attempt also failed; returning null JSON")
        return None, warnings
