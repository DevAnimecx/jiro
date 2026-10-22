"""SQLite implementation of the memory repository.

Follows the conventions of :mod:`jiro.runtime.sqlite_repo` so the two read the
same way:

* every read and write filters on ``owner_key_id`` — scope isolation is a
  property of the SQL, not a promise from the caller
* multi-row writes use :meth:`jiro.db.Database.transaction`, so a memory, its
  detail row and its provenance commit together or not at all
* timestamps are epoch seconds and booleans are ``INTEGER``

Full-text search uses an FTS5 index when the linked SQLite build provides it and
degrades to a bounded ``LIKE`` scan when it does not. The fallback is reported in
:class:`~jiro.memory.repository.RetrievalPage` so a caller can tell which path
produced its results.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import aiosqlite

from jiro.db import Database
from jiro.memory.errors import MemoryValidationError
from jiro.memory.lifecycle import validate_transition
from jiro.memory.repository import (
    DuplicateMemory,
    MemoryRepository,
    NewMemory,
    RetrievalPage,
    RetrievalQuery,
)
from jiro.memory.types import (
    EmbeddingStatus,
    MemoryFact,
    MemoryProcedure,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Provenance,
    ScopeKind,
    Verification,
)

_MEMORY_COLUMNS = (
    "id, type, status, scope_kind, scope_id, owner_key_id, title, content, "
    "content_text, tags, importance, confidence, verification, observation_count, "
    "dedup_key, dedup_scope, version, supersedes_id, superseded_by_id, expires_at, "
    "created_at, updated_at, accessed_at, embedding, embedding_model, "
    "embedding_dims, embedding_status"
)

_MEMORY_COLUMNS_NO_EMBEDDING = _MEMORY_COLUMNS.replace("embedding, ", "")

#: Bounded candidate pool. Ranking above this is meaningless: a caller asking
#: for 10 results does not need thousands of rows scored.
MAX_CANDIDATES = 2000

#: Characters that must not reach the FTS5 MATCH operator unescaped.
_FTS_UNSAFE = re.compile(r'["\'()*:^{}\[\]]')


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _loads(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def row_to_record(
    row: Dict[str, Any], *, include_embedding: bool = False
) -> MemoryRecord:
    """Map a ``memories`` row onto the frozen domain record."""
    return MemoryRecord(
        id=row["id"],
        memory_type=MemoryType(row["type"]),
        status=MemoryStatus(row["status"]),
        scope=MemoryScope(
            kind=ScopeKind(row["scope_kind"]),
            scope_id=row["scope_id"],
            owner_key_id=row.get("owner_key_id"),
        ),
        title=row["title"],
        content=_loads(row.get("content"), {}) or {},
        content_text=row.get("content_text") or "",
        tags=tuple(_loads(row.get("tags"), []) or []),
        importance=float(row.get("importance") or 0.0),
        confidence=(None if row.get("confidence") is None
                    else float(row["confidence"])),
        verification=Verification(row.get("verification") or "observed"),
        observation_count=int(row.get("observation_count") or 1),
        dedup_key=row.get("dedup_key"),
        version=int(row.get("version") or 1),
        supersedes_id=row.get("supersedes_id"),
        superseded_by_id=row.get("superseded_by_id"),
        expires_at=row.get("expires_at"),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        accessed_at=row.get("accessed_at"),
        embedding=(_loads(row.get("embedding")) if include_embedding else None),
        embedding_model=row.get("embedding_model"),
        embedding_dimensions=(None if row.get("embedding_dims") is None
                              else int(row["embedding_dims"])),
        embedding_status=EmbeddingStatus(row.get("embedding_status") or "disabled"),
    )


def row_to_fact(row: Dict[str, Any]) -> MemoryFact:
    return MemoryFact(
        memory_id=row["memory_id"],
        subject=row["subject"],
        predicate=row["predicate"],
        object_value=_loads(row.get("object_value"), row.get("object_value")),
        object_type=row.get("object_type"),
        valid_from=row.get("valid_from"),
        valid_to=row.get("valid_to"),
        observed_at=row.get("observed_at"),
        verification=Verification(row.get("verification") or "extracted"),
        verification_method=row.get("verification_method"),
        contradiction_group=row.get("contradiction_group"),
        resolution=row.get("resolution"),
        resolved_by=row.get("resolved_by"),
        resolved_at=row.get("resolved_at"),
        resolution_reason=row.get("resolution_reason"),
    )


def row_to_procedure(row: Dict[str, Any]) -> MemoryProcedure:
    return MemoryProcedure(
        memory_id=row["memory_id"],
        name=row["name"],
        purpose=row.get("purpose") or "",
        preconditions=tuple(_loads(row.get("preconditions"), []) or []),
        input_schema=_loads(row.get("input_schema"), {}) or {},
        steps=tuple(_loads(row.get("steps"), []) or []),
        required_capabilities=tuple(
            _loads(row.get("required_capabilities"), []) or []),
        expected_outputs=tuple(_loads(row.get("expected_outputs"), []) or []),
        validation_criteria=tuple(
            _loads(row.get("validation_criteria"), []) or []),
        known_limitations=tuple(_loads(row.get("known_limitations"), []) or []),
        status=row.get("proc_status") or "candidate",
        revision=int(row.get("revision") or 1),
        evaluation=_loads(row.get("evaluation"), {}) or {},
        compatibility=_loads(row.get("compatibility"), {}) or {},
        promoted_at=row.get("promoted_at"),
        promoted_by=row.get("promoted_by"),
        promotion_reason=row.get("promotion_reason"),
    )


def row_to_provenance(row: Dict[str, Any]) -> Provenance:
    return Provenance(
        source_kind=row["source_kind"],
        source_id=row["source_id"],
        source_url=row.get("source_url"),
        locator=row.get("locator"),
        method=row.get("method") or "observed",
        observed_at=row.get("observed_at"),
        published_at=row.get("published_at"),
        note=row.get("note"),
    )


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Every token is quoted, so user punctuation cannot become FTS5 operator
    syntax. Without this a query like ``title:"*"`` would be a syntax error, and
    a crafted query could probe index internals.
    """
    tokens = [t for t in re.split(r"\W+", text or "") if len(t) > 1]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens[:32])


