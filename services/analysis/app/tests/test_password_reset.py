from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def account_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'accounts.db'}")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("PUBLIC_WEB_URL", "https://kale.example")
    monkeypatch.setenv("SESSION_SECURE_COOKIE", "false")

    import app.config as config
    import app.models.db as dbm

    config.get_settings.cache_clear()
    dbm.init_db(config.get_settings().database_url)

    import app.api.auth_routes as auth_routes

    deliveries: list[dict[str, str]] = []

    def capture_email(_settings, recipient: str, name: str, reset_url: str) -> bool:
        deliveries.append({"recipient": recipient, "name": name, "url": reset_url})
        return True

    monkeypatch.setattr(auth_routes, "send_password_reset_email", capture_email)

    from app.main import app

    with TestClient(app) as client:
        yield client, deliveries
    config.get_settings.cache_clear()


def test_reset_is_one_time_revokes_sessions_and_preserves_projects(account_client):
    client, deliveries = account_client
    registered = client.post(
        "/api/auth/register",
        json={"name": "Ada Builder", "email": "ada@example.com", "password": "Original1234"},
    )
    assert registered.status_code == 201

    created = client.post("/api/projects", json={"name": "Saved drivetrain"})
    assert created.status_code == 201
    project_id = created.json()["id"]

    requested = client.post("/api/auth/forgot-password", json={"email": "ADA@example.com"})
    assert requested.status_code == 200
    assert "If an active account exists" in requested.json()["message"]
    assert len(deliveries) == 1
    assert deliveries[0]["recipient"] == "ada@example.com"
    token = parse_qs(urlparse(deliveries[0]["url"]).query)["token"][0]

    reset = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "Replacement5678"},
    )
    assert reset.status_code == 200

    # The password change revokes the session that requested it.
    assert client.get("/api/auth/me").status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "ada@example.com", "password": "Original1234"},
    ).status_code == 401

    logged_in = client.post(
        "/api/auth/login",
        json={"email": "ada@example.com", "password": "Replacement5678"},
    )
    assert logged_in.status_code == 200
    projects = client.get("/api/projects")
    assert projects.status_code == 200
    assert any(project["id"] == project_id for project in projects.json())

    reused = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "AnotherPassword9"},
    )
    assert reused.status_code == 400


def test_forgot_password_does_not_reveal_unknown_accounts(account_client):
    client, deliveries = account_client
    result = client.post("/api/auth/forgot-password", json={"email": "unknown@example.com"})
    assert result.status_code == 200
    assert "If an active account exists" in result.json()["message"]
    assert deliveries == []
