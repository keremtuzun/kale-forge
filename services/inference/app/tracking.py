"""Request logging (JSONL) and in-memory rolling latency/token statistics."""
from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Tracker:
    def __init__(self, log_dir: str, window: int = 1000):
        self.log_path = Path(log_dir) / "requests.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=window)
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._count = 0
        self._schema_valid = 0
        self._errors = 0

    def record(self, *, provider: str, model_version: str, latency_ms: float,
               prompt_tokens: int, completion_tokens: int, ok: bool, schema_valid: bool) -> None:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": provider, "model_version": model_version, "latency_ms": round(latency_ms, 2),
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "ok": ok, "schema_valid": schema_valid,
        }
        with self._lock:
            self._latencies.append(latency_ms)
            self._prompt_tokens += prompt_tokens
            self._completion_tokens += completion_tokens
            self._count += 1
            if schema_valid:
                self._schema_valid += 1
            if not ok:
                self._errors += 1
            try:
                with self.log_path.open("a") as fh:
                    fh.write(json.dumps(entry) + "\n")
            except OSError:
                pass

    def stats(self) -> dict[str, Any]:
        with self._lock:
            lat = sorted(self._latencies)
            def pct(p: float) -> float:
                if not lat:
                    return 0.0
                return round(lat[min(len(lat) - 1, int(p * len(lat)))], 2)
            return {
                "requests": self._count,
                "errors": self._errors,
                "schema_valid": self._schema_valid,
                "prompt_tokens_total": self._prompt_tokens,
                "completion_tokens_total": self._completion_tokens,
                "latency_ms_p50": pct(0.5),
                "latency_ms_p95": pct(0.95),
            }