class SqlMemoryRepository(MemoryRepository):
    """Durable memory storage on SQLite."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._fts_available: Optional[bool] = None

    # --------------------------------------------------------------- internals
    @property
    def fts_available(self) -> bool:
        """Whether the FTS5 index is usable. Probed once, lazily."""
        return bool(self._fts_available)

    async def ensure_search_index(self) -> bool:
        """Create the FTS5 index if the linked SQLite supports it.

        Returns ``True`` when full-text search is usable. A build without FTS5
        is not an error: retrieval falls back to a bounded ``LIKE`` scan and
        reports why.
        """
        if self._fts_available is not None:
            return self._fts_available
        try:
            await self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search "
                "USING fts5(memory_id UNINDEXED, title, content_text)"
            )
            self._fts_available = True
        except Exception:
            self._fts_available = False
        return self._fts_available

    async def _index_search(
        self, conn: Any, memory_id: str, title: str, content_text: str
    ) -> None:
        """Keep the FTS index in step. Silently skipped when unavailable."""
        if not self._fts_available:
            return
        try:
            await conn.execute(
                "DELETE FROM memory_search WHERE memory_id = ?", (memory_id,))
            await conn.execute(
                "INSERT INTO memory_search (memory_id, title, content_text)"
                " VALUES (?, ?, ?)",
                (memory_id, title or "", content_text or ""),
            )
        except Exception:
            # An index failure must not fail the memory write; retrieval still
            # works through the LIKE path.
            self._fts_available = False

    async def _write_memory_columns(self, conn: Any, item: NewMemory) -> None:
        record = item.record
        await conn.execute(
            "INSERT INTO memories (id, type, status, scope_kind, scope_id,"
            " owner_key_id, title, content, content_text, tags, importance,"
            " confidence, verification, observation_count, dedup_key, dedup_scope,"
            " version, supersedes_id, superseded_by_id, expires_at, created_at,"
            " updated_at, accessed_at, embedding, embedding_model, embedding_dims,"
            " embedding_status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, NULL, NULL, NULL, ?)",
            (
                record.id, record.memory_type.value, record.status.value,
                record.scope.kind.value, record.scope.scope_id,
                record.scope.owner_key_id, record.title, _dumps(record.content),
                record.content_text, _dumps(list(record.tags)), record.importance,
                record.confidence, record.verification.value,
                record.observation_count, record.dedup_key, item.dedup_scope,
                record.version, record.supersedes_id, record.superseded_by_id,
                record.expires_at, record.created_at, record.updated_at,
                record.accessed_at, record.embedding_status.value,
            ),
        )
        await self._index_search(conn, record.id, record.title, record.content_text)

    async def _insert_detail(self, conn: Any, item: NewMemory) -> None:
        if item.fact is not None:
            fact = item.fact
            await conn.execute(
                "INSERT INTO memory_facts (memory_id, subject, predicate,"
                " object_value, object_type, claim_key, valid_from, valid_to,"
                " observed_at, verification, verification_method,"
                " contradiction_group, resolution, resolved_by, resolved_at,"
                " resolution_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?)",
                (
                    fact.memory_id, fact.subject, fact.predicate,
                    _dumps(fact.object_value), fact.object_type,
                    getattr(fact, "claim_key", None) or item.fact_claim_key or "",
                    fact.valid_from, fact.valid_to,
                    fact.observed_at, fact.verification.value,
                    fact.verification_method, fact.contradiction_group,
                    fact.resolution, fact.resolved_by, fact.resolved_at,
                    fact.resolution_reason,
                ),
            )
        if item.procedure is not None:
            procedure = item.procedure
            await conn.execute(
                "INSERT INTO memory_procedures (memory_id, name, purpose,"
                " preconditions, input_schema, steps, required_capabilities,"
                " expected_outputs, validation_criteria, known_limitations,"
                " proc_status, revision, evaluation, compatibility, promoted_at,"
                " promoted_by, promotion_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    procedure.memory_id, procedure.name, procedure.purpose,
                    _dumps(list(procedure.preconditions)),
                    _dumps(procedure.input_schema),
                    _dumps([dict(s) for s in procedure.steps]),
                    _dumps(list(procedure.required_capabilities)),
                    _dumps(list(procedure.expected_outputs)),
                    _dumps(list(procedure.validation_criteria)),
                    _dumps(list(procedure.known_limitations)),
                    procedure.status, procedure.revision,
                    _dumps(procedure.evaluation), _dumps(procedure.compatibility),
                    procedure.promoted_at, procedure.promoted_by,
                    procedure.promotion_reason,
                ),
            )

    async def _insert_provenance(
        self, conn: Any, memory_id: str, items: Sequence[Provenance]
    ) -> None:
        for item in items:
            await conn.execute(
                "INSERT INTO memory_provenance (memory_id, source_kind, source_id,"
                " source_url, locator, method, observed_at, published_at, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id, item.source_kind, item.source_id, item.source_url,
                    item.locator, item.method, item.observed_at, item.published_at,
                    item.note,
                ),
            )

    async def _find_by_dedup(
        self, dedup_scope: str, dedup_key: str
    ) -> Optional[MemoryRecord]:
        row = await self._db.fetchone(
            f"SELECT {_MEMORY_COLUMNS} FROM memories"
            " WHERE dedup_scope = ? AND dedup_key = ? LIMIT 1",
            (dedup_scope, dedup_key),
        )
        return row_to_record(row) if row else None

    # ------------------------------------------------------------------ writes
    async def insert(self, item: NewMemory, *, now: float) -> MemoryRecord:
        record = item.record
        try:
            async with self._db.transaction() as conn:
                await self._write_memory_columns(conn, item)
                await self._insert_detail(conn, item)
                await self._insert_provenance(conn, record.id, item.provenance)
        except aiosqlite.IntegrityError as exc:
            if "idx_memories_dedup" in str(exc) or "UNIQUE" in str(exc).upper():
                existing = await self._find_by_dedup(
                    item.dedup_scope, record.dedup_key or "")
                if existing is not None:
                    raise DuplicateMemory(existing.id) from exc
            raise
        result = await self.get(
            record.id, owner_key_id=record.scope.owner_key_id)
        if result is None:  # pragma: no cover - would mean the insert vanished
            raise RuntimeError(f"memory {record.id} vanished after insert")
        return result

    async def upsert(
        self, item: NewMemory, *, now: float
    ) -> Tuple[MemoryRecord, bool]:
        """Insert, or acknowledge an existing record for this dedup key.

        On the duplicate path ``observation_count`` is incremented and
        ``confidence`` is deliberately left alone: seeing the same unsupported
        claim again is not evidence for it.
        """
        record = item.record
        if record.dedup_key:
            existing = await self._find_by_dedup(item.dedup_scope, record.dedup_key)
            if existing is not None:
                await self._db.execute(
                    "UPDATE memories SET observation_count = observation_count + 1,"
                    " updated_at = ? WHERE id = ? AND status = 'active'",
                    (now, existing.id),
                )
                refreshed = await self.get(
                    existing.id,
                    owner_key_id=record.scope.owner_key_id)
                return (refreshed or existing), False
        return await self.insert(item, now=now), True

    async def update(
        self, memory_id: str, *, owner_key_id: Optional[str],
        changes: Dict[str, Any], now: float,
    ) -> Optional[MemoryRecord]:
        existing = await self.get(memory_id, owner_key_id=owner_key_id)
        if existing is None:
            return None
        allowed = {
            "title", "content", "content_text", "tags", "importance",
            "confidence", "expires_at", "accessed_at",
            # Timestamps are writable so retention tests and backfills can
            # simulate age without sleeping.
            "created_at", "updated_at",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise MemoryValidationError(
                f"unknown memory update field(s): {unknown}",
                details={"unknown": unknown, "allowed": sorted(allowed)},
            )
        column_map = {
            "title": "title", "content_text": "content_text",
            "tags": "tags", "confidence": "confidence",
            "expires_at": "expires_at", "accessed_at": "accessed_at",
            "created_at": "created_at", "updated_at": "updated_at",
        }
        sets: List[str] = []
        params: List[Any] = []
        for key, value in changes.items():
            if key == "content":
                sets.append("content = ?")
                params.append(_dumps(value))
            elif key == "importance":
                sets.append("importance = ?")
                params.append(float(value))
            elif key in column_map:
                sets.append(f"{column_map[key]} = ?")
                params.append(_dumps(value) if key == "tags" else value)
        if not sets:
            return existing
        sets.append("version = version + 1")
        sets.append("updated_at = ?")
        params.extend([now, memory_id, owner_key_id])

        async with self._db.transaction() as conn:
            await conn.execute(
                f"UPDATE memories SET {', '.join(sets)}"
                " WHERE id = ? AND owner_key_id IS ?",
                tuple(params),
            )
            if "title" in changes or "content_text" in changes:
                await self._index_search(
                    conn,
                    memory_id,
                    str(changes.get("title", existing.title)),
                    str(changes.get("content_text", existing.content_text)),
                )
        return await self.get(memory_id, owner_key_id=owner_key_id)

    async def set_status(
        self, memory_id: str, *, owner_key_id: Optional[str],
        status: MemoryStatus, now: float, extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[MemoryRecord]:
        current = await self.get(memory_id, owner_key_id=owner_key_id)
        if current is None:
            return None
        extra = extra or {}
        status_changed = current.status is not status
        if status_changed:
            validate_transition(current.status, status)
        elif not extra:
            # Nothing to do. Returning early here keeps a repeated call cheap
            # and makes retry-safe deletion possible.
            return current

        sets = ["status = ?", "updated_at = ?"]
        params: List[Any] = [status.value, now]
        if status is MemoryStatus.DELETED:
            # Never leave a derived vector behind for a record we claim to have
            # deleted: it would still be reachable by any vector scan.
            sets.extend([
                "embedding = NULL", "embedding_model = NULL",
                "embedding_dims = NULL", "embedding_status = 'disabled'",
            ])
        if "superseded_by_id" in extra:
            sets.append("superseded_by_id = ?")
            params.append(extra["superseded_by_id"])
        elif status is MemoryStatus.SUPERSEDED and status_changed:
            # Marking something superseded without naming its replacement keeps
            # any existing link rather than clearing it.
            sets.append("superseded_by_id = COALESCE(?, superseded_by_id)")
            params.append(None)

        async with self._db.transaction() as conn:
            if "fact_resolution" in extra:
                await conn.execute(
                    "UPDATE memory_facts SET resolution = ?, resolved_by = ?,"
                    " resolved_at = ?, resolution_reason = ?,"
                    " contradiction_group = ? WHERE memory_id = ?",
                    (
                        str(extra.get("fact_resolution", "")),
                        str(extra.get("resolved_by") or ""),
                        now,
                        str(extra.get("reason") or "")[:1000],
                        None if extra.get("clear_contradiction")
                        else extra.get("contradiction_group"),
                        memory_id,
                    ),
                )
            params.extend([memory_id, owner_key_id])
            await conn.execute(
                f"UPDATE memories SET {', '.join(sets)}"
                " WHERE id = ? AND owner_key_id IS ?",
                tuple(params),
            )
            if status is MemoryStatus.DELETED and self._fts_available:
                await conn.execute(
                    "DELETE FROM memory_search WHERE memory_id = ?", (memory_id,))
        return await self.get(memory_id, owner_key_id=owner_key_id)

    async def link_supersede(
        self, *, new_id: str, old_id: str, now: float
    ) -> bool:
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE memories SET supersedes_id = ?, updated_at = ? WHERE id = ?",
                (old_id, now, new_id),
            )
            await conn.execute(
                "UPDATE memories SET superseded_by_id = ?, updated_at = ?"
                " WHERE id = ?",
                (new_id, now, old_id),
            )
        return True

    # ------------------------------------------------------------------- reads
    async def get(
        self, memory_id: str, *, owner_key_id: Optional[str],
        include_embedding: bool = False,
    ) -> Optional[MemoryRecord]:
        columns = (
            _MEMORY_COLUMNS if include_embedding else _MEMORY_COLUMNS_NO_EMBEDDING
        )
        row = await self._db.fetchone(
            f"SELECT {columns} FROM memories WHERE id = ? AND owner_key_id IS ?",
            (memory_id, owner_key_id),
        )
        return (
            row_to_record(row, include_embedding=include_embedding) if row else None
        )

    async def search(self, query: RetrievalQuery) -> RetrievalPage:
        """Bounded, scoped candidate selection. Ranking happens in the service."""
        await self.ensure_search_index()

        statuses = list(query.status)
        if not statuses:
            # Default: current beliefs only. `include_superseded` widens this to
            # include history — it does not merely stop filtering it out.
            statuses = [MemoryStatus.ACTIVE]
            if query.include_superseded:
                statuses.append(MemoryStatus.SUPERSEDED)
        elif not query.include_superseded:
            statuses = [s for s in statuses if s is not MemoryStatus.SUPERSEDED]
        if not statuses:
            return RetrievalPage(lexical_used=not bool(query.text))

        params: List[Any] = [query.owner_key_id]
        sql = f"SELECT {_MEMORY_COLUMNS} FROM memories m WHERE m.owner_key_id IS ?"

        status_placeholders = ", ".join("?" for _ in statuses)
        sql += f" AND m.status IN ({status_placeholders})"
        params.extend([s.value for s in statuses])

        if query.types:
            placeholders = ", ".join("?" for _ in query.types)
            sql += f" AND m.type IN ({placeholders})"
            params.extend(t.value for t in query.types)

        for tag in query.tags:
            # Tags are stored as a JSON array; match the quoted form so a tag
            # cannot be matched as a substring of a longer one.
            sql += " AND m.tags LIKE ?"
            params.append(f'%"{tag}"%')

        if query.created_after is not None:
            sql += " AND m.created_at >= ?"
            params.append(query.created_after)
        if query.created_before is not None:
            sql += " AND m.created_at <= ?"
            params.append(query.created_before)
        if query.expires_after is not None:
            sql += " AND (m.expires_at IS NULL OR m.expires_at > ?)"
            params.append(query.expires_after)

        degraded: Optional[str] = None
        text = (query.text or "").strip()
        if text:
            match = _fts_query(text)
            if match and self._fts_available:
                sql += (
                    " AND m.id IN (SELECT memory_id FROM memory_search"
                    " WHERE memory_search MATCH ?)"
                )
                params.append(match)
            else:
                degraded = "fts_unavailable_using_like" if text else None
                sql += " AND (m.content_text LIKE ? OR m.title LIKE ?)"
                params.extend([f"%{text[:200]}%", f"%{text[:200]}%"])

        # Extra scopes the caller may read. Ownership still has to match, so
        # this widens *within* an owner and never across one.
        if query.scopes:
            clauses = []
            for scope in query.scopes:
                clauses.append("(m.scope_kind = ? AND m.scope_id = ?)")
                params.extend([scope.kind.value, scope.scope_id])
            sql += f" AND ({' OR '.join(clauses)})"

        sql += " ORDER BY m.importance DESC, m.updated_at DESC LIMIT ?"
        params.append(min(max(query.limit * 10, 50), MAX_CANDIDATES))
        rows = await self._db.fetchall(sql, tuple(params))
        records = [row_to_record(r, include_embedding=query.include_embedding)
                   for r in rows]

        if query.subject or query.predicate:
            records = await self._filter_by_fact(records, query)

        # Expired rows are filtered here as well as by the sweep, so a stale row
        # is never returned even if the sweep has not run yet.
        now = time.time()
        records = [
            r for r in records
            if r.status is not MemoryStatus.EXPIRED and not r.is_expired(now)
        ]

        return RetrievalPage(
            hits=records,
            total_considered=len(records),
            truncated=len(records) >= MAX_CANDIDATES,
            lexical_used=True,
            degraded_reason=degraded,
        )

    async def _filter_by_fact(
        self, records: List[MemoryRecord], query: RetrievalQuery
    ) -> List[MemoryRecord]:
        if not records:
            return records
        clauses: List[str] = []
        params: List[Any] = []
        for record in records:
            clause = ["memory_id = ?"]
            clause_params: List[Any] = [record.id]
            if query.subject:
                clause.append("subject = ?")
                clause_params.append(query.subject)
            if query.predicate:
                clause.append("predicate = ?")
                clause_params.append(query.predicate)
            clauses.append("(" + " AND ".join(clause) + ")")
            params.extend(clause_params)
        rows = await self._db.fetchall(
            "SELECT memory_id FROM memory_facts WHERE " + " OR ".join(clauses),
            tuple(params),
        )
        keep = {r["memory_id"] for r in rows}
        return [r for r in records if r.id in keep]

    async def scope_count(
        self, *, owner_key_id: Optional[str], memory_type: Optional[MemoryType],
        scope: Optional[MemoryScope] = None,
    ) -> int:
        """Live rows only. Deleted and expired rows do not consume capacity."""
        sql = (
            "SELECT COUNT(*) AS n FROM memories"
            " WHERE owner_key_id IS ?"
            "   AND status NOT IN ('deleted', 'expired')"
        )
        params: List[Any] = [owner_key_id]
        if memory_type is not None:
            sql += " AND type = ?"
            params.append(memory_type.value)
        if scope is not None:
            sql += " AND scope_kind = ? AND scope_id = ?"
            params.extend([scope.kind.value, scope.scope_id])
        row = await self._db.fetchone(sql, tuple(params))
        return int(row["n"]) if row else 0

    async def list_scope(
        self, *, owner_key_id: Optional[str], memory_type: Optional[MemoryType],
        limit: int, now: float, scope: Optional[MemoryScope] = None,
    ) -> List[MemoryRecord]:
        """Lowest importance then oldest first, so eviction is a stable prefix."""
        sql = (
            f"SELECT {_MEMORY_COLUMNS} FROM memories"
            " WHERE owner_key_id IS ? AND status = 'active'"
            "   AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: List[Any] = [owner_key_id, now]
        if memory_type is not None:
            sql += " AND type = ?"
            params.append(memory_type.value)
        if scope is not None:
            sql += " AND scope_kind = ? AND scope_id = ?"
            params.extend([scope.kind.value, scope.scope_id])
        sql += " ORDER BY importance ASC, updated_at ASC LIMIT ?"
        params.append(limit)
        rows = await self._db.fetchall(sql, tuple(params))
        return [row_to_record(r) for r in rows]

    # ----------------------------------------------------------- type specifics
    async def get_fact(self, memory_id: str) -> Optional[MemoryFact]:
        row = await self._db.fetchone(
            "SELECT * FROM memory_facts WHERE memory_id = ?", (memory_id,))
        return row_to_fact(row) if row else None

    async def facts_on_claim(
        self, *, claim_key: str, owner_key_id: Optional[str],
        include_inactive: bool = True,
    ) -> List[Tuple[MemoryFact, MemoryRecord]]:
        """Every fact sharing a ``(subject, predicate)`` claim key.

        The basis of contradiction detection: two facts here that disagree on
        the object are a conflict, not a duplicate.
        """
        sql = (
            "SELECT f.* FROM memory_facts f"
            " JOIN memories m ON m.id = f.memory_id"
            " WHERE f.claim_key = ? AND m.owner_key_id IS ?"
        )
        params: List[Any] = [claim_key, owner_key_id]
        if not include_inactive:
            sql += " AND m.status = 'active'"
        sql += " ORDER BY f.observed_at ASC, f.memory_id ASC"
        rows = await self._db.fetchall(sql, tuple(params))
        out: List[Tuple[MemoryFact, MemoryRecord]] = []
        for row in rows:
            record = await self.get(row["memory_id"], owner_key_id=owner_key_id)
            if record is not None:
                out.append((row_to_fact(row), record))
        return out

    async def get_procedure(self, memory_id: str) -> Optional[MemoryProcedure]:
        row = await self._db.fetchone(
            "SELECT * FROM memory_procedures WHERE memory_id = ?", (memory_id,))
        return row_to_procedure(row) if row else None

    async def update_procedure(
        self, memory_id: str, changes: Dict[str, Any]
    ) -> bool:
        """Persist procedure lifecycle changes (promotion, revision, evaluation)."""
        columns = {
            "proc_status": "proc_status", "revision": "revision",
            "evaluation": "evaluation", "promoted_at": "promoted_at",
            "promoted_by": "promoted_by", "promotion_reason": "promotion_reason",
        }
        sets: List[str] = []
        params: List[Any] = []
        for key, value in changes.items():
            if key not in columns:
                raise MemoryValidationError(
                    f"unknown procedure field: {key}",
                    details={"allowed": sorted(columns)},
                )
            sets.append(f"{columns[key]} = ?")
            params.append(_dumps(value) if key == "evaluation" else value)
        if not sets:
            return True
        params.append(memory_id)
        await self._db.execute(
            f"UPDATE memory_procedures SET {', '.join(sets)} WHERE memory_id = ?",
            tuple(params),
        )
        return True

    # -------------------------------------------------------------- provenance
    async def provenance_for(self, memory_id: str) -> Tuple[Provenance, ...]:
        rows = await self._db.fetchall(
            "SELECT * FROM memory_provenance WHERE memory_id = ? ORDER BY id ASC",
            (memory_id,),
        )
        return tuple(row_to_provenance(r) for r in rows)

    async def add_provenance(
        self, memory_id: str, items: Sequence[Provenance]
    ) -> int:
        """Append provenance. Evidence is additive: re-ingesting a source never
        rewrites the original record of where a claim came from."""
        if not items:
            return 0
        async with self._db.transaction() as conn:
            await self._insert_provenance(conn, memory_id, items)
        return len(items)

    async def dependents_of_source(
        self, *, source_kind: str, source_id: str
    ) -> List[MemoryRecord]:
        """Every memory whose provenance names this source.

        Powers source deletion: the dependency edge exists, so removing the
        source can find and mark everything derived from it.
        """
        rows = await self._db.fetchall(
            "SELECT m.* FROM memories m"
            " JOIN memory_provenance p ON p.memory_id = m.id"
            " WHERE p.source_kind = ? AND p.source_id = ? GROUP BY m.id",
            (source_kind, source_id),
        )
        return [row_to_record(r) for r in rows]

    # ---------------------------------------------------------------- deletion
    async def soft_delete(
        self, *, owner_key_id: Optional[str], memory_id: str, now: float
    ) -> bool:
        before = await self.get(memory_id, owner_key_id=owner_key_id)
        if before is None:
            return False
        if before.status is MemoryStatus.DELETED:
            return True  # idempotent
        validate_transition(before.status, MemoryStatus.DELETED)
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE memories SET status = 'deleted', updated_at = ?,"
                " embedding = NULL, embedding_model = NULL, embedding_dims = NULL,"
                " embedding_status = 'disabled'"
                " WHERE id = ? AND owner_key_id IS ?",
                (now, memory_id, owner_key_id),
            )
            if self._fts_available:
                await conn.execute(
                    "DELETE FROM memory_search WHERE memory_id = ?", (memory_id,))
        return True

    async def purge(
        self, *, owner_key_id: Optional[str], memory_id: str
    ) -> bool:
        """Physically erase. ``False`` when there was nothing to remove, which
        keeps deletion idempotent."""
        existing = await self.get(memory_id, owner_key_id=owner_key_id)
        if existing is None:
            return False
        async with self._db.transaction() as conn:
            await conn.execute(
                "DELETE FROM memory_provenance WHERE memory_id = ?", (memory_id,))
            await conn.execute(
                "DELETE FROM memory_facts WHERE memory_id = ?", (memory_id,))
            await conn.execute(
                "DELETE FROM memory_procedures WHERE memory_id = ?", (memory_id,))
            if self._fts_available:
                await conn.execute(
                    "DELETE FROM memory_search WHERE memory_id = ?", (memory_id,))
            await conn.execute(
                "DELETE FROM memories WHERE id = ? AND owner_key_id IS ?",
                (memory_id, owner_key_id),
            )
        return True

    async def verify_absent(
        self, *, owner_key_id: Optional[str], memory_id: str
    ) -> Dict[str, bool]:
        """Report exactly what remains after a deletion.

        Lets a caller *verify* a deletion instead of trusting the return value.
        """
        row = await self._db.fetchone(
            "SELECT status FROM memories WHERE id = ? AND owner_key_id IS ?",
            (memory_id, owner_key_id),
        )
        residual = {}
        for table in ("memory_provenance", "memory_facts", "memory_procedures"):
            count = await self._db.fetchone(
                f"SELECT COUNT(*) AS n FROM {table} WHERE memory_id = ?",
                (memory_id,),
            )
            residual[table] = int(count["n"]) if count else 0
        fts_residual = 0
        if self._fts_available:
            count = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM memory_search WHERE memory_id = ?",
                (memory_id,),
            )
            fts_residual = int(count["n"]) if count else 0
        return {
            "row_present": row is not None,
            "status_deleted": bool(row and row["status"] == "deleted"),
            "detail_rows": sum(residual.values()),
            "search_rows": fts_residual,
        }

    async def expired(self, *, now: float, limit: int = 500) -> List[MemoryRecord]:
        rows = await self._db.fetchall(
            f"SELECT {_MEMORY_COLUMNS} FROM memories"
            " WHERE status = 'active' AND expires_at IS NOT NULL"
            "   AND expires_at <= ? ORDER BY expires_at ASC LIMIT ?",
            (now, limit),
        )
        return [row_to_record(r) for r in rows]

    async def set_embedding(
        self, memory_id: str, *, vector: Optional[List[float]], model: str,
        dimensions: int, status: EmbeddingStatus,
    ) -> bool:
        """Record or clear a vector and its model identity.

        Storing the model alongside the vector is what prevents a later model
        change from silently comparing incompatible spaces.
        """
        await self._db.execute(
            "UPDATE memories SET embedding = ?, embedding_model = ?,"
            " embedding_dims = ?, embedding_status = ? WHERE id = ?",
            (
                _dumps(vector) if vector is not None else None,
                model if vector is not None else None,
                dimensions if vector is not None else None,
                status.value,
                memory_id,
            ),
        )
        return True

    # ------------------------------------------------------------------ outbox
    async def enqueue(
        self, *, kind: str, dedup_key: str, payload: Dict[str, Any], now: float,
        available_at: Optional[float] = None,
    ) -> bool:
        """Add a deferred write. Idempotent on ``(kind, dedup_key)``."""
        try:
            await self._db.execute(
                "INSERT INTO memory_outbox (kind, dedup_key, payload, state,"
                " attempts, created_at, updated_at, available_at)"
                " VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)",
                (
                    kind, dedup_key, _dumps(payload), now, now,
                    available_at if available_at is not None else now,
                ),
            )
            return True
        except aiosqlite.IntegrityError:
            return False  # already queued; nothing to do

    async def dequeue(self, *, now: float, limit: int = 50) -> List[Dict[str, Any]]:
        """Claim due entries, marking them in-flight.

        Only rows this call actually transitioned are returned. Selecting by
        ``state = 'processing'`` would hand back entries an earlier drain had
        already claimed, and a second drainer would process the same work twice.
        """
        candidates = await self._db.fetchall(
            "SELECT * FROM memory_outbox WHERE state = 'pending'"
            " AND available_at <= ? ORDER BY id ASC LIMIT ?",
            (now, limit),
        )
        if not candidates:
            return []
        ids = [int(row["id"]) for row in candidates]
        placeholders = ", ".join("?" for _ in ids)
        await self._db.execute(
            f"UPDATE memory_outbox SET state = 'processing', updated_at = ?"
            f" WHERE id IN ({placeholders})",
            (now, *ids),
        )
        out: List[Dict[str, Any]] = []
        for row in candidates:
            entry = dict(row)
            entry["state"] = "processing"
            entry["payload"] = _loads(row["payload"], {}) or {}
            out.append(entry)
        return out

    async def complete_outbox(self, entry_id: int, *, now: float) -> None:
        await self._db.execute("DELETE FROM memory_outbox WHERE id = ?", (entry_id,))

    async def fail_outbox(
        self, entry_id: int, *, error: str, now: float, max_attempts: int,
        backoff_base: float = 1.0,
    ) -> str:
        row = await self._db.fetchone(
            "SELECT attempts FROM memory_outbox WHERE id = ?", (entry_id,))
        if row is None:
            return "gone"
        attempts = int(row["attempts"] or 0) + 1
        if attempts >= max_attempts:
            # Out of retries. The entry stays for an operator to inspect rather
            # than being deleted, so a permanent failure is visible rather than
            # silently dropped.
            state = "failed"
            available = now
        else:
            state = "pending"
            # Bounded exponential backoff so a failing source does not spin.
            available = now + min(300.0, backoff_base * (2 ** min(attempts, 8)))
        await self._db.execute(
            "UPDATE memory_outbox SET state = ?, attempts = ?, last_error = ?,"
            " updated_at = ?, available_at = ? WHERE id = ?",
            (state, attempts, error[:500], now, available, entry_id),
        )
        return state

    async def reclaim_stale_outbox(self, *, now: float, stale_after: float) -> int:
        """Return entries stuck in ``processing`` to ``pending``.

        Without this an entry claimed by a process that then died would never be
        retried and would sit invisible forever.
        """
        cutoff = now - stale_after
        rows = await self._db.fetchall(
            "SELECT id FROM memory_outbox WHERE state = 'processing'"
            " AND updated_at <= ?",
            (cutoff,),
        )
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        placeholders = ", ".join("?" for _ in ids)
        await self._db.execute(
            f"UPDATE memory_outbox SET state = 'pending', updated_at = ?"
            f" WHERE id IN ({placeholders})",
            (now, *ids),
        )
        return len(ids)

    async def outbox_depth(self) -> Dict[str, int]:
        rows = await self._db.fetchall(
            "SELECT state, COUNT(*) AS n FROM memory_outbox GROUP BY state")
        return {str(r["state"]): int(r["n"]) for r in rows}


__all__ = [
    "MAX_CANDIDATES",
    "SqlMemoryRepository",
    "row_to_fact",
    "row_to_procedure",
    "row_to_provenance",
    "row_to_record",
]