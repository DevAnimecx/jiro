"""Domain types for the Jiro cognitive memory system.

Pure data: enums, frozen records, scope objects. No FastAPI, no MCP, no
provider SDK, no SQLite. The whole memory layer depends on this module.

Design notes
------------
**Four types, one envelope.** Every memory shares the envelope in
:class:`MemoryRecord` — identity, scope, lifecycle, provenance links, version.
What differs per type lives in a sibling record (``MemoryFact`` for semantic,
``MemoryProcedure`` for procedural) or in how the service treats the row
(working is capacity-bounded and disposable; episodic is immutable evidence).

**Confidence is optional and narrow.** ``MemoryRecord.confidence`` is an
*extraction-quality hint*, not a calibrated probability, and is only populated
for semantic facts where it means something. Verification is tracked separately
by :class:`Verification`, because "the extractor was unsure" and "nobody checked
this" are different facts.

**Timestamps are epoch seconds**, matching the rest of this database. ISO
formatting happens only at a transport boundary.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def new_memory_id() -> str:
    """Stable, opaque memory identifier."""
    return "mem_" + uuid.uuid4().hex[:16]


def to_iso(ts: Optional[float]) -> Optional[str]:
    """Format an epoch timestamp for a response boundary."""
    if ts is None:
        return None
    return time.strftime(_ISO_FORMAT, time.gmtime(ts))


def canonical_json(value: Any) -> str:
    """Stable JSON for hashing.

    ``sort_keys`` and fixed separators mean the same logical content always
    produces the same string, which is what makes deduplication keys reliable.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class MemoryType(str, Enum):
    """The four memory categories.

    These are genuinely different things, not four names for one table:

    * ``WORKING``    — disposable scratch context for an in-flight scope.
    * ``EPISODIC``   — immutable evidence that something happened.
    * ``SEMANTIC``   — normalised, reusable facts with validity and provenance.
    * ``PROCEDURAL`` — validated, versioned how-to records. Never executed.
    """

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class MemoryStatus(str, Enum):
    """Lifecycle state of a memory.

    ``ACTIVE`` is the only status ordinary retrieval returns. Everything else is
    filtered out of results by the repository, not by the caller.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    EXPIRED = "expired"
    DELETED = "deleted"


#: Statuses that ordinary retrieval must never return.
INACTIVE_STATUSES: FrozenSet[MemoryStatus] = frozenset(
    {
        MemoryStatus.SUPERSEDED,
        MemoryStatus.DISPUTED,
        MemoryStatus.EXPIRED,
        MemoryStatus.DELETED,
    }
)


class ScopeKind(str, Enum):
    """Where a memory lives.

    ``GLOBAL`` records are shared within an owner (system knowledge the runtime
    learned). The others are progressively narrower. Isolation is enforced on
    ``owner_key_id`` first, then by scope — see ``jiro.memory.service``.

    Note:
        This codebase has no real tenant or user concept; ``AuthContext.key_id``
        is the only durable principal (ADR 0002, ADR 0008). ``owner_key_id`` is
        therefore the security boundary, and scope is a *narrowing* dimension
        within it, never a substitute for it.
    """

    GLOBAL = "global"
    KEY = "key"
    SESSION = "session"
    TASK = "task"


class Verification(str, Enum):
    """How a memory's content came to be believed.

    Ordered by increasing trust, but **never auto-promoted by repetition** —
    seeing the same unsupported claim again raises ``observation_count``, not
    this value.
    """

    #: Observed directly by the runtime (e.g. a task really did fail).
    OBSERVED = "observed"
    #: Supplied by the caller.
    USER_PROVIDED = "user_provided"
    #: Extracted from a source by a deterministic or model-based extractor.
    EXTRACTED = "extracted"
    #: Reasoned rather than observed or stated.
    INFERRED = "inferred"
    #: Checked by a human or a deterministic validation gate.
    VERIFIED = "verified"


#: Relative trust used by the retrieval ranker. Explicit and documented, not a
#: learned weight. It orders *how it was learned*, it does not assert truth.
VERIFICATION_WEIGHT: Dict[Verification, float] = {
    Verification.OBSERVED: 0.70,
    Verification.VERIFIED: 0.65,
    Verification.USER_PROVIDED: 0.55,
    Verification.EXTRACTED: 0.40,
    Verification.INFERRED: 0.20,
}


class EmbeddingStatus(str, Enum):
    """Whether an optional vector index exists for a memory.

    The core system works with every value here except ``READY``. Vector search
    is an accelerator, never a correctness requirement.
    """

    #: Deliberately disabled, or no provider available.
    DISABLED = "disabled"
    #: No embedding yet and none scheduled.
    MISSING = "missing"
    #: Queued in the outbox for a background indexer.
    PENDING = "pending"
    #: Embedding failed; lexical retrieval is authoritative for this record.
    FAILED = "failed"
    #: Usable for vector search.
    READY = "ready"


@dataclass(frozen=True)
class MemoryScope:
    """Where a memory belongs, and therefore who may see it.

    Attributes:
        kind: the narrowing dimension.
        scope_id: identifier for ``kind``. Ignored for ``GLOBAL``.
        owner_key_id: the durable principal. ``None`` is the anonymous/system
            namespace — a real value used by the CLI and the runtime itself, not
            a "missing" value. See ADR 0008.

    Note:
        Never construct this with a scope borrowed from another owner. The
        repository re-checks ``owner_key_id`` on every read and write regardless
        of what a caller passes.
    """

    kind: ScopeKind = ScopeKind.GLOBAL
    scope_id: str = ""
    owner_key_id: Optional[str] = None

    @property
    def key(self) -> str:
        """Stable string form used for grouping and capacity accounting."""
        if self.kind is ScopeKind.GLOBAL:
            return f"global:{self.owner_key_id or ''}"
        return f"{self.kind.value}:{self.scope_id}:{self.owner_key_id or ''}"

    def is_narrower_than(self, other: "MemoryScope") -> bool:
        """Whether this scope sits inside ``other`` for read purposes."""
        if self.owner_key_id != other.owner_key_id:
            return False
        if other.kind is ScopeKind.GLOBAL:
            return True
        return self.kind is other.kind and self.scope_id == other.scope_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "scope_id": self.scope_id,
            "owner_key_id": self.owner_key_id,
        }


@dataclass(frozen=True)
class Provenance:
    """One source reference for a memory.

    Provenance is stored in its own rows rather than inline so that deleting a
    source can find every memory derived from it.

    Attributes:
        source_kind: what kind of thing this is, e.g. ``task``, ``url``,
            ``document``, ``user``, ``memory``.
        source_id: stable identifier within that kind.
        source_url: location, when the source is addressable.
        locator: a section, passage or offset within the source.
        method: how the content was obtained — ``observed``, ``extracted``,
            ``user_provided``, ``imported``, ``summary``.
        observed_at: when the runtime saw the source.
        published_at: when the source itself was published, if known.
        note: free-text caveat. Sanitised on write like any other memory text.
    """

    source_kind: str
    source_id: str
    source_url: Optional[str] = None
    locator: Optional[str] = None
    method: str = "observed"
    observed_at: Optional[float] = None
    published_at: Optional[float] = None
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "locator": self.locator,
            "method": self.method,
            "observed_at": to_iso(self.observed_at),
            "published_at": to_iso(self.published_at),
            "note": self.note,
        }


@dataclass(frozen=True)
class MemoryRecord:
    """The shared memory envelope.

    Attributes:
        id: stable identifier.
        memory_type: which of the four categories this is.
        status: lifecycle state; only ``ACTIVE`` is returned by ordinary reads.
        scope: ownership and narrowing.
        title: short human-readable label, also indexed for lexical search.
        content: structured payload. Shape is validated per type.
        content_text: a flattened, searchable projection of ``content``.
        tags: free-form labels for filtering.
        importance: explicit author-assisted relevance weight in ``[0, 1]``.
            Distinct from confidence.
        confidence: extraction-quality hint in ``[0, 1]``, or ``None`` when the
            concept does not apply to this record type. **Not a probability.**
        verification: how the content came to be believed.
        observation_count: how many times this same claim was seen. Recorded for
            transparency; deliberately does not raise confidence.
        dedup_key: stable identity for idempotent ingestion.
        version: incremented on every update; the full history is retained.
        supersedes_id: the record this one replaced, if any.
        superseded_by_id: the record that replaced this one, if any.
        expires_at: epoch second after which this is not active.
        created_at / updated_at / accessed_at.
        embedding: optional vector. ``None`` unless the provider is available.
        embedding_model: which model produced ``embedding``. Vectors from
            different models are never compared.
        embedding_dimensions: recorded so a model change cannot silently mix
            incompatible vector spaces.
        embedding_status: see :class:`EmbeddingStatus`.
    """

    id: str
    memory_type: MemoryType
    status: MemoryStatus
    scope: MemoryScope
    title: str
    content: Dict[str, Any]
    content_text: str = ""
    tags: Tuple[str, ...] = ()
    importance: float = 0.5
    confidence: Optional[float] = None
    verification: Verification = Verification.OBSERVED
    observation_count: int = 1
    dedup_key: Optional[str] = None
    version: int = 1
    supersedes_id: Optional[str] = None
    superseded_by_id: Optional[str] = None
    expires_at: Optional[float] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    accessed_at: Optional[float] = None
    embedding: Optional[List[float]] = None
    embedding_model: Optional[str] = None
    embedding_dimensions: Optional[int] = None
    embedding_status: EmbeddingStatus = EmbeddingStatus.DISABLED

    @property
    def is_active(self) -> bool:
        return self.status is MemoryStatus.ACTIVE

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.status in (MemoryStatus.DELETED, MemoryStatus.EXPIRED):
            return True
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        """Public representation. Includes provenance-free lifecycle only.

        The embedding is deliberately omitted: it is large, derived, and not
        something a caller needs.
        """
        return {
            "id": self.id,
            "type": self.memory_type.value,
            "status": self.status.value,
            "scope": self.scope.to_dict(),
            "title": self.title,
            "content": self.content,
            "tags": list(self.tags),
            "importance": self.importance,
            "confidence": self.confidence,
            "verification": self.verification.value,
            "observation_count": self.observation_count,
            "dedup_key": self.dedup_key,
            "version": self.version,
            "supersedes_id": self.supersedes_id,
            "superseded_by_id": self.superseded_by_id,
            "created_at": to_iso(self.created_at),
            "updated_at": to_iso(self.updated_at),
            "accessed_at": to_iso(self.accessed_at),
            "expires_at": to_iso(self.expires_at),
            "has_embedding": self.embedding is not None,
            "embedding_status": self.embedding_status.value,
        }


@dataclass(frozen=True)
class MemoryFact:
    """Structured content for :attr:`MemoryType.SEMANTIC`.

    Semantic memory is deliberately not free-form prose. A fact is a
    subject/predicate/value triple with its own provenance and validity, because
    that is what makes contradiction detection possible at all.

    Attributes:
        memory_id: the owning :class:`MemoryRecord`.
        subject: the entity the claim is about.
        predicate: the relation asserted.
        object_value: the value. Serialised to text when not already a string.
        object_type: optional type hint for the object (``str``, ``int``,
            ``bool``, ``date``, ``url``...).
        valid_from / valid_to: when the claim was true, if known. Null
            validities mean "no opinion", not "always".
        observed_at: when the runtime learned it. Always set.
        verification: duplicated here for querying without a join; kept in sync
            by the repository.
        verification_method: how ``VERIFIED`` was established.
        contradiction_group: shared key for facts known to conflict. Set when a
            conflict is detected; cleared by an explicit resolution.
        resolution: ``None`` while open, or ``accepted``/``rejected`` after a
            caller resolves it.
        resolved_by / resolved_at / resolution_reason: the recorded decision.
            A resolution is always explicit — the runtime never picks a winner.
    """

    memory_id: str
    subject: str
    predicate: str
    object_value: Any
    object_type: Optional[str] = None
    valid_from: Optional[float] = None
    valid_to: Optional[float] = None
    observed_at: Optional[float] = None
    verification: Verification = Verification.EXTRACTED
    verification_method: Optional[str] = None
    contradiction_group: Optional[str] = None
    resolution: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[float] = None
    resolution_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object_value,
            "object_type": self.object_type,
            "valid_from": to_iso(self.valid_from),
            "valid_to": to_iso(self.valid_to),
            "observed_at": to_iso(self.observed_at),
            "verification": self.verification.value,
            "verification_method": self.verification_method,
            "contradiction_group": self.contradiction_group,
            "resolution": self.resolution,
            "resolved_by": self.resolved_by,
            "resolved_at": to_iso(self.resolved_at),
            "resolution_reason": self.resolution_reason,
        }


@dataclass(frozen=True)
class MemoryProcedure:
    """Structured content for :attr:`MemoryType.PROCEDURAL`.

    A procedure is **declarative data describing steps**, never executable code.
    Nothing in this system imports, evaluates, or runs anything from this
    record; there is no code path that does. See ADR 0010.

    Attributes:
        memory_id: the owning :class:`MemoryRecord`.
        name: human-readable procedure name.
        purpose: what it achieves, in one line.
        preconditions: conditions that must hold before the steps are meaningful.
        input_schema: JSON-schema-shaped description of required inputs.
        steps: ordered ``{"action", "description", "requires", "captorles"}``
            descriptors. ``action`` names a *registered* operation, not a
            callable supplied by the stored record.
        required_capabilities: coarse permission names, e.g. ``["network"]``.
        expected_outputs: what a successful run should produce.
        validation_criteria: deterministic checks a candidate must pass before
            promotion. Promotion gates are evaluated by the service.
        known_limitations: documented failure modes.
        status: ``candidate`` | ``validated`` | ``active`` | ``retired``.
        revision: bumped on every change; history is retained.
        evaluation: results of the validation gates, with timestamps.
        compatibility: version/environment constraints.
        promoted_at / promoted_by / promotion_reason: the recorded promotion
            decision. A single successful execution never promotes anything.
    """

    memory_id: str
    name: str
    purpose: str
    preconditions: Tuple[str, ...] = ()
    input_schema: Dict[str, Any] = field(default_factory=dict)
    steps: Tuple[Dict[str, Any], ...] = ()
    required_capabilities: Tuple[str, ...] = ()
    expected_outputs: Tuple[str, ...] = ()
    validation_criteria: Tuple[str, ...] = ()
    known_limitations: Tuple[str, ...] = ()
    status: str = "candidate"
    revision: int = 1
    evaluation: Dict[str, Any] = field(default_factory=dict)
    compatibility: Dict[str, Any] = field(default_factory=dict)
    promoted_at: Optional[float] = None
    promoted_by: Optional[str] = None
    promotion_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "preconditions": list(self.preconditions),
            "input_schema": self.input_schema,
            "steps": [dict(s) for s in self.steps],
            "required_capabilities": list(self.required_capabilities),
            "expected_outputs": list(self.expected_outputs),
            "validation_criteria": list(self.validation_criteria),
            "known_limitations": list(self.known_limitations),
            "status": self.status,
            "revision": self.revision,
            "evaluation": self.evaluation,
            "compatibility": self.compatibility,
            "promoted_at": to_iso(self.promoted_at),
            "promoted_by": self.promoted_by,
            "promotion_reason": self.promotion_reason,
        }


@dataclass(frozen=True)
class MemoryHit:
    """A retrieval result, with enough provenance to be trusted.

    ``score_breakdown`` is not decoration. Every ranking component is exposed so
    a caller (or a human reviewing a decision) can see *why* something was
    returned rather than trusting an opaque number.
    """

    record: MemoryRecord
    score: float
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    match_reason: str = ""
    fact: Optional[MemoryFact] = None
    procedure: Optional[MemoryProcedure] = None
    provenance: Tuple[Provenance, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = self.record.to_dict()
        payload["score"] = round(self.score, 6)
        payload["score_breakdown"] = {k: round(v, 6)
                                      for k, v in self.score_breakdown.items()}
        payload["match_reason"] = self.match_reason
        payload["provenance"] = [p.to_dict() for p in self.provenance]
        if self.fact is not None:
            payload["fact"] = self.fact.to_dict()
        if self.procedure is not None:
            payload["procedure"] = self.procedure.to_dict()
        return payload

    def to_context(self, *, budget_chars: int = 2000) -> str:
        """Render as text for a prompt, marked as untrusted data.

        Retrieved content can originate from web pages or documents. It is
        wrapped in explicit delimiters and labelled so a downstream model treats
        it as reference material rather than as instructions.
        """
        parts = [f"[memory {self.record.id} | {self.record.memory_type.value}] "
                 f"{self.record.title}"]
        body = self.record.content_text or self.record.title
        if len(body) > budget_chars:
            body = body[: budget_chars - 3] + "..."
        parts.append(body)
        if self.provenance:
            src = "; ".join(
                f"{p.source_kind}:{p.source_id}" for p in self.provenance[:3])
            parts.append(f"(source: {src})")
        return "\n".join(parts)


def dedup_digest(*parts: Any) -> str:
    """Stable short hash over canonical parts.

    Used to build deduplication keys. Deliberately a pure function of its
    inputs, so the same source always produces the same key across processes.
    """
    joined = "\x1f".join(canonical_json(p) if not isinstance(p, str) else p
                         for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "EmbeddingStatus",
    "INACTIVE_STATUSES",
    "MemoryFact",
    "MemoryHit",
    "MemoryProcedure",
    "MemoryRecord",
    "MemoryScope",
    "MemoryStatus",
    "MemoryType",
    "Provenance",
    "ScopeKind",
    "VERIFICATION_WEIGHT",
    "Verification",
    "canonical_json",
    "dedup_digest",
    "new_memory_id",
    "to_iso",
]