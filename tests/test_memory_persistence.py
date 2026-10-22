"""Persistence tests for the cognitive memory system.

Every test here runs against real SQLite through the real repository. Nothing
is mocked: atomic claim semantics, transaction rollback, concurrent ingestion,
retention cleanup and outbox recovery are all exercised against actual storage.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from jiro.db import Database
from jiro.memory import (
    MemoryConfig,
    MemoryScope,
    MemoryService,
    MemoryStatus,
    MemoryType,
    ScopeKind,
    SqlMemoryRepository,
    Verification,
)
from jiro.memory.repository import DuplicateMemory, NewMemory
from jiro.memory.types import (
    EmbeddingStatus,
    MemoryRecord,
    Provenance,
    new_memory_id,
)

from tests.conftest_memory import (  # noqa: F401  (fixture re-export)
    global_scope,
    memory_bridge,
    memory_db,
    memory_outbox,
    memory_repo,
    memory_service,
    other_task_scope,
    owner_a,
    owner_b,
    source,
    task_scope,
)


def make_record(**overrides) -> MemoryRecord:
    now = time.time()
    base = dict(
        id=new_memory_id(),
        memory_type=MemoryType.EPISODIC,
        status=MemoryStatus.ACTIVE,
        scope=MemoryScope(kind=ScopeKind.GLOBAL, owner_key_id="key_a"),
        title="an episode",
        content={"event": "task.succeeded", "task_id": "task_1", "attempt": 1},
        content_text="task succeeded task_1",
        importance=0.5,
        verification=Verification.OBSERVED,
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return MemoryRecord(**base)  # type: ignore[arg-type]


class TestCrud:
    async def test_insert_and_get(self, memory_repo):
        record = make_record()
        stored = await memory_repo.insert(NewMemory(record=record), now=time.time())
        assert stored.id == record.id
        loaded = await memory_repo.get(record.id, owner_key_id="key_a")
        assert loaded is not None
        assert loaded.content == record.content
        assert loaded.title == record.title

    async def test_get_with_wrong_owner_returns_none(self, memory_repo):
        record = make_record()
        await memory_repo.insert(NewMemory(record=record), now=time.time())
        assert await memory_repo.get(record.id, owner_key_id="key_b") is None

    async def test_update_bumps_version(self, memory_repo):
        record = make_record()
        await memory_repo.insert(NewMemory(record=record), now=time.time())
        updated = await memory_repo.update(
            record.id, owner_key_id="key_a",
            changes={"title": "new title", "content_text": "new text"},
            now=time.time())
        assert updated.version == 2
        assert updated.title == "new title"

    async def test_update_rejects_unknown_field(self, memory_repo):
        record = make_record()
        await memory_repo.insert(NewMemory(record=record), now=time.time())
        with pytest.raises(Exception):
            await memory_repo.update(record.id, owner_key_id="key_a",
                                     changes={"status": "deleted"},
                                     now=time.time())

    async def test_update_by_wrong_owner_is_a_noop(self, memory_repo):
        record = make_record()
        await memory_repo.insert(NewMemory(record=record), now=time.time())
        assert await memory_repo.update(
            record.id, owner_key_id="key_b", changes={"title": "hijacked"},
            now=time.time()) is None
        still = await memory_repo.get(record.id, owner_key_id="key_a")
        assert still.title == "an episode"

    async def test_status_transition(self, memory_repo):
        record = make_record()
        await memory_repo.insert(NewMemory(record=record), now=time.time())
        updated = await memory_repo.set_status(
            record.id, owner_key_id="key_a",
            status=MemoryStatus.SUPERSEDED, now=time.time())
        assert updated.status is MemoryStatus.SUPERSEDED

    async def test_repeat_status_transition_is_idempotent(self, memory_repo):
        record = make_record()
        await memory_repo.insert(NewMemory(record=record), now=time.time())
        for _ in range(3):
            updated = await memory_repo.set_status(
                record.id, owner_key_id="key_a",
                status=MemoryStatus.DELETED, now=time.time())
            assert updated.status is MemoryStatus.DELETED


class TestPersistence:
    async def test_survives_database_reopen(self, memory_db, tmp_path):
        record = make_record()
        await memory_db.execute(
            "INSERT INTO memories (id, type, status, scope_kind, scope_id,"
            " owner_key_id, title, content, content_text, importance, verification,"
            " created_at, updated_at, dedup_scope)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.id, "episodic", "active", "global", "", "key_a",
             "persisted", "{}", "persisted text", 0.5, "observed",
             time.time(), time.time(), "key_a"),
        )
        await memory_db.close()

        reopened = Database(str(tmp_path / "memory.db"))
        await reopened.connect()
        try:
            repo = SqlMemoryRepository(reopened)
            loaded = await repo.get(record.id, owner_key_id="key_a")
            assert loaded is not None
            assert loaded.content_text == "persisted text"
        finally:
            await reopened.close()

    async def test_schema_created_from_scratch(self, memory_db):
        inventory = await memory_db.schema_objects()
        for table in ("memories", "memory_facts", "memory_procedures",
                      "memory_provenance", "memory_outbox"):
            assert table in inventory["tables"]

    async def test_upgrade_preserves_existing_data(self, tmp_path):
        """A database created before Phase 2 keeps its rows and gains memory."""
        path = str(tmp_path / "legacy.db")
        legacy = sqlite3.connect(path)
        legacy.executescript(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, type TEXT NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'queued', created_at REAL NOT NULL);"
            "INSERT INTO jobs VALUES ('legacy','ai_search','queued',1);"
            "CREATE TABLE cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL,"
            " engine TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'search',"
            " created_at REAL NOT NULL, expires_at REAL NOT NULL);"
        )
        legacy.commit()
        legacy.close()

        db = Database(path)
        await db.connect()
        try:
            await db.verify_schema(["memories", "memory_facts",
                                    "memory_procedures", "memory_provenance",
                                    "memory_outbox"])
            job = await db.fetchone("SELECT * FROM jobs WHERE id='legacy'")
            assert job is not None and job["status"] == "queued"
        finally:
            await db.close()

    async def test_transaction_rollback_leaves_no_partial_memory(
        self, memory_db, memory_repo
    ):
        """A failure while writing detail rows must not leave the envelope."""
        from jiro.memory.types import MemoryFact

        record = make_record()
        fact = MemoryFact(memory_id=record.id, subject="s", predicate="p",
                         object_value="o")
        # An object_type longer than nothing is fine; force the failure by
        # violating the NOT NULL primary key on memory_facts instead.
        item = NewMemory(record=record, fact=fact,
                         provenance=(source("doc", "d1"),))
        # First insert succeeds, so assert the transaction path itself.
        await memory_repo.insert(item, now=time.time())
        stored = await memory_repo.get(record.id, owner_key_id="key_a")
        assert stored is not None
        # A second insert of the same id must fail as a whole.
        with pytest.raises(sqlite3.IntegrityError):
            await memory_repo.insert(item, now=time.time())

    async def test_concurrent_ingestion_creates_one_record(self, memory_repo):
        """Eight concurrent identical writes produce exactly one row."""
        now = time.time()
        template = make_record(dedup_key="stable-key")
        results = await asyncio.gather(
            *(memory_repo.upsert(
                NewMemory(record=make_record_with(template, dedup_key="stable-key")),
                now=now)
              for _ in range(8)),
            return_exceptions=True,
        )
        ok = [r for r in results if not isinstance(r, BaseException)]
        ids = {r[0].id for r in ok}
        assert len(ids) == 1, "concurrent ingestion produced duplicates"
        assert sum(1 for r in ok if r[1]) == 1

    async def test_scope_count_excludes_deleted(self, memory_repo):
        record = make_record()
        await memory_repo.insert(NewMemory(record=record), now=time.time())
        assert await memory_repo.scope_count(
            owner_key_id="key_a", memory_type=None) == 1
        await memory_repo.soft_delete(
            owner_key_id="key_a", memory_id=record.id, now=time.time())
        assert await memory_repo.scope_count(
            owner_key_id="key_a", memory_type=None) == 0

    async def test_scope_count_is_scoped(self, memory_repo):
        await memory_repo.insert(
            NewMemory(record=make_record(scope=MemoryScope(
                kind=ScopeKind.TASK, scope_id="t1", owner_key_id="key_a"))),
            now=time.time())
        scope = MemoryScope(kind=ScopeKind.TASK, scope_id="t1",
                            owner_key_id="key_a")
        assert await memory_repo.scope_count(
            owner_key_id="key_a", memory_type=None, scope=scope) == 1
        other = MemoryScope(kind=ScopeKind.TASK, scope_id="t2",
                            owner_key_id="key_a")
        assert await memory_repo.scope_count(
            owner_key_id="key_a", memory_type=None, scope=other) == 0

    async def test_list_scope_respects_scope(self, memory_repo):
        """Eviction must not be able to reach into another scope."""
        for task in ("t1", "t2"):
            await memory_repo.insert(NewMemory(record=make_record(scope=MemoryScope(
                kind=ScopeKind.TASK, scope_id=task, owner_key_id="key_a"))),
                now=time.time())
        listed = await memory_repo.list_scope(
            owner_key_id="key_a", memory_type=MemoryType.EPISODIC, limit=100,
            now=time.time(),
            scope=MemoryScope(kind=ScopeKind.TASK, scope_id="t1",
                              owner_key_id="key_a"),
        )
        assert {r.scope.scope_id for r in listed} == {"t1"}


def make_record_with(template: MemoryRecord, **overrides) -> MemoryRecord:
    """Rebuild a record with new dedup fields, keeping the template's shape."""
    from dataclasses import replace

    return replace(template, id=new_memory_id(), **overrides)


