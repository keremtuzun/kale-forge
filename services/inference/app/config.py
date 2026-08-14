"""Inference service configuration. All providers point at Kale-controlled infrastructure;
no external commercial AI provider exists here."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    provider: str = "stub"  # stub | local_llamaserver | local_llamacpp | local_transformers | local_vllm | local_mlx
    model_path: str = ""
    adapter_path: str = ""
    vllm_url: str = "http://localhost:8010/v1"
    model_version: str = "dev"
    admin_token: str = "change-me-admin"
    log_dir: str = "./logs"
    batch_size: int = 4
    llama_threads: int = 0  # 0 = half the cores, capped at 8; see LlamaCppProvider
    llama_server_url: str = "http://127.0.0.1:8010/v1"
    llama_server_timeout: float = 300.0


@lru_cache
def get_settings() -> Settings:
    return Settings(
        provider=os.getenv("KALE_INFERENCE_PROVIDER", "stub"),
        model_path=os.getenv("KALE_MODEL_PATH", ""),
        adapter_path=os.getenv("KALE_ADAPTER_PATH", ""),
        vllm_url=os.getenv("KALE_VLLM_URL", "http://localhost:8010/v1"),
        model_version=os.getenv("KALE_MODEL_VERSION", "dev"),
        admin_token=os.getenv("ADMIN_TOKEN", "change-me-admin"),
        log_dir=os.getenv("LOG_DIR", "./logs"),
        batch_size=int(os.getenv("KALE_BATCH_SIZE", "4")),
        llama_threads=int(os.getenv("KALE_LLAMA_THREADS", "0")),
        llama_server_url=os.getenv("KALE_LLAMA_SERVER_URL", "http://127.0.0.1:8010/v1"),
        llama_server_timeout=float(os.getenv("KALE_LLAMA_SERVER_TIMEOUT", "300")),
    )


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
