"""Storage interface for the memory system.

The memory service depends on this abstraction, never on SQL. Transport
adapters depend on the service. Only :mod:`jiro.memory.sqlite_repo` knows SQL.

Contracts the implementation must honour:

* **Scope isolation is enforced here, not at the service.** Every read and
  write takes an ``owner_key_id`` and filters on it. A repository that trusted
  the caller to scope its own queries would make the security boundary a
  convention.
* **Atomic writes.** A memory, its type-specific detail rows and its provenance
  commit together or not at all.
* **Idempotent writes.** ``upsert`` on a deduplication key returns the existing
  record rather than creating a second one.
* **Idempotent deletion.** Deleting an already-deleted memory succeeds quietly.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from jiro.memory.types import (
    EmbeddingStatus,
    MemoryFact,
    MemoryProcedure,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Provenance,
)


@dataclass(frozen=True)
class NewMemory:
    """A validated memory awaiting first insertion.

    Produced by the service after type-specific validation; the repository only
    persists it.
    """

    record: MemoryRecord
    fact: Optional[MemoryFact] = None
    procedure: Optional[MemoryProcedure] = None
    provenance: Tuple[Provenance, ...] = ()
    #: Storage index key for a semantic fact's ``(subject, predicate)`` claim.
    #: Deliberately not a field on the public :class:`MemoryFact` domain type:
    #: it is an index concern, not something callers should set or read.
    fact_claim_key: Optional[str] = None

    @property
    def dedup_scope(self) -> str:
        """Non-null ownership token used by the unique dedup index.

        ``owner_key_id`` may be NULL (anonymous/system namespace), and SQLite
        treats NULLs as distinct in a UNIQUE index, so uniqueness would silently
        never apply for those records.
        """
        return self.record.scope.owner_key_id or ""


@dataclass(frozen=True)
class RetrievalQuery:
    """A bounded, scoped retrieval request.

    Attributes:
        owner_key_id: mandatory. The only durable principal (ADR 0008).
        text: optional free-text query. Empty means "filter only".
        types: restrict to these memory types.
        scopes: additional scopes the caller may read, beyond the caller's own.
            A caller never widens their own access; this is for the runtime
            asking "what does this task already know" across global+task.
        tags: all tags must match (AND).
        status: restrict to specific statuses; defaults to active only.
        created_after / created_before: time range on creation.
        subject / predicate: semantic-only filters.
        limit: capped by configuration before use.
        include_superseded: return superseded records too. Off by default —
            a superseded fact is history, not a current belief.
        include_embedding: attach vectors to the returned records.
    """

    owner_key_id: Optional[str]
    text: str = ""
    types: Tuple[MemoryType, ...] = ()
    scopes: Tuple[MemoryScope, ...] = ()
    tags: Tuple[str, ...] = ()
    status: Tuple[MemoryStatus, ...] = ()
    created_after: Optional[float] = None
    created_before: Optional[float] = None
    expires_after: Optional[float] = None
    subject: Optional[str] = None
    predicate: Optional[str] = None
    limit: int = 10
    include_superseded: bool = False
    include_embedding: bool = False


@dataclass(frozen=True)
class RetrievalPage:
    """A bounded page of results plus enough context to trust them."""

    hits: List[Any] = field(default_factory=list)
    total_considered: int = 0
    truncated: bool = False
    lexical_used: bool = True
    vector_used: bool = False
    degraded_reason: Optional[str] = None


class MemoryRepository(abc.ABC):
    """Durable storage operations for memories."""

    # ------------------------------------------------------------------ writes
    @abc.abstractmethod
    async def insert(self, item: NewMemory, *, now: float) -> MemoryRecord:
        """Persist a new memory with its detail rows and provenance atomically.

        Raises:
            DuplicateMemory: a record with the same deduplication key already
                exists for this owner.
        """

    @abc.abstractmethod
    async def upsert(self, item: NewMemory, *, now: float) -> Tuple[MemoryRecord, bool]:
        """Insert, or return the existing record for this deduplication key.

        Returns:
            ``(record, created)``. ``created`` is ``False`` when an existing
            record was returned — the idempotent-ingestion signal callers use to
            distinguish a fresh write from a deduplicated one.

        Note:
            Re-observing an existing claim increments ``observation_count`` and
            leaves ``confidence`` untouched. Repetition is not evidence.
        """

    @abc.abstractmethod
    async def update(
        self, memory_id: str, *, owner_key_id: Optional[str],
        changes: Dict[str, Any], now: float,
    ) -> Optional[MemoryRecord]:
        """Apply a controlled update, bumping ``version``.

        Returns:
            The updated record, or ``None`` if it does not exist or belongs to
            another owner.
        """

    @abc.abstractmethod
    async def set_status(
        self, memory_id: str, *, owner_key_id: Optional[str],
        status: MemoryStatus, now: float, extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[MemoryRecord]:
        """Change lifecycle status, validated against the transition table.

        Returns:
            The updated record, or ``None`` when absent or not permitted. A
            repeated transition to the same status is a no-op, which is what
            makes deletion idempotent.
        """

    @abc.abstractmethod
    async def link_supersede(
        self, *, new_id: str, old_id: str, now: float
    ) -> bool:
        """Record that ``new_id`` replaces ``old_id``, on both rows."""

    # ------------------------------------------------------------------- reads
    @abc.abstractmethod
    async def get(
        self, memory_id: str, *, owner_key_id: Optional[str],
        include_embedding: bool = False,
    ) -> Optional[MemoryRecord]:
        """Fetch one memory scoped to ``owner_key_id``.

        Returns ``None`` for both "absent" and "belongs to someone else", so a
        caller cannot use the response to probe for ids.
        """

    @abc.abstractmethod
    async def search(self, query: RetrievalQuery) -> RetrievalPage:
        """Bounded, scoped retrieval. Candidates first; ranking happens above."""

    @abc.abstractmethod
    async def scope_count(
        self, *, owner_key_id: Optional[str], memory_type: Optional[MemoryType],
        scope: Optional[MemoryScope] = None,
    ) -> int:
        """How many *live* memories one owner holds.

        Deleted and expired rows do not count: capacity and ceilings are about
        what is occupying the store, not what has ever been written. Optional
        ``scope`` narrows to one scope.
        """

    @abc.abstractmethod
    async def list_scope(
        self, *, owner_key_id: Optional[str], memory_type: Optional[MemoryType],
        limit: int, now: float, scope: Optional[MemoryScope] = None,
    ) -> List[MemoryRecord]:
        """Memories in one scope, lowest importance then oldest first.

        Used for working-memory capacity eviction, which needs a deterministic
        ordering rather than a relevance ranking. ``scope`` must be honoured:
        without it, eviction could reach into an unrelated task's context.
        """

    # ----------------------------------------------------------- type specifics
    @abc.abstractmethod
    async def get_fact(self, memory_id: str) -> Optional[MemoryFact]:
        """Structured payload of a semantic memory."""

    @abc.abstractmethod
    async def facts_on_claim(
        self, *, claim_key: str, owner_key_id: Optional[str],
        include_inactive: bool = True,
    ) -> List[Tuple[MemoryFact, MemoryRecord]]:
        """Every fact sharing a ``(subject, predicate)`` claim key.

        The basis of contradiction detection: two facts here that disagree on
        the object are a conflict, not a duplicate.
        """

    @abc.abstractmethod
    async def get_procedure(self, memory_id: str) -> Optional[MemoryProcedure]:
        """Structured payload of a procedural memory."""

    @abc.abstractmethod
    async def update_procedure(
        self, memory_id: str, changes: Dict[str, Any]
    ) -> bool:
        """Persist procedure lifecycle changes: promotion, revision, evaluation."""

    # -------------------------------------------------------------- provenance
    @abc.abstractmethod
    async def provenance_for(self, memory_id: str) -> Tuple[Provenance, ...]:
        """Source references for a memory, oldest first."""

    @abc.abstractmethod
    async def add_provenance(
        self, memory_id: str, items: Sequence[Provenance]
    ) -> int:
        """Append provenance. Existing rows are not rewritten: evidence is
        additive, so re-ingesting a source never erases the original record."""

    @abc.abstractmethod
    async def dependents_of_source(
        self, *, source_kind: str, source_id: str
    ) -> List[MemoryRecord]:
        """Every memory whose provenance names this source.

        Powers source deletion: the dependency edge exists, so removing the
        source can find and mark everything derived from it.
        """

    # ---------------------------------------------------------------- deletion
    @abc.abstractmethod
    async def soft_delete(
        self, *, owner_key_id: Optional[str], memory_id: str, now: float
    ) -> bool:
        """Mark deleted and drop any embedding. Returns whether it changed."""

    @abc.abstractmethod
    async def purge(
        self, *, owner_key_id: Optional[str], memory_id: str
    ) -> bool:
        """Physically erase a memory and its detail rows.

        Returns:
            ``True`` when rows were removed. ``False`` when there was nothing to
            remove, which keeps deletion idempotent.
        """

    @abc.abstractmethod
    async def verify_absent(
        self, *, owner_key_id: Optional[str], memory_id: str
    ) -> Dict[str, bool]:
        """Report exactly what remains after a deletion.

        Lets a caller *verify* a deletion instead of trusting the return value,
        which is what makes "we deleted your data" checkable.
        """

    @abc.abstractmethod
    async def expired(
        self, *, now: float, limit: int = 500
    ) -> List[MemoryRecord]:
        """Active memories past their expiry. Candidates for the expiry sweep."""

    @abc.abstractmethod
    async def set_embedding(
        self, memory_id: str, *, vector: Optional[List[float]], model: str,
        dimensions: int, status: EmbeddingStatus,
    ) -> bool:
        """Record or clear a vector and its model identity.

        Storing the model alongside the vector is what prevents a later model
        change from comparing incompatible spaces.
        """

    # ------------------------------------------------------------------ outbox
    @abc.abstractmethod
    async def enqueue(
        self, *, kind: str, dedup_key: str, payload: Dict[str, Any], now: float,
        available_at: Optional[float] = None,
    ) -> bool:
        """Add a deferred write. Idempotent on ``(kind, dedup_key)``."""

    @abc.abstractmethod
    async def dequeue(
        self, *, now: float, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Due outbox entries, oldest first, marked in-flight atomically."""

    @abc.abstractmethod
    async def complete_outbox(self, entry_id: int, *, now: float) -> None:
        """Remove a successfully processed entry."""

    @abc.abstractmethod
    async def fail_outbox(
        self, entry_id: int, *, error: str, now: float, max_attempts: int,
        backoff_base: float = 1.0,
    ) -> str:
        """Record a failure, returning the new state (``pending`` or ``failed``)."""

    @abc.abstractmethod
    async def reclaim_stale_outbox(self, *, now: float, stale_after: float) -> int:
        """Return entries stuck in ``processing`` to ``pending``.

        Without this an entry claimed by a process that then died would never be
        retried and would sit invisible forever.
        """

    @abc.abstractmethod
    async def outbox_depth(self) -> Dict[str, int]:
        """Outbox counts by state, for metrics."""


class DuplicateMemory(Exception):
    """Raised by :meth:`MemoryRepository.insert` on a dedup-key conflict.

    Internal control-flow, never surfaced to a caller: the service resolves it
    by returning the existing record.
    """

    def __init__(self, memory_id: str) -> None:
        super().__init__(f"memory with this deduplication key exists: {memory_id}")
        self.memory_id = memory_id


class MemoryUnavailable(Exception):
    """The memory store is not usable. Callers must degrade, not crash."""


__all__ = [
    "DuplicateMemory",
    "MemoryRepository",
    "MemoryUnavailable",
    "NewMemory",
    "RetrievalPage",
    "RetrievalQuery",
]