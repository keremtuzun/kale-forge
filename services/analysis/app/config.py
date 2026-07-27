"""Application settings loaded from environment (see .env.example)."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    database_url: str = "sqlite:///./kale.db"
    storage_backend: str = "local"  # local | s3
    storage_dir: str = "./storage"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    job_backend: str = "inprocess"  # inprocess | redis
    redis_url: str = "redis://localhost:6379/0"
    max_upload_mb: int = 50
    max_obj_upload_mb: int = 200
    admin_token: str = "change-me-admin"
    auth_disabled: bool = True
    bootstrap_admin_email: str = ""
    bootstrap_api_token: str = ""
    inference_url: str = "http://localhost:8001"
    inference_timeout_seconds: float = 30.0
    model_version: str = "dev"
    session_secure_cookie: bool = False
    session_days: int = 30
    public_web_url: str = "http://localhost:3000"
    password_reset_minutes: int = 30
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "Kale Forge"
    smtp_starttls: bool = True

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def _load() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./kale.db"),
        storage_backend=os.getenv("STORAGE_BACKEND", "local"),
        storage_dir=os.getenv("STORAGE_LOCAL_DIR", "./storage"),
        cors_origins=[origin.strip() for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",") if origin.strip()],
        s3_bucket=os.getenv("S3_BUCKET", ""),
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL", ""),
        s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID", ""),
        s3_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY", ""),
        job_backend=os.getenv("JOB_BACKEND", "inprocess"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "50")),
        max_obj_upload_mb=int(os.getenv("MAX_OBJ_UPLOAD_MB", "200")),
        admin_token=os.getenv("ADMIN_TOKEN", "change-me-admin"),
        auth_disabled=os.getenv("AUTH_DISABLED", "true").lower() in {"1", "true", "yes"},
        bootstrap_admin_email=os.getenv("BOOTSTRAP_ADMIN_EMAIL", ""),
        bootstrap_api_token=os.getenv("BOOTSTRAP_API_TOKEN", ""),
        inference_url=os.getenv("INFERENCE_URL", "http://localhost:8001"),
        inference_timeout_seconds=float(os.getenv("INFERENCE_TIMEOUT_SECONDS", "30")),
        model_version=os.getenv("KALE_MODEL_VERSION", "dev"),
        session_secure_cookie=os.getenv("SESSION_SECURE_COOKIE", "false").lower() in {"1", "true", "yes"},
        session_days=int(os.getenv("SESSION_DAYS", "30")),
        public_web_url=os.getenv("PUBLIC_WEB_URL", "http://localhost:3000").rstrip("/"),
        password_reset_minutes=int(os.getenv("PASSWORD_RESET_MINUTES", "30")),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from_email=os.getenv("SMTP_FROM_EMAIL", ""),
        smtp_from_name=os.getenv("SMTP_FROM_NAME", "Kale Forge"),
        smtp_starttls=os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"},
    )


@lru_cache
def get_settings() -> Settings:
    return _load()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
