"""Bounded worker execution.

Concurrency model
-----------------
The pool starts exactly ``max_concurrency`` worker coroutines, once, at
:meth:`WorkerPool.start`. Each worker loops: claim one task, execute it, repeat.
Nothing is ever spawned per task.

That is the whole safety argument. There is no ``asyncio.create_task`` per
submission, no semaphore to leak permits from, and no unbounded growth when a
client floods the API — the number of in-flight handler executions can never
exceed the number of workers.

Cancellation
------------
A running worker's ``asyncio.Task`` is registered so the runtime can interrupt
it. The task is awaited inside a shielded wrapper so that the pool survives the
``CancelledError`` it may receive, records the honest terminal state, and only
then re-raises its own shutdown signal.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Callable, Dict, List, Optional, Set

from jiro.log import get_logger
from jiro.runtime.config import RuntimeConfig
from jiro.runtime.errors import (
    ResultTooLarge,
    UnknownTaskType,
    classify_failure,
    sanitize_error,
)
from jiro.runtime.handlers import HandlerRegistry, TaskContext
from jiro.runtime.repository import TaskRepository
from jiro.runtime.retry import RetryPolicy
from jiro.runtime.types import (
    EventType,
    FailureKind,
    TaskEvent,
    TaskRecord,
    TaskState,
)

log = get_logger("jiro.runtime.worker")


class WorkerPool:
    """Fixed-size pool of coroutines executing registered handlers.

    Args:
        repo: durable task storage.
        registry: closed set of executable handlers.
        config: validated runtime limits.
        instance_id: stable identity for this process, embedded in lease
            ownership so a lease can be traced to the worker that took it.
        metrics: optional sink for counters; must never raise.
    """

    def __init__(
        self,
        repo: TaskRepository,
        registry: HandlerRegistry,
        config: RuntimeConfig,
        *,
        instance_id: str,
        metrics: Optional[Callable[[str, str, Dict[str, float]], None]] = None,
        on_terminal: Optional[Callable[[TaskRecord], None]] = None,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._config = config
        self._instance_id = instance_id
        self._metrics = metrics
        self._on_terminal = on_terminal

        self._workers: List[asyncio.Task] = []
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._started = False
        #: The owning runtime, so a handler can query durable cancellation state
        #: through ``TaskContext.cancel_requested()``.
        self._runtime: Optional[object] = None
        #: task_id -> the asyncio Task currently executing it. The only channel
        #: through which an in-process cancellation reaches a handler.
        self._active: Dict[str, asyncio.Task] = {}

    def bind_runtime(self, runtime: object) -> None:
        """Give handlers a route to durable runtime state.

        Set once by :class:`~jiro.runtime.runtime.TaskRuntime` during
        construction so ``TaskContext.cancel_requested()`` works without the
        pool holding a reference back to its owner.
        """
        self._runtime = runtime

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        return self._started and not self._stopping.is_set()

    @property
    def worker_count(self) -> int:
        return len(self._workers)

    def active_count(self) -> int:
        return len(self._active)

    def active_task_ids(self) -> Set[str]:
        return set(self._active)

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Start the workers. Idempotent."""
        if self._started:
            return
        self._stopping.clear()
        loop = asyncio.get_running_loop()
        for index in range(self._config.max_concurrency):
            worker_id = f"{self._instance_id}:{index}"
            self._workers.append(
                loop.create_task(self._worker_loop(worker_id), name=f"jiro-runtime-worker-{index}")
            )
        self._started = True
        log.info(
            "runtime workers started",
            extra={
                "instance_id": self._instance_id,
                "workers": self._config.max_concurrency,
            },
        )
        self._count("workers_started", 1)

    async def stop(self, *, grace: Optional[float] = None) -> None:
        """Stop accepting new work, then wait for in-flight attempts.

        Args:
            grace: seconds to wait for running handlers. Defaults to the
                configured shutdown grace period. Once the grace period lapses
                the running attempts are cancelled; their leases are deliberately
                *not* released, so recovery reclaims them on the next start
                rather than assuming they stopped.
        """
        if not self._started:
            return
        grace = self._config.shutdown_grace if grace is None else grace
        self._stopping.set()
        self._wake.set()
        log.info(
            "runtime workers stopping",
            extra={
                "instance_id": self._instance_id,
                "grace": grace,
                "active": self.active_count(),
            },
        )

        if self._workers:
            done, pending = await asyncio.wait(self._workers, timeout=grace)
            if pending:
                # Grace lapsed. Cancel the attempts first so each gets to record
                # its own honest outcome, then the worker loops themselves.
                log.warning(
                    "shutdown grace elapsed; cancelling in-flight attempts",
                    extra={
                        "pending_workers": len(pending),
                        "active": self.active_count(),
                    },
                )
                self._count("shutdown_forced", len(pending))
                self._cancel_all_active()
                for task in pending:
                    task.cancel()
                await asyncio.wait(pending, timeout=5.0)
            self._workers = [t for t in self._workers if not t.cancelled()]
        self._started = False
        log.info(
            "runtime workers stopped",
            extra={
                "instance_id": self._instance_id,
            },
        )

    def _cancel_all_active(self) -> None:
        for task in list(self._active.values()):
            if not task.done():
                task.cancel()

    def wake(self) -> None:
        """Nudge idle workers after a submission, so work starts immediately."""
        self._wake.set()

    def cancel_task(self, task_id: str) -> bool:
        """Interrupt the attempt currently executing ``task_id``.

        Returns:
            ``False`` when no attempt for that task is running in this process.
            That is an honest answer, not a failure: the caller has still
            recorded cancellation intent durably.
        """
        task = self._active.get(task_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    # ------------------------------------------------------------------- claim
    async def _worker_loop(self, worker_id: str) -> None:
        # One bad iteration must never kill the worker. Without this, an
        # exception from the storage layer would silently reduce the pool and
        # the runtime would report "healthy" while executing nothing.
        while not self._stopping.is_set():
            try:
                record = await self._repo.claim(
                    worker_id=worker_id,
                    now=time.time(),
                    lease_ttl=self._config.lease_ttl,
                )
                if record is None:
                    await self._idle()
                    continue
                await self._run_attempt(worker_id, record)
            except asyncio.CancelledError:
                # Either the pool is shutting down or this worker was force
                # cancelled; both end the loop, neither is an error.
                if self._stopping.is_set():
                    return
                raise
            except (KeyboardInterrupt, SystemExit):
                # Operator signals must still reach the process.
                raise
            except BaseException:
                # Everything else — including a bare BaseException from our own
                # failure bookkeeping — must not permanently remove a worker
                # from the pool. Losing one silently shrinks concurrency while
                # the runtime still reports itself healthy, which is the worst
                # possible failure mode for a scheduler.
                log.exception("runtime worker iteration failed", extra={"worker_id": worker_id})
                self._count("worker_errors", 1)
                # Back off briefly so a persistent failure cannot spin the CPU
                # burning the write lock.
                await asyncio.sleep(min(self._config.poll_interval * 4, 5.0))

    async def _run_attempt(self, worker_id: str, record: TaskRecord) -> None:
        """Execute one attempt inside its own registered task.

        The attempt runs in a child task so that :meth:`cancel_task` has
        something to interrupt without tearing down the worker loop. The child
        is shielded so that cancelling the *worker* during shutdown does not
        orphan an attempt mid-flight.
        """
        child = asyncio.create_task(self._execute(worker_id, record), name=f"jiro-task-{record.id}")
        self._active[record.id] = child
        heartbeat = asyncio.create_task(
            self._renew_lease(worker_id, record), name=f"jiro-lease-{record.id}"
        )
        try:
            await asyncio.shield(child)
        except asyncio.CancelledError:
            # The child handled its own cancellation and recorded the outcome.
            # Anything still pending here is a force-shutdown of the worker.
            if not child.done():
                raise
        except Exception:
            # `_execute` classifies and records its own failures; reaching here
            # means storage itself failed while recording, which is already
            # logged. Swallow so the worker keeps polling.
            log.exception(
                "attempt bookkeeping failed",
                extra={
                    "task_id": record.id,
                    "attempt": record.attempts,
                },
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self._active.pop(record.id, None)

    async def _renew_lease(self, worker_id: str, record: TaskRecord) -> None:
        """Extend this attempt's lease while the handler runs.

        Renewal is what lets ``lease_ttl`` stay short: the lease tracks the
        handler's real liveness, so recovery after a hard kill is bounded by the
        TTL rather than by the longest permitted attempt.

        Losing the lease does not abort this attempt — it keeps running to
        completion — but it is logged, because another instance may now consider
        the task abandoned and re-execute it.
        """
        interval = self._config.heartbeat_interval
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            try:
                now = time.time()
                held = await self._repo.renew_lease(
                    task_id=record.id,
                    worker_id=worker_id,
                    expires_at=now + self._config.lease_ttl,
                    now=now,
                )
            except asyncio.CancelledError:
                return
            except Exception:
                # Transient storage trouble: retry next tick rather than
                # abandoning a lease we may still hold.
                log.warning(
                    "lease renewal failed",
                    extra={"task_id": record.id, "worker_id": worker_id},
                    exc_info=True,
                )
                continue
            if not held:
                log.warning(
                    "lease lost while executing",
                    extra={
                        "task_id": record.id,
                        "worker_id": worker_id,
                        "attempt": record.attempts,
                    },
                )
                self._count("lease_lost")
                return

    async def _idle(self) -> None:
        """Sleep until woken or the poll interval elapses."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self._config.poll_interval)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    # ---------------------------------------------------------------- execute