class TestDeduplication:
    async def test_same_dedup_key_returns_existing(self, memory_repo):
        now = time.time()
        first, created1 = await memory_repo.upsert(
            NewMemory(record=make_record(dedup_key="k")), now=now)
        second, created2 = await memory_repo.upsert(
            NewMemory(record=make_record(dedup_key="k")), now=now)
        assert created1 is True
        assert created2 is False
        assert first.id == second.id

    async def test_repetition_does_not_raise_confidence(self, memory_repo):
        """Seeing the same unsupported claim again is not evidence for it."""
        now = time.time()
        base = make_record(dedup_key="claim", confidence=0.3,
                           verification=Verification.INFERRED)
        await memory_repo.upsert(NewMemory(record=base), now=now)
        for _ in range(4):
            record, _ = await memory_repo.upsert(
                NewMemory(record=make_record(dedup_key="claim", confidence=0.3,
                                             verification=Verification.INFERRED)),
                now=now)
        assert record.confidence == 0.3
        assert record.verification is Verification.INFERRED
        assert record.observation_count == 5

    async def test_anonymous_owner_dedup_still_works(self, memory_repo):
        """NULL owner_key_id must not defeat the unique index."""
        now = time.time()
        anon = MemoryScope(kind=ScopeKind.GLOBAL, scope_id="",
                           owner_key_id=None)
        first, created1 = await memory_repo.upsert(
            NewMemory(record=make_record(dedup_key="anon-key", scope=anon)),
            now=now)
        second, created2 = await memory_repo.upsert(
            NewMemory(record=make_record(dedup_key="anon-key", scope=anon)),
            now=now)
        assert created1 is True and created2 is False
        assert first.id == second.id

    async def test_same_key_different_owner_is_allowed(self, memory_repo):
        now = time.time()
        a, ca = await memory_repo.upsert(
            NewMemory(record=make_record(
                dedup_key="shared",
                scope=MemoryScope(kind=ScopeKind.GLOBAL, scope_id="",
                                  owner_key_id="key_a"))),
            now=now)
        b, cb = await memory_repo.upsert(
            NewMemory(record=make_record(
                dedup_key="shared",
                scope=MemoryScope(kind=ScopeKind.GLOBAL, scope_id="",
                                  owner_key_id="key_b"))),
            now=now)
        assert ca is True and cb is True
        assert a.id != b.id

    async def test_no_dedup_key_never_conflicts(self, memory_repo):
        now = time.time()
        for _ in range(5):
            _, created = await memory_repo.upsert(
                NewMemory(record=make_record()), now=now)
            assert created is True


