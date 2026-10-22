"""Child process used by the crash-recovery tests.

Runs a real :class:`~jiro.runtime.runtime.TaskRuntime` against a real SQLite
file, prints ``REACHED <boundary>`` to stdout at each named point, and then
waits to be killed. The parent kills it with ``SIGKILL`` / ``TerminateProcess``,
so nothing in this file gets a chance to clean up — exactly the situation the
recovery path exists for.

Selected by ``JIRO_CRASH_BOUNDARY``; the database path comes from
``JIRO_CRASH_DB`` and the optional side-effect log from
``JIRO_CRASH_SIDE_EFFECT``.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jiro.db import Database  # noqa: E402
from jiro.runtime import (  # noqa: E402
    HandlerRegistry,
    SqlTaskRepository,
    TaskRuntime,
)
from jiro.runtime.config import RuntimeConfig  # noqa: E402
from jiro.runtime.errors import TaskValidationError  # noqa: E402
from jiro.runtime.validation import Field, TaskSchema  # noqa: E402

TASK_ID = "task_crash_1"

#: Lease timing is the pivot of these tests. A claim writes a lease that must
#: still be live immediately after the kill (so "crashed while running" stays
#: distinguishable from "already recovered"), and must lapse shortly after so
#: the parent's recovery pass can act on it. 6s/5s leaves the parent a wide
#: window to observe the post-kill state and still land inside the test timeout.
CONFIG = {
    "enabled": True,
    "db_path": "",
    "max_concurrency": 1,
    "poll_interval_seconds": 0.02,
    "lease_ttl_seconds": 6.0,
    "default_attempt_timeout_seconds": 4.0,
    "max_attempt_timeout_seconds": 5.0,
    "default_max_attempts": 3,
    "max_max_attempts": 5,
    "retry_backoff_base_seconds": 0.05,
    "retry_backoff_max_seconds": 0.2,
    "retry_jitter_ratio": 0.0,
    "shutdown_grace_seconds": 1.0,
    "max_result_bytes": 65536,
    "event_retention_days": 0,
}

#: Boundaries where the handler must stay in flight until the parent kills us.
_BLOCKING = ("while_running", "during_cancel", "after_side_effect")


def announce(boundary: str) -> None:
    print(f"REACHED {boundary}", flush=True)


def record_side_effect(attempt: int) -> None:
    """Append one line per external effect, for the parent to count.

    Flushed and fsynced so the record survives an uncatchable process kill.
    """
    path = os.environ.get("JIRO_CRASH_SIDE_EFFECT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"attempt={attempt}\n")
        handle.flush()
        os.fsync(handle.fileno())


async def main() -> None:
    boundary = os.environ.get("JIRO_CRASH_BOUNDARY", "after_submit")
    db_path = os.environ["JIRO_CRASH_DB"]

    from jiro.config import Settings

    settings = Settings(
        raw={
            "runtime": CONFIG,
            "db": {"path": db_path},
            "cache": {"type": "sqlite"},
            "auth": {"enabled": False},
            "logging": {"level": "critical", "file": ""},
        }
    )

    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(ctx):
        if boundary == "after_side_effect":
            # The case the at-least-once guarantee exists for: the external
            # effect has already happened, but the runtime never learns the
            # outcome. Recording it to a file makes re-execution observable.
            entered.set()
            record_side_effect(ctx.attempt)
            await release.wait()  # never released; the parent kills us here
            return {"finished": True}
        if boundary in _BLOCKING:
            entered.set()
            await release.wait()
            return {"finished": True}
        if boundary == "after_failure":
            raise TaskValidationError("scripted permanent failure")
        return {"ok": True}

    registry = HandlerRegistry()
    registry.register(
        "crash_task",
        handler,
        TaskSchema("crash_task", [Field("value", "str", default="")]),
        description="crash-test handler",
    )

    db = Database(db_path)
    await db.connect()
    runtime = TaskRuntime(SqlTaskRepository(db), registry, RuntimeConfig.from_settings(settings))

    # `after_submit` must leave the task provably unclaimed, so its workers are
    # never started. Starting them would make the boundary a race against
    # polling, and a racy crash boundary proves nothing.
    if boundary != "after_submit":
        await runtime.start()

    if boundary == "in_retry_wait":

        async def always_fails(ctx):
            raise ConnectionError("transient upstream failure")

        registry.register(
            "crash_retry",
            always_fails,
            TaskSchema("crash_retry", [Field("value", "str", default="")]),
        )
        record = await runtime.submit(
            "crash_retry", {}, limits={"max_attempts": 3}, task_id=TASK_ID
        )
        # Wait until the first attempt has failed and parked in retry_wait.
        for _ in range(2000):
            current = await runtime.get(record.id, enforce_owner=False)
            if current.state.value == "retry_wait":
                break
            await asyncio.sleep(0.01)
        announce("in_retry_wait")
        await asyncio.sleep(60)
        return

    record = await runtime.submit("crash_task", {}, limits={"max_attempts": 3}, task_id=TASK_ID)

    if boundary == "after_submit":
        announce("after_submit")
        await asyncio.sleep(60)
        return

    if boundary == "during_cancel":
        # Cancel only once the handler is genuinely executing, so the durable
        # CANCELLED state comes from the interruption path rather than from
        # cancelling a still-queued task.
        await entered.wait()
        await runtime.cancel(record.id)
        for _ in range(500):
            current = await runtime.get(record.id, enforce_owner=False)
            if current.is_terminal:
                break
            await asyncio.sleep(0.01)
        announce("during_cancel")
        await asyncio.sleep(60)
        return

    if boundary in ("while_running", "after_side_effect"):
        # The handler is parked inside the attempt, so the task is provably
        # still executing when the parent kills this process.
        await entered.wait()
        await asyncio.sleep(0.05)
        announce(boundary)
        await asyncio.sleep(60)
        return

    # after_success / after_failure: the handler runs straight through, so just
    # wait for the runtime to record the terminal state.
    for _ in range(2000):
        current = await runtime.get(record.id, enforce_owner=False)
        if current.is_terminal:
            announce(boundary)
            await asyncio.sleep(60)
            return
        await asyncio.sleep(0.01)

    announce("timeout")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
