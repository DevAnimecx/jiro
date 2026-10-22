"""Crash-recovery tests.

These kill a real OS process at specific points and reopen the same database.
A unit test that mutates a task's state by hand proves nothing about durability,
so every scenario below terminates the interpreter with ``SIGKILL`` /
``TerminateProcess`` at a genuine boundary and then reconciles from whatever
the database actually contains.

The child harness (:mod:`jiro_crash_child`) prints a line to stdout at each
boundary; the test waits for that line and then kills the process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List

import pytest

from jiro.db import Database
from jiro.runtime import SqlTaskRepository
from jiro.runtime.types import TaskState

from tests.conftest_runtime import make_settings, wait_until

CHILD = Path(__file__).parent / "jiro_crash_child.py"

# `Popen.kill()` maps to TerminateProcess on Windows and SIGKILL on POSIX. Both
# are uncatchable, which is exactly the guarantee these tests need: the child
# gets no opportunity to flush or tidy up before it dies.


class CrashHarness:
    """Spawns the child process and kills it at a named boundary."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.proc: subprocess.Popen = None  # type: ignore[assignment]

    def start(self, boundary: str, *, side_effect_file: str = "") -> None:
        env = dict(os.environ)
        env["JIRO_CRASH_DB"] = self.db_path
        env["JIRO_CRASH_BOUNDARY"] = boundary
        env["PYTHONUNBUFFERED"] = "1"
        if side_effect_file:
            env["JIRO_CRASH_SIDE_EFFECT"] = side_effect_file
        self.proc = subprocess.Popen(
            [sys.executable, str(CHILD)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )

    def wait_for_boundary(self, timeout: float = 30.0) -> str:
        """Block until the child announces it reached the boundary.

        Reads on a worker thread so a child that never prints (crashed on import,
        wedged, missing the boundary name) surfaces as a test failure rather
        than a hung test run.
        """
        assert self.proc.stdout is not None
        box: List[str] = []

        def reader() -> None:
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                if line.startswith("REACHED "):
                    box.append(line.strip())
                    return
            box.append("")

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout)
        if not box:
            raise AssertionError(
                f"child did not reach the boundary within {timeout}s "
                f"(pid {self.proc.pid}); stderr:\n{self._drain_stderr()}"
            )
        return box[0]

    def _drain_stderr(self) -> str:
        """Collect the child's stderr, killing it first.

        Reading a live process's stderr blocks until it closes, so the child is
        terminated before the read. Without this a child that fails to start
        turns a clear assertion into a hung suite.
        """
        if self.proc.stderr is None:
            return ""
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                pass
        try:
            return self.proc.stderr.read() or ""
        except (ValueError, OSError):  # pragma: no cover - already closed
            return ""

    def kill(self) -> None:
        """Terminate the child as abruptly as the platform allows."""
        if self.proc.poll() is None:
            # SIGKILL / TerminateProcess cannot be caught, so the child gets no
            # chance to flush state, cancel tasks, or close the database.
            self.proc.kill()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            pass
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream:
                try:
                    stream.close()
                except (ValueError, OSError):  # pragma: no cover
                    pass

    def __enter__(self) -> "CrashHarness":
        return self

    def __exit__(self, *exc: object) -> None:
        if self.proc and self.proc.poll() is None:
            self.kill()


async def reopen(db_path: str):
    db = Database(db_path)
    await db.connect()
    return db, SqlTaskRepository(db)


