"""Scan training rows for secrets and PII before they can enter a dataset."""
from __future__ import annotations

import math
import re
from typing import Any

_PATTERNS = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "bearer_token": re.compile(r"\b[Bb]earer\s+[A-Za-z0-9\-._~+/]{20,}"),
    "github_token": re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    "generic_api_key": re.compile(r"(?i)\b(api[_-]?key|secret|passwd|password)\b\s*[:=]\s*\S{8,}"),
    "email": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)"),
}


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def scan_text(text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for name, pattern in _PATTERNS.items():
        for m in pattern.finditer(text):
            findings.append({"type": name, "match": _redact(m.group(0))})
    # high-entropy tokens (possible secrets)
    for token in re.findall(r"[A-Za-z0-9+/=_\-]{24,}", text):
        if _shannon_entropy(token) > 4.0:
            findings.append({"type": "high_entropy_string", "match": _redact(token)})
    return findings


def _redact(s: str) -> str:
    if len(s) <= 6:
        return "***"
    return s[:3] + "***" + s[-2:]


def scan_example(example: dict) -> list[dict[str, Any]]:
    import json

    return scan_text(json.dumps(example, default=str))
