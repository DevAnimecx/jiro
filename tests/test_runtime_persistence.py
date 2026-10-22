"""Persistence tests for the durable task runtime.

Every test here runs against a real SQLite database through the real
:class:`~jiro.runtime.sqlite_repo.SqlTaskRepository`. Nothing is mocked: the
claim races, the lease guards and the migration-from-legacy-schema check are
all exercised against actual storage.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from jiro.db import Database
from jiro.runtime.errors import InvalidTransition
from jiro.runtime.repository import DuplicateTask, NewTask
from jiro.runtime.sqlite_repo import SqlTaskRepository
from jiro.runtime.types import EventType, TaskEvent, TaskLimits, TaskState
from jiro.runtime.validation import Field, TaskSchema

from tests.conftest_runtime import repo, runtime_db  # noqa: F401  (fixture re-export)


def _new_task(
    task_id="task_1", type_name="test_task", owner="acct_a", idempotency_key=None, input_payload=None
) -> NewTask:
    return NewTask(
        id=task_id,
        type=type_name,
        owner_key_id=owner,
        idempotency_key=idempotency_key,
        input=input_payload if input_payload is not None else {"value": "x"},
        limits=TaskLimits(max_attempts=3, attempt_timeout=30.0),
        created_at=time.time(),
    )


class TestTaskPersistence:
    async def test_insert_and_retrieve(self, repo):
        now = time.time()
        stored = await repo.insert(_new_task(), now=now)
        loaded = await repo.get("task_1")
        assert loaded is not None
        assert loaded.id == stored.id
        assert loaded.type == "test_task"
        assert loaded.state is TaskState.QUEUED
        assert loaded.input == {"value": "x"}
        assert loaded.attempts == 0
        assert loaded.completed_at is None

    async def test_task_survives_database_reopen(self, runtime_db, tmp_path):
        repo = SqlTaskRepository(runtime_db)
        await repo.insert(_new_task(), now=time.time())
        await runtime_db.close()

        # A genuinely new Database instance against the same file.
        reopened = Database(str(tmp_path / "jiro.db"))
        await reopened.connect()
        try:
            record = await SqlTaskRepository(reopened).get("task_1")
            assert record is not None
            assert record.state is TaskState.QUEUED
        finally:
            await reopened.close()

    async def test_limits_round_trip(self, repo):
        limits = TaskLimits(
            max_attempts=7,
            attempt_timeout=12.5,
            deadline=999.0,
            retry_backoff_base=2.0,
            retry_backoff_max=30.0,
            retry_jitter_ratio=0.35,
            max_result_bytes=4096,
        )
        task = NewTask(
            id="t",
            type="x",
            owner_key_id=None,
            idempotency_key=None,
            input={},
            limits=limits,
            created_at=0.0,
        )
        await repo.insert(task, now=1.0)
        loaded = await repo.get("t")
        assert loaded.limits == limits

    async def test_missing_task_returns_none(self, repo):
        assert await repo.get("nope") is None

    async def test_idempotent_insert_rejected(self, repo):
        await repo.insert(_new_task(idempotency_key="k1"), now=time.time())
        with pytest.raises(DuplicateTask) as exc:
            await repo.insert(_new_task(task_id="task_2", idempotency_key="k1"), now=time.time())
        assert exc.value.task_id == "task_1"

    async def test_same_key_different_owner_allowed(self, repo):
        await repo.insert(
            _new_task(task_id="t1", owner="acct_a", idempotency_key="k"), now=time.time()
        )
        await repo.insert(
            _new_task(task_id="t2", owner="acct_b", idempotency_key="k"), now=time.time()
        )
        assert await repo.get("t2") is not None

    async def test_null_idempotency_keys_do_not_collide(self, repo):
        for index in range(3):
            await repo.insert(_new_task(task_id=f"t{index}"), now=time.time())
        assert await repo.count_for_owner("acct_a") == 3

    async def test_find_by_idempotency_key(self, repo):
        await repo.insert(_new_task(idempotency_key="abc"), now=time.time())
        found = await repo.find_by_idempotency_key(owner_key_id="acct_a", idempotency_key="abc")
        assert found is not None and found.id == "task_1"
        missing = await repo.find_by_idempotency_key(owner_key_id="acct_a", idempotency_key="zzz")
        assert missing is None


class TestAtomicClaim:
    async def test_claim_moves_to_running_and_increments_attempts(self, repo):
        await repo.insert(_new_task(), now=time.time())
        claimed = await repo.claim(worker_id="w1", now=time.time(), lease_ttl=60)
        assert claimed is not None
        assert claimed.state is TaskState.RUNNING
        assert claimed.attempts == 1
        assert claimed.lease_owner == "w1"
        assert claimed.started_at is not None

    async def test_second_claim_gets_nothing(self, repo):
        await repo.insert(_new_task(), now=time.time())
        assert await repo.claim(worker_id="w1", now=time.time(), lease_ttl=60)
        assert await repo.claim(worker_id="w2", now=time.time(), lease_ttl=60) is None

    async def test_empty_queue_returns_none(self, repo):
        assert await repo.claim(worker_id="w1", now=time.time(), lease_ttl=60) is None

    async def test_concurrent_claims_never_double_execute(self, repo):
        """Ten tasks, eight workers, one claim pass: every task claimed once."""
        for index in range(10):
            await repo.insert(_new_task(task_id=f"task_{index}"), now=time.time())

        claimed = []

        async def worker(index: int) -> None:
            while True:
                record = await repo.claim(worker_id=f"w{index}", now=time.time(), lease_ttl=60)
                if record is None:
                    return
                claimed.append(record.id)

        await asyncio.gather(*(worker(i) for i in range(8)))
        assert sorted(claimed) == sorted(f"task_{i}" for i in range(10))
        assert len(claimed) == len(set(claimed)), "a task was claimed twice"

    async def test_claim_respects_available_at(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        # Claim, then hand it back with a backoff window still open.
        assert await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        assert await repo.schedule_retry(
            task_id="task_1",
            worker_id="w1",
            available_at=now + 600,
            error_code="x",
            error_message="y",
            now=now,
        )
        assert await repo.claim(worker_id="w2", now=now, lease_ttl=60) is None
        later = await repo.claim(worker_id="w2", now=now + 601, lease_ttl=60)
        assert later is not None and later.attempts == 2

    async def test_claim_skips_cancel_requested_task(self, repo):
        await repo.insert(_new_task(), now=time.time())
        await repo.request_cancel("task_1")
        assert await repo.claim(worker_id="w1", now=time.time(), lease_ttl=60) is None

    async def test_claim_skips_expired_deadline(self, repo):
        task = NewTask(
            id="task_1",
            type="x",
            owner_key_id=None,
            idempotency_key=None,
            input={},
            limits=TaskLimits(max_attempts=3, attempt_timeout=30.0, deadline=time.time() - 10),
            created_at=time.time(),
        )
        await repo.insert(task, now=time.time())
        assert await repo.claim(worker_id="w1", now=time.time(), lease_ttl=60) is None

    async def test_claim_is_fifo(self, repo):
        now = time.time()
        for index in range(3):
            await repo.insert(_new_task(task_id=f"task_{index}"), now=now + index)
        first = await repo.claim(worker_id="w1", now=now + 10, lease_ttl=60)
        assert first.id == "task_0"


class TestTerminalWrites:
    async def test_complete_success(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        assert await repo.complete_success(
            task_id="task_1", worker_id="w1", result={"out": 1}, now=now
        )
        record = await repo.get("task_1")
        assert record.state is TaskState.SUCCEEDED
        assert record.result == {"out": 1}
        assert record.completed_at is not None
        assert record.lease_owner is None

    async def test_duplicate_completion_does_not_corrupt(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        await repo.complete_success(
            task_id="task_1", worker_id="w1", result={"first": True}, now=now
        )
        # Second completion from the same worker must not overwrite the result.
        await repo.complete_success(
            task_id="task_1", worker_id="w1", result={"second": True}, now=now
        )
        record = await repo.get("task_1")
        assert record.result == {"first": True}
        assert record.state is TaskState.SUCCEEDED

    async def test_completion_from_wrong_worker_is_rejected(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        # w2 never held the lease.
        assert (
            await repo.complete_success(
                task_id="task_1", worker_id="w2", result={"stolen": True}, now=now
            )
            is False
        )
        record = await repo.get("task_1")
        assert record.state is TaskState.RUNNING
        assert record.result is None

    async def test_stale_worker_cannot_overwrite_recovered_task(self, repo):
        """The core lease invariant: a revived zombie cannot corrupt state."""
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=1)
        # Lease lapses; recovery reclaims and hands to w2.
        await repo.set_state(
            task_id="task_1", state=TaskState.RETRY_WAIT, now=now + 2, reason="interrupted"
        )
        await repo.claim(worker_id="w2", now=now + 3, lease_ttl=60)
        # The zombie w1 wakes up and tries to finish.
        assert (
            await repo.complete_success(
                task_id="task_1", worker_id="w1", result={"zombie": True}, now=now + 4
            )
            is False
        )
        record = await repo.get("task_1")
        assert record.state is TaskState.RUNNING
        assert record.lease_owner == "w2"

    async def test_complete_failure(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        assert await repo.complete_failure(
            task_id="task_1", worker_id="w1", error_code="boom", error_message="exploded", now=now
        )
        record = await repo.get("task_1")
        assert record.state is TaskState.FAILED
        assert record.error_code == "boom"
        assert record.error_message == "exploded"

    async def test_schedule_retry_records_availability(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        available = now + 30
        assert await repo.schedule_retry(
            task_id="task_1",
            worker_id="w1",
            available_at=available,
            error_code="e",
            error_message="m",
            now=now,
        )
        record = await repo.get("task_1")
        assert record.state is TaskState.RETRY_WAIT
        assert record.available_at == pytest.approx(available, abs=0.001)
        assert record.lease_owner is None
        # Not terminal, so no completion timestamp.
        assert record.completed_at is None


class TestCancellationPersistence:
    async def test_mark_cancelled_from_queued(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        assert await repo.mark_cancelled(task_id="task_1", now=now, from_states=[TaskState.QUEUED])
        record = await repo.get("task_1")
        assert record.state is TaskState.CANCELLED
        assert record.completed_at is not None
        assert record.cancel_requested

    async def test_mark_cancelled_refuses_running(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        assert (
            await repo.mark_cancelled(task_id="task_1", now=now, from_states=[TaskState.QUEUED])
            is False
        )
        assert (await repo.get("task_1")).state is TaskState.RUNNING

    async def test_request_cancel_sets_flag_without_changing_state(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        assert await repo.request_cancel("task_1")
        record = await repo.get("task_1")
        # Honest: still running, because the handler has not stopped yet.
        assert record.state is TaskState.RUNNING
        assert record.cancel_requested is True

    async def test_request_cancel_is_idempotent(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.request_cancel("task_1")
        assert await repo.request_cancel("task_1") is True  # flag already set
        record = await repo.get("task_1")
        assert record.cancel_requested is True

    async def test_terminal_task_is_not_re_cancelled(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        await repo.complete_success(task_id="task_1", worker_id="w1", result={}, now=now)
        await repo.request_cancel("task_1")
        record = await repo.get("task_1")
        assert record.state is TaskState.SUCCEEDED


class TestAdministrativeTransitions:
    async def test_set_state_validates_against_lifecycle(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        await repo.complete_success(task_id="task_1", worker_id="w1", result={}, now=now)
        with pytest.raises(InvalidTransition):
            await repo.set_state(task_id="task_1", state=TaskState.RUNNING, now=now)

    async def test_set_state_moves_running_without_a_lease(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=1)
        assert await repo.set_state(
            task_id="task_1",
            state=TaskState.RETRY_WAIT,
            now=now + 5,
            reason="interrupted",
            available_at=now + 6,
        )
        record = await repo.get("task_1")
        assert record.state is TaskState.RETRY_WAIT
        assert record.lease_owner is None
        assert record.available_at == pytest.approx(now + 6, abs=0.001)

    async def test_set_state_on_missing_task(self, repo):
        assert (
            await repo.set_state(task_id="nope", state=TaskState.FAILED, now=time.time()) is False
        )

    async def test_set_state_same_state_is_a_noop(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        assert await repo.set_state(task_id="task_1", state=TaskState.QUEUED, now=now)


class TestEvents:
    async def test_event_round_trip(self, repo):
        await repo._db.execute(  # insert task first for a realistic stream
            "INSERT INTO tasks (id, type, state, created_at, updated_at)"
            " VALUES ('task_1','x','queued',1,1)"
        )
        await repo.append_event(
            TaskEvent(
                task_id="task_1",
                type=EventType.SUBMITTED,
                ts=100.0,
                attempt=1,
                metadata={"type": "x"},
            )
        )
        events = await repo.events_for("task_1")
        assert len(events) == 1
        assert events[0].type is EventType.SUBMITTED
        assert events[0].metadata == {"type": "x"}

    async def test_events_are_ordered_oldest_first(self, repo):
        await repo._db.execute(
            "INSERT INTO tasks (id, type, state, created_at, updated_at)"
            " VALUES ('task_1','x','queued',1,1)"
        )
        for index, event_type in enumerate(
            [EventType.SUBMITTED, EventType.CLAIMED, EventType.SUCCEEDED]
        ):
            await repo.append_event(TaskEvent(task_id="task_1", type=event_type, ts=float(index)))
        events = await repo.events_for("task_1")
        assert [e.type for e in events] == [
            EventType.SUBMITTED,
            EventType.CLAIMED,
            EventType.SUCCEEDED,
        ]

    async def test_event_metadata_is_sanitised_at_write(self, repo):
        await repo._db.execute(
            "INSERT INTO tasks (id, type, state, created_at, updated_at)"
            " VALUES ('task_1','x','queued',1,1)"
        )
        await repo.append_event(
            TaskEvent(
                task_id="task_1",
                type=EventType.SUBMITTED,
                ts=1.0,
                metadata={"api_key": "sk-leak-me", "payload": {"big": "data"}},
            )
        )
        event = (await repo.events_for("task_1"))[0]
        assert event.metadata["api_key"] == "[redacted]"
        assert "sk-leak-me" not in str(event.metadata)

    async def test_event_can_share_a_transaction(self, repo):
        """State change and its event must be able to land together."""
        now = time.time()
        await repo.insert(_new_task(), now=now)
        async with repo.db.transaction() as conn:
            await conn.execute(
                "UPDATE tasks SET state='cancelled', completed_at=? WHERE id=?", (now, "task_1")
            )
            await repo.append_event(
                TaskEvent(task_id="task_1", type=EventType.CANCELLED, ts=now), conn=conn
            )
        assert (await repo.get("task_1")).state is TaskState.CANCELLED
        assert len(await repo.events_for("task_1")) == 1

    async def test_prune_events(self, repo):
        await repo._db.execute(
            "INSERT INTO tasks (id, type, state, created_at, updated_at)"
            " VALUES ('task_1','x','queued',1,1)"
        )
        await repo.append_event(TaskEvent(task_id="task_1", type=EventType.SUBMITTED, ts=100.0))
        await repo.append_event(
            TaskEvent(task_id="task_1", type=EventType.SUCCEEDED, ts=time.time())
        )
        assert await repo.prune_events(older_than=1000.0) == 1
        assert len(await repo.events_for("task_1")) == 1


class TestOwnerScoping:
    async def test_list_is_scoped_to_owner(self, repo):
        now = time.time()
        for index in range(3):
            await repo.insert(_new_task(task_id=f"a{index}", owner="acct_a"), now=now)
        for index in range(2):
            await repo.insert(_new_task(task_id=f"b{index}", owner="acct_b"), now=now)
        mine = await repo.list_for_owner("acct_a")
        assert {r.id for r in mine} == {"a0", "a1", "a2"}
        assert await repo.count_for_owner("acct_a") == 3
        assert await repo.count_for_owner("acct_b") == 2

    async def test_null_owner_is_its_own_scope(self, repo):
        await repo.insert(_new_task(task_id="anon", owner=None), now=time.time())
        await repo.insert(_new_task(task_id="owned", owner="acct_a"), now=time.time())
        anon = await repo.list_for_owner(None)
        assert [r.id for r in anon] == ["anon"]

    async def test_list_filtered_by_state(self, repo):
        now = time.time()
        await repo.insert(_new_task(task_id="t1"), now=now)
        await repo.insert(_new_task(task_id="t2"), now=now)
        await repo.mark_cancelled(task_id="t2", now=now, from_states=[TaskState.QUEUED])
        queued = await repo.list_for_owner("acct_a", states=[TaskState.QUEUED])
        assert [r.id for r in queued] == ["t1"]

    async def test_queue_depth_counts_every_state(self, repo):
        now = time.time()
        await repo.insert(_new_task(task_id="t1"), now=now)
        await repo.insert(_new_task(task_id="t2"), now=now)
        await repo.insert(_new_task(task_id="t3"), now=now)
        await repo.mark_cancelled(task_id="t3", now=now, from_states=[TaskState.QUEUED])
        depth = await repo.queue_depth()
        assert depth["queued"] == 2
        assert depth["cancelled"] == 1
        assert depth["succeeded"] == 0


class TestSchemaAndMigration:
    async def test_verify_schema_passes_on_a_fresh_database(self, runtime_db):
        await runtime_db.verify_schema(["tasks", "task_events"])

    async def test_verify_schema_names_missing_tables(self, runtime_db):
        from jiro.errors import CacheError

        with pytest.raises(CacheError) as exc:
            await runtime_db.verify_schema(["tasks", "does_not_exist"])
        assert "does_not_exist" in str(exc.value)

    async def test_existing_tables_are_untouched(self, runtime_db):
        """Opening a database that already had Jiro data must preserve it."""
        await runtime_db.execute(
            "INSERT INTO cache (key, payload, engine, kind, created_at, expires_at)"
            " VALUES ('k','{\"v\":1}','bing','search',1,99999999999)"
        )
        await runtime_db.execute(
            "INSERT INTO jobs (id, type, status, payload, created_at)"
            " VALUES ('job_1','ai_search','queued','{}',1)"
        )
        await runtime_db.close()

        reopened = Database(runtime_db.path)
        await reopened.connect()
        try:
            assert await reopened.cache_get("k") is not None
            job = await reopened.fetchone("SELECT * FROM jobs WHERE id='job_1'")
            assert job is not None and job["status"] == "queued"
            await reopened.verify_schema(["tasks", "task_events"])
        finally:
            await reopened.close()

    async def test_upgrade_from_pre_runtime_schema(self, tmp_path):
        """A v0.3.3-shaped database gains the runtime tables without data loss."""
        path = str(tmp_path / "legacy.db")
        # Build a database using the pre-runtime schema only.
        legacy = sqlite3.connect(path)
        legacy.executescript(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', payload TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            INSERT INTO jobs (id, type, status, payload, created_at)
            VALUES ('legacy_job','ai_search','queued','{}',1);
            CREATE TABLE cache (
                key TEXT PRIMARY KEY, payload TEXT NOT NULL, engine TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'search', created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            """
        )
        legacy.commit()
        legacy.close()

        db = Database(path)
        await db.connect()
        try:
            await db.verify_schema(["tasks", "task_events"])
            columns = await db.table_columns("tasks")
            assert "owner_key_id" in columns
            assert "lease_expires_at" in columns
            # Pre-existing row survived.
            job = await db.fetchone("SELECT * FROM jobs WHERE id='legacy_job'")
            assert job is not None
        finally:
            await db.close()

    async def test_connect_is_repeatable(self, runtime_db):
        """Running connect twice must not error or reset data."""
        await runtime_db.execute(
            "INSERT INTO tasks (id,type,state,created_at,updated_at) VALUES ('t','x','queued',1,1)"
        )
        await runtime_db.connect()  # already connected; returns immediately
        assert await SqlTaskRepository(runtime_db).get("t") is not None

    async def test_expected_columns_present(self, runtime_db):
        columns = set(await runtime_db.table_columns("tasks"))
        required = {
            "id",
            "type",
            "state",
            "owner_key_id",
            "idempotency_key",
            "input",
            "result",
            "error_code",
            "error_message",
            "attempts",
            "max_attempts",
            "attempt_timeout_s",
            "deadline_at",
            "lease_owner",
            "lease_expires_at",
            "cancel_requested",
            "available_at",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
        }
        assert required <= columns, f"missing: {required - columns}"


