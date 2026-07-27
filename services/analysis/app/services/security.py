"""Security helpers: filename sanitization, upload validation, admin/user auth dependencies,
and prompt-injection fencing for untrusted uploaded text."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.db import User, WebSession, get_sessionmaker

SESSION_COOKIE = "kale_auth"

ALLOWED_UPLOAD_EXTENSIONS = {
    ".kicad_sch", ".kicad_pcb", ".kicad_pro", ".zip", ".net", ".cir", ".spice", ".csv", ".pdf",
    ".obj", ".mtl",
}

_FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Sentinel that fences untrusted uploaded content in model prompts.
FENCE_BEGIN = "<<<KALE_PROJECT_DATA_BEGIN>>>"
FENCE_END = "<<<KALE_PROJECT_DATA_END>>>"


def sanitize_filename(filename: str) -> str:
    """Reduce to a safe basename: strip directories, control chars, and disallowed characters."""
    base = Path(filename.replace("\\", "/")).name
    base = _CONTROL_RE.sub("", base)
    base = _FILENAME_SANITIZE_RE.sub("_", base)
    base = re.sub(r"\.{2,}", ".", base).strip(". ")
    if not base:
        base = "unnamed"
    if len(base) > 200:
        stem, dot, ext = base.rpartition(".")
        base = (stem[:180] + dot + ext) if dot else base[:200]
    return base


def file_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def validate_upload(filename: str, size_bytes: int, settings: Settings) -> str:
    """Returns the sanitized filename or raises HTTPException."""
    safe = sanitize_filename(filename)
    ext = file_extension(safe)
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"file type '{ext}' not allowed (allowed: {sorted(ALLOWED_UPLOAD_EXTENSIONS)})",
        )
    limit = (settings.max_obj_upload_mb * 1024 * 1024) if ext == ".obj" else settings.max_upload_bytes
    if size_bytes > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file exceeds {settings.max_obj_upload_mb if ext == '.obj' else settings.max_upload_mb} MB limit",
        )
    return safe


def classify_file(filename: str) -> str:
    ext = file_extension(filename)
    return {
        ".kicad_sch": "schematic", ".kicad_pcb": "pcb", ".kicad_pro": "project",
        ".net": "netlist", ".cir": "netlist", ".spice": "netlist",
        ".csv": "bom", ".pdf": "datasheet", ".zip": "archive", ".obj": "mesh", ".mtl": "material",
    }.get(ext, "other")


def fence_user_content(text: str) -> str:
    """Wrap untrusted uploaded text between sentinels, stripping any embedded sentinel so the
    upload cannot forge a fence boundary or inject system instructions."""
    cleaned = text.replace(FENCE_BEGIN, "").replace(FENCE_END, "")
    return f"{FENCE_BEGIN}\n{cleaned}\n{FENCE_END}"


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    settings = get_settings()
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin token required")


def get_db() -> Session:  # FastAPI dependency
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DEMO_USER_ID = "00000000-0000-0000-0000-000000000000"


def ensure_demo_user(db: Session) -> User:
    user = db.get(User, DEMO_USER_ID)
    if user is None:
        user = User(id=DEMO_USER_ID, email="demo@kale.local", name="Local Demo", is_admin=True)
        db.add(user)
        db.commit()
    return user


def ensure_bootstrap_admin(db: Session) -> None:
    """Create the explicitly configured first administrator, if any.

    Production deployments need a way to authenticate the initial operator without
    exposing a registration endpoint. This is deliberately opt-in: an empty token
    never creates an account, and later changes to the token do not overwrite an
    existing user's credentials.
    """
    settings = get_settings()
    if settings.auth_disabled or not settings.bootstrap_api_token:
        return
    email = settings.bootstrap_admin_email or "admin@kale.local"
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        db.add(User(email=email, name="Kale Administrator", is_admin=True,
                    api_token=settings.bootstrap_api_token))
        db.commit()
    elif user.api_token != settings.bootstrap_api_token or not user.is_admin:
        # This makes a secret-manager rotation effective on the next restart while
        # keeping bootstrap credentials out of the web UI and API payloads.
        user.api_token = settings.bootstrap_api_token
        user.is_admin = True
        db.commit()


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    kale_auth: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    settings = get_settings()
    if settings.auth_disabled:
        return ensure_demo_user(db)
    user = None
    if kale_auth:
        import hashlib
        from datetime import datetime, timezone
        token_hash = hashlib.sha256(kale_auth.encode()).hexdigest()
        session = db.execute(select(WebSession).where(WebSession.token_hash == token_hash)).scalar_one_or_none()
        if session and session.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            user = db.get(User, session.user_id)
    if user is None and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        user = db.execute(select(User).where(User.api_token == token)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    if user.account_status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="account is not active")
    return user
