"""Public account endpoints and secure cookie-backed web sessions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.db import PasswordResetToken, User, WebSession
from app.services.design_studio import get_design_studio
from app.services.email import send_password_reset_email
from app.services.security import SESSION_COOKIE, get_current_user, get_db

router = APIRouter(prefix="/api/auth", tags=["accounts"])
logger = logging.getLogger(__name__)
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_attempts: dict[str, list[float]] = {}
_attempt_lock = threading.Lock()


class RegisterBody(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=10, max_length=200)


class LoginBody(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class ProfileBody(BaseModel):
    name: str = Field(min_length=2, max_length=100)


class ForgotPasswordBody(BaseModel):
    email: str = Field(min_length=5, max_length=320)


class ResetPasswordBody(BaseModel):
    token: str = Field(min_length=32, max_length=512)
    password: str = Field(min_length=10, max_length=200)


def _limited(request: Request, limit: int = 30) -> None:
    key = request.client.host if request.client else "unknown"
    now = time.time()
    with _attempt_lock:
        recent = [stamp for stamp in _attempts.get(key, []) if now - stamp < 900]
        if len(recent) >= limit:
            raise HTTPException(429, "Too many account attempts. Try again later.")
        recent.append(now); _attempts[key] = recent


def _password_hash(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(derived).decode()


def _password_ok(password: str, encoded: str | None) -> bool:
    try:
        scheme, work, salt_text, digest_text = (encoded or "").split("$")
        if scheme != "scrypt": return False
        actual = hashlib.scrypt(password.encode(), salt=base64.urlsafe_b64decode(salt_text), n=int(work), r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, base64.urlsafe_b64decode(digest_text))
    except (ValueError, TypeError):
        return False


def _validate_password(password: str) -> None:
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(400, "Password must contain a letter and a number")


def _user_out(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat()}


def _new_session(response: Response, db: Session, user: User) -> None:
    settings = get_settings(); raw = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_days)
    db.add(WebSession(user_id=user.id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=expires))
    db.commit()
    response.set_cookie(SESSION_COOKIE, raw, max_age=settings.session_days * 86400, httponly=True,
                        secure=settings.session_secure_cookie, samesite="lax", path="/")


@router.post("/register", status_code=201)
def register(body: RegisterBody, request: Request, response: Response, db: Session = Depends(get_db)):
    _limited(request); email = body.email.strip().lower(); name = body.name.strip()
    if not _EMAIL.match(email): raise HTTPException(400, "Enter a valid email address")
    _validate_password(body.password)
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(409, "An account with that email already exists")
    user = User(email=email, name=name, password_hash=_password_hash(body.password), account_status="active")
    db.add(user); db.commit()
    # New workspaces start empty on purpose: people explore faster from a blank Design Studio
    # than from someone else's robot. The worked example is available on demand instead
    # (POST /api/designs/example), so nothing about signup can fail on storage.
    _new_session(response, db, user)
    return _user_out(user)


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    _limited(request); email = body.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not _password_ok(body.password, user.password_hash):
        time.sleep(0.25); raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")
    if user.account_status != "active": raise HTTPException(403, "Account is not active")
    # Clear the starter example left on accounts created before it became opt-in. Only
    # untouched copies are removed — anything the owner revised or published is their work.
    try:
        get_design_studio().purge_untouched_example(user.id)
    except Exception:  # never block a sign-in on housekeeping
        logger.warning("could not purge starter example for user %s", user.id, exc_info=True)
    _new_session(response, db, user); return _user_out(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        row = db.execute(select(WebSession).where(WebSession.token_hash == hashlib.sha256(raw.encode()).hexdigest())).scalar_one_or_none()
        if row: db.delete(row); db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_out(user)


@router.patch("/me")
def update_me(body: ProfileBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user.name = body.name.strip(); db.commit(); return _user_out(user)


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordBody, request: Request, db: Session = Depends(get_db)):
    """Create and email a one-time reset token without disclosing account existence."""

    _limited(request, limit=10)
    settings = get_settings()
    email = body.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is not None and user.account_status == "active":
        now = datetime.now(timezone.utc)
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        ).update({"used_at": now}, synchronize_session=False)
        raw = secrets.token_urlsafe(48)
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=now + timedelta(minutes=max(10, min(settings.password_reset_minutes, 120))),
        )
        db.add(token)
        db.commit()
        reset_url = f"{settings.public_web_url}/reset-password?token={quote(raw)}"
        if not send_password_reset_email(settings, user.email, user.name, reset_url):
            db.delete(token)
            db.commit()
            logger.warning("password reset token discarded after email delivery failure")
    # Keep response body and timing similar for known and unknown addresses.
    time.sleep(0.15)
    return {
        "message": (
            "If an active account exists for that email, a password reset link "
            "will arrive shortly."
        )
    }


@router.post("/reset-password")
def reset_password(body: ResetPasswordBody, request: Request, db: Session = Depends(get_db)):
    """Consume a one-time token, replace the password, and revoke all sessions."""

    _limited(request)
    _validate_password(body.password)
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    token = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if token is None or token.used_at is not None:
        raise HTTPException(400, "This password reset link is invalid or has already been used")
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        token.used_at = now
        db.commit()
        raise HTTPException(400, "This password reset link has expired")
    user = db.get(User, token.user_id)
    if user is None or user.account_status != "active":
        token.used_at = now
        db.commit()
        raise HTTPException(400, "This password reset link is invalid")

    user.password_hash = _password_hash(body.password)
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": now}, synchronize_session=False)
    db.query(WebSession).filter(WebSession.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    return {"message": "Your password has been reset. Sign in with your new password."}
