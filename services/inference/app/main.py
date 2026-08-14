"""Kale Forge Inference Service — self-hosted model server.

All providers point at Kale-controlled infrastructure. No external commercial AI API is
reachable from this service by design."""
from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import get_settings
from app.jsonschema_lite import validate
from app.providers.base import GenerateRequest, GenerateResult, Provider, ProviderUnavailable
from app.providers.stub import StubProvider
from app.registry import ModelRegistry
from app.tracking import Tracker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Kale Forge Inference Service", version="0.1.0")

_settings = get_settings()
_registry = ModelRegistry()
_tracker = Tracker(_settings.log_dir)
_provider: Provider = StubProvider(_settings.model_version)

# On a random quick-tunnel hostname, obscurity was the perimeter. On a stable public URL
# (the cloud deployment) anyone who finds the box could spend minutes of CPU per request,
# so when KALE_API_TOKEN is set every route except /health requires the bearer. Unset —
# the laptop/localhost case — nothing changes.
_API_TOKEN = os.getenv("KALE_API_TOKEN", "")


@app.middleware("http")
async def _require_bearer(request, call_next):
    if _API_TOKEN and request.url.path != "/health":
        supplied = (request.headers.get("Authorization") or "")
        supplied = supplied[7:] if supplied.startswith("Bearer ") else supplied
        if not hmac.compare_digest(supplied.strip(), _API_TOKEN):
            return JSONResponse({"detail": "bearer token required"}, status_code=401)
    return await call_next(request)


def _build_provider(settings) -> Provider:
    p = settings.provider
    if p == "local_llamaserver":
        # Out-of-process llama.cpp. Preferred over local_llamacpp: a model crash cannot
        # take this API down with it.
        from app.providers.llamaserver import LlamaServerProvider

        return LlamaServerProvider(settings.llama_server_url, settings.model_version,
                                   settings.llama_server_timeout)
    if p == "local_llamacpp":
        from app.providers.llamacpp import LlamaCppProvider

        return LlamaCppProvider(settings.model_path, settings.model_version,
                                n_threads=settings.llama_threads)
    if p == "local_transformers":
        from app.providers.transformers_local import TransformersProvider

        return TransformersProvider(settings.model_path, settings.adapter_path,
                                    settings.model_version, settings.batch_size)
    if p == "local_vllm":
        from app.providers.vllm_client import VLLMProvider

        return VLLMProvider(settings.vllm_url, settings.model_version)
    if p == "local_mlx":
        from app.providers.mlx_local import MLXProvider

        return MLXProvider(settings.model_path, settings.adapter_path, settings.model_version)
    return StubProvider(settings.model_version)


@app.on_event("startup")
def _startup() -> None:
    global _provider
    try:
        _provider = _build_provider(_settings)
        _provider.load()
        logger.info("inference provider ready: %s", _provider.name)
    except ProviderUnavailable as exc:
        logger.warning("provider %s unavailable (%s); falling back to stub", _settings.provider, exc)
        _provider = StubProvider(_settings.model_version)


