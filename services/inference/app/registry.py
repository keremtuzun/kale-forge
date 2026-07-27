"""Model registry with versioning, active-version selection, and rollback. Persisted as JSON
at models/registry.json so the analysis service and ops can inspect it."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "models" / "registry.json"


class ModelRegistry:
    def __init__(self, path: Path = REGISTRY_PATH):
        self.path = path
        self._entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._entries = json.loads(self.path.read_text()).get("versions", [])
            except (json.JSONDecodeError, OSError):
                self._entries = []
        if not self._entries:
            self._entries = [{
                "version": "dev-stub", "base_model": "none",
                "adapter_path": "", "provider": "stub", "status": "active",
                "created_at": "1970-01-01T00:00:00Z",
                "notes": "Default development stub; no weights loaded.",
            }]
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"versions": self._entries}, indent=2))

    def list(self) -> list[dict]:
        return list(self._entries)

    def get(self, version: str) -> Optional[dict]:
        return next((e for e in self._entries if e["version"] == version), None)

    def get_active(self) -> Optional[dict]:
        return next((e for e in self._entries if e["status"] == "active"), None)

    def register(self, version: str, base_model: str, adapter_path: str, provider: str,
                 created_at: str, notes: str = "", activate: bool = False) -> dict:
        if self.get(version):
            raise ValueError(f"version {version} already exists")
        entry = {"version": version, "base_model": base_model, "adapter_path": adapter_path,
                 "provider": provider, "status": "available", "created_at": created_at, "notes": notes}
        self._entries.append(entry)
        if activate:
            self.select(version)
        else:
            self._save()
        return entry

    def select(self, version: str) -> dict:
        target = self.get(version)
        if target is None:
            raise ValueError(f"version {version} not found")
        for e in self._entries:
            if e["status"] == "active":
                e["status"] = "available"
        target["status"] = "active"
        self._save()
        return target

    def rollback(self) -> Optional[dict]:
        """Re-point active to the most recently created previously-active/available version."""
        active = self.get_active()
        candidates = [e for e in self._entries if e is not active]
        if not candidates:
            return active
        # choose the newest non-active by created_at
        prev = sorted(candidates, key=lambda e: e.get("created_at", ""), reverse=True)[0]
        if active is not None:
            active["status"] = "rolled_back"
        prev["status"] = "active"
        self._save()
        return prev
