"""Storage interface for durable tasks.

The runtime depends on this abstraction, never on SQL. That keeps the worker
and the transport adapters free of database knowledge, and lets unit tests use
an in-memory double while every integration test runs against the real SQLite
implementation.

Design rules that the concrete implementation must honour:

* **Claiming is atomic.** :meth:`claim` must be safe against two workers racing.
  No read-then-write sequence is acceptable.
* **Terminal writes are idempotent.** Completing an already-terminal task is a
  no-op that reports the conflict, never a corruption.
* **Lease ownership is conditional.** A write from a worker that no longer
  holds the lease must fail rather than overwrite recovered state.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from jiro.runtime.types import EventType, TaskEvent, TaskLimits, TaskRecord, TaskState


@dataclass(frozen=True)
class NewTask:
    """A validated task awaiting its first insertion.

    Produced by the runtime facade after input and limit validation; the
    repository only persists it.
    """

    id: str
    type: str
    owner_key_id: Optional[str]
    idempotency_key: Optional[str]
    input: Dict[str, Any]
    limits: TaskLimits
    created_at: float


class TaskRepository(abc.ABC):
    """Durable storage operations for the task runtime."""

    # ------------------------------------------------------------------ writes
    @abc.abstractmethod
    async def insert(self, task: NewTask, *, now: float) -> TaskRecord:
        """Persist a new task in ``queued``.

        Returns:
            The stored record.

        Raises:
            DuplicateTask: when ``(owner_key_id, idempotency_key)`` already
                exists. The caller resolves this by returning the existing task.
        """

    @abc.abstractmethod
    async def insert_with_events(
        self, task: NewTask, events: Sequence[TaskEvent], *, now: float
    ) -> TaskRecord:
        """Insert a task and its submission events as one atomic unit.

        Written to close a real race: if the row committed before its events, a
        worker polling every few milliseconds could claim the task and emit
        ``execution.started`` before ``task.validated`` was recorded, leaving a
        lifecycle stream in an order no reader would expect.
        """

    @abc.abstractmethod
    async def renew_lease(
        self, *, task_id: str, worker_id: str, expires_at: float, now: float
    ) -> bool:
        """Extend the lease held by ``worker_id``.

        Called periodically while a handler runs. Without renewal a lease must
        outlive the longest permitted attempt, which makes stale-task recovery
        as slow as that worst case even for tasks that take milliseconds.
        Renewal lets the TTL track real liveness instead.

        Returns:
            ``False`` if the worker no longer holds the lease, which means the
            task was reclaimed and this worker must stop touching it.
        """

    @abc.abstractmethod
    async def claim(self, *, worker_id: str, now: float, lease_ttl: float) -> Optional[TaskRecord]:
        """Atomically transition one eligible task to ``running``.

        Eligible means: state in ``{queued, retry_wait}``, ``available_at`` due,
        deadline not passed, and not already flagged for cancellation.

        Returns:
            The claimed record, or ``None`` when no work is available.

        Note:
            Must be a single conditional UPDATE. A worker that loses the race
            gets ``None``; it must never block on the winner.
        """

    @abc.abstractmethod
    async def complete_success(
        self, *, task_id: str, worker_id: str, result: Dict[str, Any], now: float
    ) -> bool:
        """Record a successful result and transition to ``succeeded``.

        Returns:
            ``True`` if this call performed the transition, ``False`` if the
            task was already terminal or the lease was lost.
        """

    @abc.abstractmethod
    async def complete_failure(
        self,
        *,
        task_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        now: float,
    ) -> bool:
        """Record a terminal failure and transition to ``failed``."""

    @abc.abstractmethod
    async def schedule_retry(
        self,
        *,
        task_id: str,
        worker_id: str,
        available_at: float,
        error_code: str,
        error_message: str,
        now: float,
    ) -> bool:
        """Transition to ``retry_wait`` until ``available_at``.

        Returns:
            ``True`` if the transition happened, ``False`` if the task was
            already terminal or the lease was lost.
        """

    @abc.abstractmethod
    async def mark_cancelled(
        self, *, task_id: str, now: float, from_states: Sequence[TaskState]
    ) -> bool:
        """Transition a non-running task straight to ``cancelled``.

        Used for queued and retry-waiting tasks, where no handler is executing
        and cancellation can be made unconditional.

        Returns:
            ``True`` if this call cancelled the task.
        """

    @abc.abstractmethod
    async def request_cancel(self, task_id: str) -> bool:
        """Durably flag cancellation intent for a running task.

        This records intent only. The state stays ``running`` until the handler
        actually unwinds, so the database never claims a stop that has not
        happened.

        Returns:
            ``True`` if the flag was newly set.
        """

    @abc.abstractmethod
    async def set_state(
        self,
        *,
        task_id: str,
        state: TaskState,
        now: float,
        reason: Optional[str] = None,
        available_at: Optional[float] = None,
    ) -> bool:
        """Administrative state change used by recovery.

        Validated against the lifecycle transition table. Recovery must be able
        to move ``running`` to ``retry_wait`` or ``failed`` without holding a
        lease, so unlike the worker paths this takes no ``worker_id``.

        Args:
            available_at: wake-up time to persist when moving into
                ``retry_wait``. Recovery recomputes it from the retry policy
                because the backoff for an interrupted attempt differs from the
                one the worker had computed before it died.
        """

    # ------------------------------------------------------------------- reads
    @abc.abstractmethod
    async def get(self, task_id: str) -> Optional[TaskRecord]:
        """Fetch one task, or ``None`` if it does not exist."""

    @abc.abstractmethod
    async def find_by_idempotency_key(
        self, *, owner_key_id: Optional[str], idempotency_key: str
    ) -> Optional[TaskRecord]:
        """Fetch a task by its owner-scoped idempotency key."""

    @abc.abstractmethod
    async def list_for_owner(
        self,
        owner_key_id: Optional[str],
        *,
        limit: int = 50,
        offset: int = 0,
        states: Optional[Sequence[TaskState]] = None,
    ) -> List[TaskRecord]:
        """List a single owner's tasks, newest first.

        Scoping here rather than in the router is deliberate: an unscoped list
        must not be reachable by a transport adapter by accident.
        """

    @abc.abstractmethod
    async def count_for_owner(self, owner_key_id: Optional[str]) -> int:
        """Total task count for one owner."""

    @abc.abstractmethod
    async def list_by_state(self, state: TaskState, *, limit: int = 200) -> List[TaskRecord]:
        """Tasks in one state, across every owner.

        Used only by startup recovery, which must reconcile the whole system
        rather than a single tenant's slice of it.
        """

    @abc.abstractmethod
    async def queue_depth(self) -> Dict[str, int]:
        """Number of tasks in each non-terminal state. Used by metrics."""

    # ------------------------------------------------------------------ events
    @abc.abstractmethod
    async def append_event(self, event: TaskEvent, *, conn: Any = None) -> None:
        """Record a lifecycle event.

        Args:
            event: the event to persist.
            conn: an open transaction connection, when the event must be
                atomic with a state change. ``None`` autocommits.
        """

    @abc.abstractmethod
    async def events_for(self, task_id: str, *, limit: int = 100) -> List[TaskEvent]:
        """Lifecycle events for one task, oldest first."""

    # ---------------------------------------------------------------- recovery
    @abc.abstractmethod
    async def expired_leases(self, *, now: float, limit: int = 100) -> List[TaskRecord]:
        """Running tasks whose lease has lapsed — the worker died mid-flight."""

    @abc.abstractmethod
    async def due_retries(self, *, now: float, limit: int = 100) -> List[TaskRecord]:
        """``retry_wait`` tasks whose backoff has elapsed."""

    @abc.abstractmethod
    async def prune_events(self, *, older_than: float) -> int:
        """Delete lifecycle events older than ``older_than``. Returns the count."""


class DuplicateTask(Exception):
    """Raised by :meth:`TaskRepository.insert` on an idempotency conflict.

    Deliberately not a :class:`~jiro.errors.JiroError`: it is an internal
    control-flow signal the runtime resolves by returning the existing task,
    never an error surfaced to a caller.
    """

    def __init__(self, task_id: str) -> None:
        super().__init__(f"a task with this idempotency key already exists: {task_id}")
        self.task_id = task_id


__all__ = ["DuplicateTask", "EventType", "NewTask", "TaskRepository"]
