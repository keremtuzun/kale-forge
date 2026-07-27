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
