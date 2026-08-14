"""Hugging Face Transformers provider (self-hosted staging tier) with optional PEFT/LoRA
adapter loading and simple micro-batching. Runs locally-downloaded open weights."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from app.jsonschema_lite import validate
from app.providers.base import GenerateRequest, GenerateResult, Provider, ProviderUnavailable


@dataclass
class _PendingRequest:
    req: GenerateRequest
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[GenerateResult] = None
    error: Optional[Exception] = None


class TransformersProvider(Provider):
    name = "local_transformers"

    def __init__(self, model_path: str, adapter_path: str = "", model_version: str = "dev",
                 batch_size: int = 4, batch_window_ms: int = 20):
        self.model_path = model_path
        self.adapter_path = adapter_path
        self.model_version = model_version
        self.batch_size = batch_size
        self.batch_window_ms = batch_window_ms
        self._model = None
        self._tokenizer = None
        self._queue: list[_PendingRequest] = []
        self._lock = threading.Lock()
        self._worker_running = False

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch  # noqa: PLC0415
            from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415
        except ImportError as exc:
            raise ProviderUnavailable(
                "transformers/torch not installed; pip install 'kale-inference[transformers]'"
            ) from exc
        if not self.model_path:
            raise ProviderUnavailable("KALE_MODEL_PATH is not set")
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_path)
        model = AutoModelForCausalLM.from_pretrained(
            self.model_path, torch_dtype="auto", device_map="auto"
        )
        if self.adapter_path:
            try:
                from peft import PeftModel  # noqa: PLC0415

                model = PeftModel.from_pretrained(model, self.adapter_path)
            except ImportError as exc:
                raise ProviderUnavailable("peft not installed but KALE_ADAPTER_PATH is set") from exc
        self._model = model
        self._torch = torch

    def generate(self, req: GenerateRequest) -> GenerateResult:
        """Enqueue and batch requests arriving within a short window (micro-batching)."""
        self.load()
        pending = _PendingRequest(req=req)
        with self._lock:
            self._queue.append(pending)
            if not self._worker_running:
                self._worker_running = True
                threading.Thread(target=self._drain, daemon=True).start()
        pending.event.wait(timeout=120)
        if pending.error is not None:
            if isinstance(pending.error, ProviderUnavailable):
                raise pending.error
            raise ProviderUnavailable(f"generation failed: {pending.error}") from pending.error
        if pending.result is None:
            raise ProviderUnavailable("generation timed out")
        return pending.result

    def _drain(self) -> None:
        time.sleep(self.batch_window_ms / 1000.0)
        with self._lock:
            # A JSON grammar is request-specific. Never combine two different grammars in one
            # sampling call; structured requests trade a little throughput for correctness.
            take = 1 if self._queue and self._queue[0].req.json_schema is not None else self.batch_size
            batch = self._queue[:take]
            self._queue = self._queue[take:]
            if not self._queue:
                self._worker_running = False
        try:
            self._run_batch(batch)
        except Exception as exc:
            for pending in batch:
                pending.error = exc
                pending.event.set()
        with self._lock:
            if self._queue and not self._worker_running:
                self._worker_running = True
                threading.Thread(target=self._drain, daemon=True).start()

    def _run_batch(self, batch: list[_PendingRequest]) -> None:
        started = time.monotonic()
        prompts = [self._format(p.req) for p in batch]
        inputs = self._tokenizer(prompts, return_tensors="pt", padding=True).to(self._model.device)
        generation_kwargs = {}
        if batch[0].req.json_schema is not None:
            try:
                from lmformatenforcer import JsonSchemaParser  # noqa: PLC0415
                from lmformatenforcer.integrations.transformers import (  # noqa: PLC0415
                    build_transformers_prefix_allowed_tokens_fn,
                )
            except ImportError as exc:
                raise ProviderUnavailable(
                    "constrained JSON generation requires lm-format-enforcer; refusing "
                    "unconstrained structured output"
                ) from exc
            parser = JsonSchemaParser(batch[0].req.json_schema)
            generation_kwargs["prefix_allowed_tokens_fn"] = (
                build_transformers_prefix_allowed_tokens_fn(self._tokenizer, parser)
            )
        with self._torch.no_grad():
            out = self._model.generate(
                **inputs,
                max_new_tokens=max(p.req.max_tokens for p in batch),
                do_sample=batch[0].req.temperature > 0,
                temperature=max(batch[0].req.temperature, 1e-4),
                **generation_kwargs,
            )
        latency = (time.monotonic() - started) * 1000
        for i, pending in enumerate(batch):
            gen = out[i][inputs["input_ids"].shape[1]:]
            text = self._tokenizer.decode(gen, skip_special_tokens=True)
            json_obj, warnings = self._maybe_json(text, pending.req)
            pending.result = GenerateResult(
                text=text, json=json_obj, model_version=self.model_version, provider=self.name,
                prompt_tokens=int(inputs["input_ids"][i].ne(self._tokenizer.pad_token_id or 0).sum()),
                completion_tokens=len(gen), latency_ms=latency, warnings=warnings,
            )
            pending.event.set()

    def _format(self, req: GenerateRequest) -> str:
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})
        if self._tokenizer.chat_template:
            return self._tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        return (req.system + "\n\n" + req.user + "\n")

    def _maybe_json(self, text: str, req: GenerateRequest):
        if req.json_schema is None:
            return None, []
        try:
            obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
        except (json.JSONDecodeError, ValueError):
            return None, ["output was not valid JSON"]
        errs = validate(obj, req.json_schema)
        return obj, (["schema validation errors: " + "; ".join(errs)] if errs else [])