class TestTransactionPrimitives:
    async def test_rollback_on_exception(self, runtime_db):
        with pytest.raises(RuntimeError):
            async with runtime_db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO tasks (id,type,state,created_at,updated_at)"
                    " VALUES ('t','x','queued',1,1)"
                )
                raise RuntimeError("boom")
        assert await runtime_db.fetchone("SELECT id FROM tasks WHERE id='t'") is None

    async def test_commit_on_success(self, runtime_db):
        async with runtime_db.transaction() as conn:
            await conn.execute(
                "INSERT INTO tasks (id,type,state,created_at,updated_at)"
                " VALUES ('t','x','queued',1,1)"
            )
        assert await runtime_db.fetchone("SELECT id FROM tasks WHERE id='t'") is not None

    async def test_state_change_and_event_commit_together(self, runtime_db):
        """A crash inside the block must leave neither half applied."""
        repo = SqlTaskRepository(runtime_db)
        now = time.time()
        await repo.insert(_new_task(), now=now)
        with pytest.raises(RuntimeError):
            async with runtime_db.transaction() as conn:
                await conn.execute(
                    "UPDATE tasks SET state='cancelled', completed_at=? WHERE id=?", (now, "task_1")
                )
                await repo.append_event(
                    TaskEvent(task_id="task_1", type=EventType.CANCELLED, ts=now), conn=conn
                )
                raise RuntimeError("crash between the two writes")
        assert (await repo.get("task_1")).state is TaskState.QUEUED
        assert await repo.events_for("task_1") == []