def _require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    if not x_admin_token or x_admin_token != os.getenv("ADMIN_TOKEN", _settings.admin_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin token required")


@app.get("/health")
def health() -> dict:
    active = _registry.get_active()
    return {"status": "ok", "service": "inference", "provider": _provider.name,
            "model_version": getattr(_provider, "model_version", _settings.model_version),
            "active_registry_version": active["version"] if active else None}


@app.post("/v1/generate")
def generate(req: GenerateRequest):
    if req.stream:
        return _stream(req)
    try:
        result = _provider.generate(req)
    except ProviderUnavailable as exc:
        # Say the model is gone, rather than inventing an answer and blaming the model for it.
        #
        # This used to fall back to StubProvider, which synthesizes a schema-shaped object with
        # empty strings in it. For any request carrying a schema that object then failed
        # validation below, so a *dead model server* was reported to the caller as "model
        # output violated the constrained JSON contract" — pointing the finger at a process
        # that was not even running, and costing a synthesize-and-validate round trip to do it.
        #
        # 503 is what actually happened. Every caller already treats a non-2xx as
        # InferenceUnavailable and falls back to deterministic synthesis, so the behaviour
        # downstream is unchanged; only the reason recorded on the design becomes true.
        logger.warning("provider unavailable (%s)", exc)
        _tracker.record(provider=_provider.name, model_version=_settings.model_version,
                        latency_ms=0.0, prompt_tokens=0, completion_tokens=0,
                        ok=False, schema_valid=False)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"message": "inference provider unavailable", "provider": _provider.name,
             "detail": str(exc)},
        ) from exc

    schema_valid = True
    if req.json_schema is not None:
        obj = result.json
        errors = validate(obj, req.json_schema) if obj is not None else ["no JSON produced"]
        if errors:
            schema_valid = False
            _tracker.record(
                provider=result.provider, model_version=result.model_version,
                latency_ms=result.latency_ms, prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens, ok=False, schema_valid=False,
            )
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"message": "model output violated the constrained JSON contract",
                 "errors": errors[:20], "provider": result.provider},
            )
    _tracker.record(
        provider=result.provider, model_version=result.model_version, latency_ms=result.latency_ms,
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        ok=True, schema_valid=schema_valid,
    )
    return result


def _stream(req: GenerateRequest) -> StreamingResponse:
    def event_gen():
        try:
            for chunk in _provider.generate_stream(req):
                yield f"data: {json.dumps({'delta': chunk})}\n\n"
        except ProviderUnavailable as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/export/step")
def export_step(prompt: str, season: str = ""):
    """The design as an editable STEP assembly — named, coloured solid bodies.

    Built by step_worker.py in its own interpreter: OpenCascade is a large native library
    with its own failure modes (the llama-server lesson), and the Design Studio's modules
    live in a package that is also called `app`. A subprocess answers both. Deterministic
    synthesis only — same design the studio shows for the same prompt."""
    import subprocess
    import sys as _sys
    import tempfile

    from fastapi.responses import Response

    if not prompt or len(prompt.strip()) < 4:
        raise HTTPException(400, "prompt required")
    worker = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "step_worker.py")
    fd, out_path = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        proc = subprocess.run(
            [_sys.executable, worker, out_path],
            input=json.dumps({"prompt": prompt[:5000], "season": season}).encode(),
            capture_output=True, timeout=240)
        if proc.returncode != 0:
            raise HTTPException(500, "step export failed: "
                                + proc.stderr.decode("utf-8", "replace")[-400:])
        try:
            meta = json.loads(proc.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            meta = {}
        with open(out_path, "rb") as fh:
            data = fh.read()
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "step export timed out")
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    safe = "".join(ch if ch.isalnum() or ch in " -_." else "_"
                   for ch in (meta.get("name") or "Kale FRC Robot"))[:80]
    return Response(
        content=data, media_type="application/step",
        headers={"Content-Disposition": f'attachment; filename="{safe}.step"',
                 "X-Kale-Parts": str(meta.get("parts", ""))})


@app.get("/v1/models")
def list_models() -> dict:
    return {"versions": _registry.list(), "active": _registry.get_active()}


@app.post("/v1/models/select")
def select_model(body: dict, _admin=Depends(_require_admin)) -> dict:
    version = body.get("version")
    try:
        return {"active": _registry.select(version)}
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.post("/v1/models/rollback")
def rollback_model(_admin=Depends(_require_admin)) -> dict:
    return {"active": _registry.rollback()}


@app.post("/v1/adapters/load")
def load_adapter(body: dict, _admin=Depends(_require_admin)) -> dict:
    from pathlib import Path

    adapters_root = (Path(__file__).resolve().parents[3] / "models" / "adapters").resolve()
    path = (adapters_root / body.get("adapter", "")).resolve()
    if not str(path).startswith(str(adapters_root)):
        raise HTTPException(400, "adapter path escapes models/adapters")
    if not path.exists():
        raise HTTPException(404, f"adapter not found under models/adapters: {body.get('adapter')}")
    return {"status": "registered", "adapter_path": str(path),
            "note": "Adapter recorded; restart the provider or select a registry version to activate."}


@app.get("/v1/stats")
def stats() -> dict:
    return _tracker.stats()
