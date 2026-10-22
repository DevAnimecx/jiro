"""SQLite persistence layer (aiosqlite, WAL mode).

Tables:
  * ``cache``    — key/value search & scrape cache with TTL
  * ``api_keys`` — hashed API keys with roles/scopes
  * ``usage``    — per-request usage accounting
  * ``cookies``  — per-engine persistent cookies
  * ``tos_acknowledgments`` — legal ToS acceptance records
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import aiosqlite

from jiro.errors import CacheError
from jiro.rbac import ROLE_TEMPLATES

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    engine     TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'search',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);

CREATE TABLE IF NOT EXISTS api_keys (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    key_hash       TEXT NOT NULL UNIQUE,
    key_prefix     TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'user',
    scopes         TEXT NOT NULL DEFAULT '["search","scrape","ai"]',
    rate_limit_rpm INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    revoked        INTEGER NOT NULL DEFAULT 0,
    last_used_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_keys_prefix ON api_keys(key_prefix);

CREATE TABLE IF NOT EXISTS usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    key_id     TEXT,
    endpoint   TEXT NOT NULL,
    engine     TEXT,
    query      TEXT,
    status     INTEGER,
    latency_ms REAL,
    tokens_in  INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cached     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS idx_usage_key ON usage(key_id);

CREATE TABLE IF NOT EXISTS semantic_cache (
    query      TEXT PRIMARY KEY,
    embedding  TEXT NOT NULL,
    result_key TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_created ON semantic_cache(created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued',
    payload          TEXT NOT NULL DEFAULT '{}',
    result           TEXT,
    error            TEXT,
    progress         TEXT DEFAULT '',
    webhook_url      TEXT,
    webhook_secret   TEXT,
    webhook_delivered INTEGER DEFAULT 0,
    created_at       REAL NOT NULL,
    completed_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

-- Durable task runtime (jiro/runtime). Additive: does not touch `jobs` above.
-- All instants are epoch seconds (REAL), matching every other table here.
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    state             TEXT NOT NULL DEFAULT 'queued',
    owner_key_id      TEXT,
    idempotency_key   TEXT,
    -- Non-null echo of the owner used for the uniqueness rule. SQLite treats
    -- NULLs as distinct in a UNIQUE index, so a plain (owner_key_id,
    -- idempotency_key) index would silently never conflict for anonymous
    -- callers, whose owner is NULL.
    idempotency_scope TEXT NOT NULL DEFAULT '',
    input             TEXT NOT NULL DEFAULT '{}',
    result            TEXT,
    result_ref        TEXT,
    error_code        TEXT,
    error_message     TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 3,
    attempt_timeout_s REAL NOT NULL DEFAULT 60,
    deadline_at       REAL,
    retry_backoff_base REAL NOT NULL DEFAULT 1,
    retry_backoff_max  REAL NOT NULL DEFAULT 60,
    retry_jitter_ratio REAL NOT NULL DEFAULT 0.2,
    max_result_bytes   INTEGER NOT NULL DEFAULT 262144,
    lease_owner       TEXT,
    lease_expires_at  REAL,
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    available_at      REAL,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    started_at        REAL,
    completed_at      REAL
);
-- Idempotency is scoped per owner. The index is partial so tasks with no
-- idempotency key never collide, and it uses the non-null idempotency_scope
-- column so it still works for anonymous callers (owner_key_id IS NULL).
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency
    ON tasks(idempotency_scope, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
-- Claim path: filter by state + availability, order by age.
CREATE INDEX IF NOT EXISTS idx_tasks_claim ON tasks(state, available_at);
-- Recovery path: find running tasks whose lease has expired.
CREATE INDEX IF NOT EXISTS idx_tasks_lease ON tasks(state, lease_expires_at);
-- Deadline sweep.
CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks(state, deadline_at);
-- Owner-scoped listing.
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_key_id, created_at);

CREATE TABLE IF NOT EXISTS task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    type       TEXT NOT NULL,
    attempt    INTEGER,
    ts         REAL NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, id);
CREATE INDEX IF NOT EXISTS idx_task_events_ts ON task_events(ts);

-- Cognitive memory (jiro/memory). Additive: touches no existing table.
-- All instants are epoch seconds (REAL), matching every other table here.
--
-- `memories` is the shared envelope (identity, scope, lifecycle, version).
-- Type-specific structure lives in sibling tables so the four memory types
-- are not four names for one undifferentiated row:
--   memory_facts       semantic  — subject/predicate/value + validity
--   memory_procedures  procedural — declarative steps, never executable
CREATE TABLE IF NOT EXISTS memories (
    id                 TEXT PRIMARY KEY,
    type               TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'active',
    scope_kind         TEXT NOT NULL DEFAULT 'global',
    scope_id           TEXT NOT NULL DEFAULT '',
    owner_key_id       TEXT,
    title              TEXT NOT NULL DEFAULT '',
    content            TEXT NOT NULL DEFAULT '{}',
    content_text       TEXT NOT NULL DEFAULT '',
    tags               TEXT NOT NULL DEFAULT '[]',
    importance         REAL NOT NULL DEFAULT 0.5,
    confidence         REAL,
    verification       TEXT NOT NULL DEFAULT 'observed',
    observation_count  INTEGER NOT NULL DEFAULT 1,
    dedup_key          TEXT,
    dedup_scope        TEXT NOT NULL DEFAULT '',
    version            INTEGER NOT NULL DEFAULT 1,
    supersedes_id      TEXT,
    superseded_by_id   TEXT,
    expires_at         REAL,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    accessed_at        REAL,
    embedding          TEXT,
    embedding_model    TEXT,
    embedding_dims     INTEGER,
    embedding_status   TEXT NOT NULL DEFAULT 'disabled'
);
-- Idempotent ingestion. Same as the task runtime's problem: owner_key_id may be
-- NULL, and SQLite treats NULLs as distinct in a UNIQUE index, so a non-null
-- dedup_scope column carries the ownership instead.
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup
    ON memories(dedup_scope, dedup_key) WHERE dedup_key IS NOT NULL;
-- Retrieval: owner + active status + type.
CREATE INDEX IF NOT EXISTS idx_memories_owner ON memories(owner_key_id, type, status);
-- Scope listing and working-memory capacity accounting.
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope_kind, scope_id, status);
-- Working-memory eviction order: lowest importance first, then oldest.
CREATE INDEX IF NOT EXISTS idx_memories_evict
    ON memories(type, scope_kind, scope_id, importance, updated_at);
-- Expiry sweeps.
CREATE INDEX IF NOT EXISTS idx_memories_expiry ON memories(expires_at, status);
-- Supersession chains.
CREATE INDEX IF NOT EXISTS idx_memories_supersedes ON memories(supersedes_id);
-- Optional vector filter: only ever populated when a provider is enabled.
CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories(embedding_model, embedding_status) WHERE embedding IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_facts (
    memory_id           TEXT PRIMARY KEY,
    subject             TEXT NOT NULL,
    predicate           TEXT NOT NULL,
    object_value        TEXT,
    object_type         TEXT,
    claim_key           TEXT NOT NULL,
    valid_from          REAL,
    valid_to            REAL,
    observed_at         REAL,
    verification        TEXT NOT NULL DEFAULT 'extracted',
    verification_method TEXT,
    contradiction_group TEXT,
    resolution          TEXT,
    resolved_by         TEXT,
    resolved_at         REAL,
    resolution_reason   TEXT,
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);
-- Contradiction detection compares on (scope, subject, predicate).
CREATE INDEX IF NOT EXISTS idx_memory_facts_claim
    ON memory_facts(claim_key, memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_facts_conflict
    ON memory_facts(contradiction_group) WHERE contradiction_group IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_procedures (
    memory_id             TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    purpose               TEXT NOT NULL DEFAULT '',
    preconditions         TEXT NOT NULL DEFAULT '[]',
    input_schema          TEXT NOT NULL DEFAULT '{}',
    steps                 TEXT NOT NULL DEFAULT '[]',
    required_capabilities TEXT NOT NULL DEFAULT '[]',
    expected_outputs      TEXT NOT NULL DEFAULT '[]',
    validation_criteria   TEXT NOT NULL DEFAULT '[]',
    known_limitations     TEXT NOT NULL DEFAULT '[]',
    proc_status           TEXT NOT NULL DEFAULT 'candidate',
    revision              INTEGER NOT NULL DEFAULT 1,
    evaluation            TEXT NOT NULL DEFAULT '{}',
    compatibility         TEXT NOT NULL DEFAULT '{}',
    promoted_at           REAL,
    promoted_by           TEXT,
    promotion_reason      TEXT,
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);
CREATE INDEX IF NOT EXISTS idx_memory_procedures_status
    ON memory_procedures(proc_status, name);

-- Provenance is a separate table so deleting a source can find every memory
-- derived from it. Inlining it in `memories` would make that a full scan.
CREATE TABLE IF NOT EXISTS memory_provenance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id     TEXT NOT NULL,
    source_kind   TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    source_url    TEXT,
    locator       TEXT,
    method        TEXT NOT NULL DEFAULT 'observed',
    observed_at   REAL,
    published_at  REAL,
    note          TEXT,
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);
CREATE INDEX IF NOT EXISTS idx_memory_provenance_memory
    ON memory_provenance(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_provenance_source
    ON memory_provenance(source_kind, source_id);

-- Durable outbox. The runtime commits a task result and a memory record in two
-- different transactions; anything deferred goes here so a crash between them
-- loses nothing. Handlers are idempotent, so draining twice is safe.
CREATE TABLE IF NOT EXISTS memory_outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    dedup_key   TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    state       TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    available_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_outbox_dedup
    ON memory_outbox(kind, dedup_key);
CREATE INDEX IF NOT EXISTS idx_memory_outbox_due
    ON memory_outbox(state, available_at);

-- Research engine (jiro/research). Additive: touches no existing table.
-- All instants are epoch seconds (REAL), matching every other table here.
--
-- The research task row is the durable checkpoint. Every other table hangs off
-- it, so a restart can resume from the last committed stage rather than
-- repeating work. Budgets live here rather than in the serialized plan so that
-- a crash cannot reset them: a plan is data, this is the contract.
CREATE TABLE IF NOT EXISTS research_tasks (
    id                TEXT PRIMARY KEY,
    owner_key_id      TEXT,
    -- queued | planning | searching | extracting | assessing | following_up
    -- | synthesizing | validating | persisting | completed | partial | blocked
    -- | failed | cancelled
    stage             TEXT NOT NULL DEFAULT 'queued',
    question          TEXT NOT NULL,
    request           TEXT NOT NULL DEFAULT '{}',   -- normalized ResearchRequest
    plan              TEXT,                          -- current ResearchPlan JSON
    -- Non-null echo of the owner for the uniqueness rule. SQLite treats NULLs
    -- as distinct in a UNIQUE index, so anonymous callers would never collide.
    idempotency_scope TEXT NOT NULL DEFAULT '',
    idempotency_key   TEXT,
    task_id           TEXT,                          -- Phase 1 runtime task id
    -- Budget accounting. `consumed_*` are monotonic and never reset, so a
    -- resumed run cannot buy itself a second budget.
    max_queries       INTEGER NOT NULL DEFAULT 8,
    consumed_queries  INTEGER NOT NULL DEFAULT 0,
    max_sources       INTEGER NOT NULL DEFAULT 12,
    consumed_sources  INTEGER NOT NULL DEFAULT 0,
    max_extractions   INTEGER NOT NULL DEFAULT 20,
    consumed_extractions INTEGER NOT NULL DEFAULT 0,
    max_iterations    INTEGER NOT NULL DEFAULT 3,
    consumed_iterations INTEGER NOT NULL DEFAULT 0,
    max_duration_seconds REAL NOT NULL DEFAULT 300,
    started_at        REAL,
    deadline_at       REAL,
    completed_at      REAL,
    -- Stop reason and completeness, persisted so a report can state honestly
    -- whether it is finished or merely as far as the budget allowed.
    stop_reason       TEXT,
    result_status     TEXT,                          -- complete | partial | blocked
    error_code        TEXT,
    error_message     TEXT,
    checkpoint        TEXT,                          -- last resumable stage state
    checkpoint_version INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_idempotency
    ON research_tasks(idempotency_scope, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_research_owner ON research_tasks(owner_key_id, created_at);
CREATE INDEX IF NOT EXISTS idx_research_stage ON research_tasks(stage, updated_at);
CREATE INDEX IF NOT EXISTS idx_research_runtime_task ON research_tasks(task_id);

-- One row per distinct URL discovered. Deduplication happens here, on
-- (owner, normalized_url), so re-discovering a source does not create a second
-- extraction budget charge.
CREATE TABLE IF NOT EXISTS research_sources (
    id              TEXT PRIMARY KEY,
    research_id     TEXT NOT NULL,
    owner_key_id    TEXT,
    original_url    TEXT NOT NULL,
    normalized_url  TEXT NOT NULL,
    canonical_url   TEXT,
    final_url       TEXT,
    title           TEXT,
    domain          TEXT,
    author          TEXT,
    published_at    REAL,                 -- NULL when the page does not say
    retrieved_at    REAL,
    content_type    TEXT,
    content_hash    TEXT,
    char_count      INTEGER NOT NULL DEFAULT 0,
    truncated       INTEGER NOT NULL DEFAULT 0,
    -- extracted | denied | timeout | parse_error | no_content | unsupported_type
    -- | too_large | ssrf_blocked | robots_denied | not_attempted | error
    extraction_status TEXT NOT NULL DEFAULT 'not_attempted',
    extraction_error TEXT,
    discovered_by   TEXT NOT NULL DEFAULT '[]',  -- JSON list of query ids
    discovery_queries TEXT NOT NULL DEFAULT '[]',
    discovery_engines  TEXT NOT NULL DEFAULT '[]',
    rank            INTEGER,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (research_id) REFERENCES research_tasks(id)
);
-- The dedup boundary. Scoped by owner like every other Phase 3 table, using the
-- non-null echo column so anonymous callers cannot collide spuriously.
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_sources_dedup
    ON research_sources(research_id, normalized_url);
CREATE INDEX IF NOT EXISTS idx_research_sources_research
    ON research_sources(research_id, rank);

-- Extracted passages. References the source rather than copying its text: a
-- report claim must be reproducible from (source_id, offset, hash).
CREATE TABLE IF NOT EXISTS research_evidence (
    id              TEXT PRIMARY KEY,
    research_id     TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    owner_key_id    TEXT,
    subquestion_id  TEXT,
    -- The passage itself. Bounded by config; this is the only place extracted
    -- third-party text is stored.
    passage         TEXT NOT NULL,
    passage_hash    TEXT NOT NULL,
    passage_start   INTEGER NOT NULL DEFAULT 0,
    passage_end     INTEGER NOT NULL DEFAULT 0,
    locator         TEXT,                -- heading or section path
    evidence_type   TEXT NOT NULL DEFAULT 'passage',
    -- unresolved | supporting | contradicting | off_topic
    relevance       TEXT NOT NULL DEFAULT 'unresolved',
    relevance_note  TEXT,
    extracted_at    REAL NOT NULL,
    created_at      REAL NOT NULL,
    FOREIGN KEY (research_id) REFERENCES research_tasks(id),
    FOREIGN KEY (source_id) REFERENCES research_sources(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_evidence_dedup
    ON research_evidence(research_id, source_id, passage_hash);
CREATE INDEX IF NOT EXISTS idx_research_evidence_research
    ON research_evidence(research_id, relevance);

-- Material factual assertions in the output, each mapped to the evidence that
-- supports or contradicts it. `kind` keeps a source's own assertion distinct
-- from the engine's inference.
CREATE TABLE IF NOT EXISTS research_claims (
    id              TEXT PRIMARY KEY,
    research_id     TEXT NOT NULL,
    owner_key_id    TEXT,
    claim_text      TEXT NOT NULL,
    subquestion_id  TEXT,
    -- observation | source_assertion | inference | synthesis
    kind            TEXT NOT NULL DEFAULT 'source_assertion',
    -- unsupported | weak | moderate | strong | disputed | contradicted
    assessment      TEXT NOT NULL DEFAULT 'unsupported',
    confidence_label TEXT,
    limitations     TEXT,
    temporal_scope  TEXT,
    -- JSON list of evidence ids. Stored here rather than in a join table so the
    -- whole claim state is one atomic row, which matters for restart safety.
    supporting_evidence   TEXT NOT NULL DEFAULT '[]',
    contradicting_evidence TEXT NOT NULL DEFAULT '[]',
    -- Distinct publisher domains actually counted as independent corroboration.
    -- Same press release on three domains is one source, not three.
    independent_domains   TEXT NOT NULL DEFAULT '[]',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    FOREIGN KEY (research_id) REFERENCES research_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_research_claims_research
    ON research_claims(research_id, assessment);

-- Plan revisions. A plan is a hypothesis, and changing it is a decision worth
-- recording with the evidence that prompted it.
CREATE TABLE IF NOT EXISTS research_plan_revisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id     TEXT NOT NULL,
    revision        INTEGER NOT NULL,
    plan            TEXT NOT NULL,
    reason          TEXT NOT NULL,
    trigger_evidence TEXT NOT NULL DEFAULT '[]',
    created_at      REAL NOT NULL,
    FOREIGN KEY (research_id) REFERENCES research_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_research_plan_rev
    ON research_plan_revisions(research_id, revision);

-- One row per research iteration, so budget consumption is auditable after the
-- fact rather than only in the counters.
CREATE TABLE IF NOT EXISTS research_iterations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id     TEXT NOT NULL,
    iteration       INTEGER NOT NULL,
    queries         TEXT NOT NULL DEFAULT '[]',
    new_sources     INTEGER NOT NULL DEFAULT 0,
    new_evidence    INTEGER NOT NULL DEFAULT 0,
    follow_up_reason TEXT,
    decided_continue INTEGER,
    created_at      REAL NOT NULL,
    FOREIGN KEY (research_id) REFERENCES research_tasks(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_iterations_unique
    ON research_iterations(research_id, iteration);

-- Queries already issued. Prevents the loop from re-running a query that
-- already produced nothing, which is the classic unbounded-research failure.
CREATE TABLE IF NOT EXISTS research_queries (
    id              TEXT PRIMARY KEY,
    research_id     TEXT NOT NULL,
    query           TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    subquestion_id  TEXT,
    iteration       INTEGER NOT NULL DEFAULT 0,
    engine_requested TEXT,
    engines_used    TEXT NOT NULL DEFAULT '[]',
    result_count    INTEGER NOT NULL DEFAULT 0,
    -- ok | provider_error | empty | rate_limited | timeout
    status          TEXT NOT NULL DEFAULT 'ok',
    error           TEXT,
    issued_at       REAL NOT NULL,
    FOREIGN KEY (research_id) REFERENCES research_tasks(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_queries_dedup
    ON research_queries(research_id, normalized_query);

-- Durable report artifacts. The body is stored inline (bounded by
-- research.max_report_bytes) so a report survives restart without depending on
-- a filesystem or blob store that this project does not have.
CREATE TABLE IF NOT EXISTS research_reports (
    id              TEXT PRIMARY KEY,
    research_id     TEXT NOT NULL,
    owner_key_id    TEXT,
    format          TEXT NOT NULL DEFAULT 'markdown',
    body            TEXT NOT NULL,
    body_hash       TEXT NOT NULL,
    byte_size       INTEGER NOT NULL,
    citation_count  INTEGER NOT NULL DEFAULT 0,
    claim_count     INTEGER NOT NULL DEFAULT 0,
    unsupported_claims INTEGER NOT NULL DEFAULT 0,
    complete        INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL,
    FOREIGN KEY (research_id) REFERENCES research_tasks(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_reports_unique
    ON research_reports(research_id);

CREATE TABLE IF NOT EXISTS cookies (
    engine     TEXT NOT NULL,
    name       TEXT NOT NULL,
    value      TEXT NOT NULL,
    domain     TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT '/',
    expires_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    PRIMARY KEY (engine, name, domain)
);
CREATE INDEX IF NOT EXISTS idx_cookies_engine ON cookies(engine);
CREATE INDEX IF NOT EXISTS idx_cookies_expires ON cookies(expires_at);

CREATE TABLE IF NOT EXISTS proxy_costs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy_url  TEXT NOT NULL,
    request_ts REAL NOT NULL,
    latency_ms REAL NOT NULL DEFAULT 0,
    status     INTEGER NOT NULL DEFAULT 200,
    cost       REAL NOT NULL DEFAULT 0.0,
    engine     TEXT,
    success    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_proxy_costs_url ON proxy_costs(proxy_url);
CREATE INDEX IF NOT EXISTS idx_proxy_costs_ts ON proxy_costs(request_ts);

CREATE TABLE IF NOT EXISTS tos_acknowledgments (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    engine      TEXT NOT NULL,
    tos_version TEXT NOT NULL,
    ip_address  TEXT,
    user_agent  TEXT,
    acknowledged_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tos_user ON tos_acknowledgments(user_id);
CREATE INDEX IF NOT EXISTS idx_tos_user_engine ON tos_acknowledgments(user_id, engine);

CREATE TABLE IF NOT EXISTS role_permissions (
    identity    TEXT NOT NULL,
    permission  TEXT NOT NULL,
    granted_at  REAL NOT NULL,
    granted_by  TEXT,
    PRIMARY KEY (identity, permission)
);
CREATE INDEX IF NOT EXISTS idx_rp_identity ON role_permissions(identity);
CREATE INDEX IF NOT EXISTS idx_rp_permission ON role_permissions(permission);

CREATE TABLE IF NOT EXISTS oidc_identities (
    id          TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    subject     TEXT NOT NULL,
    email       TEXT,
    name        TEXT,
    created_at  REAL NOT NULL,
    last_login  REAL NOT NULL,
    UNIQUE(provider, subject)
);
CREATE INDEX IF NOT EXISTS idx_oidc_provider_subject ON oidc_identities(provider, subject);

CREATE TABLE IF NOT EXISTS oidc_api_keys (
    identity_id TEXT NOT NULL,
    key_id      TEXT NOT NULL,
    linked_at   REAL NOT NULL,
    PRIMARY KEY (identity_id, key_id)
);
CREATE INDEX IF NOT EXISTS idx_oidc_ak_identity ON oidc_api_keys(identity_id);
CREATE INDEX IF NOT EXISTS idx_oidc_ak_key ON oidc_api_keys(key_id);

CREATE TABLE IF NOT EXISTS sessions (
    jti         TEXT PRIMARY KEY,
    key_id      TEXT NOT NULL,
    expires_at  REAL NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    user_agent  TEXT,
    ip_address  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_key ON sessions(key_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""


class Database:
    """Async SQLite wrapper with a small connection pool.

    SQLite allows many concurrent *readers* but only one *writer* at a time.
    We keep a pool of connections (so reads can run concurrently) and serialize
    writes through a single async lock. For horizontally-scaled (multi-replica)
    deployments use PostgreSQL — see the roadmap — and run the Helm chart with a
    single replica when using the built-in SQLite backend.
    """

    # Schema migrations are applied in order, once each, tracked by `schema_version`.
    #
    # The v0.4.0 runtime tables (`tasks`, `task_events`) are additive and need no
    # ALTER: `SCHEMA` above already creates them complete, so a database created
    # by v0.3.3 gains them on the next `connect()` exactly like a fresh one.
    MIGRATIONS: List[str] = [
        # Future ALTER TABLE statements go here, e.g.:
        # "ALTER TABLE api_keys ADD COLUMN notes TEXT NOT NULL DEFAULT '';",
    ]

    #: How long a blocked writer waits for the SQLite write lock before raising
    #: "database is locked". The in-process ``_writer_lock`` serialises writers
    #: inside one process; this covers a *second* process touching the same file
    #: (multiple uvicorn workers, CLI alongside server, restart-recovery tests).
    BUSY_TIMEOUT_MS = 10_000

    def __init__(self, path: str, *, pool_size: int = 4) -> None:
        self.path = path
        # `:memory:` databases are per-connection; force a single shared
        # connection so writes are visible to subsequent reads.
        self._is_memory = path == ":memory:"
        self._pool_size = 1 if self._is_memory else max(1, pool_size)
        self._pool: List[aiosqlite.Connection] = []
        self._available: Optional["asyncio.Queue[aiosqlite.Connection]"] = None
        self._writer_lock = asyncio.Lock()
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        if not self._is_memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._available = asyncio.Queue()
        try:
            for _ in range(self._pool_size):
                conn = await aiosqlite.connect(self.path)
                conn.row_factory = aiosqlite.Row
                if not self._is_memory:
                    await conn.execute("PRAGMA journal_mode=WAL")
                    await conn.execute("PRAGMA synchronous=NORMAL")
                    await conn.execute(f"PRAGMA busy_timeout={self.BUSY_TIMEOUT_MS}")
                await conn.execute("PRAGMA foreign_keys=ON")
                self._pool.append(conn)
                await self._available.put(conn)
            self._connected = True
            # Apply schema + migrations on a single connection.
            primary = await self._acquire()
            try:
                await primary.executescript(SCHEMA)
                await self._apply_migrations(primary)
                await primary.commit()
            finally:
                await self._release(primary)
        except Exception as exc:  # pragma: no cover - defensive
            self._connected = False
            raise CacheError(f"failed to open database {self.path}: {exc}")

    async def _apply_migrations(self, conn: "aiosqlite.Connection") -> None:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        row = await (await conn.execute("SELECT MAX(version) AS v FROM schema_version")).fetchone()
        current = int(row["v"]) if row and row["v"] is not None else 0
        for idx, sql in enumerate(self.MIGRATIONS, start=1):
            if idx <= current:
                continue
            await conn.executescript(sql)
            await conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (idx, time.time()),
            )

    async def close(self) -> None:
        if self._available is None:
            self._pool.clear()
            self._connected = False
            return
        while not self._available.empty():
            try:
                conn = self._available.get_nowait()
            except Exception:
                break
            try:
                await conn.close()
            except Exception:
                pass
        self._pool.clear()
        self._connected = False

    async def _acquire(self) -> "aiosqlite.Connection":
        if not self._connected:
            raise CacheError("database not connected")
        assert self._available is not None
        return await self._available.get()

    async def _release(self, conn: "aiosqlite.Connection") -> None:
        assert self._available is not None
        await self._available.put(conn)

    async def execute(self, sql: str, params: tuple = ()) -> None:
        async with self._writer_lock:
            conn = await self._acquire()
            try:
                async with conn.execute(sql, params):
                    await conn.commit()
            except BaseException:
                # A statement that fails — a UNIQUE violation, most often —
                # leaves the connection inside an open write transaction.
                # Handing that connection back to the pool in this state keeps
                # the file's write lock held, so every later writer blocks
                # until its busy timeout expires. Roll back before releasing.
                try:
                    await conn.rollback()
                except Exception:  # pragma: no cover - dead connection
                    pass
                raise
            finally:
                await self._release(conn)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["aiosqlite.Connection"]:
        """Open an exclusive write transaction on one pooled connection.

        Needed wherever several writes must land together or not at all — the
        task runtime writes a task row and its lifecycle events as a unit, so a
        crash cannot leave a task whose submission events never arrived.

        ``BEGIN IMMEDIATE`` takes SQLite's write lock up front rather than
        upgrading a read lock partway through, which is what turns a contended
        write into a deadlock instead of a clean wait.

        Usage::

            async with db.transaction() as conn:
                await conn.execute("UPDATE ...", (...))
                await conn.execute("INSERT ...", (...))

        Note:
            Inside the block, use the yielded ``conn`` directly. Calling
            ``db.execute()`` would re-acquire the same non-reentrant
            ``_writer_lock`` and deadlock.

        Note:
            Never hold this open across a network call or a running handler. It
            blocks every other writer in the process.
        """
        async with self._writer_lock:
            conn = await self._acquire()
            try:
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                    # Commit inside the guarded block: a failing or cancelled
                    # commit must roll back too, or the connection returns to
                    # the pool still holding the write lock.
                    await conn.commit()
                except BaseException:
                    # Roll back on error *and* on cancellation, then re-raise so
                    # the caller's own error handling is untouched.
                    try:
                        await conn.rollback()
                    except Exception:  # pragma: no cover - rollback of a dead conn
                        pass
                    raise
            finally:
                await self._release(conn)

    async def schema_objects(self) -> Dict[str, List[str]]:
        """Inventory of tables and indexes actually present.

        Used by :meth:`verify_schema` so a partially created database fails at
        startup with a precise message instead of much later with a cryptic
        "no such column".
        """
        rows = await self.fetchall(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
        )
        tables = sorted(r["name"] for r in rows if r["type"] == "table")
        indexes = sorted(r["name"] for r in rows if r["type"] == "index")
        return {"tables": tables, "indexes": indexes}

    async def verify_schema(self, required_tables: List[str]) -> None:
        """Assert that every required table exists.

        Raises:
            CacheError: naming the missing objects. Called at startup so a
                half-migrated or corrupt database is reported immediately
                rather than on the first task submission.
        """
        present = await self.schema_objects()
        missing_tables = sorted(set(required_tables) - set(present["tables"]))
        if missing_tables:
            raise CacheError(
                "database schema is incomplete; missing table(s): "
                f"{', '.join(missing_tables)}",
                details={"path": self.path, "missing": missing_tables},
            )

    async def table_columns(self, table: str) -> List[str]:
        """Column names for ``table``. Used to detect schema drift."""
        rows = await self.fetchall(f"PRAGMA table_info({table})")
        return [r["name"] for r in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        conn = await self._acquire()
        try:
            cur = await conn.execute(sql, params)
            row = await cur.fetchone()
            await cur.close()
            return dict(row) if row else None
        finally:
            await self._release(conn)

    async def fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        conn = await self._acquire()
        try:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
            await cur.close()
            return [dict(r) for r in rows]
        finally:
            await self._release(conn)

    async def _require(self) -> None:
        if not self._connected:
            raise CacheError("database not connected")

    # ------------------------------------------------------------------ cache
    async def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone(
            "SELECT payload, created_at FROM cache WHERE key = ? AND expires_at > ?",
            (key, time.time()),
        )
        if row is None:
            return None
        return {"payload": json.loads(row["payload"]), "created_at": row["created_at"]}

    async def cache_put(self, key: str, payload: Any, *, engine: str, kind: str,
                        ttl: int) -> None:
        now = time.time()
        await self.execute(
            "INSERT OR REPLACE INTO cache (key, payload, engine, kind, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (key, json.dumps(payload, default=str), engine, kind, now, now + ttl),
        )

    async def cache_stats(self) -> Dict[str, Any]:
        row = await self.fetchone(
            "SELECT COUNT(*) AS entries, COALESCE(SUM(expires_at > ?), 0) AS live FROM cache",
            (time.time(),),
        )
        return {"entries": row.get("entries", 0) if row else 0,
                "live": row.get("live", 0) if row else 0}

    async def cache_clear(self) -> int:
        await self.execute("DELETE FROM cache")
        row = await self.fetchone("SELECT changes() AS n")
        return int(row.get("n", 0)) if row else 0

    # ----------------------------------------------------------------- api keys
    async def key_create(self, key_id: str, name: str, key_hash: str, prefix: str,
                         role: str, scopes: List[str], rate_limit_rpm: int) -> None:
        await self.execute(
            "INSERT INTO api_keys (id, name, key_hash, key_prefix, role, scopes,"
            " rate_limit_rpm, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key_id, name, key_hash, prefix, role, json.dumps(scopes),
             rate_limit_rpm, time.time()),
        )

    async def key_get_by_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0", (key_hash,)
        )
        return self._key_row(row)

    async def key_get(self, key_id: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        return self._key_row(row)

    async def key_list(self, include_revoked: bool = False) -> List[Dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT * FROM api_keys" + ("" if include_revoked else " WHERE revoked = 0")
            + " ORDER BY created_at DESC"
        )
        return [
            out
            for out in (self._key_row(r) for r in rows if r is not None)
            if out is not None
        ]

    async def key_revoke(self, key_id: str) -> bool:
        await self.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
        return True

    async def key_touch(self, key_id: str) -> None:
        await self.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                           (time.time(), key_id))

    @staticmethod
    def _key_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        row["scopes"] = json.loads(row.get("scopes") or "[]")
        row["revoked"] = bool(row.get("revoked"))
        return row

    # ------------------------------------------------------------------- usage
    async def usage_add(self, *, key_id: Optional[str], endpoint: str,
                        engine: Optional[str] = None, query: Optional[str] = None,
                        status: int = 200, latency_ms: float = 0.0,
                        tokens_in: int = 0, tokens_out: int = 0,
                        cached: bool = False) -> None:
        await self.execute(
            "INSERT INTO usage (ts, key_id, endpoint, engine, query, status, latency_ms,"
            " tokens_in, tokens_out, cached) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), key_id, endpoint, engine, query, status, latency_ms,
             tokens_in, tokens_out, int(cached)),
        )

    async def usage_summary(self, key_id: Optional[str] = None,
                            since: float = 0.0) -> Dict[str, Any]:
        where = " WHERE ts >= ?"
        params: tuple = (since,)
        if key_id:
            where += " AND key_id = ?"
            params += (key_id,)
        total = await self.fetchone(
            f"SELECT COUNT(*) AS n, SUM(tokens_in) AS ti, SUM(tokens_out) AS to_,"
            f" SUM(cached) AS c FROM usage{where}",
            params,
        )
        by_endpoint = await self.fetchall(
            f"SELECT endpoint, COUNT(*) AS n FROM usage{where} GROUP BY endpoint"
            f" ORDER BY n DESC",
            params,
        )
        return {
            "requests": total["n"] if total else 0,
            "tokens_in": total["ti"] if total else 0,
            "tokens_out": total["to_"] if total else 0,
            "cached": total["c"] if total else 0,
            "by_endpoint": by_endpoint,
        }

    # ------------------------------------------------------------------ cookies
    async def cookie_load_all(self) -> Dict[str, Dict[str, str]]:
        """Load all non-expired cookies grouped by engine."""
        rows = await self.fetchall(
            "SELECT engine, name, value FROM cookies WHERE expires_at = 0 OR expires_at > ?",
            (time.time(),),
        )
        result: Dict[str, Dict[str, str]] = {}
        for row in rows:
            eng = row["engine"]
            if eng not in result:
                result[eng] = {}
            result[eng][row["name"]] = row["value"]
        return result

    async def cookie_save(self, engine: str, cookies: Dict[str, str],
                          ttl: int = 86400) -> None:
        """Save cookies for an engine with a TTL."""
        now = time.time()
        expires = now + ttl
        for name, value in cookies.items():
            await self.execute(
                "INSERT OR REPLACE INTO cookies (engine, name, value, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (engine, name, value, now, expires),
            )

    async def cookie_clear(self, engine: Optional[str] = None) -> int:
        """Delete cookies for an engine or all engines."""
        if engine:
            await self.execute("DELETE FROM cookies WHERE engine = ?", (engine,))
        else:
            await self.execute("DELETE FROM cookies")
        row = await self.fetchone("SELECT changes() AS n")
        return int(row.get("n", 0)) if row else 0

    # ------------------------------------------------------------- proxy costs
    async def proxy_cost_add(self, *, proxy_url: str, latency_ms: float,
                             status: int, cost: float, engine: Optional[str] = None,
                             success: bool = True) -> None:
        await self.execute(
            "INSERT INTO proxy_costs (proxy_url, request_ts, latency_ms, status,"
            " cost, engine, success) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (proxy_url, time.time(), latency_ms, status, cost, engine, int(success)),
        )

    async def proxy_cost_summary(self, proxy_url: Optional[str] = None,
                                 since: float = 0.0) -> Dict[str, Any]:
        where = " WHERE request_ts >= ?"
        params: tuple = (since,)
        if proxy_url:
            where += " AND proxy_url = ?"
            params += (proxy_url,)
        total = await self.fetchone(
            f"SELECT COUNT(*) AS n, SUM(cost) AS total_cost,"
            f" AVG(latency_ms) AS avg_latency,"
            f" SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes"
            f" FROM proxy_costs{where}",
            params,
        )
        by_proxy = await self.fetchall(
            f"SELECT proxy_url, COUNT(*) AS n, SUM(cost) AS total_cost,"
            f" AVG(latency_ms) AS avg_latency"
            f" FROM proxy_costs{where} GROUP BY proxy_url ORDER BY n DESC",
            params,
        )
        return {
            "total_requests": total["n"] if total else 0,
            "total_cost": round(total["total_cost"] or 0, 6) if total else 0,
            "avg_latency_ms": round(total["avg_latency"] or 0, 1) if total else 0,
            "successes": total["successes"] if total else 0,
            "by_proxy": by_proxy,
        }

    # ---------------------------------------------------- tos acknowledgments
    async def tos_ack_create(self, ack: Dict[str, Any]) -> None:
        """Persist a ToS acknowledgment record (legal record-keeping)."""
        await self.execute(
            "INSERT OR REPLACE INTO tos_acknowledgments"
            " (id, user_id, engine, tos_version, ip_address, user_agent, acknowledged_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ack.get("id"),
                ack.get("user_id"),
                ack.get("engine"),
                ack.get("tos_version"),
                ack.get("ip_address"),
                ack.get("user_agent"),
                ack.get("acknowledged_at", time.time()),
            ),
        )

    async def tos_ack_get(self, user_id: str, engine: str) -> Optional[Dict[str, Any]]:
        """Return the latest ToS acknowledgment for a user+engine, if any."""
        row = await self.fetchone(
            "SELECT * FROM tos_acknowledgments WHERE user_id = ? AND engine = ?"
            " ORDER BY acknowledged_at DESC LIMIT 1",
            (user_id, engine),
        )
        return dict(row) if row else None

    # ------------------------------------------------------------- role permissions (RBAC)
    async def rbac_get_permissions(self, identity: str) -> List[str]:
        """Get all permissions for an identity."""
        rows = await self.fetchall(
            "SELECT permission FROM role_permissions WHERE identity = ?",
            (identity,),
        )
        return [row["permission"] for row in rows]

    async def rbac_grant(self, identity: str, permission: str, granted_by: Optional[str] = None) -> None:
        """Grant a permission to an identity."""
        await self.execute(
            "INSERT OR IGNORE INTO role_permissions (identity, permission, granted_at, granted_by)"
            " VALUES (?, ?, ?, ?)",
            (identity, permission, time.time(), granted_by),
        )

    async def rbac_revoke(self, identity: str, permission: str) -> None:
        """Revoke a permission from an identity."""
        await self.execute(
            "DELETE FROM role_permissions WHERE identity = ? AND permission = ?",
            (identity, permission),
        )

    async def rbac_set_role(self, identity: str, role: str, granted_by: Optional[str] = None) -> None:
        """Replace all permissions for an identity with a role template."""
        await self.execute("DELETE FROM role_permissions WHERE identity = ?", (identity,))
        perms = ROLE_TEMPLATES.get(role, set())
        now = time.time()
        for perm in perms:
            await self.execute(
                "INSERT INTO role_permissions (identity, permission, granted_at, granted_by)"
                " VALUES (?, ?, ?, ?)",
                (identity, perm, now, granted_by),
            )

    async def rbac_list_by_permission(self, permission: str) -> List[str]:
        """List all identities with a specific permission."""
        rows = await self.fetchall(
            "SELECT identity FROM role_permissions WHERE permission = ?",
            (permission,),
        )
        return [row["identity"] for row in rows]

    # ------------------------------------------------------------- sessions (JWT revocation)
    async def session_create(self, jti: str, key_id: str, expires_at: float,
                            user_agent: Optional[str] = None,
                            ip_address: Optional[str] = None) -> None:
        """Create a new session record for JWT revocation tracking."""
        await self.execute(
            "INSERT INTO sessions (jti, key_id, expires_at, created_at, user_agent, ip_address)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (jti, key_id, expires_at, time.time(), user_agent, ip_address),
        )

    async def session_revoke(self, jti: str) -> None:
        """Revoke a session by JTI."""
        await self.execute("UPDATE sessions SET revoked = 1 WHERE jti = ?", (jti,))

    async def session_is_valid(self, jti: str) -> bool:
        """Check if a session is still valid (not revoked and not expired)."""
        row = await self.fetchone(
            "SELECT revoked, expires_at FROM sessions WHERE jti = ?",
            (jti,),
        )
        if row is None:
            return False
        if row["revoked"]:
            return False
        if time.time() > row["expires_at"]:
            return False
        return True

    async def session_cleanup(self) -> int:
        """Remove expired sessions. Returns count of deleted rows."""
        await self.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        row = await self.fetchone("SELECT changes() AS n")
        return int(row.get("n", 0)) if row else 0

    async def session_list_for_key(self, key_id: str) -> List[Dict[str, Any]]:
        """List all sessions for an API key."""
        rows = await self.fetchall(
            "SELECT * FROM sessions WHERE key_id = ? ORDER BY created_at DESC",
            (key_id,),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- OIDC identities
    async def oidc_get(self, provider: str, subject: str) -> Optional[Dict[str, Any]]:
        """Get OIDC identity by provider + subject."""
        row = await self.fetchone(
            "SELECT * FROM oidc_identities WHERE provider = ? AND subject = ?",
            (provider, subject),
        )
        return dict(row) if row else None

    async def oidc_create(self, identity_id: str, provider: str, subject: str,
                          email: Optional[str] = None, name: Optional[str] = None) -> None:
        """Create a new OIDC identity."""
        now = time.time()
        await self.execute(
            "INSERT INTO oidc_identities (id, provider, subject, email, name, created_at, last_login)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (identity_id, provider, subject, email, name, now, now),
        )

    async def oidc_update_last_login(self, identity_id: str) -> None:
        """Update last login timestamp."""
        await self.execute(
            "UPDATE oidc_identities SET last_login = ? WHERE id = ?",
            (time.time(), identity_id),
        )

    async def oidc_link_api_key(self, identity_id: str, key_id: str) -> None:
        """Link an OIDC identity to an API key."""
        await self.execute(
            "INSERT OR IGNORE INTO oidc_api_keys (identity_id, key_id, linked_at)"
            " VALUES (?, ?, ?)",
            (identity_id, key_id, time.time()),
        )
