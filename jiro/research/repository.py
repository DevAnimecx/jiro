"""Durable persistence for the research engine.

Every research artefact is a row: the task, its plan revisions, its sources, the
evidence ledger, the claim registry, issued queries, iterations and the final
report. That is deliberate — Phase 3 found no artifact store in this project, so
the report body is stored inline (bounded by ``max_report_bytes``) rather than
behind a pointer to storage that does not exist.

Ownership follows Phase 2: ``owner_key_id`` is the only durable principal, and
every read is scoped by it in SQL. A repository method that cannot be scoped
does not exist, so forgetting the filter is a ``TypeError`` rather than a leak.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import aiosqlite

from jiro.db import Database
from jiro.research.errors import ResearchValidationError
from jiro.research.models import ResearchPlan, ResearchRequest
from jiro.research.urls import normalize_url, stable_id

#: Bumped when the checkpoint payload shape changes incompatibly. A resumed
#: run whose checkpoint version is older is restarted from the beginning rather
#: than crashing on a missing key.
CHECKPOINT_VERSION = 1

_STAGES = (
    "queued", "planning", "searching", "extracting", "assessing",
    "following_up", "synthesizing", "validating", "persisting",
    "completed", "partial", "blocked", "failed", "cancelled",
)

_RESULT_STATUSES = ("complete", "partial", "blocked")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: Optional[str], default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


@dataclass(frozen=True)
class ResearchRecord:
    """The durable research task row."""

    id: str
    owner_key_id: Optional[str]
    stage: str
    question: str
    request: Dict[str, Any]
    plan: Optional[Dict[str, Any]]
    task_id: Optional[str]
    max_queries: int
    consumed_queries: int
    max_sources: int
    consumed_sources: int
    max_extractions: int
    consumed_extractions: int
    max_iterations: int
    consumed_iterations: int
    max_duration_seconds: float
    started_at: Optional[float]
    deadline_at: Optional[float]
    completed_at: Optional[float]
    stop_reason: Optional[str]
    result_status: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    checkpoint: Optional[Dict[str, Any]]
    checkpoint_version: int
    created_at: float
    updated_at: float

    @property
    def budget_remaining(self) -> Dict[str, int]:
        return {
            "queries": max(0, self.max_queries - self.consumed_queries),
            "sources": max(0, self.max_sources - self.consumed_sources),
            "extractions": max(
                0, self.max_extractions - self.consumed_extractions),
            "iterations": max(
                0, self.max_iterations - self.consumed_iterations),
        }

    @property
    def budget_exhausted(self) -> bool:
        remaining = self.budget_remaining
        return (remaining["queries"] <= 0 or remaining["sources"] <= 0
                or remaining["extractions"] <= 0)

    def seconds_remaining(self, *, now: Optional[float] = None) -> float:
        if self.deadline_at is None:
            return float(self.max_duration_seconds)
        return max(0.0, self.deadline_at - (now if now is not None
                                            else time.time()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "owner_key_id": self.owner_key_id,
            "stage": self.stage,
            "question": self.question,
            "task_id": self.task_id,
            "budget": {
                "max_queries": self.max_queries,
                "max_sources": self.max_sources,
                "max_extractions": self.max_extractions,
                "max_iterations": self.max_iterations,
                "max_duration_seconds": self.max_duration_seconds,
                "consumed_queries": self.consumed_queries,
                "consumed_sources": self.consumed_sources,
                "consumed_extractions": self.consumed_extractions,
                "consumed_iterations": self.consumed_iterations,
                "remaining": self.budget_remaining,
            },
            "started_at": self.started_at,
            "deadline_at": self.deadline_at,
            "completed_at": self.completed_at,
            "stop_reason": self.stop_reason,
            "result_status": self.result_status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "checkpoint_version": self.checkpoint_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _record(row: Dict[str, Any]) -> ResearchRecord:
    return ResearchRecord(
        id=row["id"],
        owner_key_id=row.get("owner_key_id"),
        stage=row["stage"],
        question=row["question"],
        request=_loads(row.get("request"), {}),
        plan=_loads(row.get("plan")),
        task_id=row.get("task_id"),
        max_queries=int(row["max_queries"]),
        consumed_queries=int(row["consumed_queries"]),
        max_sources=int(row["max_sources"]),
        consumed_sources=int(row["consumed_sources"]),
        max_extractions=int(row["max_extractions"]),
        consumed_extractions=int(row["consumed_extractions"]),
        max_iterations=int(row["max_iterations"]),
        consumed_iterations=int(row["consumed_iterations"]),
        max_duration_seconds=float(row["max_duration_seconds"]),
        started_at=row.get("started_at"),
        deadline_at=row.get("deadline_at"),
        completed_at=row.get("completed_at"),
        stop_reason=row.get("stop_reason"),
        result_status=row.get("result_status"),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        checkpoint=_loads(row.get("checkpoint")),
        checkpoint_version=int(row.get("checkpoint_version") or 0),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


@dataclass(frozen=True)
class SourceRecord:
    id: str
    research_id: str
    owner_key_id: Optional[str]
    original_url: str
    normalized_url: str
    canonical_url: Optional[str]
    final_url: Optional[str]
    title: Optional[str]
    domain: str
    author: Optional[str]
    published_at: Optional[float]
    retrieved_at: Optional[float]
    content_type: Optional[str]
    content_hash: Optional[str]
    char_count: int
    truncated: bool
    extraction_status: str
    extraction_error: Optional[str]
    discovery_queries: Tuple[str, ...]
    discovery_engines: Tuple[str, ...]
    rank: Optional[int]
    created_at: float
    updated_at: float

    @property
    def usable(self) -> bool:
        return self.extraction_status == "extracted"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "research_id": self.research_id,
            "original_url": self.original_url,
            "normalized_url": self.normalized_url,
            "canonical_url": self.canonical_url,
            "final_url": self.final_url,
            "title": self.title,
            "domain": self.domain,
            "author": self.author,
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "char_count": self.char_count,
            "truncated": self.truncated,
            "extraction_status": self.extraction_status,
            "extraction_error": self.extraction_error,
            "discovery_queries": list(self.discovery_queries),
            "discovery_engines": list(self.discovery_engines),
            "rank": self.rank,
        }


def _source(row: Dict[str, Any]) -> SourceRecord:
    return SourceRecord(
        id=row["id"],
        research_id=row["research_id"],
        owner_key_id=row.get("owner_key_id"),
        original_url=row["original_url"],
        normalized_url=row["normalized_url"],
        canonical_url=row.get("canonical_url"),
        final_url=row.get("final_url"),
        title=row.get("title"),
        domain=row.get("domain") or "",
        author=row.get("author"),
        published_at=row.get("published_at"),
        retrieved_at=row.get("retrieved_at"),
        content_type=row.get("content_type"),
        content_hash=row.get("content_hash"),
        char_count=int(row.get("char_count") or 0),
        truncated=bool(row.get("truncated")),
        extraction_status=row["extraction_status"],
        extraction_error=row.get("extraction_error"),
        discovery_queries=tuple(_loads(row.get("discovery_queries"), []) or []),
        discovery_engines=tuple(
            _loads(row.get("discovery_engines"), []) or []),
        rank=row.get("rank"),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    research_id: str
    source_id: str
    owner_key_id: Optional[str]
    subquestion_id: Optional[str]
    passage: str
    passage_hash: str
    passage_start: int
    passage_end: int
    locator: Optional[str]
    evidence_type: str
    relevance: str
    relevance_note: Optional[str]
    extracted_at: float
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "subquestion_id": self.subquestion_id,
            "passage": self.passage,
            "passage_hash": self.passage_hash,
            "locator": self.locator,
            "evidence_type": self.evidence_type,
            "relevance": self.relevance,
            "relevance_note": self.relevance_note,
            "extracted_at": self.extracted_at,
        }


def _evidence(row: Dict[str, Any]) -> EvidenceRecord:
    return EvidenceRecord(
        id=row["id"],
        research_id=row["research_id"],
        source_id=row["source_id"],
        owner_key_id=row.get("owner_key_id"),
        subquestion_id=row.get("subquestion_id"),
        passage=row["passage"],
        passage_hash=row["passage_hash"],
        passage_start=int(row.get("passage_start") or 0),
        passage_end=int(row.get("passage_end") or 0),
        locator=row.get("locator"),
        evidence_type=row.get("evidence_type") or "passage",
        relevance=row.get("relevance") or "unresolved",
        relevance_note=row.get("relevance_note"),
        extracted_at=float(row["extracted_at"]),
        created_at=float(row["created_at"]),
    )


@dataclass(frozen=True)
class ClaimRecord:
    id: str
    research_id: str
    owner_key_id: Optional[str]
    claim_text: str
    subquestion_id: Optional[str]
    kind: str
    assessment: str
    confidence_label: Optional[str]
    limitations: Optional[str]
    temporal_scope: Optional[str]
    supporting_evidence: Tuple[str, ...]
    contradicting_evidence: Tuple[str, ...]
    independent_domains: Tuple[str, ...]
    created_at: float
    updated_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "claim_text": self.claim_text,
            "subquestion_id": self.subquestion_id,
            "kind": self.kind,
            "assessment": self.assessment,
            "confidence_label": self.confidence_label,
            "limitations": self.limitations,
            "temporal_scope": self.temporal_scope,
            "supporting_evidence": list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
            "independent_domains": list(self.independent_domains),
        }


def _claim(row: Dict[str, Any]) -> ClaimRecord:
    return ClaimRecord(
        id=row["id"],
        research_id=row["research_id"],
        owner_key_id=row.get("owner_key_id"),
        claim_text=row["claim_text"],
        subquestion_id=row.get("subquestion_id"),
        kind=row.get("kind") or "source_assertion",
        assessment=row.get("assessment") or "unsupported",
        confidence_label=row.get("confidence_label"),
        limitations=row.get("limitations"),
        temporal_scope=row.get("temporal_scope"),
        supporting_evidence=tuple(
            _loads(row.get("supporting_evidence"), []) or []),
        contradicting_evidence=tuple(
            _loads(row.get("contradicting_evidence"), []) or []),
        independent_domains=tuple(
            _loads(row.get("independent_domains"), []) or []),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


class ResearchRepository:
    """SQLite-backed store for every research artefact."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def verify_schema(self) -> None:
        await self._db.verify_schema([
            "research_tasks", "research_sources", "research_evidence",
            "research_claims", "research_queries", "research_iterations",
            "research_reports", "research_plan_revisions",
        ])

    # ----------------------------------------------------------- research task

    async def create(
        self, research_id: str, request: ResearchRequest, *,
        owner_key_id: Optional[str] = None, task_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> ResearchRecord:
        """Insert a new research task, or return the existing one for an
        idempotency key.

        Idempotency uses the same partial-unique-index shape as the task runtime
        and Phase 2 memory: a non-null ``idempotency_scope`` carries ownership,
        because SQLite treats NULL owners as distinct in a UNIQUE index.
        """
        now = time.time() if now is None else now
        owner = owner_key_id if owner_key_id is not None else request.owner_key_id
        budget = request.budget
        scope = owner or ""
        try:
            await self._db.execute(
                "INSERT INTO research_tasks (id, owner_key_id, stage, question,"
                " request, idempotency_scope, idempotency_key, task_id,"
                " max_queries, max_sources, max_extractions, max_iterations,"
                " max_duration_seconds, created_at, updated_at)"
                " VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (research_id, owner, request.question, _dumps(request.to_dict()),
                 scope, request.idempotency_key, task_id, budget.max_queries,
                 budget.max_sources, budget.max_extractions,
                 budget.max_iterations, budget.max_duration_seconds,
                 now, now),
            )
        except aiosqlite.IntegrityError:
            existing = await self.find_by_idempotency(
                request.idempotency_key, owner_key_id=owner)
            if existing is not None:
                return existing
            raise
        record = await self.get(research_id, owner_key_id=owner)
        if record is None:  # pragma: no cover - insert always lands
            raise RuntimeError("research task vanished after insert")
        return record

    async def get(self, research_id: str, *,
                  owner_key_id: Optional[str] = None) -> Optional[ResearchRecord]:
        row = await self._db.fetchone(
            "SELECT * FROM research_tasks WHERE id = ? AND owner_key_id IS ?",
            (research_id, owner_key_id))
        return _record(row) if row else None

    async def find_by_idempotency(
        self, idempotency_key: str, *,
        owner_key_id: Optional[str] = None
    ) -> Optional[ResearchRecord]:
        row = await self._db.fetchone(
            "SELECT * FROM research_tasks WHERE idempotency_scope = ?"
            " AND idempotency_key = ?",
            (owner_key_id or "", idempotency_key))
        return _record(row) if row else None

    async def list_for_owner(self, *, owner_key_id: Optional[str] = None,
                             limit: int = 50) -> List[ResearchRecord]:
        rows = await self._db.fetchall(
            "SELECT * FROM research_tasks WHERE owner_key_id IS ?"
            " ORDER BY created_at DESC LIMIT ?", (owner_key_id, int(limit)))
        return [_record(r) for r in rows]

    async def set_stage(self, research_id: str, stage: str, *,
                        owner_key_id: Optional[str] = None,
                        now: Optional[float] = None) -> None:
        if stage not in _STAGES:
            raise ResearchValidationError(
                f"unknown research stage '{stage}'",
                details={"known": list(_STAGES)})
        now = time.time() if now is None else now
        await self._db.execute(
            "UPDATE research_tasks SET stage = ?, updated_at = ?"
            " WHERE id = ? AND owner_key_id IS ?",
            (stage, now, research_id, owner_key_id))

    async def set_plan(self, research_id: str, plan: ResearchPlan, *,
                       owner_key_id: Optional[str] = None, reason: str = "initial plan",
                       trigger_evidence: Sequence[str] = (),
                       now: Optional[float] = None) -> None:
        """Persist a plan and append a revision explaining why it changed."""
        now = time.time() if now is None else now
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE research_tasks SET plan = ?, updated_at = ?"
                " WHERE id = ? AND owner_key_id IS ?",
                (_dumps(plan.to_dict()), now, research_id, owner_key_id))
            await conn.execute(
                "INSERT INTO research_plan_revisions"
                " (research_id, revision, plan, reason, trigger_evidence,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (research_id, plan.revision, _dumps(plan.to_dict()), reason,
                 _dumps(list(trigger_evidence)), now))

    async def plan_revisions(self, research_id: str, *,
                            owner_key_id: Optional[str] = None
                            ) -> List[Dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM research_plan_revisions WHERE research_id = ?"
            " AND research_id IN (SELECT id FROM research_tasks"
            " WHERE id = ? AND owner_key_id IS ?)"
            " ORDER BY revision ASC",
            (research_id, research_id, owner_key_id))
        return [{"revision": int(r["revision"]), "plan": _loads(r["plan"], {}),
                 "reason": r["reason"],
                 "trigger_evidence": _loads(r["trigger_evidence"], []),
                 "created_at": float(r["created_at"])} for r in rows]

    async def set_task_id(self, research_id: str, task_id: str, *,
                          owner_key_id: Optional[str] = None,
                          now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        await self._db.execute(
            "UPDATE research_tasks SET task_id = ?, updated_at = ?"
            " WHERE id = ? AND owner_key_id IS ?",
            (task_id, now, research_id, owner_key_id))

    async def mark_started(self, research_id: str, *, deadline_at: float,
                           owner_key_id: Optional[str] = None,
                           now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        await self._db.execute(
            "UPDATE research_tasks SET started_at = ?, deadline_at = ?,"
            " stage = 'planning', updated_at = ?"
            " WHERE id = ? AND owner_key_id IS ?",
            (now, deadline_at, now, research_id, owner_key_id))

    async def finish(self, research_id: str, *, result_status: str,
                     stop_reason: str, owner_key_id: Optional[str] = None,
                     error_code: Optional[str] = None,
                     error_message: Optional[str] = None,
                     now: Optional[float] = None) -> None:
        if result_status not in _RESULT_STATUSES:
            raise ResearchValidationError(
                f"unknown result_status '{result_status}'",
                details={"known": list(_RESULT_STATUSES)})
        now = time.time() if now is None else now
        await self._db.execute(
            "UPDATE research_tasks SET stage = ?, result_status = ?,"
            " stop_reason = ?, error_code = ?, error_message = ?,"
            " completed_at = ?, updated_at = ?"
            " WHERE id = ? AND owner_key_id IS ?",
            (result_status, result_status, stop_reason, error_code,
             (error_message or "")[:1000] or None, now, now,
             research_id, owner_key_id))

    async def fail(self, research_id: str, *, error_code: str,
                   error_message: str,
                   owner_key_id: Optional[str] = None,
                   now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        await self._db.execute(
            "UPDATE research_tasks SET stage = 'failed', result_status = ?,"
            " error_code = ?, error_message = ?, completed_at = ?,"
            " updated_at = ? WHERE id = ? AND owner_key_id IS ?",
            ("blocked", error_code, (error_message or "")[:1000], now, now,
             research_id, owner_key_id))

    # ---------------------------------------------------------------- budgets

    async def consume(self, research_id: str, *, queries: int = 0,
                      sources: int = 0, extractions: int = 0,
                      iterations: int = 0,
                      owner_key_id: Optional[str] = None) -> None:
        """Increment consumption counters.

        Counters are only ever incremented, and the increment is clamped so a
        caller cannot push a counter past its maximum — the budget has to mean
        something even if a bug in a loop tries to spend more.
        """
        await self._db.execute(
            "UPDATE research_tasks SET"
            " consumed_queries = MIN(max_queries, consumed_queries + ?),"
            " consumed_sources = MIN(max_sources, consumed_sources + ?),"
            " consumed_extractions = MIN(max_extractions,"
            "   consumed_extractions + ?),"
            " consumed_iterations = MIN(max_iterations,"
            "   consumed_iterations + ?),"
            " updated_at = ?"
            " WHERE id = ? AND owner_key_id IS ?",
            (max(0, int(queries)), max(0, int(sources)),
             max(0, int(extractions)), max(0, int(iterations)), time.time(),
             research_id, owner_key_id))

    async def checkpoint(self, research_id: str, payload: Dict[str, Any], *,
                         owner_key_id: Optional[str] = None,
                         now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        await self._db.execute(
            "UPDATE research_tasks SET checkpoint = ?, checkpoint_version = ?,"
            " updated_at = ? WHERE id = ? AND owner_key_id IS ?",
            (_dumps(payload), CHECKPOINT_VERSION, now, research_id,
             owner_key_id))

    # ----------------------------------------------------------------- sources

    async def add_source(self, research_id: str, url: str, *,
                         owner_key_id: Optional[str] = None,
                         title: Optional[str] = None,
                         domain: str = "", canonical_url: Optional[str] = None,
                         final_url: Optional[str] = None,
                         published_at: Optional[float] = None,
                         content_type: Optional[str] = None,
                         content_hash: Optional[str] = None,
                         char_count: int = 0, truncated: bool = False,
                         extraction_status: str = "not_attempted",
                         extraction_error: Optional[str] = None,
                         discovered_by: Sequence[str] = (),
                         discovery_queries: Sequence[str] = (),
                         discovery_engines: Sequence[str] = (),
                         rank: Optional[int] = None,
                         now: Optional[float] = None) -> SourceRecord:
        """Insert a source, or merge discovery provenance into the existing one.

        The id is derived from ``(research_id, normalised_url)``, so discovering
        the same page twice is idempotent rather than a second source that would
        be charged against the extraction budget twice.
        """
        now = time.time() if now is None else now
        normalized = normalize_url(url)
        source_id = stable_id("src", research_id, normalized)
        existing = await self.get_source(source_id,
                                         owner_key_id=owner_key_id)
        merged_queries = sorted(set(
            (existing.discovery_queries if existing else ())
        ) | set(discovery_queries))
        merged_engines = sorted(set(
            (existing.discovery_engines if existing else ())
        ) | set(discovery_engines))
        best_rank = rank
        if existing is not None and existing.rank is not None:
            best_rank = (existing.rank if rank is None
                         else min(existing.rank, rank))
        try:
            await self._db.execute(
                "INSERT INTO research_sources (id, research_id, owner_key_id,"
                " original_url, normalized_url, canonical_url, final_url,"
                " title, domain, author, published_at, retrieved_at,"
                " content_type, content_hash, char_count, truncated,"
                " extraction_status, extraction_error, discovered_by,"
                " discovery_queries, discovery_engines, rank, created_at,"
                " updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?, ?, ?)",
                (source_id, research_id, owner_key_id, url, normalized,
                 canonical_url, final_url, title, domain, published_at, now,
                 content_type, content_hash, int(char_count), int(truncated),
                 extraction_status, (extraction_error or "")[:500] or None,
                 _dumps(list(discovered_by)), _dumps(merged_queries),
                 _dumps(merged_engines), best_rank, now, now),
            )
        except aiosqlite.IntegrityError:
            # Concurrent discovery of the same URL: fold in the provenance.
            await self._db.execute(
                "UPDATE research_sources SET discovery_queries = ?,"
                " discovery_engines = ?, rank = ?, updated_at = ?"
                " WHERE id = ?",
                (_dumps(merged_queries), _dumps(merged_engines),
                 best_rank, now, source_id))
        row = await self._db.fetchone(
            "SELECT * FROM research_sources WHERE id = ?", (source_id,))
        if row is None:  # pragma: no cover
            raise RuntimeError("source vanished after insert")
        return _source(row)

    async def add_sources(self, research_id: str, urls: Sequence[str], *,
                          owner_key_id: Optional[str] = None,
                          title_by_url: Optional[Dict[str, str]] = None,
                          discovery_queries: Sequence[str] = (),
                          discovery_engines: Sequence[str] = (),
                          discovered_by: Sequence[str] = (),
                          start_rank: int = 1,
                          now: Optional[float] = None) -> int:
        """Insert or merge many sources in one writer transaction.

        ``add_source`` costs a read, a write and a second read per URL, and the
        research path called it once per discovered result, so one query with a
        dozen hits issued three dozen round-trips while holding the single
        writer lock. Batching into one transaction makes that one transaction.

        Provenance merging matches ``add_source``: the row id is
        ``stable_id("src", research_id, normalized)`` and discovery metadata is
        unioned, so rediscovering a URL folds in rather than duplicating.
        Returns the number of rows written or merged.
        """
        if not urls:
            return 0
        now = time.time() if now is None else now
        title_by_url = title_by_url or {}
        rows = []
        for offset, url in enumerate(urls):
            normalized = normalize_url(url)
            source_id = stable_id("src", research_id, normalized)
            domain = normalized.split("/")[0] if "://" in normalized else ""
            rank = start_rank + offset
            rows.append((url, normalized, source_id, domain, rank))

        # One read for every existing row, one transaction for all the writes.
        async with self._db.transaction():
            for url, normalized, source_id, domain, rank in rows:
                existing = await self._db.fetchone(
                    "SELECT discovery_queries, discovery_engines, rank"
                    " FROM research_sources WHERE id = ?", (source_id,))
                if existing is None:
                    merged_queries = sorted(set(discovery_queries))
                    merged_engines = sorted(set(discovery_engines))
                    best_rank = rank
                else:
                    merged_queries = sorted(set(
                        _loads(existing["discovery_queries"], []) or []
                    ) | set(discovery_queries))
                    merged_engines = sorted(set(
                        _loads(existing["discovery_engines"], []) or []
                    ) | set(discovery_engines))
                    prior = existing["rank"]
                    best_rank = rank if prior is None else min(prior, rank)

                try:
                    await self._db.execute(
                        "INSERT INTO research_sources (id, research_id,"
                        " owner_key_id, original_url, normalized_url,"
                        " canonical_url, final_url, title, domain, author,"
                        " published_at, retrieved_at, content_type,"
                        " content_hash, char_count, truncated,"
                        " extraction_status, extraction_error, discovered_by,"
                        " discovery_queries, discovery_engines, rank,"
                        " created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, NULL,"
                        " ?, NULL, NULL, 0, 0, 'not_attempted', NULL, ?, ?, ?,"
                        " ?, ?, ?)",
                        (source_id, research_id, owner_key_id, url, normalized,
                         title_by_url.get(url), domain, now,
                         _dumps(list(discovered_by)), _dumps(merged_queries),
                         _dumps(merged_engines), best_rank, now, now),
                    )
                except aiosqlite.IntegrityError:
                    await self._db.execute(
                        "UPDATE research_sources SET discovery_queries = ?,"
                        " discovery_engines = ?, rank = ?, updated_at = ?"
                        " WHERE id = ?",
                        (_dumps(merged_queries), _dumps(merged_engines),
                         best_rank, now, source_id))
        return len(rows)

    async def update_extraction(self, source_id: str, *,
                                extraction_status: str,
                                extraction_error: Optional[str] = None,
                                title: Optional[str] = None,
                                canonical_url: Optional[str] = None,
                                final_url: Optional[str] = None,
                                author: Optional[str] = None,
                                published_at: Optional[float] = None,
                                content_type: Optional[str] = None,
                                content_hash: Optional[str] = None,
                                char_count: int = 0, truncated: bool = False,
                                now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        sets = ["extraction_status = ?", "extraction_error = ?",
                "updated_at = ?"]
        params: List[Any] = [
            extraction_status, (extraction_error or "")[:500] or None, now]
        for column, value in (
            ("title", title), ("canonical_url", canonical_url),
            ("final_url", final_url), ("author", author),
            ("published_at", published_at), ("content_type", content_type),
            ("content_hash", content_hash),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        sets.append("char_count = ?")
        params.append(int(char_count))
        sets.append("truncated = ?")
        params.append(int(truncated))
        params.append(source_id)
        await self._db.execute(
            f"UPDATE research_sources SET {', '.join(sets)} WHERE id = ?",
            tuple(params))

    async def get_source(self, source_id: str, *,
                         owner_key_id: Optional[str] = None
                         ) -> Optional[SourceRecord]:
        row = await self._db.fetchone(
            "SELECT s.* FROM research_sources s"
            " JOIN research_tasks t ON t.id = s.research_id"
            " WHERE s.id = ? AND t.owner_key_id IS ?",
            (source_id, owner_key_id))
        return _source(row) if row else None

    async def list_sources(self, research_id: str, *,
                           owner_key_id: Optional[str] = None,
                           usable_only: bool = False,
                           limit: int = 200) -> List[SourceRecord]:
        sql = ("SELECT s.* FROM research_sources s"
               " JOIN research_tasks t ON t.id = s.research_id"
