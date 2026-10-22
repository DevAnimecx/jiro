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
    task_id="task_1", type_name="test_task", owner="key_a", idempotency_key=None, input_payload=None
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
            _new_task(task_id="t1", owner="key_a", idempotency_key="k"), now=time.time()
        )
        await repo.insert(
            _new_task(task_id="t2", owner="key_b", idempotency_key="k"), now=time.time()
        )
        assert await repo.get("t2") is not None

    async def test_null_idempotency_keys_do_not_collide(self, repo):
        for index in range(3):
            await repo.insert(_new_task(task_id=f"t{index}"), now=time.time())
        assert await repo.count_for_owner("key_a") == 3

    async def test_find_by_idempotency_key(self, repo):
        await repo.insert(_new_task(idempotency_key="abc"), now=time.time())
        found = await repo.find_by_idempotency_key(owner_key_id="key_a", idempotency_key="abc")
        assert found is not None and found.id == "task_1"
        missing = await repo.find_by_idempotency_key(owner_key_id="key_a", idempotency_key="zzz")
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
