"""Explainable ranking for memory retrieval.

Every score is a **weighted sum of independently normalised signals**. Each
signal is clamped to ``[0, 1]``, multiplied by a documented weight, and the
resulting breakdown is returned alongside the score. There are no hidden
multipliers, no per-type bonuses that are not declared as weights, and no
unexplained constants.

Signals
-------
``text``
    Token-overlap relevance between the query and the record's searchable text,
    measured as a Jaccard-style coverage of the query's tokens. Saturated at
    full query coverage so a long document cannot out-score a short precise one.

``recency``
    Exponential decay on age with a configured half-life: ``0.5 ** (age /
    half_life)``. Uniformly 0.5 at exactly one half-life, whatever the scale.

``importance``
    The author-supplied weight on the record, in ``[0, 1]``. Passed through
    unchanged, never rescaled.

``verification``
    A fixed lookup from :data:`~jiro.memory.types.VERIFICATION_WEIGHT`. It orders
    *how a claim was learned*; it is not a truth probability and does not
    combine with ``confidence``.

``scope``
    Exact scope match is 1.0, a wider (global) record read from a narrower
    request is 0.6, anything else 0.0. Exactness matters because a memory from
    another task is usually less appropriate than a shared one.

A record matching **no** signal still scores, and is returned only if the caller
asked for filter-only retrieval. That is deliberate: filtering is a valid reason
to return a row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from jiro.memory.config import MemoryConfig
from jiro.memory.types import (
    VERIFICATION_WEIGHT,
    MemoryRecord,
    MemoryScope,
    ScopeKind,
)
from jiro.memory.validation import tokenise

#: Weight applied to a shared (global) record when a narrower scope is asked.
WIDER_SCOPE_MATCH = 0.6

#: Signals reported in every breakdown, in a fixed order, so a caller can diff
#: two results meaningfully.
SIGNAL_NAMES = ("text", "recency", "importance", "verification", "scope")


def lexical_score(query_text: str, record: MemoryRecord) -> float:
    """Fraction of the query's tokens present in the record.

    Uses title and content together; title matches are counted, because a title
    is a stronger signal than body text and both are already fields.
    """
    query_tokens = tokenise(query_text)
    if not query_tokens:
        return 0.0
    haystack = tokenise(
        " ".join(filter(None, [record.title, record.content_text]))
    )
    if not haystack:
        return 0.0
    present = set(haystack)
    covered = sum(1 for token in query_tokens if token in present)
    return min(1.0, covered / len(query_tokens))


def recency_score(record: MemoryRecord, *, now: float,
                  half_life: float) -> float:
    """Exponential decay on age, measured from the most recent of create/update."""
    if half_life <= 0:
        return 0.0
    reference = max(record.updated_at or record.created_at, 0.0)
    age = max(0.0, now - reference)
    return float(0.5 ** (age / half_life))


def importance_score(record: MemoryRecord) -> float:
    return max(0.0, min(1.0, float(record.importance)))


def verification_score(record: MemoryRecord) -> float:
    return VERIFICATION_WEIGHT.get(record.verification, 0.0)


def scope_score(record: MemoryRecord, requested: Optional[MemoryScope]) -> float:
    """How well the record's scope matches what the caller asked for."""
    if requested is None:
        return 1.0
    if record.scope.is_narrower_than(requested):
        return 1.0
    if requested.kind is not ScopeKind.GLOBAL and \
            record.scope.kind is ScopeKind.GLOBAL and \
            record.scope.owner_key_id == requested.owner_key_id:
        return WIDER_SCOPE_MATCH
    return 0.0


@dataclass
class RankedRecord:
    """A scored record with its full explanation.

    Mutable because the optional vector blend adjusts ``score`` and adds a
    ``vector`` signal in place once candidates have been ranked lexically.
    """

    record: MemoryRecord
    score: float
    breakdown: Dict[str, float] = field(default_factory=dict)
    reason: str = ""

    def top_signals(self, count: int = 2) -> List[str]:
        """Names of the signals that contributed most. For human diagnostics."""
        ordered = sorted(self.breakdown.items(), key=lambda kv: kv[1], reverse=True)
        return [name for name, value in ordered[:count] if value > 0]


