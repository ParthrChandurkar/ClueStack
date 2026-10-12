"""Sanitization helpers for persisted deployment report data.

The sanitizer is intentionally conservative and bounded. It reduces the risk of
persisting common secret shapes, but it is not a complete secret-detection
guarantee.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.schemas import DeploymentReportCreate, DeploymentReportError

REDACTION_MARKER = "[REDACTED]"
MAX_DEPTH = 6
MAX_DICT_KEYS = 50
MAX_LIST_ITEMS = 100
MAX_STRING_LENGTH = 2_000
MAX_TOTAL_NODES = 1_000
MAX_ERROR_MESSAGE_LENGTH = 500

SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "authorization",
    "proxy_authorization",
    "cookie",
    "set_cookie",
    "session",
    "sessionid",
    "private_key",
    "client_secret",
    "credential",
    "connection_string",
    "database_url",
    "dsn",
)

SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(proxy-authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(password|passwd|pwd|token|api[_-]?key|secret)\s*([=:])\s*([^\s,;]+)"),
    re.compile(r"(?i)(postgres(?:ql)?|mysql|mongodb|redis)://([^:\s/@]+):([^@\s]+)@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


class _SanitizerBudget:
    """Mutable node budget used to bound sanitizer work."""

    def __init__(self) -> None:
        self.nodes = 0

    def consume(self) -> bool:
        """Return whether one more node may be processed."""

        self.nodes += 1
        return self.nodes <= MAX_TOTAL_NODES


def sanitize_report(report: DeploymentReportCreate) -> DeploymentReportCreate:
    """Return a report payload with sanitized evidence and error messages."""

    sanitized_evidence = sanitize_evidence(report.evidence)
    sanitized_errors = [
        DeploymentReportError(
            code=error.code,
            message=sanitize_text(error.message)[:MAX_ERROR_MESSAGE_LENGTH],
        )
        for error in report.errors
    ]
    return report.model_copy(update={"evidence": sanitized_evidence, "errors": sanitized_errors})


def sanitize_evidence(value: Any) -> dict[str, Any]:
    """Sanitize report evidence and return a schema-compatible dictionary."""

    sanitized = _sanitize_value(value, depth=0, budget=_SanitizerBudget(), key_hint=None)
    if isinstance(sanitized, dict):
        return sanitized
    return {"value": sanitized}


def sanitize_text(value: str) -> str:
    """Redact common secret-bearing text patterns from a string."""

    text = value[:MAX_STRING_LENGTH]
    text = SECRET_TEXT_PATTERNS[0].sub(r"\1" + REDACTION_MARKER, text)
    text = SECRET_TEXT_PATTERNS[1].sub(r"\1" + REDACTION_MARKER, text)
    text = SECRET_TEXT_PATTERNS[2].sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTION_MARKER}", text)
    text = SECRET_TEXT_PATTERNS[3].sub(lambda match: f"{match.group(1)}://{REDACTION_MARKER}:{REDACTION_MARKER}@", text)
    text = SECRET_TEXT_PATTERNS[4].sub(REDACTION_MARKER, text)
    return text


def _sanitize_value(value: Any, *, depth: int, budget: _SanitizerBudget, key_hint: str | None) -> Any:
    """Recursively sanitize bounded JSON-like data."""

    if not budget.consume():
        return "[TRUNCATED]"
    if key_hint and _is_sensitive_key(key_hint):
        return REDACTION_MARKER
    if depth > MAX_DEPTH:
        return "[MAX_DEPTH_EXCEEDED]"
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, depth=depth, budget=budget)
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [
            _sanitize_value(item, depth=depth + 1, budget=budget, key_hint=None)
            for item in list(value)[:MAX_LIST_ITEMS]
        ]
    return "[UNSUPPORTED]"


def _sanitize_mapping(value: Mapping[Any, Any], *, depth: int, budget: _SanitizerBudget) -> dict[str, Any]:
    """Sanitize a mapping with bounded key count."""

    sanitized: dict[str, Any] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= MAX_DICT_KEYS:
            sanitized["__truncated__"] = True
            break
        key_text = str(key)[:MAX_STRING_LENGTH]
        sanitized[key_text] = _sanitize_value(item, depth=depth + 1, budget=budget, key_hint=key_text)
    return sanitized


def _is_sensitive_key(key: str) -> bool:
    """Return whether a field name commonly carries credentials."""

    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    compact = normalized.replace("_", "")
    return any(fragment in normalized or fragment.replace("_", "") in compact for fragment in SENSITIVE_KEY_FRAGMENTS)
