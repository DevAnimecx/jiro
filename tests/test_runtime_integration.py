"""Runtime integration tests.

These drive the real :class:`~jiro.runtime.runtime.TaskRuntime` with real
workers against real SQLite persistence. Only the handler bodies are scripted;
the scheduling, claiming, retry, cancellation and persistence logic under test
is the production code path.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from jiro.runtime import (
    RuntimeConfig,
    SqlTaskRepository,
    TaskRuntime,
    UnknownTaskType,
)
from jiro.runtime.errors import (
    ResultTooLarge,
    TaskLimitExceeded,
    TaskNotFound,
    TaskValidationError,
)
from jiro.runtime.types import EventType, TaskState
from jiro.runtime.validation import Field

from tests.conftest_runtime import (  # noqa: F401  (fixture re-exports)
    await_state,
    await_terminal,
    make_handler,
    make_runtime,
    make_settings,
    repo,
    runtime,
    runtime_db,
    runtime_settings,
    registry,
    wait_until,
)


# ------------------------------------------------------------------- submission
class TestSubmission:
    async def test_successful_execution(self, runtime, registry):
        make_handler({"answer": 42}, registry)
        record = await runtime.submit("test_task", {"value": "x"}, owner_key_id="key_a")
        assert record.state is TaskState.QUEUED  # durable acceptance, not success

        done = await await_terminal(runtime, record.id, "key_a")
        assert done.state is TaskState.SUCCEEDED
        assert done.result == {"answer": 42}
        assert done.attempts == 1
        assert done.completed_at is not None

    async def test_result_is_durable_across_runtime_instances(
        self, runtime_db, runtime_settings, registry
    ):
        make_handler({"persisted": True}, registry)
        first = TaskRuntime(
            SqlTaskRepository(runtime_db), registry, RuntimeConfig.from_settings(runtime_settings)
        )
        await first.start()
        record = await first.submit("test_task", {}, owner_key_id="key_a")
        await await_terminal(first, record.id, "key_a")
        await first.stop()

        # A fresh runtime object against the same database.
        second = TaskRuntime(
            SqlTaskRepository(runtime_db), registry, RuntimeConfig.from_settings(runtime_settings)
        )
        try:
            reloaded = await second.get(record.id, owner_key_id="key_a")
            assert reloaded.state is TaskState.SUCCEEDED
            assert reloaded.result == {"persisted": True}
        finally:
            await second.stop()

    async def test_unknown_type_rejected(self, runtime):
        with pytest.raises(UnknownTaskType):
            await runtime.submit("nope", {})

    async def test_invalid_input_rejected_and_not_persisted(self, runtime, registry):
        make_handler({}, registry)
        with pytest.raises(TaskValidationError):
            await runtime.submit("test_task", {"wrong": "field"})
        assert await runtime.count() == 0

    async def test_limit_ceiling_enforced(self, runtime, registry):
        make_handler({}, registry)
        with pytest.raises(TaskLimitExceeded):
            await runtime.submit("test_task", {}, limits={"max_attempts": 999})

    async def test_idempotent_submission_returns_same_task(self, runtime, registry):
        handler = make_handler({"ok": True}, registry)
        first = await runtime.submit("test_task", {}, owner_key_id="key_a", idempotency_key="same")
        second = await runtime.submit("test_task", {}, owner_key_id="key_a", idempotency_key="same")
        assert first.id == second.id
        await await_terminal(runtime, first.id, "key_a")
        # The duplicate must not have caused a second execution.
        assert handler.call_count == 1

    async def test_idempotency_key_is_scoped_per_owner(self, runtime, registry):
        make_handler({}, registry)
        a = await runtime.submit("test_task", {}, owner_key_id="key_a", idempotency_key="shared")
        b = await runtime.submit("test_task", {}, owner_key_id="key_b", idempotency_key="shared")
        assert a.id != b.id

    async def test_oversized_input_rejected(self, runtime, registry):
        make_handler({}, registry, fields=[Field("blob", "str", max_bytes=64)])
        with pytest.raises(TaskValidationError):
            await runtime.submit("test_task", {"blob": "x" * 5000})


# --------------------------------------------------------------------- failure
class TestFailureHandling:
    async def test_permanent_failure_fails_immediately(self, runtime, registry):
        handler = make_handler(TaskValidationError("bad input"), registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        done = await await_terminal(runtime, record.id, "key_a")
        assert done.state is TaskState.FAILED
        assert done.attempts == 1, "a permanent error must not be retried"
        assert handler.call_count == 1
        assert done.error_code == "task_validation_error"
        assert "bad input" in done.error_message

    async def test_retryable_failure_then_success(self, runtime, registry):
        state = {"fail": True}

        async def flaky(ctx):
            if state["fail"]:
                state["fail"] = False
                raise ConnectionError("upstream hiccup")
            return {"recovered": True}

        handler = make_handler(flaky, registry)
        record = await runtime.submit(
            "test_task",
            {},
            owner_key_id="key_a",
            limits={"max_attempts": 3, "attempt_timeout_seconds": 2},
        )
        done = await await_terminal(runtime, record.id, "key_a")
        assert done.state is TaskState.SUCCEEDED
        assert done.result == {"recovered": True}
        assert done.attempts == 2
        assert handler.attempts == [1, 2]

    async def test_retry_exhaustion_fails_the_task(self, runtime, registry):
        async def always_fails(ctx):
            raise ConnectionError("down")

        handler = make_handler(always_fails, registry)
        record = await runtime.submit(
            "test_task",
            {},
            owner_key_id="key_a",
            limits={"max_attempts": 2, "attempt_timeout_seconds": 2},
        )
        done = await await_terminal(runtime, record.id, "key_a")
        assert done.state is TaskState.FAILED
        assert done.attempts == 2
        assert handler.call_count == 2
        assert "down" in done.error_message

    async def test_failure_message_is_sanitised(self, runtime, registry):
        async def leaky(ctx):
            raise RuntimeError("token Bearer sk-supersecret123 leaked")

        make_handler(leaky, registry)
        record = await runtime.submit(
            "test_task", {}, owner_key_id="key_a", limits={"max_attempts": 1}
        )
        done = await await_terminal(runtime, record.id, "key_a")
        assert "sk-supersecret123" not in (done.error_message or "")
        assert "redacted" in (done.error_message or "")

    async def test_no_stack_trace_in_public_status(self, runtime, registry):
        async def boom(ctx):
            raise RuntimeError("inner detail")

        make_handler(boom, registry)
        record = await runtime.submit(
            "test_task", {}, owner_key_id="key_a", limits={"max_attempts": 1}
        )
        done = await await_terminal(runtime, record.id, "key_a")
        status = done.to_status()
        rendered = repr(status)
        assert "Traceback" not in rendered
        assert "boom" not in rendered  # the handler function name never leaks

    async def test_oversized_result_fails_permanently(self, runtime, registry):
        make_handler({"blob": "x" * 100_000}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        done = await await_terminal(runtime, record.id, "key_a")
        assert done.state is TaskState.FAILED
        assert done.error_code == "result_too_large"

    async def test_worker_error_does_not_kill_the_runtime(self, runtime, registry):
        """One exploding task must not stop the next one from running."""

        async def explodes(ctx):
            raise BaseException("not even an Exception")

        make_handler(explodes, registry, type_name="boom")
        make_handler({"fine": True}, registry, type_name="healthy")

        bad = await runtime.submit("boom", {}, owner_key_id="key_a", limits={"max_attempts": 1})
        bad_done = await await_terminal(runtime, bad.id, "key_a")
        assert bad_done.state is TaskState.FAILED

        good = await runtime.submit("healthy", {}, owner_key_id="key_a")
        good_done = await await_terminal(runtime, good.id, "key_a")
        assert good_done.state is TaskState.SUCCEEDED

    async def test_pool_capacity_survives_a_hostile_handler(self, runtime_db, registry):
        """A BaseException must not permanently shrink the worker pool."""

        async def hostile(ctx):
            raise BaseException("worse than Exception")

        make_handler(hostile, registry, type_name="hostile")
        make_handler({"ok": True}, registry, type_name="fine")
        settings = make_settings(runtime={"max_concurrency": 1}, db_path=runtime_db.path)
        runtime = await make_runtime(runtime_db, registry, settings)
        try:
            for _ in range(4):
                await runtime.submit(
                    "hostile", {}, owner_key_id="key_a", limits={"max_attempts": 1}
                )
            for _ in range(4):
                await runtime.submit("fine", {}, owner_key_id="key_a")

            async def all_done():
                done = [r for r in await runtime.list(owner_key_id="key_a") if r.is_terminal]
                return done if len(done) == 8 else None

            records = await wait_until(all_done, timeout=15)
            states = {r.type: r.state for r in records}
            assert states["hostile"] is TaskState.FAILED
            # With a single worker, this only passes if it survived all four.
            assert states["fine"] is TaskState.SUCCEEDED
            assert runtime.stats()["workers"] == 1
        finally:
            await runtime.stop()


# ------------------------------------------------------- timeouts and deadlines
class TestTimeoutsAndDeadlines:
    async def test_attempt_timeout_is_enforced(self, runtime, registry):
        async def sleeper(ctx):
            await asyncio.sleep(30)
            return {"never": True}

        make_handler(sleeper, registry)
        record = await runtime.submit(
            "test_task",
            {},
            owner_key_id="key_a",
            limits={"max_attempts": 1, "attempt_timeout_seconds": 0.1},
        )
        done = await await_terminal(runtime, record.id, "key_a", timeout=5)
        assert done.state is TaskState.FAILED
        assert done.error_code == "attempt_timeout"
        assert done.result is None, "a timeout is never a success"

    async def test_timeout_consumes_an_attempt_then_retries(self, runtime, registry):
        attempts = {"n": 0}

        async def sometimes_slow(ctx):
            attempts["n"] += 1
            if attempts["n"] == 1:
                await asyncio.sleep(30)
            return {"ok": True}

        make_handler(sometimes_slow, registry)
        record = await runtime.submit(
            "test_task",
            {},
            owner_key_id="key_a",
            limits={"max_attempts": 2, "attempt_timeout_seconds": 0.2},
        )
        done = await await_terminal(runtime, record.id, "key_a", timeout=6)
        assert done.state is TaskState.SUCCEEDED
        assert done.attempts == 2

    async def test_expired_deadline_prevents_execution(self, runtime_db, registry):
        """A task whose deadline lapsed while down must never run."""
        handler = make_handler({"never": True}, registry)
        # Pool deliberately not started, so nothing can claim the task before
        # its deadline is moved into the past.
        runtime = TaskRuntime(
            SqlTaskRepository(runtime_db),
            registry,
            RuntimeConfig.from_settings(make_settings(db_path=runtime_db.path)),
        )
        record = await runtime.submit(
            "test_task", {}, owner_key_id="key_a", limits={"max_attempts": 3}
        )
        # Submission rejects a non-positive span, so simulate a deadline that
        # lapsed while the process was down.
        await runtime.repo.db.execute(
            "UPDATE tasks SET deadline_at = ? WHERE id = ?", (time.time() - 1, record.id)
        )

        await runtime.start()  # recovery runs before any worker starts
        try:
            done = await runtime.get(record.id, owner_key_id="key_a")
            assert done.state is TaskState.FAILED
            assert handler.call_count == 0, "an expired task must never execute"
        finally:
            await runtime.stop()

    async def test_deadline_reported_in_status(self, runtime, registry):
        make_handler({}, registry)
        record = await runtime.submit(
            "test_task",
            {},
            owner_key_id="key_a",
            limits={"deadline_in_seconds": 3600},
        )
        status = record.to_status()
        assert status["deadline_at"] is not None
        assert status["deadline_at"].endswith("Z")


# ------------------------------------------------------------------ concurrency
class TestConcurrency:
    async def test_concurrency_limit_is_enforced(self, runtime_db, registry):
        """Five slow tasks, two workers: never more than two in flight."""
        settings = make_settings(
            runtime={
                "max_concurrency": 2,
                "default_attempt_timeout_seconds": 5.0,
                "max_attempt_timeout_seconds": 10.0,
                "lease_ttl_seconds": 30,
                "shutdown_grace_seconds": 2.0,
            },
            db_path=runtime_db.path,
        )

        live = {"now": 0, "peak": 0}

        async def slow(ctx):
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            await asyncio.sleep(0.15)
            live["now"] -= 1
            return {"ok": True}

        make_handler(slow, registry)
        runtime = await make_runtime(runtime_db, registry, settings)
        try:
            for _ in range(5):
                await runtime.submit("test_task", {}, owner_key_id="key_a")

            async def all_done():
                done = [r for r in await runtime.list(owner_key_id="key_a") if r.is_terminal]
                return done if len(done) == 5 else None

            records = await wait_until(all_done, timeout=15)
            assert all(r.state is TaskState.SUCCEEDED for r in records)
            assert live["peak"] <= 2, f"concurrency exceeded: peak={live['peak']}"
            assert live["peak"] >= 1
        finally:
            await runtime.stop()

    async def test_many_tasks_all_complete(self, runtime, registry):
        handler = make_handler({"ok": True}, registry)
        for _ in range(12):
            await runtime.submit("test_task", {}, owner_key_id="key_a")

        async def all_done():
            done = [r for r in await runtime.list(owner_key_id="key_a") if r.is_terminal]
            return done if len(done) == 12 else None

        records = await wait_until(all_done, timeout=15)
        assert len({r.id for r in records}) == 12
        assert handler.call_count == 12


# ---------------------------------------------------------------- cancellation
class TestCancellation:
    async def test_cancel_queued_task_before_it_starts(
        self, runtime_db, runtime_settings, registry
    ):
        """With the pool stopped, the task stays queued until cancelled."""
        runtime = TaskRuntime(
            SqlTaskRepository(runtime_db), registry, RuntimeConfig.from_settings(runtime_settings)
        )
        handler = make_handler({"should_not_run": True}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        assert record.state is TaskState.QUEUED

        cancelled = await runtime.cancel(record.id, owner_key_id="key_a")
        assert cancelled.state is TaskState.CANCELLED
        assert cancelled.completed_at is not None

        # Even starting the runtime afterwards must not execute it.
        await runtime.start()
        try:
            await asyncio.sleep(0.2)
            final = await runtime.get(record.id, owner_key_id="key_a")
            assert final.state is TaskState.CANCELLED
            assert handler.call_count == 0
        finally:
            await runtime.stop()

    async def test_cancel_running_cooperative_task(self, runtime, registry):
        started = asyncio.Event()
        cleaned_up = []

        async def cooperative(ctx):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cleaned_up.append(True)
                raise
            return {"never": True}

        make_handler(cooperative, registry)
        record = await runtime.submit(
            "test_task",
            {},
            owner_key_id="key_a",
            limits={"attempt_timeout_seconds": 2},
        )
        await asyncio.wait_for(started.wait(), timeout=5)

        interim = await runtime.cancel(record.id, owner_key_id="key_a")
        # Honest: the handler has not unwound yet.
        assert interim.state in (TaskState.RUNNING, TaskState.CANCELLED)
        assert interim.cancel_requested is True

        done = await await_terminal(runtime, record.id, "key_a")
        assert done.state is TaskState.CANCELLED
        assert cleaned_up, "handler cleanup must run"
        assert done.result is None

    async def test_cancel_task_waiting_for_retry(self, runtime_db, registry):
        async def always_fails(ctx):
            raise ConnectionError("down")

        make_handler(always_fails, registry)
        # A 30s backoff parks the task in retry_wait long enough to cancel it.
        settings = make_settings(
            runtime={
                "retry_backoff_base_seconds": 30.0,
                "retry_backoff_max_seconds": 30.0,
                "default_max_attempts": 5,
                "default_attempt_timeout_seconds": 2.0,
                "max_attempt_timeout_seconds": 5.0,
                "lease_ttl_seconds": 60,
            },
            db_path=runtime_db.path,
        )
        runtime = await make_runtime(runtime_db, registry, settings)
        try:
            record = await runtime.submit(
                "test_task", {}, owner_key_id="key_a", limits={"max_attempts": 5}
            )
            await await_state(runtime, record.id, TaskState.RETRY_WAIT, "key_a")

            cancelled = await runtime.cancel(record.id, owner_key_id="key_a")
            assert cancelled.state is TaskState.CANCELLED
            assert cancelled.completed_at is not None

            # It must not be woken again when the backoff would have elapsed.
            await asyncio.sleep(0.2)
            final = await runtime.get(record.id, owner_key_id="key_a")
            assert final.state is TaskState.CANCELLED
            assert final.attempts == 1, "a cancelled task must not execute again"
        finally:
            await runtime.stop()

    async def test_repeated_cancellation_is_safe(self, runtime_db, registry):
        """Cancelling three times yields one terminal state, never a conflict."""
        # Pool not started, so the task is deterministically still queued.
        runtime = TaskRuntime(
            SqlTaskRepository(runtime_db),
            registry,
            RuntimeConfig.from_settings(make_settings(db_path=runtime_db.path)),
        )
        make_handler({"ok": True}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        first = await runtime.cancel(record.id, owner_key_id="key_a")
        second = await runtime.cancel(record.id, owner_key_id="key_a")
        third = await runtime.cancel(record.id, owner_key_id="key_a")
        assert first.state is second.state is third.state is TaskState.CANCELLED

    async def test_cancel_after_success_is_a_noop(self, runtime, registry):
        make_handler({"done": True}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        done = await await_terminal(runtime, record.id, "key_a")
        assert done.state is TaskState.SUCCEEDED
        after = await runtime.cancel(record.id, owner_key_id="key_a")
        assert after.state is TaskState.SUCCEEDED

    async def test_cancel_unknown_task_raises(self, runtime):
        with pytest.raises(TaskNotFound):
            await runtime.cancel("task_nope", owner_key_id="key_a")

    async def test_cancel_is_ownership_checked(self, runtime, registry):
        make_handler({}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        with pytest.raises(TaskNotFound):
            await runtime.cancel(record.id, owner_key_id="key_b")

    async def test_cancellation_records_events(self, runtime_db, registry):
        runtime = TaskRuntime(
            SqlTaskRepository(runtime_db),
            registry,
            RuntimeConfig.from_settings(make_settings(db_path=runtime_db.path)),
        )
        make_handler({"ok": True}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        await runtime.cancel(record.id, owner_key_id="key_a")
        types = [e.type for e in await runtime.events(record.id, owner_key_id="key_a")]
        assert EventType.SUBMITTED in types
        assert EventType.CANCELLATION_REQUESTED in types
        assert EventType.CANCELLED in types


# ------------------------------------------------------------------- ownership
class TestOwnership:
    async def test_owner_can_read_their_task(self, runtime, registry):
        make_handler({}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        assert (await runtime.get(record.id, owner_key_id="key_a")).id == record.id

    async def test_other_owner_cannot_read(self, runtime, registry):
        make_handler({}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        with pytest.raises(TaskNotFound):
            await runtime.get(record.id, owner_key_id="key_b")

    async def test_other_owner_sees_nothing_in_list(self, runtime, registry):
        make_handler({}, registry)
        await runtime.submit("test_task", {}, owner_key_id="key_a")
        await runtime.submit("test_task", {}, owner_key_id="key_b")
        mine = await runtime.list(owner_key_id="key_a")
        assert len(mine) == 1
        assert mine[0].owner_key_id == "key_a"

    async def test_list_requires_an_explicit_owner(self, runtime):
        """`owner_key_id` is mandatory, so a query cannot be left unscoped."""
        with pytest.raises(TypeError):
            await runtime.list()  # type: ignore[call-arg]

    async def test_list_allows_the_anonymous_namespace(self, runtime, registry):
        """`None` is a real value: the namespace used when auth is disabled."""
        make_handler({"ok": True}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id=None)
        found = await runtime.list(owner_key_id=None)
        assert [r.id for r in found] == [record.id]

    async def test_unowned_task_is_admin_reachable_only_without_identity(self, runtime, registry):
        make_handler({}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id=None)
        # Anonymous caller may reach an anonymous task.
        assert (await runtime.get(record.id, owner_key_id=None)).id == record.id
        # An identified caller may not.
        with pytest.raises(TaskNotFound):
            await runtime.get(record.id, owner_key_id="key_a")

    async def test_events_are_ownership_checked(self, runtime, registry):
        make_handler({}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        with pytest.raises(TaskNotFound):
            await runtime.events(record.id, owner_key_id="key_b")


# --------------------------------------------------------------------- events
class TestLifecycleEvents:
    async def test_success_lifecycle_event_sequence(self, runtime, registry):
        make_handler({"ok": True}, registry)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        await await_terminal(runtime, record.id, "key_a")
        types = [e.type.value for e in await runtime.events(record.id, owner_key_id="key_a")]
        assert types[0] == EventType.SUBMITTED.value
        assert types[1] == EventType.VALIDATED.value
        assert EventType.EXECUTION_STARTED.value in types
        assert types[-1] == EventType.SUCCEEDED.value

    async def test_retry_lifecycle_events(self, runtime, registry):
        async def fails_once(ctx):
            raise ConnectionError("down")

        make_handler(fails_once, registry)
        record = await runtime.submit(
            "test_task",
            {},
            owner_key_id="key_a",
            limits={"max_attempts": 2, "attempt_timeout_seconds": 2},
        )
        await await_terminal(runtime, record.id, "key_a")
        # The terminal state commits before its event is written, so wait for
        # the event rather than racing it.
        await wait_until(lambda: _has_event(runtime, record.id, "key_a", EventType.FAILED))
        types = [e.type for e in await runtime.events(record.id, owner_key_id="key_a")]
        assert EventType.ATTEMPT_FAILED in types
        assert EventType.RETRY_SCHEDULED in types
        assert EventType.FAILED in types

    async def test_retry_event_records_the_reason(self, runtime, registry):
        async def fails(ctx):
            raise RateLimitErrorProbe()

        make_handler(fails, registry)
        record = await runtime.submit(
            "test_task",
            {},
            owner_key_id="key_a",
            limits={"max_attempts": 2, "attempt_timeout_seconds": 2},
        )
        await await_terminal(runtime, record.id, "key_a")
        events = await runtime.events(record.id, owner_key_id="key_a")
        retry = next(e for e in events if e.type is EventType.RETRY_SCHEDULED)
        assert "reason" in retry.metadata
        assert "delay" in retry.metadata


class RateLimitErrorProbe(Exception):
    """Stand-in for a transient upstream failure."""


async def _has_event(runtime, task_id: str, owner: str, event_type) -> bool:
    events = await runtime.events(task_id, owner_key_id=owner)
    return any(e.type is event_type for e in events)


# ------------------------------------------------------------------- shutdown
class TestShutdown:
    async def test_graceful_shutdown_lets_in_flight_work_finish(self, runtime_db, registry):
        """Shutdown waits for a *running* attempt; it does not start queued work."""
        settings = make_settings(
            runtime={
                "max_concurrency": 2,
                "default_attempt_timeout_seconds": 5.0,
                "max_attempt_timeout_seconds": 10.0,
                "lease_ttl_seconds": 60,
                "shutdown_grace_seconds": 3.0,
            },
            db_path=runtime_db.path,
        )

        running = asyncio.Event()

        async def slow_but_bounded(ctx):
            running.set()
            await asyncio.sleep(0.2)
            return {"ok": True}

        make_handler(slow_but_bounded, registry)
        runtime = await make_runtime(runtime_db, registry, settings)
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        await asyncio.wait_for(running.wait(), timeout=5)  # genuinely in flight

        await runtime.stop()  # grace is 3s, the attempt needs 0.2s
        final = await runtime.get(record.id, owner_key_id="key_a")
        assert final.state is TaskState.SUCCEEDED
        assert final.result == {"ok": True}

    async def test_shutdown_does_not_start_queued_work(self, runtime_db, registry):
        """Queued work is left for recovery, not picked up on the way out."""
        handler = make_handler({"ok": True}, registry)
        runtime = TaskRuntime(
            SqlTaskRepository(runtime_db),
            registry,
            RuntimeConfig.from_settings(make_settings(db_path=runtime_db.path)),
        )
        await runtime.start()
        await runtime.stop()
        record = await runtime.submit("test_task", {}, owner_key_id="key_a")
        assert record.state is TaskState.QUEUED
        assert handler.call_count == 0

    async def test_shutdown_before_start_is_safe(self, runtime_db, registry):
        runtime = TaskRuntime(
            SqlTaskRepository(runtime_db),
            registry,
            RuntimeConfig.from_settings(make_settings(db_path=runtime_db.path)),
        )
        await runtime.stop()  # must not raise
        assert runtime.started is False

    async def test_restart_resumes_a_stopped_runtime(self, runtime, registry):
        make_handler({"ok": True}, registry)
        first = await runtime.submit("test_task", {}, owner_key_id="key_a")
        await await_terminal(runtime, first.id, "key_a")
        await runtime.stop()
        assert runtime.started is False
        await runtime.start()
        assert runtime.started is True
        await runtime.stop()

    async def test_no_new_execution_after_stop(self, runtime_db, registry):
        runtime = TaskRuntime(
            SqlTaskRepository(runtime_db),
            registry,
            RuntimeConfig.from_settings(make_settings(db_path=runtime_db.path)),
        )
        handler = make_handler({"ok": True}, registry)
        await runtime.start()
        await runtime.stop()
        await runtime.submit("test_task", {}, owner_key_id="key_a")
        await asyncio.sleep(0.2)
        assert handler.call_count == 0
        record = await runtime.get(
            (await runtime.list(owner_key_id="key_a"))[0].id, owner_key_id="key_a"
        )
        assert record.state is TaskState.QUEUED
