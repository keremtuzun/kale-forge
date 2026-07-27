"""Provider contract for the Kale Forge self-hosted model server."""
from __future__ import annotations

import abc
from typing import Any, Iterator, Optional

from pydantic import BaseModel, Field


class ProviderUnavailable(RuntimeError):
    """Raised when a provider's backend (weights, library, remote host) is not available."""


class GenerateRequest(BaseModel):
    system: str = ""
    user: str
    json_schema: Optional[dict[str, Any]] = None
    max_tokens: int = 1024
    temperature: float = 0.2
    stream: bool = False


class GenerateResult(BaseModel):
    text: str = ""
    json: Optional[dict[str, Any]] = None
    model_version: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class Provider(abc.ABC):
    name: str = "base"

    def load(self) -> None:
        """Optional heavy initialization (load weights). Default no-op."""

    @abc.abstractmethod
    def generate(self, req: GenerateRequest) -> GenerateResult: ...

    def generate_stream(self, req: GenerateRequest) -> Iterator[str]:
        """Default streaming: yield the full text once. Providers with token streaming override."""
        yield self.generate(req).text
