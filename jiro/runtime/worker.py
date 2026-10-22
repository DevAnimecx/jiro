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
    async def _execute(self, worker_id: str, record: TaskRecord) -> None:
        """Run one attempt and durably record its outcome."""
        started = time.monotonic()
        try:
            spec = self._registry.get(record.type)
        except UnknownTaskType as exc:
            # The type is no longer registered — a deploy removed the handler,
            # or the task predates the current build. Retrying cannot fix that,
            # so fail it permanently rather than let the worker spin on it
            # forever and hold its concurrency slot.
            code, message = sanitize_error(exc)
            await self._repo.complete_failure(
                task_id=record.id,
                worker_id=worker_id,
                error_code=code,
                error_message=message,
                now=time.time(),
            )
            self._count("tasks_failed", 1)
            await self._record_event(
                record.id,
                EventType.FAILED,
                record.attempts,
                {"error_code": code, "reason": "unregistered_type"},
            )
            log.error(
                "task type is not registered",
                extra={
                    "task_id": record.id,
                    "type": record.type,
                },
            )
            return

        await self._record_event(
            record.id,
            EventType.EXECUTION_STARTED,
            record.attempts,
            {"worker": worker_id, "attempt": record.attempts},
        )
        ctx = TaskContext(
            task_id=record.id,
            task_type=record.type,
            input=record.input,
            attempt=record.attempts,
            idempotency_key=record.idempotency_key,
            deadline_at=record.limits.deadline,
            limits=record.limits,
            owner_key_id=record.owner_key_id,
            runtime=self._runtime,
        )
        try:
            result = await self._run_handler(ctx, spec.handler, record.limits.attempt_timeout)
        except asyncio.CancelledError:
            # Either the runtime cancelled this task, or the pool is shutting
            # down. Re-check the durable cancellation flag rather than assuming:
            # a shutdown must not be recorded as a user-requested cancellation.
            await self._on_cancelled(record, worker_id)
            raise
        except BaseException as exc:  # noqa: BLE001 - classified immediately below
            # BaseException so a handler raising e.g. KeyboardInterrupt cannot
            # bypass failure recording; classification decides the outcome.
            await self._on_failure(record, worker_id, exc, started)
            if isinstance(exc, Exception):
                return
            raise

        elapsed = time.monotonic() - started
        encoded = await asyncio.to_thread(_encode_result, result, record.limits.max_result_bytes)
        if isinstance(encoded, str):
            # Result exceeded the task's cap. Treated as a permanent failure:
            # retrying an oversized payload cannot make it smaller.
            await self._on_failure(
                record,
                worker_id,
                _oversize(encoded),
                started,
            )
            return

        await self._repo.complete_success(
            task_id=record.id, worker_id=worker_id, result=encoded, now=time.time()
        )
        self._observe(record.id, "succeeded", elapsed)
        self._count("tasks_succeeded", 1)
        await self._record_event(
            record.id,
            EventType.SUCCEEDED,
            record.attempts,
            {"duration_ms": round(elapsed * 1000, 2)},
        )
        await self._emit_terminal(record.id)

    async def _run_handler(self, ctx: TaskContext, handler, timeout: float):
        """Await ``handler(ctx)`` under a wall-clock attempt timeout.

        ``wait_for`` cancels the inner task on timeout, so an async handler that
        cooperates with cancellation stops promptly. A handler that ignores
        cancellation cannot be forced, which is why sync handlers are refused at
        registration and why the attempt timeout is documented as the backstop
        rather than a guarantee.
        """
        return await asyncio.wait_for(handler(ctx), timeout=timeout)

    # --------------------------------------------------------------- outcomes
    async def _on_failure(
        self, record: TaskRecord, worker_id: str, exc: BaseException, started: float
    ) -> None:
        kind = classify_failure(exc)
        code, message = sanitize_error(exc)
        elapsed = time.monotonic() - started
        log.warning(
            "task attempt failed",
            extra={
                "task_id": record.id,
                "type": record.type,
                "attempt": record.attempts,
                "failure_kind": kind.value,
                "error_code": code,
            },
        )

        await self._record_event(
            record.id,
            EventType.ATTEMPT_FAILED,
            record.attempts,
            {
                "failure_kind": kind.value,
                "error_code": code,
                "duration_ms": round(elapsed * 1000, 2),
            },
        )
        if kind is FailureKind.TIMEOUT:
            self._count("task_timeouts", 1)

        policy = RetryPolicy(record.limits)
        decision = policy.decide(
            kind=kind,
            attempts=record.attempts,
            deadline_at=record.limits.deadline,
        )
        now = time.time()

        if decision.retry and decision.delay is not None:
            available_at = now + decision.delay
            scheduled = await self._repo.schedule_retry(
                task_id=record.id,
                worker_id=worker_id,
                available_at=available_at,
                error_code=code,
                error_message=message,
                now=now,
            )
            if scheduled:
                log.info(
                    "task retry scheduled",
                    extra={
                        "task_id": record.id,
                        "attempt": record.attempts,
                        "delay": round(decision.delay, 3),
                        "reason": decision.reason,
                    },
                )
                self._count("task_retries", 1)
                await self._record_event(
                    record.id,
                    EventType.RETRY_SCHEDULED,
                    record.attempts,
                    {
                        "delay": round(decision.delay, 3),
                        "reason": decision.reason,
                        "failure_kind": kind.value,
                    },
                )
                self._observe(record.id, "retry_wait", elapsed)
                return

        failed = await self._repo.complete_failure(
            task_id=record.id,
            worker_id=worker_id,
            error_code=code,
            error_message=message,
            now=now,
        )
        if failed:
            self._count("tasks_failed", 1)
            await self._record_event(
                record.id,
                EventType.FAILED,
                record.attempts,
                {"error_code": code, "reason": decision.reason, "failure_kind": kind.value},
            )
            await self._emit_terminal(record.id)
        self._observe(record.id, "failed", elapsed)

    async def _on_cancelled(self, record: TaskRecord, worker_id: str) -> None:
        """Persist the honest outcome of an interrupted attempt.

        Only records ``cancelled`` when the runtime's cancellation flag is
        durably set. A shutdown-driven interruption leaves the lease in place so
        recovery reclaims the task and re-runs it — which is the truthful
        description of what happened.
        """
        now = time.time()
        flag = await self._repo.request_cancel(record.id)
        current = await self._repo.get(record.id)
        state = current.state if current else record.state

        if state is TaskState.CANCELLED:
            return

        if flag and state is TaskState.RUNNING:
            await self._repo.set_state(
                task_id=record.id,
                state=TaskState.CANCELLED,
                now=now,
                reason="cancelled_by_request",
            )
            self._count("tasks_cancelled", 1)
            await self._record_event(
                record.id, EventType.EXECUTION_CANCELLED, record.attempts, {"worker": worker_id}
            )
            await self._record_event(record.id, EventType.CANCELLED, record.attempts, {})
            await self._emit_terminal(record.id)
            log.info(
                "task cancelled",
                extra={
                    "task_id": record.id,
                    "attempt": record.attempts,
                },
            )
            return

        # Interrupted without a user request (pool shutdown). Leave the task
        # running with a lapsing lease so recovery handles it on next start.
        log.info(
            "attempt interrupted by shutdown; lease left for recovery",
            extra={
                "task_id": record.id,
                "attempt": record.attempts,
            },
        )
        self._count("attempts_interrupted", 1)

    # ----------------------------------------------------------------- helpers
    async def _record_event(
        self,
        task_id: str,
        event_type: EventType,
        attempt: Optional[int],
        metadata: Dict[str, object],
    ) -> None:
        """Persist a lifecycle event; never let observability break execution."""
        try:
            await self._repo.append_event(
                TaskEvent(
                    task_id=task_id,
                    type=event_type,
                    ts=time.time(),
                    attempt=attempt,
                    metadata=dict(metadata),
                )
            )
        except Exception:
            log.exception(
                "failed to record task event",
                extra={
                    "task_id": task_id,
                    "event": event_type.value,
                },
            )
            self._count("event_write_failures", 1)

    async def _emit_terminal(self, task_id: str) -> None:
        """Notify the terminal-state hook, awaiting it when it is a coroutine.

        Awaiting matters: an async hook that was merely scheduled could be
        cancelled by a shutdown landing before it committed its own write.
        """
        if self._on_terminal is None:
            return
        try:
            record = await self._repo.get(task_id)
            if record is None:
                return
            outcome = self._on_terminal(record)
            if inspect.isawaitable(outcome):
                await outcome
        except asyncio.CancelledError:
            raise
        except Exception:
            # A terminal hook must never be able to fail a completed task. The
            # task's own state is already committed at this point.
            log.exception("terminal callback failed", extra={"task_id": task_id})

    def _observe(self, task_id: str, state: str, elapsed: float) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics("jiro_task_duration_seconds", state, {"seconds": elapsed})
        except Exception:
            log.debug("metrics sink raised", exc_info=True)

    def _count(self, name: str, value: float = 1.0) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics(f"jiro_runtime_{name}", "", {"value": value})
        except Exception:
            log.debug("metrics sink raised", exc_info=True)


def _encode_result(result: Any, max_bytes: int):
    """Serialise and size-check a handler result.

    Returns:
        The decoded dict on success, or an error string when the payload is
        oversized or not JSON-serialisable.
    """
    import json

    try:
        encoded = json.dumps(result, default=str)
    except (TypeError, ValueError) as exc:
        return f"result is not JSON-serialisable: {exc}"
    if len(encoded.encode("utf-8")) > max_bytes:
        return f"result exceeds {max_bytes} bytes"
    if not isinstance(result, dict):
        # Normalised to an object so the persisted column is always JSON-object
        # shaped and the API contract stays stable.
        return {"value": result}
    try:
        return json.loads(encoded)
    except ValueError:  # pragma: no cover - round-trip of our own output
        return {"value": result}


def _oversize(detail: str) -> ResultTooLarge:
    """Build the marker that routes an oversized result into the failure path.

    ``ResultTooLarge`` is classified as permanent, so the task fails
    immediately instead of burning its retry budget on a payload that cannot
    shrink.
    """
    return ResultTooLarge(detail)


__all__ = ["WorkerPool"]
