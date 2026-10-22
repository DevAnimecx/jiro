"""SQLite implementation of :class:`~jiro.runtime.repository.TaskRepository`.

Concurrency model
-----------------
SQLite permits many readers but one writer. This class relies on two facts:

1. A single conditional ``UPDATE`` is atomic, so a claim never needs a
   read-then-write sequence that two workers could interleave.
2. ``BEGIN IMMEDIATE`` plus the pool's writer lock serialises the multi-statement
   units (state change + lifecycle event) that must land together.

Every write that a worker performs carries ``AND lease_owner = ?`` so a worker
whose lease was already reclaimed by recovery cannot overwrite the new owner's
progress. Terminal writes additionally carry a state guard, so a duplicate
completion reports a conflict instead of corrupting a finished task.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence

import aiosqlite

from jiro.db import Database
from jiro.runtime.errors import StorageFailure, sanitize_metadata
from jiro.runtime.lifecycle import (
    assert_state,
    completing_states,
    validate_transition,
)
from jiro.runtime.repository import DuplicateTask, NewTask, TaskRepository
from jiro.runtime.types import EventType, TaskEvent, TaskLimits, TaskRecord, TaskState

#: Columns written on every state change. Kept in one place so no update path
#: can forget to advance ``updated_at``.
_UPDATED_AT = "updated_at = ?"

_CLAIMABLE_SQL = "('queued', 'retry_wait')"

_SELECT_COLUMNS = """
    id, type, state, owner_key_id, idempotency_key, input, result, result_ref,
    error_code, error_message, attempts, max_attempts, attempt_timeout_s,
    deadline_at, retry_backoff_base, retry_backoff_max, retry_jitter_ratio,
    max_result_bytes, lease_owner, lease_expires_at, cancel_requested,
    available_at, created_at, updated_at, started_at, completed_at
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _loads(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        # A corrupt payload must not take down a status read.
        return default


def row_to_record(row: Dict[str, Any]) -> TaskRecord:
    """Map a ``tasks`` row onto the frozen domain record."""
    return TaskRecord(
        id=row["id"],
        type=row["type"],
        state=assert_state(row["state"]),
        owner_key_id=row.get("owner_key_id"),
        idempotency_key=row.get("idempotency_key"),
        input=_loads(row.get("input"), {}) or {},
        result=_loads(row.get("result")),
        result_ref=row.get("result_ref"),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        attempts=int(row.get("attempts") or 0),
        limits=TaskLimits(
            max_attempts=int(row.get("max_attempts") or 3),
            attempt_timeout=float(row.get("attempt_timeout_s") or 60.0),
            deadline=row.get("deadline_at"),
            retry_backoff_base=float(row.get("retry_backoff_base") or 1.0),
            retry_backoff_max=float(row.get("retry_backoff_max") or 60.0),
            retry_jitter_ratio=float(row.get("retry_jitter_ratio") or 0.0),
            max_result_bytes=int(row.get("max_result_bytes") or 262_144),
        ),
        deadline_at=row.get("deadline_at"),
        lease_owner=row.get("lease_owner"),
        lease_expires_at=row.get("lease_expires_at"),
        cancel_requested=bool(row.get("cancel_requested")),
        available_at=row.get("available_at"),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
    )


def row_to_event(row: Dict[str, Any]) -> TaskEvent:
    return TaskEvent(
        task_id=row["task_id"],
        type=EventType(row["type"]),
        ts=float(row["ts"]),
        attempt=row.get("attempt"),
        metadata=_loads(row.get("metadata"), {}) or {},
    )


class SqlTaskRepository(TaskRepository):
    """Durable task storage on SQLite via the existing ``Database`` pool."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        return self._db

    # ------------------------------------------------------------------ writes
    async def insert(self, task: NewTask, *, now: float) -> TaskRecord:
        limits = task.limits
        try:
            await self._db.execute(
                "INSERT INTO tasks (id, type, state, owner_key_id, idempotency_key,"
                " idempotency_scope, input, attempts, max_attempts,"
                " attempt_timeout_s, deadline_at, retry_backoff_base,"
                " retry_backoff_max, retry_jitter_ratio, max_result_bytes,"
                " cancel_requested, available_at, created_at, updated_at)"
                " VALUES (?, ?, 'queued', ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 0, ?,"
                " ?, ?)",
                (
                    task.id,
                    task.type,
                    task.owner_key_id,
                    task.idempotency_key,
                    task.owner_key_id or "",
                    _dumps(task.input),
                    limits.max_attempts,
                    limits.attempt_timeout,
                    limits.deadline,
                    limits.retry_backoff_base,
                    limits.retry_backoff_max,
                    limits.retry_jitter_ratio,
                    limits.max_result_bytes,
                    now,
                    now,
                    now,
                ),
            )
        except aiosqlite.IntegrityError as exc:
            # The unique index on (owner_key_id, idempotency_key) is what makes
            # idempotent submission safe under concurrency; resolve it by
            # returning the task that won the race.
            if "idx_tasks_idempotency" in str(exc) or "UNIQUE" in str(exc).upper():
                existing = await self.find_by_idempotency_key(
                    owner_key_id=task.owner_key_id,
                    idempotency_key=task.idempotency_key or "",
                )
                if existing is not None:
                    raise DuplicateTask(existing.id) from exc
            raise StorageFailure(f"failed to persist task: {exc}") from exc
        except Exception as exc:
            raise StorageFailure(f"failed to persist task: {exc}") from exc

        record = await self.get(task.id)
        if record is None:  # pragma: no cover - would mean the insert vanished
            raise StorageFailure(f"task {task.id} vanished immediately after insert")
        return record

    async def insert_with_events(
        self, task: NewTask, events: Sequence[TaskEvent], *, now: float
    ) -> TaskRecord:
        limits = task.limits
        event_rows = [
            (
                e.task_id,
                e.type.value,
                e.attempt,
                e.ts,
                _dumps(sanitize_metadata(e.metadata)),
            )
            for e in events
        ]
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO tasks (id, type, state, owner_key_id, idempotency_key,"
                    " idempotency_scope, input, attempts, max_attempts,"
                    " attempt_timeout_s, deadline_at, retry_backoff_base,"
                    " retry_backoff_max, retry_jitter_ratio, max_result_bytes,"
                    " cancel_requested, available_at, created_at, updated_at)"
                    " VALUES (?, ?, 'queued', ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 0, ?,"
                    " ?, ?)",
                    (
                        task.id,
                        task.type,
                        task.owner_key_id,
                        task.idempotency_key,
                        task.owner_key_id or "",
                        _dumps(task.input),
                        limits.max_attempts,
                        limits.attempt_timeout,
                        limits.deadline,
                        limits.retry_backoff_base,
                        limits.retry_backoff_max,
                        limits.retry_jitter_ratio,
                        limits.max_result_bytes,
                        now,
                        now,
                        now,
                    ),
                )
                for row in event_rows:
                    await conn.execute(
                        "INSERT INTO task_events (task_id, type, attempt, ts, metadata)"
                        " VALUES (?, ?, ?, ?, ?)",
                        row,
                    )
        except aiosqlite.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                existing = await self.find_by_idempotency_key(
                    owner_key_id=task.owner_key_id,
                    idempotency_key=task.idempotency_key or "",
                )
                if existing is not None:
                    raise DuplicateTask(existing.id) from exc
            raise StorageFailure(f"failed to persist task: {exc}") from exc
        except StorageFailure:
            raise
        except Exception as exc:
            raise StorageFailure(f"failed to persist task: {exc}") from exc

        record = await self.get(task.id)
        if record is None:  # pragma: no cover - would mean the insert vanished
            raise StorageFailure(f"task {task.id} vanished immediately after insert")
        return record

    async def renew_lease(
        self, *, task_id: str, worker_id: str, expires_at: float, now: float
    ) -> bool:
        await self._db.execute(
            "UPDATE tasks SET lease_expires_at = ?, updated_at = ?"
            " WHERE id = ? AND lease_owner = ? AND state = 'running'",
            (expires_at, now, task_id, worker_id),
        )
        row = await self._db.fetchone("SELECT lease_owner FROM tasks WHERE id = ?", (task_id,))
        return bool(row and row["lease_owner"] == worker_id)

    async def claim(self, *, worker_id: str, now: float, lease_ttl: float) -> Optional[TaskRecord]:
        lease_expires = now + lease_ttl
        candidate = await self._db.fetchone(
            "SELECT id, state FROM tasks"
            f" WHERE state IN {_CLAIMABLE_SQL}"
            "   AND cancel_requested = 0"
            "   AND (available_at IS NULL OR available_at <= ?)"
            "   AND (deadline_at IS NULL OR deadline_at > ?)"
            " ORDER BY available_at ASC, created_at ASC LIMIT 1",
            (now, now),
        )
        if candidate is None:
            return None

        # Compare-and-swap. The state guard is re-checked inside the UPDATE, so
        # if another worker claimed the same row between our SELECT and here we
        # lose the race and loop round rather than double-executing.
        await self._db.execute(
            "UPDATE tasks SET state = 'running', attempts = attempts + 1,"
            " lease_owner = ?, lease_expires_at = ?,"
            " started_at = COALESCE(started_at, ?),"
            f" {_UPDATED_AT}, error_code = NULL, error_message = NULL"
            f" WHERE id = ? AND state IN {_CLAIMABLE_SQL} AND cancel_requested = 0",
            (worker_id, lease_expires, now, now, candidate["id"]),
        )
        claimed = await self.get(candidate["id"])
        if claimed is None or claimed.state is not TaskState.RUNNING:
            # Lost the race, or the row changed underneath us.
            return None
        if claimed.lease_owner != worker_id:
            return None
        return claimed

    async def complete_success(
        self, *, task_id: str, worker_id: str, result: Dict[str, Any], now: float
    ) -> bool:
        payload = _dumps(result)
        await self._db.execute(
            "UPDATE tasks SET state = 'succeeded', result = ?, result_ref = NULL,"
            " error_code = NULL, error_message = NULL, completed_at = ?,"
            " lease_owner = NULL, lease_expires_at = NULL,"
            f" {_UPDATED_AT}"
            " WHERE id = ? AND lease_owner = ? AND state = 'running'",
            (payload, now, now, task_id, worker_id),
        )
        return await self._state_is(task_id, TaskState.SUCCEEDED)

    async def complete_failure(
        self,
        *,
        task_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        now: float,
    ) -> bool:
        await self._db.execute(
            "UPDATE tasks SET state = 'failed', error_code = ?, error_message = ?,"
            " completed_at = ?, lease_owner = NULL, lease_expires_at = NULL,"
            f" {_UPDATED_AT}"
            " WHERE id = ? AND lease_owner = ? AND state = 'running'",
            (error_code, error_message, now, now, task_id, worker_id),
        )
        return await self._state_is(task_id, TaskState.FAILED)

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
        await self._db.execute(
            "UPDATE tasks SET state = 'retry_wait', available_at = ?,"
            " error_code = ?, error_message = ?, lease_owner = NULL,"
            f" lease_expires_at = NULL, {_UPDATED_AT}"
            " WHERE id = ? AND lease_owner = ? AND state = 'running'",
            (available_at, error_code, error_message, now, task_id, worker_id),
        )
        return await self._state_is(task_id, TaskState.RETRY_WAIT)

    async def mark_cancelled(
        self, *, task_id: str, now: float, from_states: Sequence[TaskState]
    ) -> bool:
        placeholders = ", ".join("?" for _ in from_states)
        await self._db.execute(
            "UPDATE tasks SET state = 'cancelled', cancel_requested = 1,"
            f" completed_at = ?, {_UPDATED_AT}, lease_owner = NULL,"
            " lease_expires_at = NULL"
            f" WHERE id = ? AND state IN ({placeholders})",
            (now, now, task_id, *[s.value for s in from_states]),
        )
        return await self._state_is(task_id, TaskState.CANCELLED)

    async def request_cancel(self, task_id: str) -> bool:
        await self._db.execute(
            "UPDATE tasks SET cancel_requested = 1, updated_at = ?"
            " WHERE id = ? AND cancel_requested = 0 AND state NOT IN (?, ?, ?)",
            (time.time(), task_id, *[s.value for s in completing_states()]),
        )
        row = await self._db.fetchone("SELECT cancel_requested FROM tasks WHERE id = ?", (task_id,))
        return bool(row and row["cancel_requested"])

    async def set_state(
        self,
        *,
        task_id: str,
        state: TaskState,
        now: float,
        reason: Optional[str] = None,
        available_at: Optional[float] = None,
    ) -> bool:
        row = await self._db.fetchone("SELECT state FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            return False
        current = assert_state(row["state"])
        if current is state:
            return True
        # Recovery is the only caller, and it must be able to move a task whose
        # lease has lapsed — which is exactly why this path skips the lease guard
        # the worker paths use.
        validate_transition(current, state)

        completed = now if state.is_terminal else None
        # `reason` is recorded as the error code only when the task has none yet,
        # so recovery can explain a transition without erasing a real handler error.
        await self._db.execute(
            f"UPDATE tasks SET state = ?, {_UPDATED_AT}, completed_at = ?,"
            " lease_owner = NULL, lease_expires_at = NULL,"
            " available_at = ?, error_code = COALESCE(error_code, ?)"
            " WHERE id = ?",
            (state.value, now, completed, available_at, reason, task_id),
        )
        return True

    # ------------------------------------------------------------------- reads
    async def get(self, task_id: str) -> Optional[TaskRecord]:
        row = await self._db.fetchone(
            f"SELECT {_SELECT_COLUMNS} FROM tasks WHERE id = ?", (task_id,)
        )
        return row_to_record(row) if row else None

    async def find_by_idempotency_key(
        self, *, owner_key_id: Optional[str], idempotency_key: str
    ) -> Optional[TaskRecord]:
        if idempotency_key is None:
            return None
        row = await self._db.fetchone(
            f"SELECT {_SELECT_COLUMNS} FROM tasks"
            " WHERE owner_key_id IS ? AND idempotency_key = ? LIMIT 1",
            (owner_key_id, idempotency_key),
        )
        return row_to_record(row) if row else None

    async def list_for_owner(
        self,
        owner_key_id: Optional[str],
        *,
        limit: int = 50,
        offset: int = 0,
        states: Optional[Sequence[TaskState]] = None,
    ) -> List[TaskRecord]:
        params: List[Any] = [owner_key_id]
        sql = f"SELECT {_SELECT_COLUMNS} FROM tasks WHERE owner_key_id IS ?"
        if states:
            placeholders = ", ".join("?" for _ in states)
            sql += f" AND state IN ({placeholders})"
            params.extend(s.value for s in states)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = await self._db.fetchall(sql, tuple(params))
        return [row_to_record(r) for r in rows]

    async def count_for_owner(self, owner_key_id: Optional[str]) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM tasks WHERE owner_key_id IS ?",
            (owner_key_id,),
        )
        return int(row["n"]) if row else 0

    async def list_by_state(self, state: TaskState, *, limit: int = 200) -> List[TaskRecord]:
        rows = await self._db.fetchall(
            f"SELECT {_SELECT_COLUMNS} FROM tasks WHERE state = ? ORDER BY created_at ASC LIMIT ?",
            (state.value, limit),
        )
        return [row_to_record(r) for r in rows]

    async def queue_depth(self) -> Dict[str, int]:
        rows = await self._db.fetchall("SELECT state, COUNT(*) AS n FROM tasks GROUP BY state")
        depth = {s.value: 0 for s in TaskState}
        for row in rows:
            depth[str(row["state"])] = int(row["n"])
        return depth

    # ------------------------------------------------------------------ events
    async def append_event(self, event: TaskEvent, *, conn: Any = None) -> None:
        metadata = sanitize_metadata(event.metadata)
        sql = (
            "INSERT INTO task_events (task_id, type, attempt, ts, metadata) VALUES (?, ?, ?, ?, ?)"
        )
        params = (
            event.task_id,
            event.type.value,
            event.attempt,
            event.ts,
            _dumps(metadata),
        )
        if conn is not None:
            await conn.execute(sql, params)
            return
        await self._db.execute(sql, params)

    async def events_for(self, task_id: str, *, limit: int = 100) -> List[TaskEvent]:
        rows = await self._db.fetchall(
            "SELECT task_id, type, attempt, ts, metadata FROM task_events"
            " WHERE task_id = ? ORDER BY id ASC LIMIT ?",
            (task_id, limit),
        )
        return [row_to_event(r) for r in rows]

    # ---------------------------------------------------------------- recovery
    async def expired_leases(self, *, now: float, limit: int = 100) -> List[TaskRecord]:
        rows = await self._db.fetchall(
            f"SELECT {_SELECT_COLUMNS} FROM tasks"
            " WHERE state = 'running'"
            "   AND lease_expires_at IS NOT NULL"
            "   AND lease_expires_at <= ?"
            " ORDER BY lease_expires_at ASC LIMIT ?",
            (now, limit),
        )
        return [row_to_record(r) for r in rows]

    async def due_retries(self, *, now: float, limit: int = 100) -> List[TaskRecord]:
        rows = await self._db.fetchall(
            f"SELECT {_SELECT_COLUMNS} FROM tasks"
            " WHERE state = 'retry_wait'"
            "   AND (available_at IS NULL OR available_at <= ?)"
            " ORDER BY available_at ASC LIMIT ?",
            (now, limit),
        )
        return [row_to_record(r) for r in rows]

    async def prune_events(self, *, older_than: float) -> int:
        before = await self._db.fetchone("SELECT COUNT(*) AS n FROM task_events")
        await self._db.execute("DELETE FROM task_events WHERE ts < ?", (older_than,))
        after = await self._db.fetchone("SELECT COUNT(*) AS n FROM task_events")
        return int(before["n"]) - int(after["n"]) if before and after else 0

    # ------------------------------------------------------------------ helper
    async def _state_is(self, task_id: str, state: TaskState) -> bool:
        row = await self._db.fetchone("SELECT state FROM tasks WHERE id = ?", (task_id,))
        return bool(row and str(row["state"]) == state.value)


__all__ = ["SqlTaskRepository", "row_to_event", "row_to_record"]