class TestCrashRecovery:
    async def test_crash_after_persistence_before_execution(self, tmp_path):
        """Task persisted, process dies before any claim. It must still run."""
        db_path = str(tmp_path / "jiro.db")
        with CrashHarness(db_path) as harness:
            harness.start("after_submit")
            harness.wait_for_boundary()
            harness.kill()

        db, repo = await reopen(db_path)
        try:
            record = await repo.get("task_crash_1")
            assert record is not None
            # Nothing executed, so it is still queued — never terminal.
            assert record.state is TaskState.QUEUED
            assert record.attempts == 0

            # A fresh runtime picks it up and finishes it.
            from tests.conftest_runtime import make_handler, make_runtime

            registry = harness_registry()
            make_handler({"recovered": True}, registry, type_name="crash_task")
            runtime = await make_runtime(db, registry, make_settings(db_path=db_path))
            try:
                assert (
                    await runtime.get("task_crash_1", enforce_owner=False)
                ).state is TaskState.QUEUED
                await wait_until(lambda: _terminal(repo, "task_crash_1"), timeout=15)
                final = await repo.get("task_crash_1")
                assert final.state is TaskState.SUCCEEDED
                assert final.result == {"recovered": True}
                assert final.attempts == 1
            finally:
                await runtime.stop()
        finally:
            await db.close()

    async def test_crash_after_claim_while_running(self, tmp_path):
        """Marked running, process killed. Lease lapses, budget is respected."""
        db_path = str(tmp_path / "jiro.db")
        with CrashHarness(db_path) as harness:
            harness.start("while_running")
            harness.wait_for_boundary()
            harness.kill()

        db, repo = await reopen(db_path)
        try:
            record = await repo.get("task_crash_1")
            assert record.state is TaskState.RUNNING
            assert record.attempts == 1
            # The lease is still valid in the immediate moment after the kill.
            assert await repo.expired_leases(now=time.time()) == []

            # Once the lease lapses, recovery sees the interrupted attempt.
            later = time.time() + 10_000
            expired = await repo.expired_leases(now=later)
            assert [r.id for r in expired] == ["task_crash_1"]

            from tests.conftest_runtime import make_handler, make_runtime

            registry = harness_registry()
            make_handler({"recovered": True}, registry, type_name="crash_task")
            runtime = await make_runtime(db, registry, make_settings(db_path=db_path))
            try:
                final = await _await_recovery(repo, runtime, "task_crash_1")
                # At-least-once: the attempt budget was not reset, so this is
                # the second attempt, not a fresh one.
                assert final.state is TaskState.SUCCEEDED
                assert final.attempts == 2
            finally:
                await runtime.stop()
        finally:
            await db.close()

    async def test_crash_after_external_side_effect_before_commit(self, tmp_path):
        """The guarantee that matters: at-least-once, not exactly-once.

        The handler performs a non-idempotent external effect (recorded to a
        file) and the process dies before the runtime learns the outcome.
        Recovery re-runs the task, so the effect happens twice. The test asserts
        that honestly rather than pretending a database can prevent it.
        """
        db_path = str(tmp_path / "jiro.db")
        effects = tmp_path / "side_effects.txt"

        with CrashHarness(db_path) as harness:
            harness.start("after_side_effect", side_effect_file=str(effects))
            harness.wait_for_boundary()
            harness.kill()

        # The first effect is durable in the child's world, invisible to Jiro.
        assert effects.read_text().strip() == "attempt=1"

        db, repo = await reopen(db_path)
        try:
            record = await repo.get("task_crash_1")
            # Claimed, and the effect happened, but no result was committed.
            assert record.state is TaskState.RUNNING
            assert record.result is None
            assert record.completed_at is None

            from tests.conftest_runtime import make_handler, make_runtime

            registry = harness_registry()

            async def repeats_the_effect(ctx):
                with open(effects, "a", encoding="utf-8") as handle:
                    handle.write(f"attempt={ctx.attempt}\n")
                    handle.flush()
                return {"ran_again": True}

            make_handler(repeats_the_effect, registry, type_name="crash_task")
            runtime = await make_runtime(db, registry, make_settings(db_path=db_path))
            try:
                final = await _await_recovery(repo, runtime, "task_crash_1")
                assert final.state is TaskState.SUCCEEDED
                assert final.attempts == 2
                # Documented at-least-once: the effect occurred twice. A handler
                # that must not duplicate it should key on
                # `ctx.idempotency_key`; that is the caller's contract.
                recorded = effects.read_text().split()
                assert recorded == ["attempt=1", "attempt=2"]
            finally:
                await runtime.stop()
        finally:
            await db.close()

    async def test_crash_while_waiting_for_retry(self, tmp_path):
        """Retry scheduling survives the process that computed it."""
        db_path = str(tmp_path / "jiro.db")
        with CrashHarness(db_path) as harness:
            harness.start("in_retry_wait")
            harness.wait_for_boundary()
            harness.kill()

        db, repo = await reopen(db_path)
        try:
            record = await repo.get("task_crash_1")
            assert record.state is TaskState.RETRY_WAIT
            assert record.attempts == 1
            assert record.available_at is not None
            persisted_availability = record.available_at

            # Attempts already burned are not reset by the restart.
            from tests.conftest_runtime import make_handler, make_runtime

            registry = harness_registry()
            make_handler({"ok": True}, registry, type_name="crash_retry")
            runtime = await make_runtime(db, registry, make_settings(db_path=db_path))
            try:
                # The persisted backoff survives the restart; once it elapses,
                # recovery promotes the task and the pool finishes it.
                await wait_until(lambda: _terminal(repo, "task_crash_1"), timeout=20)
                final = await repo.get("task_crash_1")
                assert final.state is TaskState.SUCCEEDED
                assert final.attempts == 2
                assert final.available_at is None or final.available_at >= persisted_availability
            finally:
                await runtime.stop()
        finally:
            await db.close()

    async def test_crash_during_cancellation(self, tmp_path):
        """A cancellation committed before the crash is final; no resurrection."""
        db_path = str(tmp_path / "jiro.db")
        with CrashHarness(db_path) as harness:
            harness.start("during_cancel")
            harness.wait_for_boundary()
            harness.kill()

        db, repo = await reopen(db_path)
        try:
            record = await repo.get("task_crash_1")
            assert record.state is TaskState.CANCELLED
            assert record.cancel_requested is True

            # A restart must leave it terminal and must not execute it.
            from tests.conftest_runtime import make_handler, make_runtime

            registry = harness_registry()
            handler = make_handler({"never": True}, registry, type_name="crash_task")
            runtime = await make_runtime(db, registry, make_settings(db_path=db_path))
            try:
                await asyncio_sleep(0.3)
                final = await repo.get("task_crash_1")
                assert final.state is TaskState.CANCELLED
                assert handler.call_count == 0
            finally:
                await runtime.stop()
        finally:
            await db.close()

    async def test_crash_during_startup_recovery(self, tmp_path):
        """Reconciliation is idempotent: re-running it changes nothing more."""
        db_path = str(tmp_path / "jiro.db")
        with CrashHarness(db_path) as harness:
            harness.start("while_running")
            harness.wait_for_boundary()
            harness.kill()

        db, repo = await reopen(db_path)
        try:
            from jiro.runtime import RecoveryManager

            manager = RecoveryManager(repo, clock=lambda: time.time() + 10_000)
            first = await manager.reconcile()
            assert first.requeued == 1

            after_first = await repo.get("task_crash_1")
            assert after_first.state is TaskState.RETRY_WAIT

            # Running recovery again must be a no-op.
            second = await manager.reconcile()
            assert second.total_changed == 0
            after_second = await repo.get("task_crash_1")
            assert after_second.state is TaskState.RETRY_WAIT
            assert after_second.attempts == after_first.attempts
        finally:
            await db.close()

    async def test_recovery_is_auditable(self, tmp_path):
        """Every recovery decision leaves a durable record."""
        db_path = str(tmp_path / "jiro.db")
        with CrashHarness(db_path) as harness:
            harness.start("while_running")
            harness.wait_for_boundary()
            harness.kill()

        db, repo = await reopen(db_path)
        try:
            from jiro.runtime import RecoveryManager
            from jiro.runtime.types import EventType

            manager = RecoveryManager(repo, clock=lambda: time.time() + 10_000)
            await manager.reconcile()
            events = await repo.events_for("task_crash_1")
            recovery_events = [e for e in events if e.type is EventType.RECOVERY_APPLIED]
            assert recovery_events, "recovery must leave an audit trail"
            assert recovery_events[0].metadata["reason"] == "interrupted_lease_expired"
            assert recovery_events[0].metadata["state"] == "retry_wait"
        finally:
            await db.close()

    async def test_no_duplicate_task_records_after_recovery(self, tmp_path):
        """Recovery requeues the same row; it never creates a second one."""
        db_path = str(tmp_path / "jiro.db")
        with CrashHarness(db_path) as harness:
            harness.start("while_running")
            harness.wait_for_boundary()
            harness.kill()

        db, repo = await reopen(db_path)
        try:
            from jiro.runtime import RecoveryManager

            for offset in (10_000, 20_000, 30_000):
                manager = RecoveryManager(repo, clock=lambda o=offset: o)
                await manager.reconcile()
            rows = await db.fetchall("SELECT id, COUNT(*) AS n FROM tasks GROUP BY id HAVING n > 1")
            assert rows == []
            assert await db.fetchone("SELECT COUNT(*) AS n FROM tasks") is not None
            total = (await db.fetchone("SELECT COUNT(*) AS n FROM tasks"))["n"]
            assert total == 1
        finally:
            await db.close()

    async def test_live_lease_from_another_instance_is_not_stolen(self, tmp_path):
        """A running task in another process must not be reclaimed."""
        db_path = str(tmp_path / "jiro.db")
        with CrashHarness(db_path) as harness:
            harness.start("while_running")
            harness.wait_for_boundary()
            harness.kill()

        db, repo = await reopen(db_path)
        try:
            from jiro.runtime import RecoveryManager

            # "Now" is only moments after the claim, so the lease is still live.
            manager = RecoveryManager(repo, clock=time.time)
            report = await manager.reconcile()
            assert report.total_changed == 0
            record = await repo.get("task_crash_1")
            assert record.state is TaskState.RUNNING
        finally:
            await db.close()

    async def test_terminal_tasks_are_never_reopened(self, tmp_path):
        """Succeeded and failed stay terminal across any number of restarts."""
        for boundary, expected in (
            ("after_success", TaskState.SUCCEEDED),
            ("after_failure", TaskState.FAILED),
        ):
            path = str(tmp_path / f"{boundary}.db")
            with CrashHarness(path) as harness:
                harness.db_path = path
                harness.start(boundary)
                harness.wait_for_boundary()
                harness.kill()

            db, repo = await reopen(path)
            try:
                from jiro.runtime import RecoveryManager

                manager = RecoveryManager(repo, clock=lambda: time.time() + 10_000)
                report = await manager.reconcile()
                assert report.total_changed == 0
                record = await repo.get("task_crash_1")
                assert record.state is expected
            finally:
                await db.close()


# ------------------------------------------------------------------- helpers
async def _terminal(repo, task_id: str):
    record = await repo.get(task_id)
    return record if record and record.is_terminal else None


async def _await_recovery(repo, runtime, task_id: str, timeout: float = 20.0):
    """Wait for recovery to requeue and the pool to finish the task."""
    await wait_until(lambda: _terminal(repo, task_id), timeout=timeout)
    return await repo.get(task_id)


def harness_registry():
    from jiro.runtime import HandlerRegistry

    return HandlerRegistry()


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
