"""Validated configuration for the research engine.

Follows the Phase 2 pattern: a frozen dataclass built from a settings mapping,
with every field clamped at construction. An invalid research config disables
research rather than preventing the API from starting, because research is an
enhancement to search and not a dependency of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from jiro.research.errors import ResearchValidationError
from jiro.research.models import (
    DEFAULT_MAX_DURATION_SECONDS,
    DEFAULT_MAX_EXTRACTIONS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_QUERIES,
    DEFAULT_MAX_REPORT_CHARS,
    DEFAULT_MAX_SOURCES,
    HARD_MAX_DURATION_SECONDS,
    HARD_MAX_EXTRACTIONS,
    HARD_MAX_ITERATIONS,
    HARD_MAX_QUERIES,
    HARD_MAX_REPORT_CHARS,
    HARD_MAX_SOURCES,
)

DEFAULT_RESEARCH_CONFIG: Dict[str, Any] = {
    "enabled": True,
    # Per-run budget defaults. Callers may request less; these are the ceiling
    # the engine itself will plan against.
    "max_queries": DEFAULT_MAX_QUERIES,
    "max_sources": DEFAULT_MAX_SOURCES,
    "max_extractions": DEFAULT_MAX_EXTRACTIONS,
    "max_iterations": DEFAULT_MAX_ITERATIONS,
    "max_duration_seconds": DEFAULT_MAX_DURATION_SECONDS,
    "max_report_chars": DEFAULT_MAX_REPORT_CHARS,
    # Hard ceilings, applied after the per-run budget so a generous request
    # cannot escape them.
    "hard_max_queries": HARD_MAX_QUERIES,
    "hard_max_sources": HARD_MAX_SOURCES,
    "hard_max_extractions": HARD_MAX_EXTRACTIONS,
    "hard_max_iterations": HARD_MAX_ITERATIONS,
    "hard_max_duration_seconds": HARD_MAX_DURATION_SECONDS,
    "hard_max_report_chars": HARD_MAX_REPORT_CHARS,
    # Extraction bounds.
    "max_page_bytes": 2_000_000,
    "max_passage_chars": 4_000,
    "max_passages_per_source": 6,
    "max_retained_chars_per_source": 40_000,
    "allowed_content_types": (
        "text/html", "text/plain", "application/xhtml+xml", "text/markdown",
    ),
    "fetch_timeout_seconds": 20.0,
    "search_timeout_seconds": 30.0,
    "max_concurrent_fetches": 4,
    # Synthesis context. Never send a whole page to a model.
    "synthesis_context_chars": 24_000,
    "min_evidence_per_claim": 1,
    # High-risk runs demand corroboration from distinct domains before the
    # engine will report itself complete.
    "high_risk_min_domains": 2,
    "auto_link_memory": True,
    "outbox_enabled": True,
}


def _as_int(raw: Any, default: int, low: int, high: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ResearchValidationError(
            f"expected an integer, got {raw!r}") from exc
    return max(low, min(value, high))


@dataclass(frozen=True)
class ResearchConfig:
    """Effective research engine configuration."""

    enabled: bool = True
    max_queries: int = DEFAULT_MAX_QUERIES
    max_sources: int = DEFAULT_MAX_SOURCES
    max_extractions: int = DEFAULT_MAX_EXTRACTIONS
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_duration_seconds: float = DEFAULT_MAX_DURATION_SECONDS
    max_report_chars: int = DEFAULT_MAX_REPORT_CHARS
    hard_max_queries: int = HARD_MAX_QUERIES
    hard_max_sources: int = HARD_MAX_SOURCES
    hard_max_extractions: int = HARD_MAX_EXTRACTIONS
    hard_max_iterations: int = HARD_MAX_ITERATIONS
    hard_max_duration_seconds: float = float(HARD_MAX_DURATION_SECONDS)
    hard_max_report_chars: int = HARD_MAX_REPORT_CHARS
    max_page_bytes: int = 2_000_000
    max_passage_chars: int = 4_000
    max_passages_per_source: int = 6
    max_retained_chars_per_source: int = 40_000
    allowed_content_types: tuple = (
        "text/html", "text/plain", "application/xhtml+xml", "text/markdown",
    )
    fetch_timeout_seconds: float = 20.0
    search_timeout_seconds: float = 30.0
    max_concurrent_fetches: int = 4
    synthesis_context_chars: int = 24_000
    min_evidence_per_claim: int = 1
    high_risk_min_domains: int = 2
    auto_link_memory: bool = True
    outbox_enabled: bool = True

    @classmethod
    def from_settings(cls, settings: Any) -> "ResearchConfig":
        """Build from project settings, raising on an invalid config.

        The caller (``build_research_service``) catches the error and disables
        research, so a bad value never stops the API from serving search.
        """
        raw: Mapping[str, Any]
        if settings is None:
            raw = {}
        elif hasattr(settings, "raw") and isinstance(settings.raw, dict):
            raw = settings.raw.get("research") or {}
        elif isinstance(settings, dict):
            raw = settings.get("research") or {}
        else:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ResearchValidationError(
                "research config must be a mapping",
                details={"got": type(raw).__name__})
        merged = {**DEFAULT_RESEARCH_CONFIG, **dict(raw)}
        return cls(
            enabled=bool(merged["enabled"]),
            max_queries=_as_int(merged["max_queries"], DEFAULT_MAX_QUERIES,
                                1, HARD_MAX_QUERIES),
            max_sources=_as_int(merged["max_sources"], DEFAULT_MAX_SOURCES,
                                1, HARD_MAX_SOURCES),
            max_extractions=_as_int(merged["max_extractions"],
                                    DEFAULT_MAX_EXTRACTIONS, 1,
                                    HARD_MAX_EXTRACTIONS),
            max_iterations=_as_int(merged["max_iterations"],
                                   DEFAULT_MAX_ITERATIONS, 1,
                                   HARD_MAX_ITERATIONS),
            max_duration_seconds=float(_as_int(
                merged["max_duration_seconds"],
                int(DEFAULT_MAX_DURATION_SECONDS), 10,
                int(HARD_MAX_DURATION_SECONDS))),
            max_report_chars=_as_int(merged["max_report_chars"],
                                     DEFAULT_MAX_REPORT_CHARS, 1000,
                                     HARD_MAX_REPORT_CHARS),
            hard_max_queries=HARD_MAX_QUERIES,
            hard_max_sources=HARD_MAX_SOURCES,
            hard_max_extractions=HARD_MAX_EXTRACTIONS,
            hard_max_iterations=HARD_MAX_ITERATIONS,
            hard_max_duration_seconds=float(HARD_MAX_DURATION_SECONDS),
            hard_max_report_chars=HARD_MAX_REPORT_CHARS,
            max_page_bytes=_as_int(merged["max_page_bytes"], 2_000_000,
                                   10_000, 20_000_000),
            max_passage_chars=_as_int(merged["max_passage_chars"], 4_000,
                                      200, 20_000),
            max_passages_per_source=_as_int(merged["max_passages_per_source"],
                                            6, 1, 50),
            max_retained_chars_per_source=_as_int(
                merged["max_retained_chars_per_source"], 40_000, 1000,
                500_000),
            allowed_content_types=tuple(
                str(t).strip().lower()
                for t in (merged["allowed_content_types"] or ())
                if str(t).strip()),
            fetch_timeout_seconds=float(_as_int(
                merged["fetch_timeout_seconds"], 20, 1, 300)),
            search_timeout_seconds=float(_as_int(
                merged["search_timeout_seconds"], 30, 1, 300)),
            max_concurrent_fetches=_as_int(
                merged["max_concurrent_fetches"], 4, 1, 16),
            synthesis_context_chars=_as_int(
                merged["synthesis_context_chars"], 24_000, 1000, 200_000),
            min_evidence_per_claim=_as_int(
                merged["min_evidence_per_claim"], 1, 1, 10),
            high_risk_min_domains=_as_int(merged["high_risk_min_domains"], 2,
                                          1, 10),
            auto_link_memory=bool(merged["auto_link_memory"]),
            outbox_enabled=bool(merged["outbox_enabled"]),
        )

    def clamp_budget(self, budget: Any) -> Any:
        """Apply this config's hard ceilings to a requested budget."""
        from jiro.research.models import ResearchBudget

        if budget is None:
            budget = ResearchBudget()
        return ResearchBudget(
            max_queries=min(budget.max_queries, self.hard_max_queries),
            max_sources=min(budget.max_sources, self.hard_max_sources),
            max_extractions=min(budget.max_extractions,
                                self.hard_max_extractions),
            max_iterations=min(budget.max_iterations, self.hard_max_iterations),
            max_duration_seconds=min(budget.max_duration_seconds,
                                     self.hard_max_duration_seconds),
            max_report_chars=min(budget.max_report_chars,
                                 self.hard_max_report_chars),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_queries": self.max_queries,
            "max_sources": self.max_sources,
            "max_extractions": self.max_extractions,
            "max_iterations": self.max_iterations,
            "max_duration_seconds": self.max_duration_seconds,
            "max_report_chars": self.max_report_chars,
            "max_page_bytes": self.max_page_bytes,
            "max_passage_chars": self.max_passage_chars,
            "max_passages_per_source": self.max_passages_per_source,
            "max_retained_chars_per_source": self.max_retained_chars_per_source,
            "fetch_timeout_seconds": self.fetch_timeout_seconds,
            "search_timeout_seconds": self.search_timeout_seconds,
            "max_concurrent_fetches": self.max_concurrent_fetches,
            "synthesis_context_chars": self.synthesis_context_chars,
            "min_evidence_per_claim": self.min_evidence_per_claim,
            "high_risk_min_domains": self.high_risk_min_domains,
            "auto_link_memory": self.auto_link_memory,
        }


def research_config(**overrides: Any) -> ResearchConfig:
    """Build a config from the defaults with test-friendly overrides."""
    merged = {**DEFAULT_RESEARCH_CONFIG, **overrides}
    raw = ResearchConfig.from_settings({"research": merged})
    return raw


__all__ = ["DEFAULT_RESEARCH_CONFIG", "ResearchConfig", "research_config"]