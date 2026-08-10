"""Inference service tests: schema synthesis/validation, generate roundtrip, registry, admin gate."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.jsonschema_lite import synthesize, validate
from app.providers.base import GenerateRequest
from app.providers.stub import StubProvider

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "confidence": {"type": "number"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "findings": {"type": "array", "items": {"type": "object",
                     "properties": {"title": {"type": "string"}}, "required": ["title"]}},
    },
    "required": ["summary", "confidence"],
}


class TestJsonSchemaLite:
    def test_synthesize_valid(self):
        obj = synthesize(SCHEMA)
        assert validate(obj, SCHEMA) == []
        assert obj["summary"] == "" and obj["confidence"] == 0.0 and obj["limitations"] == []

    def test_validate_catches_missing_required(self):
        errs = validate({"summary": "x"}, SCHEMA)
        assert any("confidence" in e for e in errs)

    def test_validate_type_mismatch(self):
        errs = validate({"summary": 5, "confidence": 0.1}, SCHEMA)
        assert any("summary" in e for e in errs)

    def test_bool_not_number(self):
        errs = validate({"summary": "x", "confidence": True}, SCHEMA)
        assert any("confidence" in e for e in errs)

    def test_strict_keywords(self):
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"kind": {"type": "string", "enum": ["tube"]},
                                 "size": {"type": "number", "exclusiveMinimum": 0,
                                          "maximum": 10}},
                  "required": ["kind", "size"]}
        errs = validate({"kind": "magic", "size": float("nan"), "extra": 1}, schema)
        assert any("not allowed" in e for e in errs)
        assert any("finite" in e for e in errs)
        assert any("additional property" in e for e in errs)


class TestStubProvider:
    def test_stub_synthesizes_schema(self):
        result = StubProvider().generate(GenerateRequest(user="hi", json_schema=SCHEMA))
        assert result.provider == "stub"
        assert validate(result.json, SCHEMA) == []
        assert "stub" in result.json["summary"].lower()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KALE_INFERENCE_PROVIDER", "stub")
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("KALE_MODEL_VERSION", "test-model")
    import app.config as config
    config.get_settings.cache_clear()
    # point the registry at a temp file
    import app.main as main
    from app.registry import ModelRegistry
    reg_path = tmp_path / "registry.json"
    main._registry = ModelRegistry(reg_path)
    main._tracker.__init__(str(tmp_path / "logs"))
    main._provider = StubProvider("test-model")
    with TestClient(main.app) as c:
        yield c
    config.get_settings.cache_clear()


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["provider"] == "stub"


def test_generate_roundtrip_and_stats(client):
    resp = client.post("/v1/generate", json={"user": "review this", "json_schema": SCHEMA})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "stub"
    assert validate(body["json"], SCHEMA) == []
    stats = client.get("/v1/stats").json()
    assert stats["requests"] == 1


def test_generate_without_schema(client):
    resp = client.post("/v1/generate", json={"user": "hello"})
    assert resp.status_code == 200
    assert resp.json()["json"] is None


def test_registry_select_and_rollback(client):
    models = client.get("/v1/models").json()
    assert models["active"]["version"] == "dev-stub"
    # register a second version directly then select via API
    import app.main as main
    main._registry.register("v1", "Qwen/Qwen2.5-7B-Instruct", "models/adapters/v1", "local_vllm",
                            created_at="2026-01-01T00:00:00Z")
    resp = client.post("/v1/models/select", json={"version": "v1"}, headers={"X-Admin-Token": "test-admin"})
    assert resp.status_code == 200
    assert client.get("/v1/models").json()["active"]["version"] == "v1"
    # rollback
    resp = client.post("/v1/models/rollback", headers={"X-Admin-Token": "test-admin"})
    assert resp.status_code == 200
    assert client.get("/v1/models").json()["active"]["version"] == "dev-stub"


def test_admin_gate(client):
    assert client.post("/v1/models/select", json={"version": "v1"}).status_code == 403
    assert client.post("/v1/models/rollback").status_code == 403


def test_streaming(client):
    resp = client.post("/v1/generate", json={"user": "stream please", "stream": True})
    assert resp.status_code == 200
    assert "data:" in resp.text
    assert "[DONE]" in resp.text


class TestMLXProvider:
    def test_build_provider_selects_mlx(self):
        from app.config import Settings
        from app.main import _build_provider
        from app.providers.mlx_local import MLXProvider

        provider = _build_provider(Settings(provider="local_mlx", model_path="some/model",
                                            adapter_path="models/adapters/x",
                                            model_version="v-test"))
        assert isinstance(provider, MLXProvider)
        assert provider.adapter_path == "models/adapters/x"
        assert provider.model_version == "v-test"

    def test_load_requires_model_path(self):
        from app.providers.base import ProviderUnavailable
        from app.providers.mlx_local import MLXProvider

        with pytest.raises(ProviderUnavailable):
            MLXProvider("").load()

    def test_load_requires_existing_adapter_dir(self):
        from app.providers.base import ProviderUnavailable
        from app.providers.mlx_local import MLXProvider

        with pytest.raises(ProviderUnavailable):
            MLXProvider("some/model", adapter_path="/nonexistent/adapter").load()

    def test_fence_stripping(self):
        from app.providers.mlx_local import _strip_fences

        assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
        assert _strip_fences('{"a": 1}') == '{"a": 1}'


def test_an_unavailable_provider_is_reported_as_unavailable_not_as_bad_model_output(monkeypatch):
    """A dead model server must not be blamed on the model.

    The handler used to answer ProviderUnavailable by synthesizing a StubProvider result, which
    for any schema-carrying request then failed validation — so a model process that was not
    running came back to the caller as "model output violated the constrained JSON contract".
    """
    from fastapi.testclient import TestClient

    from app import main as main_module
    from app.providers.base import Provider, ProviderUnavailable

    class DeadProvider(Provider):
        name = "local_llamaserver"

        def generate(self, req):
            raise ProviderUnavailable("llama-server unreachable at http://127.0.0.1:8010/v1")

    monkeypatch.setattr(main_module, "_provider", DeadProvider())
    client = TestClient(main_module.app)
    response = client.post("/v1/generate", json={
        "system": "s", "user": "u", "max_tokens": 32, "temperature": 0.1,
        "json_schema": {"type": "object", "properties": {"a": {"type": "string", "enum": ["x"]}},
                        "required": ["a"]},
    })
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "unavailable" in detail["message"]
    assert "llama-server unreachable" in detail["detail"]
    assert "constrained JSON contract" not in str(detail)
