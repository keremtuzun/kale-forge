"""llama.cpp provider for local CPU/Metal inference with small GGUF models (dev tier).
Runs a locally-downloaded open-weight GGUF; contacts no external service."""
from __future__ import annotations

import json
import threading
import time

from app.jsonschema_lite import validate
from app.providers.base import GenerateRequest, GenerateResult, Provider, ProviderUnavailable


class LlamaCppProvider(Provider):
    name = "local_llamacpp"

    def __init__(self, model_path: str, model_version: str = "dev", n_ctx: int = 4096,
                 n_threads: int = 0):
        self.model_path = model_path
        self.model_version = model_version
        self.n_ctx = n_ctx
        self.n_threads = n_threads or self._default_threads()
        self._llm = None
        # One `Llama` object is not safe to drive from two threads, and FastAPI runs every sync
        # endpoint on a threadpool — so two overlapping design requests were entering
        # create_chat_completion at once and corrupting shared decode state. That aborts the
        # whole process, which in-process means the API dies too:
        #
        #     llama-kv-cache.cpp:791: GGML_ASSERT(!states.empty() || !success) failed
        #
        # Reproduced in 3 seconds with 4 concurrent requests, against 48 completions of the
        # same load survived by an out-of-process llama-server. Serialising here costs
        # concurrency this provider never actually had — it was corruption, not parallelism.
        self._lock = threading.Lock()

    @staticmethod
    def _default_threads() -> int:
        """Half the logical cores, capped at 8.

        llama.cpp's default is every core, and on a 24-thread box that reliably aborted this
        service inside the CPU repack kernel:

            GGML_ASSERT(src1_ptr + src1_col_stride * nrows <= params->wdata + params->wsize)
            ggml-cpu/repack.cpp:4238

        The per-thread workspace is sized from the thread count, so oversubscribing overflows
        it and kills the process mid-request. Eight threads survived a burst that killed the
        default, and was roughly twice as fast — the extra threads were costing time as well as
        stability. Override with KALE_LLAMA_THREADS if a box wants something else.
        """
        import os  # noqa: PLC0415

        return max(1, min(8, (os.cpu_count() or 4) // 2))

    def load(self) -> None:
        if self._llm is not None:
            return
        try:
            from llama_cpp import Llama  # noqa: PLC0415
        except ImportError as exc:
            raise ProviderUnavailable(
                "llama-cpp-python is not installed; pip install 'kale-inference[llamacpp]'"
            ) from exc
        if not self.model_path:
            raise ProviderUnavailable("KALE_MODEL_PATH is not set")
        import os

        if not os.path.exists(self.model_path):
            raise ProviderUnavailable(f"model file not found: {self.model_path}")
        self._llm = Llama(model_path=self.model_path, n_ctx=self.n_ctx,
                          n_threads=self.n_threads, verbose=False)

    def generate(self, req: GenerateRequest) -> GenerateResult:
        with self._lock:
            return self._generate_locked(req)

    def _generate_locked(self, req: GenerateRequest) -> GenerateResult:
        self.load()
        started = time.monotonic()
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})

        kwargs = {"messages": messages, "max_tokens": req.max_tokens, "temperature": req.temperature}
        if req.json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_object",
                "schema": req.json_schema,
            }
        resp = self._llm.create_chat_completion(**kwargs)
        text = resp["choices"][0]["message"]["content"]
        usage = resp.get("usage", {})
        warnings: list[str] = []
        json_obj = None
        if req.json_schema is not None:
            json_obj, warnings = _parse_and_validate(text, req.json_schema, self._llm, messages, req)
        return GenerateResult(
            text=text, json=json_obj, model_version=self.model_version, provider=self.name,
            prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=(time.monotonic() - started) * 1000, warnings=warnings,
        )


def _parse_and_validate(text, schema, llm, messages, req):
    warnings: list[str] = []
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None
    if obj is not None and not validate(obj, schema):
        return obj, warnings
    # one repair retry
    warnings.append("first generation was not schema-valid JSON; retried once")
    repair = messages + [{
        "role": "user",
        "content": "Your previous reply was not valid JSON for the schema. Reply with ONLY the JSON object.",
    }]
    resp = llm.create_chat_completion(messages=repair, max_tokens=req.max_tokens, temperature=0.0)
    text2 = resp["choices"][0]["message"]["content"]
    try:
        obj2 = json.loads(text2)
        if not validate(obj2, schema):
            return obj2, warnings
    except json.JSONDecodeError:
        pass
    warnings.append("repair attempt also failed; returning null JSON")
    return None, warnings