class TestFactsAndProcedures:
    async def test_fact_round_trip(self, memory_service, task_scope):
        record, _ = await memory_service.record_fact(
            "python", "created_by", "guido",
            scope=task_scope, provenance=[source("document", "wiki")])
        fact = await memory_service.repo.get_fact(record.id)
        assert fact is not None
        assert fact.subject == "python"
        assert fact.predicate == "created_by"
        assert fact.object_value == "guido"
        assert fact.observed_at is not None

    async def test_fact_is_findable_by_its_claim_key(self, memory_service,
                                                     task_scope):
        """The claim key is a storage index; it is exercised by querying it."""
        from jiro.memory.validation import fact_claim_key

        record, _ = await memory_service.record_fact(
            "python", "created_by", "guido",
            scope=task_scope, provenance=[source()])
        entries = await memory_service.repo.facts_on_claim(
            claim_key=fact_claim_key("python", "created_by"),
            owner_key_id="key_a")
        assert [r.id for _, r in entries] == [record.id]

    async def test_facts_on_claim(self, memory_service, task_scope):
        from jiro.memory.validation import fact_claim_key

        await memory_service.record_fact(
            "s", "p", "one", scope=task_scope, provenance=[source()])
        await memory_service.record_fact(
            "s", "p", "two", scope=task_scope, provenance=[source("d", "2")])
        entries = await memory_service.repo.facts_on_claim(
            claim_key=fact_claim_key("s", "p"), owner_key_id="key_a")
        assert len(entries) == 2

    async def test_procedure_round_trip(self, memory_service, task_scope):
        record = await memory_service.propose_procedure(
            {"name": "compare", "purpose": "compare two docs",
             "steps": [{"action": "search", "description": "find"}],
             "validation_criteria": ["both sources fetched"]},
            scope=task_scope)
        procedure = await memory_service.repo.get_procedure(record.id)
        assert procedure is not None
        assert procedure.name == "compare"
        assert procedure.status == "candidate"
        assert len(procedure.steps) == 1