class Ranker:
    """Applies the documented weighting to retrieval candidates."""

    def __init__(self, config: MemoryConfig) -> None:
        self._config = config

    def rank(
        self,
        records: Sequence[MemoryRecord],
        *,
        query_text: str,
        requested_scope: Optional[MemoryScope],
        now: float,
        limit: int,
    ) -> List[RankedRecord]:
        """Score, sort and truncate.

        Ties break on ``(score, importance, updated_at, id)`` so ordering is
        total and reproducible — two runs over the same data give the same
        order, which matters when a caller caches or compares results.
        """
        ranked: List[RankedRecord] = []
        weights = self._config.weights.as_dict()

        for record in records:
            components = {
                "text": lexical_score(query_text, record),
                "recency": recency_score(
                    record, now=now,
                    half_life=self._config.recency_half_life_seconds),
                "importance": importance_score(record),
                "verification": verification_score(record),
                "scope": scope_score(record, requested_scope),
            }
            score = sum(components[name] * weights[name] for name in SIGNAL_NAMES)
            ranked.append(RankedRecord(
                record=record,
                score=score,
                breakdown=components,
                reason=self._reason(components, bool(query_text.strip())),
            ))

        ranked.sort(key=lambda r: (
            -r.score,
            -r.record.importance,
            -r.record.updated_at,
            r.record.id,
        ))
        return ranked[:limit]

    @staticmethod
    def _reason(components: Dict[str, float], has_query: bool) -> str:
        """A short, factual explanation of why a record was returned."""
        if has_query and components["text"] > 0:
            return f"text match ({components['text']:.2f})"
        if components["scope"] >= 1.0:
            return "exact scope match"
        if components["recency"] > 0.5:
            return "recent and in scope"
        return "matched filters"

    def explain(self, ranked: RankedRecord) -> Dict[str, Any]:
        """Full explanation for one result, including the weights applied."""
        return {
            "score": round(ranked.score, 6),
            "signals": {k: round(v, 6) for k, v in ranked.breakdown.items()},
            "weights": self._config.weights.as_dict(),
            "reason": ranked.reason,
        }


def summarise_ranking(ranked: Sequence[RankedRecord]) -> Dict[str, Any]:
    """Aggregate view of a result set, for logging and metrics."""
    if not ranked:
        return {"count": 0, "mean_score": 0.0, "top_signals": {}}
    mean = sum(r.score for r in ranked) / len(ranked)
    totals: Dict[str, float] = {name: 0.0 for name in SIGNAL_NAMES}
    for item in ranked:
        for name, value in item.breakdown.items():
            totals[name] = totals.get(name, 0.0) + value
    return {
        "count": len(ranked),
        "mean_score": round(mean, 6),
        "mean_signals": {k: round(v / len(ranked), 6) for k, v in totals.items()},
    }


def context_window(
    ranked: Sequence[RankedRecord],
    *,
    max_memories: int,
    total_budget_chars: int,
    per_memory_budget: int,
) -> List[RankedRecord]:
    """Select records that fit a prompt budget.

    Stops at the first record that does not fit rather than truncating it, so a
    caller never receives half a memory that looks complete. Returns the
    selected prefix in rank order.
    """
    selected: List[RankedRecord] = []
    used = 0
    for item in ranked[:max_memories]:
        size = len(item.record.content_text or item.record.title)
        allowance = min(per_memory_budget, total_budget_chars - used)
        if size > allowance:
            break
        selected.append(item)
        used += size
    return selected


__all__ = [
    "SIGNAL_NAMES",
    "WIDER_SCOPE_MATCH",
    "RankedRecord",
    "Ranker",
    "context_window",
    "importance_score",
    "lexical_score",
    "recency_score",
    "scope_score",
    "summarise_ranking",
    "verification_score",
]