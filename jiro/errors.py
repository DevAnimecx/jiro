"""Error hierarchy and error codes for Jiro.

Every error carries a stable machine-readable ``code`` so clients can handle
failures programmatically, and an HTTP status for the REST layer.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class JiroError(Exception):
    """Base class for all Jiro errors."""

    code = "jiro_error"
    status_code = 500

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"error": self.message, "error_code": self.code}
        if self.details:
            payload["details"] = self.details
        return payload


class ConfigError(JiroError):
    code = "config_error"
    status_code = 500


class ValidationError(JiroError):
    code = "validation_error"
    status_code = 422


class EngineError(JiroError):
    """Generic failure while talking to a search engine."""

    code = "engine_error"
    status_code = 502


class EngineBlockedError(EngineError):
    """Engine detected bot traffic (CAPTCHA, anomaly page, 403/429...).

    This is the trigger for fallback engines / proxies / CAPTCHA solving.
    """

    code = "engine_blocked"
    status_code = 429


class EngineTimeoutError(EngineError):
    code = "engine_timeout"
    status_code = 504


class EngineParseError(EngineError):
    code = "engine_parse_error"
    status_code = 502


class NotFoundError(JiroError):
    code = "not_found"
    status_code = 404


class AuthError(JiroError):
    code = "auth_error"
    status_code = 401


class ForbiddenError(JiroError):
    code = "permission_denied"
    status_code = 403


PermissionError = ForbiddenError


class RateLimitError(JiroError):
    code = "rate_limit_exceeded"
    status_code = 429


class CacheError(JiroError):
    code = "cache_error"
    status_code = 500


class LLMError(JiroError):
    code = "llm_error"
    status_code = 502


class ScrapeError(JiroError):
    code = "scrape_error"
    status_code = 502
