"""Jiro v0.4.0 Phase 3: Autonomous Research Engine.

This package implements a self-contained research engine that:

1. **Plans** investigations from a question (deterministic + LLM-refined)
2. **Searches** multiple providers with budget accounting
3. **Extracts** evidence from sources with citation tracking
4. **Assesses** claims with confidence labels and contradiction detection
5. **Loops** with a bounded stopping policy (budget, evidence, iterations)
6. **Synthesises** a cited report persisted to durable storage

All components reuse Phase 1's durable runtime and Phase 2's cognitive memory.
"""

from __future__ import annotations

from jiro.research.config import ResearchConfig, research_config
from jiro.research.errors import (
    CitationError,
    ResearchBudgetExceeded,
    ResearchCancelled,
    ResearchError,
    ResearchNoEvidence,
    ResearchProviderError,
    ResearchValidationError,
    SourceFetchError,
)
from jiro.research.models import (
    DEFAULT_MAX_DURATION_SECONDS,
    DEFAULT_MAX_EXTRACTIONS,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_QUERIES,
    DEFAULT_MAX_REPORT_CHARS,
    DEFAULT_MAX_SOURCES,
    PlannedQuery,
    ResearchBudget,
    ResearchPlan,
    ResearchRequest,
    SubQuestion,
)
from jiro.research.planner import MAX_SUBQUESTIONS
from jiro.research.repository import (
    CHECKPOINT_VERSION,
    ClaimRecord,
    EvidenceRecord,
    ResearchRecord,
    ResearchRepository,
    SourceRecord,
)
from jiro.research.search import ResearchSearchAdapter, SearchOutcome

__all__ = [
    # Config
    "ResearchConfig",
    "research_config",
    # Errors
    "CitationError",
    "ResearchBudgetExceeded",
    "ResearchCancelled",
    "ResearchError",
    "ResearchNoEvidence",
    "ResearchProviderError",
    "ResearchValidationError",
    "SourceFetchError",
    # Models
    "DEFAULT_MAX_DURATION_SECONDS",
    "DEFAULT_MAX_EXTRACTIONS",
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_MAX_QUERIES",
    "DEFAULT_MAX_REPORT_CHARS",
    "DEFAULT_MAX_SOURCES",
    "MAX_SUBQUESTIONS",
    "PlannedQuery",
    "ResearchBudget",
    "ResearchPlan",
    "ResearchRequest",
    "SubQuestion",
    # Repository
    "CHECKPOINT_VERSION",
    "ClaimRecord",
    "EvidenceRecord",
    "ResearchRecord",
    "ResearchRepository",
    "SourceRecord",
    # Search
    "ResearchSearchAdapter",
    "SearchOutcome",
]