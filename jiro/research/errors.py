"""Typed errors for the research engine.

Every failure the engine can produce has a named class and a stable ``code``.
The report and the API both surface these codes, so a caller can distinguish
"the provider was down" from "we asked and there was nothing" — a distinction
that matters, because presenting an outage as an absence of evidence is how a
research tool starts lying.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from jiro.errors import JiroError


class ResearchError(JiroError):
    """Base class for research engine failures."""

    code = "research_error"
    status_code = 500


class ResearchValidationError(ResearchError, ValueError):
    """The request is malformed, oversized, or internally inconsistent.

    Never retryable: replaying the same invalid request produces the same error.
    """

    code = "research_validation_error"
    status_code = 422


class ResearchBudgetExceeded(ResearchError):
    """A budget was exhausted.

    Not a bug. Reaching a budget is a normal, expected way for research to end,
    and the caller is expected to produce a partial report rather than fail.
    """

    code = "research_budget_exceeded"
    status_code = 429


class ResearchCancelled(ResearchError):
    """The task was cancelled while research was in flight."""

    code = "research_cancelled"
    status_code = 499


class ResearchProviderError(ResearchError):
    """A search provider failed.

    Kept distinct from an empty result set on purpose. A provider that is down
    has told us nothing about the world.
    """

    code = "research_provider_error"
    status_code = 502


class SourceFetchError(ResearchError):
    """A source could not be retrieved or parsed.

    Attributes:
        reason: one of ``timeout``, ``denied``, ``ssrf_blocked``,
            ``robots_denied``, ``unsupported_type``, ``too_large``,
            ``parse_error``, ``no_content``, ``dns_error``, ``tls_error``,
            ``error``. Recorded on the source row so a report can say *why* a
            source is missing rather than silently omitting it.
    """

    code = "source_fetch_error"
    status_code = 502

    def __init__(self, message: str, *, reason: str = "error",
                 details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, details=details)
        self.reason = reason


class ResearchNoEvidence(ResearchError):
    """No reliable evidence was gathered, so no report can be written.

    Raised instead of generating a report with nothing behind it. A research
    engine that answers an unanswerable question with a plausible essay is worse
    than one that says it could not find out.
    """

    code = "research_no_evidence"
    status_code = 409


class CitationError(ResearchError):
    """A citation in a draft report does not resolve to a stored source.

    Raised during citation validation, before the report is persisted. It is a
    safety net for synthesis, not a user-facing failure.
    """

    code = "citation_error"
    status_code = 500


__all__ = [
    "CitationError",
    "ResearchBudgetExceeded",
    "ResearchCancelled",
    "ResearchError",
    "ResearchNoEvidence",
    "ResearchProviderError",
    "ResearchValidationError",
    "SourceFetchError",
]