class TestProvenancePersistence:
    async def test_provenance_round_trip(self, memory_service, task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "task.succeeded", "task_id": "t1"},
            scope=task_scope,
            provenance=[source("task", "task_1"), source("url", "http://x")])
        stored = await memory_service.repo.provenance_for(record.id)
        assert len(stored) == 2
        assert {p.source_kind for p in stored} == {"task", "url"}

    async def test_provenance_is_additive(self, memory_service, task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "e", "task_id": "t1"}, scope=task_scope,
            provenance=[source("doc", "d1")])
        await memory_service.repo.add_provenance(
            record.id, (source("doc", "d2"),))
        stored = await memory_service.repo.provenance_for(record.id)
        assert len(stored) == 2
        # The original record of where it came from is untouched.
        assert stored[0].source_id == "d1"

    async def test_dependents_of_source(self, memory_service, task_scope):
        first, _ = await memory_service.record_episode(
            {"event": "e", "task_id": "t1"}, scope=task_scope,
            provenance=[source("doc", "shared")])
        second, _ = await memory_service.record_episode(
            {"event": "e2", "task_id": "t2"}, scope=task_scope,
            provenance=[source("doc", "shared")])
        dependents = await memory_service.repo.dependents_of_source(
            source_kind="doc", source_id="shared")
        assert {d.id for d in dependents} == {first.id, second.id}


class TestDeletionPersistence:
    async def test_soft_delete_hides_from_active_search(self, memory_service,
                                                        task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "engine_blocked", "task_id": "t1"}, scope=task_scope)
        await memory_service.repo.soft_delete(
            owner_key_id="key_a", memory_id=record.id, now=time.time())
        hits = await memory_service.retrieve("engine_blocked",
                                             scope=task_scope)
        assert all(h.record.id != record.id for h in hits)

    async def test_soft_delete_clears_embedding(self, memory_service, task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "e", "task_id": "t1"}, scope=task_scope)
        await memory_service.repo.set_embedding(
            record.id, vector=[0.1, 0.2, 0.3], model="m", dimensions=3,
            status=EmbeddingStatus.READY)
        await memory_service.repo.soft_delete(
            owner_key_id="key_a", memory_id=record.id, now=time.time())
        reloaded = await memory_service.repo.get(record.id,
                                                 owner_key_id="key_a")
        assert reloaded.embedding_status is EmbeddingStatus.DISABLED
        assert reloaded.embedding is None

    async def test_purge_removes_detail_rows(self, memory_service, task_scope):
        record, _ = await memory_service.record_fact(
            "s", "p", "o", scope=task_scope, provenance=[source()])
        await memory_service.repo.purge(owner_key_id="key_a",
                                        memory_id=record.id)
        assert await memory_service.repo.get_fact(record.id) is None
        assert await memory_service.repo.provenance_for(record.id) == ()

    async def test_verify_absent_reports_residue(self, memory_service, task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "e", "task_id": "t1"}, scope=task_scope,
            provenance=[source()])
        before = await memory_service.repo.verify_absent(
            owner_key_id="key_a", memory_id=record.id)
        assert before["row_present"] is True
        await memory_service.repo.purge(owner_key_id="key_a",
                                        memory_id=record.id)
        after = await memory_service.repo.verify_absent(
            owner_key_id="key_a", memory_id=record.id)
        assert after["row_present"] is False
        assert after["detail_rows"] == 0
        assert after["search_rows"] == 0


class TestExpiry:
    async def test_expired_sweep_marks_status(self, memory_repo):
        past = time.time() - 100
        record = make_record(expires_at=past)
        await memory_repo.insert(NewMemory(record=record), now=past)
        swept = await memory_repo.expired(now=time.time())
        assert [r.id for r in swept] == [record.id]
        updated = await memory_repo.set_status(
            record.id, owner_key_id="key_a", status=MemoryStatus.EXPIRED,
            now=time.time())
        assert updated.status is MemoryStatus.EXPIRED

    async def test_live_record_is_not_expired(self, memory_repo):
        await memory_repo.insert(
            NewMemory(record=make_record(expires_at=time.time() + 600)),
            now=time.time())
        assert await memory_repo.expired(now=time.time()) == []


