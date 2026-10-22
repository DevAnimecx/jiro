"""Jiro cognitive memory system.

Four memory types with genuinely different semantics, one shared envelope:

* **working**    — bounded, disposable scratch context for an in-flight scope.
* **episodic**   — immutable evidence that something happened, with provenance.
* **semantic**   — structured facts with validity, provenance and a contradiction
  workflow.
* **procedural** — declarative, versioned how-to records that are validated and
  promoted, and **never executed** by the memory system.

Guarantees
----------
* **Durable** — a write is committed before the call returns; nothing lives only
  in process memory.
* **Scoped** — ``owner_key_id`` is the only durable principal (ADR 0008) and is
  enforced in SQL, not by convention.
* **Idempotent** — ingestion deduplicates on a key derived from source identity;
  re-observing a claim increments a counter and never raises its confidence.
* **Honest** — contradictions are surfaced rather than resolved, and a cancelled
  or failed task produces an episode that says so.
* **Degradable** — the optional vector index can be absent or failing; every
  operation still works, using deterministic lexical ranking.

Typical use::

    service = MemoryService(SqlMemoryRepository(db), MemoryConfig.from_settings(settings))
    await service.initialize()

    scope = MemoryScope(kind=ScopeKind.TASK, scope_id=task_id, owner_key_id=key_id)
    await service.remember({"text": "user prefers concise answers"}, scope=scope)
    hits = await service.retrieve("preferences", scope=scope, limit=5)
"""

from __future__ import annotations

from jiro.memory.config import MemoryConfig, RetrievalWeights
from jiro.memory.embeddings import (
    EmbeddingAdapter,
    EmbeddingProvider,
    EmbeddingResult,
    LocalEmbeddingProvider,
)
from jiro.memory.errors import (
    ContradictionError,
    EmbeddingUnavailable,
    MemoryError,
    MemoryLimitExceeded,
    MemoryNotDeletable,
    MemoryNotExecutable,
    MemoryNotFound,
    MemoryProviderError,
    MemoryScopeError,
    MemoryValidationError,
)
from jiro.memory.lifecycle import TRANSITIONS as MEMORY_TRANSITIONS
from jiro.memory.outbox import MemoryOutbox
from jiro.memory.ranking import RankedRecord, Ranker
from jiro.memory.repository import (
    MemoryRepository,
    NewMemory,
    RetrievalPage,
    RetrievalQuery,
)
from jiro.memory.runtime_integration import TaskMemoryBridge
from jiro.memory.service import Contradiction, MemoryService
from jiro.memory.sqlite_repo import SqlMemoryRepository
from jiro.memory.types import (
    EmbeddingStatus,
    MemoryFact,
    MemoryHit,
    MemoryProcedure,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Provenance,
    ScopeKind,
    Verification,
    new_memory_id,
)

__all__ = [
    "Contradiction",
    "ContradictionError",
    "EmbeddingAdapter",
    "EmbeddingProvider",
    "EmbeddingResult",
    "EmbeddingStatus",
    "EmbeddingUnavailable",
    "LocalEmbeddingProvider",
    "MEMORY_TRANSITIONS",
    "MemoryConfig",
    "MemoryError",
    "MemoryFact",
    "MemoryHit",
    "MemoryLimitExceeded",
    "MemoryNotDeletable",
    "MemoryNotExecutable",
    "MemoryNotFound",
    "MemoryOutbox",
    "MemoryProcedure",
    "MemoryProviderError",
    "MemoryRecord",
    "MemoryRepository",
    "MemoryScope",
    "MemoryScopeError",
    "MemoryService",
    "MemoryStatus",
    "MemoryType",
    "MemoryValidationError",
    "NewMemory",
    "Provenance",
    "RankedRecord",
    "Ranker",
    "RetrievalPage",
    "RetrievalQuery",
    "RetrievalWeights",
    "ScopeKind",
    "SqlMemoryRepository",
    "TaskMemoryBridge",
    "Verification",
    "new_memory_id",
]