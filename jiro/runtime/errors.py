"""Runtime error hierarchy, retry classification, and error sanitisation.

Two rules govern this module:

1. **No traceback ever reaches a durable record or an API response.** Failures
   are reduced to ``(code, message)`` by :func:`sanitize_error` before they are
   written. Full context goes to the structured log, not the task row.
2. **Classification is centralised here.** No other module decides whether a
   failure is retryable. Call :func:`classify_failure`.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, Tuple

from jiro.errors import (
    AuthError,
    CacheError,
    ConfigError,
    EngineBlockedError,
    EngineError,
    EngineTimeoutError,
    ForbiddenError,
    LLMError,
    LicenseError,
    NotFoundError,
    RateLimitError,
    ScrapeError,
    SSRFError,
    ValidationError,
)
from jiro.runtime.types import FailureKind

#: Task-specific errors. These are runtime concerns and intentionally live here
#: rather than in ``jiro/errors.py``, which is transport-agnostic and already
#: consumed by every existing router.
from jiro.errors import JiroError  # noqa: F401  (re-exported for convenience)


class TaskError(JiroError):
    """Base class for durable-runtime failures."""

    code = "task_error"
    status_code = 500


class TaskNotFound(TaskError):
    code = "task_not_found"
    status_code = 404


class TaskValidationError(TaskError, ValueError):
    """Task input or requested limits failed validation. Never retryable."""

    code = "task_validation_error"
    status_code = 422


class InvalidTransition(TaskError):
    """A state transition the lifecycle does not permit was attempted."""

    code = "invalid_transition"
    status_code = 409


class TaskLimitExceeded(TaskError):
    """A requested limit is outside the server's hard maximum."""

    code = "task_limit_exceeded"
    status_code = 422


class UnknownTaskType(TaskError):
    """No handler is registered for the requested task type."""

    code = "unknown_task_type"
    status_code = 422


class TaskCancelled(TaskError):
    """Raised internally when a task's execution was cancelled."""

    code = "task_cancelled"
    status_code = 409


class StorageFailure(TaskError):
    """The persistence layer failed. Retryable, but never swallowed."""

    code = "storage_failure"
    status_code = 500


class ResultTooLarge(TaskError):
    """The handler returned a payload larger than the task's result cap."""

    code = "result_too_large"
    status_code = 422


class HandlerNotCancellable(TaskError):
    """A handler cannot be interrupted safely. Reported honestly, never faked."""

    code = "handler_not_cancellable"
    status_code = 409


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

#: Permanent by construction: the same input would fail identically forever.
_PERMANENT_EXCEPTIONS: Tuple[type, ...] = (
    TaskValidationError,
    TaskLimitExceeded,
    UnknownTaskType,
    ValidationError,
    ConfigError,
    NotFoundError,
    AuthError,
    ForbiddenError,
    LicenseError,
    SSRFError,
    InvalidTransition,
    ResultTooLarge,
)

#: Transient: the operation is safe to repeat and may succeed later.
#: ``TimeoutError`` is deliberately absent — on Python 3.11+ ``asyncio.TimeoutError``
#: *is* ``TimeoutError``, and a timeout must classify as TIMEOUT so the runtime
#: reports a distinguishable timeout count rather than a generic retry.
_TRANSIENT_EXCEPTIONS: Tuple[type, ...] = (
    RateLimitError,
    EngineBlockedError,
    EngineTimeoutError,
)

_INFRASTRUCTURE_EXCEPTIONS: Tuple[type, ...] = (CacheError, StorageFailure, OSError)