class TestRecoveryQueries:
    async def test_expired_leases_found_after_ttl(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=1)
        assert await repo.expired_leases(now=now) == []
        expired = await repo.expired_leases(now=now + 2)
        assert [r.id for r in expired] == ["task_1"]

    async def test_live_lease_is_not_expired(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=600)
        assert await repo.expired_leases(now=now + 5) == []

    async def test_due_retries_respects_availability(self, repo):
        now = time.time()
        await repo.insert(_new_task(), now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)
        await repo.schedule_retry(
            task_id="task_1",
            worker_id="w1",
            available_at=now + 100,
            error_code="e",
            error_message="m",
            now=now,
        )
        assert await repo.due_retries(now=now) == []
        assert len(await repo.due_retries(now=now + 101)) == 1

    async def test_list_by_state_spans_owners(self, repo):
        now = time.time()
        await repo.insert(_new_task(task_id="t1", owner="acct_a"), now=now)
        await repo.insert(_new_task(task_id="t2", owner="acct_b"), now=now)
        assert len(await repo.list_by_state(TaskState.QUEUED)) == 2


class TestRowMapping:
    async def test_corrupt_input_json_does_not_break_a_read(self, runtime_db):
        repo = SqlTaskRepository(runtime_db)
        await runtime_db.execute(
            "INSERT INTO tasks (id,type,state,input,created_at,updated_at)"
            " VALUES ('t','x','queued','{not json',1,1)"
        )
        record = await repo.get("t")
        assert record is not None
        assert record.input == {}

    async def test_oversized_result_column_is_preserved(self, repo):
        from jiro.runtime.sqlite_repo import row_to_record

        row = {
            "id": "t",
            "type": "x",
            "state": "succeeded",
            "input": "{}",
            "result": '{"big":"payload"}',
            "attempts": 1,
            "created_at": 1.0,
            "updated_at": 2.0,
        }
        record = row_to_record(row)
        assert record.result == {"big": "payload"}
        assert record.state is TaskState.SUCCEEDED


class TestSchemaDeclaration:
    def test_schema_rejects_duplicate_fields(self):
        from jiro.runtime.errors import TaskValidationError

        with pytest.raises(TaskValidationError):
            TaskSchema("t", [Field("a"), Field("a")])

    def test_schema_describe_is_serialisable(self):
        import json

        described = TaskSchema("t", [Field("a", "int", required=True, max_value=5)]).describe()
        assert json.loads(json.dumps(described))["required"] == ["a"]
