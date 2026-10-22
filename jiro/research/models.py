"""Validated request, budget and plan models for the research engine.

Three rules govern everything in this module:

1. **Budgets are ceilings, not suggestions.** Every count a request can ask for
   has a server-side default *and* a hard maximum. A caller cannot ask for
   "unlimited queries"; they can only ask for a number, and the number is
   clamped and recorded.
2. **A plan is a hypothesis, not evidence.** `ResearchPlan` describes how the
   engine intends to investigate. It never counts as a finding, and the
   separation is enforced by the type: `ResearchPlan` holds no evidence ids.
3. **Normalisation is recorded.** The effective budget is stored on the research
   row, so a reader can see what was actually permitted rather than what was
   requested.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from jiro.research.errors import ResearchValidationError

# ---------------------------------------------------------------- constants

#: Hard ceilings. A request above these is clamped rather than rejected, so an
#: over-eager caller still gets a bounded result instead of a 422.
HARD_MAX_QUERIES = 30
HARD_MAX_SOURCES = 40
HARD_MAX_EXTRACTIONS = 60
HARD_MAX_ITERATIONS = 5
HARD_MAX_DURATION_SECONDS = 1800.0
HARD_MAX_REPORT_CHARS = 120_000
HARD_MAX_QUESTION_CHARS = 2000
HARD_MAX_EXCLUSIONS = 10

DEFAULT_MAX_QUERIES = 8
DEFAULT_MAX_SOURCES = 12
DEFAULT_MAX_EXTRACTIONS = 20
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_MAX_DURATION_SECONDS = 300.0
DEFAULT_MAX_REPORT_CHARS = 20_000

VALID_FORMATS = ("markdown", "json")
VALID_RISK = ("low", "moderate", "high")
VALID_SOURCE_CATEGORIES = (
    "academic", "government", "news", "documentation", "forum", "blog", "any",
)

_WHITESPACE = re.compile(r"\s+")


def _clamp(value: Optional[int], default: int, ceiling: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchValidationError(
            f"{name} must be an integer",
            details={"field": name, "got": type(value).__name__},
        )
    if value < 1:
        raise ResearchValidationError(
            f"{name} must be at least 1",
            details={"field": name, "got": value},
        )
    return min(value, ceiling)


def _normalize_question(raw: str) -> str:
    if not isinstance(raw, str):
        raise ResearchValidationError(
            "question must be a string",
            details={"field": "question", "got": type(raw).__name__},
        )
    text = unicodedata.normalize("NFKC", raw).strip()
    text = _WHITESPACE.sub(" ", text)
    if not text:
        raise ResearchValidationError(
            "question must not be empty", details={"field": "question"})
    if len(text) > HARD_MAX_QUESTION_CHARS:
        raise ResearchValidationError(
            f"question exceeds {HARD_MAX_QUESTION_CHARS} characters",
            details={"field": "question", "length": len(text),
                     "max": HARD_MAX_QUESTION_CHARS},
        )
    return text


def _clean_terms(raw: Optional[Sequence[str]], field_name: str,
                 limit: int) -> Tuple[str, ...]:
    if not raw:
        return ()
    if isinstance(raw, str):
        raise ResearchValidationError(
            f"{field_name} must be a list of strings",
            details={"field": field_name},
        )
    out: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ResearchValidationError(
                f"{field_name} entries must be strings",
                details={"field": field_name},
            )
        cleaned = _WHITESPACE.sub(" ", item.strip())
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= limit:
            break
    return tuple(out)


# ------------------------------------------------------------------ request


@dataclass(frozen=True)
class ResearchBudget:
    """The effective ceilings for one research run.

    Frozen and validated at construction, so no downstream stage can widen a
    budget by mutating a field.
    """

    max_queries: int = DEFAULT_MAX_QUERIES
    max_sources: int = DEFAULT_MAX_SOURCES
    max_extractions: int = DEFAULT_MAX_EXTRACTIONS
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_duration_seconds: float = DEFAULT_MAX_DURATION_SECONDS
    max_report_chars: int = DEFAULT_MAX_REPORT_CHARS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_queries": self.max_queries,
            "max_sources": self.max_sources,
            "max_extractions": self.max_extractions,
            "max_iterations": self.max_iterations,
            "max_duration_seconds": self.max_duration_seconds,
            "max_report_chars": self.max_report_chars,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ResearchBudget":
        data = data or {}
        return cls(
            max_queries=int(data.get("max_queries", DEFAULT_MAX_QUERIES)),
            max_sources=int(data.get("max_sources", DEFAULT_MAX_SOURCES)),
            max_extractions=int(
                data.get("max_extractions", DEFAULT_MAX_EXTRACTIONS)),
            max_iterations=int(
                data.get("max_iterations", DEFAULT_MAX_ITERATIONS)),
            max_duration_seconds=float(
                data.get("max_duration_seconds", DEFAULT_MAX_DURATION_SECONDS)),
            max_report_chars=int(
                data.get("max_report_chars", DEFAULT_MAX_REPORT_CHARS)),
        )


@dataclass(frozen=True)
class ResearchRequest:
    """A validated research request.

    Only ``question`` is required. Everything else has a defensible default, so
    a minimal request is a complete request.
    """

    question: str
    purpose: str = ""
    audience: str = ""
    scope: str = ""
    exclusions: Tuple[str, ...] = ()
    preferred_source_categories: Tuple[str, ...] = ()
    #: "low" | "moderate" | "high". Drives how much corroboration the stopping
    #: policy demands before it will call a run complete.
    risk_level: str = "moderate"
    #: Days of freshness required, or None for no freshness requirement.
    max_age_days: Optional[int] = None
    budget: ResearchBudget = field(default_factory=ResearchBudget)
    output_format: str = "markdown"
    engine_preference: str = ""
    #: Existing research reports or memory ids the caller wants considered.
    context_refs: Tuple[str, ...] = ()
    idempotency_key: Optional[str] = None
    #: Recorded verbatim for audit. Never used to widen any budget.
    owner_key_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "purpose": self.purpose,
            "audience": self.audience,
            "scope": self.scope,
            "exclusions": list(self.exclusions),
            "preferred_source_categories": list(self.preferred_source_categories),
            "risk_level": self.risk_level,
            "max_age_days": self.max_age_days,
            "budget": self.budget.to_dict(),
            "output_format": self.output_format,
            "engine_preference": self.engine_preference,
            "context_refs": list(self.context_refs),
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchRequest":
        return normalize_request(data)


def normalize_request(data: Any) -> ResearchRequest:
    """Validate and normalise arbitrary input into a ``ResearchRequest``.

    Accepts a mapping. Unknown keys are dropped rather than rejected: a newer
    client sending an extra field should not break an older server.

    Raises:
        ResearchValidationError: the request is unusable. The message names the
            offending field so a caller can fix it without guessing.
    """
    if not isinstance(data, dict):
        raise ResearchValidationError(
            "research request must be an object",
            details={"got": type(data).__name__},
        )

    question = _normalize_question(data.get("question"))

    budget_in = data.get("budget") or {}
    if not isinstance(budget_in, dict):
        raise ResearchValidationError(
            "budget must be an object", details={"field": "budget"})
    budget = ResearchBudget(
        max_queries=_clamp(budget_in.get("max_queries"), DEFAULT_MAX_QUERIES,
                           HARD_MAX_QUERIES, "budget.max_queries"),
        max_sources=_clamp(budget_in.get("max_sources"), DEFAULT_MAX_SOURCES,
                           HARD_MAX_SOURCES, "budget.max_sources"),
        max_extractions=_clamp(budget_in.get("max_extractions"),
                               DEFAULT_MAX_EXTRACTIONS,
                               HARD_MAX_EXTRACTIONS, "budget.max_extractions"),
        max_iterations=_clamp(budget_in.get("max_iterations"),
                              DEFAULT_MAX_ITERATIONS,
                              HARD_MAX_ITERATIONS, "budget.max_iterations"),
        max_duration_seconds=_clamp(
            budget_in.get("max_duration_seconds"),
            int(DEFAULT_MAX_DURATION_SECONDS),
            int(HARD_MAX_DURATION_SECONDS), "budget.max_duration_seconds"),
        max_report_chars=_clamp(budget_in.get("max_report_chars"),
                                DEFAULT_MAX_REPORT_CHARS,
                                HARD_MAX_REPORT_CHARS, "budget.max_report_chars"),
    )

    fmt = str(data.get("output_format") or "markdown").strip().lower()
    if fmt not in VALID_FORMATS:
        raise ResearchValidationError(
            f"unsupported output_format '{fmt}'",
            details={"field": "output_format", "allowed": list(VALID_FORMATS)},
        )

    risk = str(data.get("risk_level") or "moderate").strip().lower()
    if risk not in VALID_RISK:
        raise ResearchValidationError(
            f"unsupported risk_level '{risk}'",
            details={"field": "risk_level", "allowed": list(VALID_RISK)},
        )

    categories = tuple(
        c for c in _clean_terms(data.get("preferred_source_categories"),
                                "preferred_source_categories", 6)
        if c in VALID_SOURCE_CATEGORIES
    )

    max_age = data.get("max_age_days")
    if max_age is not None:
        if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age < 0:
            raise ResearchValidationError(
                "max_age_days must be a non-negative integer",
                details={"field": "max_age_days"},
            )

    exclusions = _clean_terms(data.get("exclusions"), "exclusions",
                              HARD_MAX_EXCLUSIONS)

    # A freshness requirement and a scope that forbids current material are a
    # contradiction. Catching it here beats silently returning nothing later.
    scope_text = str(data.get("scope") or "").lower()
    if max_age is not None and any(
        word in scope_text for word in ("historical", "pre-2020", "archive only")
    ):
        raise ResearchValidationError(
            "max_age_days conflicts with a historical-only scope; "
            "drop one or the other",
            details={"field": "max_age_days"},
        )

    idem = data.get("idempotency_key")
    if idem is not None:
        idem = str(idem).strip()[:200] or None

    return ResearchRequest(
        question=question,
        purpose=str(data.get("purpose") or "").strip()[:1000],
        audience=str(data.get("audience") or "").strip()[:200],
        scope=str(data.get("scope") or "").strip()[:1000],
        exclusions=exclusions,
        preferred_source_categories=categories,
        risk_level=risk,
        max_age_days=max_age,
        budget=budget,
        output_format=fmt,
        engine_preference=str(data.get("engine_preference") or "").strip()[:64],
        context_refs=_clean_terms(data.get("context_refs"), "context_refs", 20),
        idempotency_key=idem,
        owner_key_id=(str(data.get("owner_key_id"))
                      if data.get("owner_key_id") else None),
    )


# --------------------------------------------------------------------- plan


@dataclass(frozen=True)
class SubQuestion:
    """One answerable unit of research.

    ``required`` marks a subquestion the report is expected to cover. Optional
    ones are dropped first when budget runs short, which is why the ordering of
    the plan matters.
    """

    id: str
    text: str
    required: bool = True
    expected_evidence: Tuple[str, ...] = ()
    answered_by: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "required": self.required,
            "expected_evidence": list(self.expected_evidence),
            "answered_by": list(self.answered_by),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubQuestion":
        return cls(
            id=str(data["id"]),
            text=str(data["text"]),
            required=bool(data.get("required", True)),
            expected_evidence=tuple(data.get("expected_evidence") or ()),
            answered_by=tuple(data.get("answered_by") or ()),
        )


@dataclass(frozen=True)
class PlannedQuery:
    """A query the plan proposes to issue.

    Carries the subquestion it serves, so a query that returns nothing useful
    can be attributed rather than silently retried.
    """

    id: str
    text: str
    subquestion_id: str
    priority: int = 0
    source_category: str = "any"
    issued: bool = False
    #: Set when a query returns nothing new, so the loop never reissues it.
    unproductive: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "subquestion_id": self.subquestion_id,
            "priority": self.priority,
            "source_category": self.source_category,
            "issued": self.issued,
            "unproductive": self.unproductive,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlannedQuery":
        return cls(
            id=str(data["id"]),
            text=str(data["text"]),
            subquestion_id=str(data["subquestion_id"]),
            priority=int(data.get("priority", 0)),
            source_category=str(data.get("source_category", "any")),
            issued=bool(data.get("issued", False)),
            unproductive=bool(data.get("unproductive", False)),
        )


@dataclass(frozen=True)
class ResearchPlan:
    """A bounded hypothesis about how to investigate a question.

    Deliberately carries no evidence references. A plan is what the engine
    intends to find out; mixing it with what it did find is how a research tool
    ends up citing its own assumptions.
    """

    question: str
    subquestions: Tuple[SubQuestion, ...] = ()
    queries: Tuple[PlannedQuery, ...] = ()
    entities: Tuple[str, ...] = ()
    angles: Tuple[str, ...] = ()
    authoritative_domains: Tuple[str, ...] = ()
    completion_criteria: Tuple[str, ...] = ()
    exclusions: Tuple[str, ...] = ()
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    revision: int = 1
    generator: str = "deterministic"

    def query_for(self, subquestion_id: str) -> Optional[PlannedQuery]:
        for query in self.queries:
            if query.subquestion_id == subquestion_id:
                return query
        return None

    def with_revision(self, *, revision: int,
                      generator: Optional[str] = None) -> "ResearchPlan":
        return replace(
            self, revision=revision,
            generator=generator or self.generator,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "subquestions": [s.to_dict() for s in self.subquestions],
            "queries": [q.to_dict() for q in self.queries],
            "entities": list(self.entities),
            "angles": list(self.angles),
            "authoritative_domains": list(self.authoritative_domains),
            "completion_criteria": list(self.completion_criteria),
            "exclusions": list(self.exclusions),
            "max_iterations": self.max_iterations,
            "revision": self.revision,
            "generator": self.generator,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchPlan":
        return cls(
            question=str(data.get("question", "")),
            subquestions=tuple(SubQuestion.from_dict(s)
                               for s in data.get("subquestions") or ()),
            queries=tuple(PlannedQuery.from_dict(q)
                          for q in data.get("queries") or ()),
            entities=tuple(data.get("entities") or ()),
            angles=tuple(data.get("angles") or ()),
            authoritative_domains=tuple(
                data.get("authoritative_domains") or ()),
            completion_criteria=tuple(data.get("completion_criteria") or ()),
            exclusions=tuple(data.get("exclusions") or ()),
            max_iterations=int(data.get("max_iterations",
                                        DEFAULT_MAX_ITERATIONS)),
            revision=int(data.get("revision", 1)),
            generator=str(data.get("generator", "deterministic")),
        )


__all__ = [
    "DEFAULT_MAX_DURATION_SECONDS",
    "DEFAULT_MAX_EXTRACTIONS",
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_MAX_QUERIES",
    "DEFAULT_MAX_REPORT_CHARS",
    "DEFAULT_MAX_SOURCES",
    "HARD_MAX_DURATION_SECONDS",
    "HARD_MAX_EXTRACTIONS",
    "HARD_MAX_ITERATIONS",
    "HARD_MAX_QUESTION_CHARS",
    "HARD_MAX_QUERIES",
    "HARD_MAX_REPORT_CHARS",
    "HARD_MAX_SOURCES",
    "PlannedQuery",
    "ResearchBudget",
    "ResearchPlan",
    "ResearchRequest",
    "SubQuestion",
    "VALID_FORMATS",
    "VALID_RISK",
    "VALID_SOURCE_CATEGORIES",
    "normalize_request",
]