def classify_failure(exc: BaseException) -> FailureKind:
    """Decide how the runtime should treat ``exc``.

    Order matters: cancellation is checked first so that a
    ``CancelledError`` raised inside a handler is never mistaken for a
    transient failure and retried.
    """
    # ``asyncio.CancelledError`` is a BaseException on 3.8+, and a subclass of
    # BaseException rather than Exception, so it must be tested explicitly.
    if isinstance(exc, asyncio.CancelledError):
        return FailureKind.CANCELLED
    if isinstance(exc, TaskCancelled):
        return FailureKind.CANCELLED
    if isinstance(exc, _PERMANENT_EXCEPTIONS):
        return FailureKind.PERMANENT
    # Checked before the transient tuple so a timeout is reported as TIMEOUT.
    if isinstance(exc, TimeoutError):
        return FailureKind.TIMEOUT
    if isinstance(exc, _INFRASTRUCTURE_EXCEPTIONS):
        return FailureKind.INFRASTRUCTURE
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return FailureKind.TRANSIENT
    # Network-layer failures from the HTTP stack Jiro already depends on. Kept
    # optional so the runtime never hard-depends on httpx at import time.
    try:  # pragma: no cover - exercised only when httpx is installed
        import httpx

        if isinstance(exc, (httpx.TransportError, httpx.HTTPStatusError)):
            return FailureKind.TRANSIENT
    except ImportError:  # pragma: no cover
        pass
    # Scrape/LLM/engine failures are network-shaped by construction.
    if isinstance(exc, (ScrapeError, LLMError, EngineError)):
        return FailureKind.TRANSIENT
    if isinstance(exc, JiroError):
        # An unlisted JiroError is a bug rather than a handler failure. Treat it
        # as permanent so a coding error cannot trigger a retry storm.
        return FailureKind.PERMANENT
    return FailureKind.UNEXPECTED


def is_retryable(kind: FailureKind) -> bool:
    """Whether a failure of this kind may be retried if budget remains."""
    return kind not in (FailureKind.PERMANENT, FailureKind.CANCELLED)


# --------------------------------------------------------------------------
# Sanitisation
# --------------------------------------------------------------------------

#: Key names whose *values* must never be persisted, logged, or returned.
_SECRET_KEYS = re.compile(
    r"(?i)(pass(word|wd)?|secret|token|api[_-]?key|authorization|auth[_-]?token"
    r"|cookie|sessionid|private[_-]?key|credential|bearer|access[_-]?key)"
)

#: Inline secrets embedded in free-form text (``sk-...``, ``Bearer ...``).
_SECRET_VALUES = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/=]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\b[A-Za-z0-9._\-]{8,}@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),  # emails
)

MAX_ERROR_MESSAGE = 500
REDACTED = "[redacted]"


def _redact(message: str) -> str:
    cleaned = message
    for pattern in _SECRET_VALUES:
        cleaned = pattern.sub(REDACTED, cleaned)
    return cleaned


def sanitize_error(exc: BaseException, *, max_length: int = MAX_ERROR_MESSAGE) -> Tuple[str, str]:
    """Reduce an exception to a safe ``(code, message)`` pair.

    The message is scrubbed for obvious credential material and truncated. The
    traceback is deliberately dropped — it is logged separately at the call
    site where it is useful, and never persisted.

    Note:
        This is defence in depth, not a guarantee. Handlers must still avoid
        embedding secrets in their own exception messages.
    """
    if isinstance(exc, JiroError):
        code = exc.code
    elif isinstance(exc, asyncio.TimeoutError) or isinstance(exc, TimeoutError):
        code = "attempt_timeout"
    elif isinstance(exc, asyncio.CancelledError):
        code = "task_cancelled"
    else:
        code = "unexpected_error"

    message = _redact(str(exc) or exc.__class__.__name__).replace("\n", " ").strip()
    if len(message) > max_length:
        message = message[: max_length - 3] + "..."
    return code, message


def sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Drop secret-looking keys and oversized values from event metadata.

    Keeps the durable event table safe to expose to an operator: it stores only
    small scalars, never payloads or credentials.
    """
    clean: Dict[str, Any] = {}
    for key, value in metadata.items():
        if _SECRET_KEYS.search(str(key)):
            clean[key] = REDACTED
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = _redact(value)[:200]
        else:
            # Nested structures and lists could carry payloads; replace with a
            # shape-only summary rather than guessing at their contents.
            clean[key] = type(value).__name__
    return clean


__all__ = [
    "FailureKind",
    "HandlerNotCancellable",
    "InvalidTransition",
    "ResultTooLarge",
    "StorageFailure",
    "TaskCancelled",
    "TaskError",
    "TaskLimitExceeded",
    "TaskNotFound",
    "TaskValidationError",
    "UnknownTaskType",
    "classify_failure",
    "is_retryable",
    "sanitize_error",
    "sanitize_metadata",
]
