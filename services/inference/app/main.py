"""Kale Forge Inference Service — self-hosted model server.

All providers point at Kale-controlled infrastructure. No external commercial AI API is
reachable from this service by design."""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse

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


def _build_provider(settings) -> Provider:
    p = settings.provider
    if p == "local_llamacpp":
        from app.providers.llamacpp import LlamaCppProvider

        return LlamaCppProvider(settings.model_path, settings.model_version)
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
        logger.warning("provider failed (%s); using stub", exc)
        result = StubProvider(_settings.model_version).generate(req)

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
