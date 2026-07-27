"""Evaluation configurations comparing rules-only, base, fine-tuned, and rules+fine-tuned."""
from __future__ import annotations

from pydantic import BaseModel

CONFIGS = {
    "rules-only": {"use_rules": True, "use_model": False},
    "base": {"use_rules": False, "use_model": True},
    "finetuned": {"use_rules": False, "use_model": True},
    "rules+finetuned": {"use_rules": True, "use_model": True},
}


class EvalConfig(BaseModel):
    name: str
    use_rules: bool = True
    use_model: bool = False
    inference_url: str = "http://localhost:8001"
    model_version: str | None = None

    @classmethod
    def named(cls, name: str, inference_url: str = "http://localhost:8001",
              model_version: str | None = None) -> "EvalConfig":
        if name not in CONFIGS:
            raise ValueError(f"unknown config '{name}'; valid: {list(CONFIGS)}")
        return cls(name=name, inference_url=inference_url, model_version=model_version, **CONFIGS[name])
