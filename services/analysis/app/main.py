"""Kale Forge Analysis Service — FastAPI application entrypoint."""
from __future__ import annotations

import json
import hashlib
import hmac
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.api.routes import router
from app.api.design_routes import router as design_router
from app.api.auth_routes import router as auth_router
from app.config import get_settings
from app.models.db import ModelVersion, get_sessionmaker, init_db
from app.rules.registry import discover_rules, engine_version
from app.services.security import ensure_bootstrap_admin

logging.basicConfig(level=logging.INFO)

settings = get_settings()
app = FastAPI(title="Kale Forge Analysis Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(design_router)
app.include_router(auth_router)

_AUTH_COOKIE = "kale_session"


def _site_token() -> str:
    secret = os.getenv("SITE_COOKIE_SECRET", "")
    if not secret:
        return ""
    return hmac.new(secret.encode(), b"kale-ai-site-session-v1", hashlib.sha256).hexdigest()


def _site_authenticated(request: Request) -> bool:
    expected = _site_token()
    supplied = request.cookies.get(_AUTH_COOKIE, "")
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in — Kale Forge</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; color: #f5f7f8;
      background: radial-gradient(circle at 20% 10%, #244b3c 0, transparent 34%), #09110e; }
    main { width: min(92vw, 420px); padding: 34px; border: 1px solid #315142; border-radius: 22px;
      background: rgba(13, 27, 21, .94); box-shadow: 0 24px 80px rgba(0,0,0,.45); }
    .mark { width: 48px; height: 48px; display: grid; place-items: center; border-radius: 14px;
      background: #8ce5b2; color: #092217; font-size: 25px; font-weight: 800; }
    h1 { margin: 22px 0 8px; font-size: 27px; }
    p { margin: 0 0 25px; color: #aebeb6; line-height: 1.5; }
    label { display: block; margin-bottom: 9px; font-size: 14px; font-weight: 650; }
    input { width: 100%; padding: 14px; border: 1px solid #426253; border-radius: 11px;
      background: #09140f; color: white; font-size: 16px; outline: none; }
    input:focus { border-color: #8ce5b2; box-shadow: 0 0 0 3px rgba(140,229,178,.14); }
    button { width: 100%; margin-top: 15px; padding: 14px; border: 0; border-radius: 11px;
      background: #8ce5b2; color: #092217; font-size: 15px; font-weight: 800; cursor: pointer; }
    .error { padding: 11px 13px; margin-bottom: 16px; border-radius: 9px; color: #ffb4ab;
      background: rgba(130, 45, 42, .28); }
    footer { margin-top: 22px; color: #71877c; font-size: 12px; }
  </style>
</head>
<body><main>
  <div class="mark">K</div>
  <h1>Sign in to Kale Forge</h1>
  <p>Enter the private website password to open the hardware design workspace.</p>
  {error}
  <form method="post" action="/auth/login">
    <label for="password">Website password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
    <button type="submit">Open Kale Forge</button>
  </form>
  <footer>Protected with HTTPS · AI and project data stay on your server</footer>
</main></body></html>"""


@app.get("/auth/login", response_class=HTMLResponse)
def site_login_page(request: Request):
    if _site_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_LOGIN_PAGE.replace("{error}", ""))


@app.post("/auth/login")
def site_login(password: str = Form(...)):
    expected = os.getenv("SITE_PASSWORD", "")
    if not expected or not hmac.compare_digest(password, expected):
        message = '<div class="error">That password is not correct. Please try again.</div>'
        return HTMLResponse(_LOGIN_PAGE.replace("{error}", message), status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        _AUTH_COOKIE, _site_token(), max_age=604800, httponly=True,
        secure=True, samesite="lax", path="/",
    )
    return response


@app.get("/auth/check")
def site_auth_check(request: Request):
    if _site_authenticated(request):
        return Response(status_code=204)
    return RedirectResponse("/auth/login", status_code=303)


@app.post("/auth/logout")
def site_logout():
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(_AUTH_COOKIE, path="/")
    return response


@app.on_event("startup")
def _startup() -> None:
    current_settings = get_settings()
    init_db(current_settings.database_url)
    with get_sessionmaker()() as db:
        ensure_bootstrap_admin(db)
        _seed_model_registry(db)
    discover_rules()  # warm the rule registry


def _seed_model_registry(db) -> None:
    """Expose checked-in development registry entries in the analysis dashboard."""
    registry_path = Path(__file__).resolve().parents[3] / "models" / "registry.json"
    try:
        versions = json.loads(registry_path.read_text()).get("versions", [])
    except (OSError, json.JSONDecodeError):
        logger.warning("could not load model registry from %s", registry_path)
        return
    existing = {version for version, in db.query(ModelVersion.version).all()}
    for entry in versions:
        version = entry.get("version")
        if not version or version in existing:
            continue
        db.add(ModelVersion(
            version=version,
            base_model=entry.get("base_model", ""),
            adapter_path=entry.get("adapter_path", ""),
            provider=entry.get("provider", ""),
            status=entry.get("status", "available"),
            notes=entry.get("notes", ""),
        ))
    db.commit()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "analysis",
        "rule_engine_version": engine_version(),
        "rule_count": len(discover_rules()),
    }
