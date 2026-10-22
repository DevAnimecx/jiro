"""Public facade of the Jiro durable task runtime.

This is the only object transport adapters (REST, CLI, MCP) talk to. It owns
validation, durable submission, cancellation and lifecycle; it contains no
SQL and no framework types.

Usage::

    runtime = await TaskRuntime.create(settings, db, registry)
    await runtime.start()
    record = await runtime.submit("my_type", {"q": "x"}, owner_key_id="key_abc")
    await runtime.cancel(record.id, owner_key_id="key_abc")
    await runtime.stop()
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from jiro.db import Database
from jiro.log import get_logger
from jiro.runtime.config import RuntimeConfig
from jiro.runtime.errors import StorageFailure, TaskNotFound
from jiro.runtime.handlers import HandlerRegistry
from jiro.runtime.recovery import RecoveryManager, RecoveryReport
from jiro.runtime.repository import DuplicateTask, NewTask, TaskRepository
from jiro.runtime.sqlite_repo import SqlTaskRepository
from jiro.runtime.types import (
    EventType,
    TaskEvent,
    TaskRecord,
    TaskState,
    new_task_id,
)
from jiro.runtime.validation import resolve_limits, validate_input

log = get_logger("jiro.runtime")

#: Tables the runtime requires to exist. Checked at startup so a broken or
#: half-migrated database fails immediately with a precise message.
REQUIRED_TABLES = ["tasks", "task_events"]


def default_instance_id() -> str:
    """Stable-per-process identity embedded in every lease this pool takes.

    Including it in the lease owner is what lets an operator tell which process
    a stuck task belonged to, and is what makes a stale lease attributable.
    """
    try:
        host = socket.gethostname()
    except Exception:  # pragma: no cover - defensive
        host = "unknown-host"
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class TaskRuntime:
    """Durable, bounded, cancellable task execution.

    Args:
        repo: durable storage. Defaults to SQLite over ``db``.
        registry: the closed set of executable handlers.
        config: validated limits.
        instance_id: identity stamped into leases.
    """

    def __init__(
        self,
        repo: TaskRepository,
        registry: HandlerRegistry,
        config: RuntimeConfig,
        *,
instance_id: Optional[str] = None,
        metrics: Optional[Callable[[str, str, Dict[str, float]], None]] = None,
        on_terminal: Optional[Callable[[Any], Any]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        from jiro.runtime.worker import WorkerPool

        self._repo = repo
        self._registry = registry
        self._config = config
        self._instance_id = instance_id or default_instance_id()
        self._metrics = metrics
        self._clock = clock
        self._pool = WorkerPool(
            repo, registry, config,
            instance_id=self._instance_id,
            metrics=metrics,
            on_terminal=on_terminal,
        )
        self._pool.bind_runtime(self)
        self._recovery = RecoveryManager(repo, clock=clock, metrics=metrics)
        self._started = False
        self._stopping = False
        self._reconciler: Optional[asyncio.Task] = None
        self._last_report: Optional[RecoveryReport] = None

    # ------------------------------------------------------------- construction
    @classmethod
    async def create(
        cls,
        settings: Any,
        db: Database,
        registry: HandlerRegistry,
        *,
        instance_id: Optional[str] = None,
        metrics: Optional[Callable[[str, str, Dict[str, float]], None]] = None,
        on_terminal: Optional[Callable[[Any], Any]] = None,
    ) -> "TaskRuntime":
        """Build a runtime from project settings and an open database.

        Storage location defaults to the main Jiro database so the process keeps
        a single SQLite writer. An explicit ``runtime.db_path`` opens a separate
        file, which is only appropriate for a dedicated worker process.
        """
        config = RuntimeConfig.from_settings(settings)
        path = config.resolve_db_path(settings.db_path)

        if path == settings.db_path:
            repo: TaskRepository = SqlTaskRepository(db)
        else:
            from pathlib import Path

            Path(path).parent.mkdir(parents=True, exist_ok=True)
            own_db = Database(path)
            await own_db.connect()
            await own_db.verify_schema(REQUIRED_TABLES)
            repo = SqlTaskRepository(own_db)

        await db.verify_schema(REQUIRED_TABLES)
        return cls(repo, registry, config, instance_id=instance_id,
                   metrics=metrics, on_terminal=on_terminal)

    # ------------------------------------------------------------------ access
    @property
    def config(self) -> RuntimeConfig:
        return self._config

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def started(self) -> bool:
        return self._started

    @property
    def registry(self) -> HandlerRegistry:
        return self._registry

    @property
    def repo(self) -> TaskRepository:
        return self._repo

    def stats(self) -> Dict[str, Any]:
        """Synchronous snapshot for health and metrics endpoints."""
        return {
            "instance_id": self._instance_id,
            "started": self._started,
            "workers": self._pool.worker_count,
            "active": self._pool.active_count(),
            "enabled": self._config.enabled,
        }

    async def queue_depth(self) -> Dict[str, int]:
        return await self._repo.queue_depth()

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> RecoveryReport:
        """Reconcile durable state, then start the workers.

        Recovery runs *before* any worker can claim, so a task recovered into
        ``queued`` or ``retry_wait`` cannot be picked up by a worker that has not
        yet seen its prior state.
        """
        self._stopping = False
        report = await self._recovery.reconcile()
        self._last_report = report
        if self._config.enabled:
            await self._pool.start()
            self._reconciler = asyncio.create_task(
                self._reconcile_loop(), name="jiro-runtime-reconciler"
            )
            self._started = True
        else:
            log.warning(
                "runtime disabled by configuration; workers not started",
                extra={"instance_id": self._instance_id},
            )
        self._wake_workers()
        return report

    async def _reconcile_loop(self) -> None:
        """Periodically re-reconcile durable state.

        Startup recovery alone leaves a hole: a task whose owner process dies
        while this process keeps running holds a live lease, so recovery at
        startup legitimately skips it, and it would then sit in ``running``
        until the next restart. The lease only becomes reclaimable once it
        lapses, so the sweep has to keep running.
        """
        interval = self._config.reconcile_interval
        if interval <= 0:
            return
        while self._started or self._pool.running:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            if self._stopping:
                return
            try:
                report = await self._recovery.reconcile()
                if report.total_changed:
                    self._last_report = report
                    self._wake_workers()
            except asyncio.CancelledError:
                return
            except Exception:
                # A failed sweep must never take the runtime down; the next
                # tick retries against the same durable state.
                log.exception(
                    "periodic reconciliation failed", extra={"instance_id": self._instance_id}
                )

    async def stop(self, *, grace: Optional[float] = None) -> None:
        """Stop the workers and await in-flight attempts up to the grace period."""
        self._stopping = True
        reconciler = self._reconciler
        self._reconciler = None
        if reconciler is not None and not reconciler.done():
            reconciler.cancel()
            # Await it so the task is actually gone before returning; a bare
            # cancel() would leave a pending task warning behind.
            await asyncio.gather(reconciler, return_exceptions=True)
        await self._pool.stop(grace=grace)
        self._started = False
        await self._prune_events()

    async def recover(self) -> RecoveryReport:
        """Run a reconciliation pass on demand (the ``jiro tasks recover`` path)."""
        report = await self._recovery.reconcile()
        self._last_report = report
        self._wake_workers()
        return report

    @property
    def last_recovery_report(self) -> Optional[RecoveryReport]:
        return self._last_report

    # ------------------------------------------------------------------- submit
    async def submit(
        self,
        task_type: str,
        payload: Mapping[str, Any],
        *,
        owner_key_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        limits: Optional[Mapping[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> TaskRecord:
        """Validate and durably persist a task.

        Durable acceptance is the contract here: this coroutine does not return
        until the row and its ``task.submitted`` event are committed. A caller
        that receives a record knows the work will be executed after a restart.

        Args:
            task_type: a registered handler type.
            payload: task input, validated against the handler's schema.
            owner_key_id: the authenticated caller, used for authorization on
                every later read and cancel.
            idempotency_key: optional per-owner dedupe key.
            limits: optional execution-limit overrides.
            task_id: supply an id to make submission itself idempotent; otherwise
                one is generated.

        Returns:
            The stored :class:`~jiro.runtime.types.TaskRecord`.

        Raises:
            UnknownTaskType: no handler is registered for ``task_type``.
            TaskValidationError: the payload failed schema validation.
            TaskLimitExceeded: a requested limit exceeds a server ceiling.
        """
        spec = self._registry.get(task_type)
        cleaned = validate_input(spec.schema, payload)
        now = self._clock()
        resolved = resolve_limits(self._config, limits, now=now)

        new = NewTask(
            id=task_id or new_task_id(),
            type=task_type,
            owner_key_id=owner_key_id,
            idempotency_key=idempotency_key,
            input=cleaned,
            limits=resolved,
            created_at=now,
        )
        # The row and both submission events land in one transaction. Splitting
        # them would let a polling worker claim the task and record
        # `execution.started` before `task.validated` was written.
        events = [
            TaskEvent(
                task_id=new.id,
                type=EventType.SUBMITTED,
                ts=now,
                metadata={"type": task_type, "has_idempotency_key": bool(idempotency_key)},
            ),
            TaskEvent(
                task_id=new.id,
                type=EventType.VALIDATED,
                ts=now,
                metadata={"fields": len(cleaned), "max_attempts": resolved.max_attempts},
            ),
        ]
        try:
            record = await self._repo.insert_with_events(new, events, now=now)
        except DuplicateTask as exc:
            existing = await self._repo.get(exc.task_id)
            if existing is None:  # pragma: no cover - defensive
                raise StorageFailure("idempotent task lookup failed") from exc
            log.info(
                "idempotent submission returned existing task",
                extra={
                    "task_id": existing.id,
                    "type": task_type,
                    "owner_key_id": owner_key_id,
                },
            )
            return existing

        log.info(
            "task submitted",
            extra={
                "task_id": record.id,
                "type": task_type,
                "owner_key_id": owner_key_id,
                "max_attempts": resolved.max_attempts,
                "deadline_at": resolved.deadline,
            },
        )
        self._count("tasks_submitted")
        self._wake_workers()
        return record

    # --------------------------------------------------------------------- read
    async def get(
        self,
        task_id: str,
        *,
        owner_key_id: Optional[str] = None,
        enforce_owner: bool = True,
    ) -> TaskRecord:
        """Fetch one task, enforcing ownership.

        Args:
            task_id: the task to fetch.
            owner_key_id: the caller's identity. Required when
                ``enforce_owner`` is true.
            enforce_owner: when false, skips the ownership comparison. Reserved
                for internal reconciliation and administrative tooling; a
                transport adapter must leave it on.

        Raises:
            TaskNotFound: the task does not exist **or** belongs to someone
                else. The two cases are deliberately indistinguishable so the
                API cannot be used to enumerate other users' task ids.
        """
        record = await self._repo.get(task_id)
        if record is None:
            raise TaskNotFound(f"no such task: {task_id}", details={"task_id": task_id})
        if enforce_owner and not _owns(record, owner_key_id):
            raise TaskNotFound(f"no such task: {task_id}", details={"task_id": task_id})
        return record

    async def list(
        self,
        *,
        owner_key_id: Optional[str],
        limit: int = 50,
        offset: int = 0,
        states: Optional[Sequence[TaskState]] = None,
    ) -> List[TaskRecord]:
        """List one owner's tasks, newest first.

        Args:
            owner_key_id: **required and keyword-only**. ``None`` is not a
                default — it is a real value meaning "the anonymous namespace",
                which is what an install running with auth disabled uses.
                Making it mandatory removes the ambiguity between "I meant
                anonymous" and "I forgot to scope this query", which would
                otherwise be an unscoped read of every task in the database.
        """
        return await self._repo.list_for_owner(
            owner_key_id, limit=limit, offset=offset, states=states
        )

    async def count(self, *, owner_key_id: Optional[str] = None) -> int:
        return await self._repo.count_for_owner(owner_key_id)

    async def events(
        self, task_id: str, *, owner_key_id: Optional[str] = None, limit: int = 100
    ) -> List[TaskEvent]:
        """Lifecycle events for a task the caller owns.

        Raises:
            TaskNotFound: unknown task, or one owned by another caller.
        """
        await self.get(task_id, owner_key_id=owner_key_id)
        return await self._repo.events_for(task_id, limit=limit)

    async def result(
        self, task_id: str, *, owner_key_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """The stored result of a succeeded task, or ``None``."""
        record = await self.get(task_id, owner_key_id=owner_key_id)
        if not record.is_terminal:
            return None
        return record.result

    async def wait_for(
        self,
        task_id: str,
        *,
        timeout: float = 30.0,
        poll: float = 0.05,
        owner_key_id: Optional[str] = None,
    ) -> TaskRecord:
        """Poll until the task reaches a terminal state or ``timeout`` elapses.

        Returns:
            The terminal record, or the latest non-terminal record on timeout —
            never a fabricated success.
        """
        deadline = time.monotonic() + timeout
        while True:
            record = await self.get(task_id, owner_key_id=owner_key_id)
            if record.is_terminal or time.monotonic() >= deadline:
                return record
            await asyncio.sleep(poll)

    # ------------------------------------------------------------------ cancel
    async def cancel(
        self,
        task_id: str,
        *,
        owner_key_id: Optional[str] = None,
        enforce_owner: bool = True,
    ) -> TaskRecord:
        """Request cancellation and report the actual resulting state.

        The returned record is the truth. For a running cooperative task the
        state remains ``running`` with ``cancel_requested`` set until the handler
        unwinds; this method never claims a stop that has not happened.

        Raises:
            TaskNotFound: unknown task, or one owned by another caller.
        """
        record = await self.get(task_id, owner_key_id=owner_key_id, enforce_owner=enforce_owner)
        now = self._clock()

        if record.is_terminal:
            # Idempotent: repeated cancellation of a finished task is a no-op
            # that reports the terminal state, never an error and never a
            # resurrection.
            log.info(
                "cancel requested for terminal task",
                extra={
                    "task_id": task_id,
                    "state": record.state.value,
                },
            )
            return record

        await self._record(
            record.id,
            EventType.CANCELLATION_REQUESTED,
            record.attempts,
            {"state": record.state.value},
        )

        # Queued and retry-waiting work has no handler to interrupt, so the
        # terminal transition can be made unconditional. The state guard makes it
        # safe against a worker claiming the task in the same instant.
        if record.state in (TaskState.QUEUED, TaskState.RETRY_WAIT):
            cancelled = await self._repo.mark_cancelled(
                task_id=task_id,
                now=now,
                from_states=[TaskState.QUEUED, TaskState.RETRY_WAIT],
            )
            if cancelled:
                self._count("tasks_cancelled")
                await self._record(task_id, EventType.CANCELLED, record.attempts, {})
                log.info(
                    "task cancelled before execution",
                    extra={
                        "task_id": task_id,
                        "state": record.state.value,
                    },
                )
            return await self._repo.get(task_id)  # type: ignore[return-value]

        # Running: persist intent, then interrupt the in-process attempt if we
        # own it. A task executing in another process keeps running until its
        # lease lapses — reported honestly rather than faked.
        await self._repo.request_cancel(task_id)
        interrupted = self._pool.cancel_task(task_id)
        log.info(
            "cancellation requested",
            extra={
                "task_id": task_id,
                "state": record.state.value,
                "interrupted": interrupted,
            },
        )
        self._count("cancellations_requested")
        return await self._repo.get(task_id)  # type: ignore[return-value]

    async def is_cancel_requested(self, task_id: str) -> bool:
        """Whether cancellation has been durably requested. Used by handlers."""
        record = await self._repo.get(task_id)
        return bool(record and record.cancel_requested)

    # ------------------------------------------------------------------ events
    async def events_for(self, task_id: str, *, limit: int = 100) -> List[TaskEvent]:
        return await self._repo.events_for(task_id, limit=limit)

    # ----------------------------------------------------------------- helpers
    async def _record(
        self,
        task_id: str,
        event_type: EventType,
        attempt: Optional[int],
        metadata: Dict[str, Any],
    ) -> None:
        try:
            await self._repo.append_event(
                TaskEvent(
                    task_id=task_id,
                    type=event_type,
                    ts=self._clock(),
                    attempt=attempt,
                    metadata=metadata,
                )
            )
        except Exception:
            log.exception(
                "failed to record lifecycle event",
                extra={
                    "task_id": task_id,
                    "event": event_type.value,
                },
            )

    async def _prune_events(self) -> None:
        days = self._config.event_retention_days
        if days <= 0:
            return
        cutoff = self._clock() - days * 86400
        try:
            removed = await self._repo.prune_events(older_than=cutoff)
            if removed:
                log.info(
                    "pruned task events",
                    extra={
                        "removed": removed,
                        "older_than_days": days,
                    },
                )
        except Exception:
            log.exception("failed to prune task events")

    def _wake_workers(self) -> None:
        if self._config.enabled:
            self._pool.wake()

    def _count(self, name: str, value: float = 1.0) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics(f"jiro_runtime_{name}", "", {"value": value})
        except Exception:
            log.debug("metrics sink raised", exc_info=True)


def _owns(record: TaskRecord, owner_key_id: Optional[str]) -> bool:
    """Whether ``owner_key_id`` may operate on ``record``.

    A task with no owner can only be touched by a caller with no identity
    either — the runtime itself, or a local single-user install. A task *with*
    an owner is visible only to that owner.
    """
    if record.owner_key_id is None:
        return owner_key_id is None
    return record.owner_key_id == owner_key_id


__all__ = ["REQUIRED_TABLES", "TaskRuntime", "default_instance_id"]