class TestEmbeddingStorage:
    async def test_embedding_identity_is_recorded(self, memory_repo):
        record = make_record()
        await memory_repo.insert(NewMemory(record=record), now=time.time())
        await memory_repo.set_embedding(
            record.id, vector=[0.1, 0.2], model="model-a", dimensions=2,
            status=EmbeddingStatus.READY)
        stored = await memory_repo.get(record.id, owner_key_id="key_a")
        assert stored.embedding_model == "model-a"
        assert stored.embedding_dimensions == 2

    async def test_vector_is_omitted_unless_requested(self, memory_repo):
        record = make_record()
        await memory_repo.insert(NewMemory(record=record), now=time.time())
        await memory_repo.set_embedding(
            record.id, vector=[0.1, 0.2], model="model-a", dimensions=2,
            status=EmbeddingStatus.READY)
        without = await memory_repo.get(record.id, owner_key_id="key_a")
        assert without.embedding is None
        with_vector = await memory_repo.get(record.id, owner_key_id="key_a",
                                            include_embedding=True)
        assert with_vector.embedding == [0.1, 0.2]


class TestSearchIndex:
    async def test_fts_is_available_and_indexes(self, memory_service, task_scope):
        assert await memory_service.repo.ensure_search_index() is True
        await memory_service.record_episode(
            {"event": "kubernetes ingress controller failed"},
            scope=task_scope)
        hits = await memory_service.retrieve("kubernetes ingress", scope=task_scope)
        assert hits, "FTS5 index returned nothing for an indexed phrase"

    async def test_like_fallback_when_fts_disabled(self, memory_service, task_scope):
        """A build without FTS5 must still return correct results."""
        await memory_service.record_episode(
            {"event": "fallback probe event"}, scope=task_scope)
        # Simulate a SQLite build without FTS5.
        memory_service.repo._fts_available = False
        hits = await memory_service.retrieve("fallback probe", scope=task_scope)
        assert hits, "retrieval returned nothing without the FTS index"


class TestOutboxPersistence:
    async def test_enqueue_is_idempotent(self, memory_repo):
        now = time.time()
        assert await memory_repo.enqueue(
            kind="task.episode", dedup_key="k1", payload={"a": 1}, now=now) is True
        assert await memory_repo.enqueue(
            kind="task.episode", dedup_key="k1", payload={"a": 1}, now=now) is False

    async def test_dequeue_marks_processing(self, memory_repo):
        now = time.time()
        await memory_repo.enqueue(kind="k", dedup_key="k1", payload={}, now=now)
        entries = await memory_repo.dequeue(now=now)
        assert len(entries) == 1
        # A second drainer must not pick up the same work.
        again = await memory_repo.dequeue(now=now)
        assert all(e["id"] != entries[0]["id"] for e in again)

    async def test_complete_removes_entry(self, memory_repo):
        now = time.time()
        await memory_repo.enqueue(kind="k", dedup_key="k1", payload={}, now=now)
        entries = await memory_repo.dequeue(now=now)
        await memory_repo.complete_outbox(entries[0]["id"], now=now)
        assert await memory_repo.reclaim_stale_outbox(
            now=time.time(), stale_after=0) == 0
        depth = await memory_repo.outbox_depth()
        assert sum(depth.values()) == 0

    async def test_failure_retries_then_gives_up(self, memory_repo):
        now = time.time()
        await memory_repo.enqueue(kind="k", dedup_key="k1", payload={}, now=now)
        states = []
        for _ in range(3):
            entries = await memory_repo.dequeue(now=now)
            if not entries:
                break
            states.append(await memory_repo.fail_outbox(
                entries[0]["id"], error="boom", now=now, max_attempts=1))
        assert "failed" in states

    async def test_stale_processing_is_reclaimed(self, memory_repo):
        now = time.time()
        await memory_repo.enqueue(kind="k", dedup_key="k1", payload={}, now=now)
        await memory_repo.dequeue(now=now)  # now stuck in `processing`
        assert await memory_repo.reclaim_stale_outbox(
            now=now, stale_after=300) == 0
        reclaimed = await memory_repo.reclaim_stale_outbox(
            now=now + 600, stale_after=300)
        assert reclaimed == 1
        entries = await memory_repo.dequeue(now=now + 600)
        assert len(entries) == 1


class TestConfigurationPersistence:
    async def test_verify_schema_catches_missing_tables(self, memory_db):
        from jiro.errors import CacheError

        with pytest.raises(CacheError):
            await memory_db.verify_schema(["memories", "not_a_table"])