"""Memory error hierarchy and content redaction.

Two rules:

1. **Errors are narrow.** Each subclass names one failure so callers can react
   specifically instead of catching ``Exception``.
2. **Content is sanitised before it is stored.** Memory is the subsystem most
   likely to capture text from the outside world — web pages, documents, tool
   output — so credentials must be redacted on write, not on read. Scrubbing at
   read time would leave the secret sitting in the database.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from jiro.errors import JiroError

#: Key names whose values must never be persisted.
_SECRET_KEYS = re.compile(
    r"(?i)(pass(word|wd)?|secret|token|api[_-]?key|authorization|auth[_-]?token"
    r"|cookie|sessionid|private[_-]?key|credential|bearer|access[_-]?key"
    r"|client[_-]?secret|refresh[_-]?token|signature)"
)

#: Inline credentials embedded in free text.
_SECRET_VALUES = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/=]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b[A-Za-z0-9._\-]{3,}@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|password|secret)\s*[=:]\s*\S{8,}"),
)

REDACTED = "[redacted]"
MAX_TEXT_CHARS = 200_000
TRUNCATION_MARKER = "...[truncated]"


class MemoryError(JiroError):
    """Base class for memory failures."""

    code = "memory_error"
    status_code = 500


class MemoryNotFound(MemoryError):
    """No such memory, or it belongs to another owner."""

    code = "memory_not_found"
    status_code = 404


class MemoryValidationError(MemoryError, ValueError):
    """Content or limits failed validation."""

    code = "memory_validation_error"
    status_code = 422


class MemoryScopeError(MemoryError):
    """A scope operation would cross an ownership boundary."""

    code = "memory_scope_error"
    status_code = 403


class MemoryLimitExceeded(MemoryError):
    """A configured capacity or size limit was reached."""

    code = "memory_limit_exceeded"
    status_code = 429


class MemoryNotDeletable(MemoryError):
    """Deletion was refused, with the reason attached."""

    code = "memory_not_deletable"
    status_code = 409


class MemoryNotExecutable(MemoryError):
    """A stored procedure was asked to run.

    Raised by the service so the refusal is explicit and testable rather than
    being an absence of a feature nobody noticed. See ADR 0010.
    """

    code = "memory_not_executable"
    status_code = 501


class ContradictionError(MemoryError):
    """A contradiction workflow was invoked incorrectly."""

    code = "memory_contradiction"
    status_code = 409


class EmbeddingUnavailable(MemoryError):
    """The optional embedding provider could not be used.

    Never fatal: retrieval falls back to deterministic lexical ranking.
    """

    code = "embedding_unavailable"
    status_code = 503


class MemoryProviderError(MemoryError):
    """An embedding provider failed or returned an unusable vector."""

    code = "memory_provider_error"
    status_code = 502


def redact_text(text: str, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Remove obvious credential material and cap the total length.

    Defence in depth, not a guarantee. Callers must still avoid embedding
    secrets in the first place; this catches the common accidents.

    Returns at most ``max_chars`` characters in total, truncation marker
    included, so callers can size a buffer with this bound.
    """
    if not text:
        return ""
    cleaned = text
    for pattern in _SECRET_VALUES:
        cleaned = pattern.sub(REDACTED, cleaned)
    if len(cleaned) > max_chars:
        if max_chars <= len(TRUNCATION_MARKER):
            return TRUNCATION_MARKER[:max_chars]
        cleaned = cleaned[: max_chars - len(TRUNCATION_MARKER)] + \
            TRUNCATION_MARKER
    return cleaned


def redact_value(value: Any, *, max_chars: int = MAX_TEXT_CHARS) -> Any:
    """Recursively redact strings inside a JSON-shaped value.

    Scalars pass through. Lists and dicts are walked to a bounded depth so a
    deeply nested hostile payload cannot exhaust the stack.
    """
    return _redact_value(value, max_chars, depth=0)


def _redact_value(value: Any, max_chars: int, *, depth: int) -> Any:
    if depth > 12:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value, max_chars=max_chars)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEYS.search(str(key)):
                out[key] = REDACTED
            else:
                out[key] = _redact_value(item, max_chars, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, max_chars, depth=depth + 1) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(str(value), max_chars=max_chars)


def scrub_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitise caller-supplied metadata: redact secrets, cap depth and size."""
    if not metadata:
        return {}
    scrubbed = redact_value(dict(metadata), max_chars=2000)
    assert isinstance(scrubbed, dict)
    return scrubbed


def find_secret_hits(text: str) -> List[str]:
    """Names of credential patterns present in ``text``. Used by tests."""
    hits: List[str] = []
    for index, pattern in enumerate(_SECRET_VALUES):
        if pattern.search(text or ""):
            hits.append(f"pattern_{index}")
    return hits


def describe_scrubbing() -> Tuple[str, ...]:
    """Human-readable description of what redaction covers, for documentation."""
    return (
        "secret-looking keys are replaced with [redacted]",
        "Bearer tokens, sk- keys, GitHub and AWS tokens are removed",
        "email addresses are redacted",
        "'key=value' credential assignments are removed",
        "strings are capped at 200000 characters",
        "structures are walked to depth 12",
    )


__all__ = [
    "ContradictionError",
    "EmbeddingUnavailable",
    "MAX_TEXT_CHARS",
    "MemoryError",
    "MemoryLimitExceeded",
    "MemoryNotDeletable",
    "MemoryNotExecutable",
    "MemoryNotFound",
    "MemoryProviderError",
    "MemoryScopeError",
    "MemoryValidationError",
    "REDACTED",
    "describe_scrubbing",
    "find_secret_hits",
    "redact_text",
    "redact_value",
    "scrub_metadata",
]