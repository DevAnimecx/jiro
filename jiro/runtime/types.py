"""Domain types for the Jiro durable task runtime.

This module is pure data: enums, frozen records and timestamp helpers. It has
no dependency on FastAPI, the CLI, MCP, aiosqlite, or any transport. The whole
runtime layer depends on this module, never the other way around.

Timestamp convention (matches every pre-existing table in ``jiro/db.py``):
all persisted instants are **epoch seconds as a float** (``time.time()``).
ISO-8601 formatting happens only at the HTTP/CLI/MCP boundary.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional

# Epoch seconds -> ISO-8601 UTC, the representation every existing Jiro
# response model uses (see jiro/jobs.py ``_to_out``).
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def new_task_id() -> str:
    """Stable, opaque task identifier. Matches the existing ``job_<hex>`` style."""
    return "task_" + uuid.uuid4().hex[:16]


def to_iso(ts: Optional[float]) -> Optional[str]:
    """Format an epoch timestamp for a response boundary. ``None`` passes through."""
    if ts is None:
        return None
    return time.strftime(_ISO_FORMAT, time.gmtime(ts))


class TaskState(str, Enum):
    """Lifecycle states.

    Inherits ``str`` so the value is directly JSON-serialisable and compares
    equal to the raw column text.
    """

    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATES


#: No transition may leave this set. Enforced by ``jiro.runtime.lifecycle``.
TERMINAL_STATES = frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED})

#: States from which a worker may atomically claim a task.
CLAIMABLE_STATES = frozenset({TaskState.QUEUED, TaskState.RETRY_WAIT})


class EventType(str, Enum):
    """Durable lifecycle event names recorded in ``task_events``."""

    SUBMITTED = "task.submitted"
    VALIDATED = "task.validated"
    CLAIMED = "task.claimed"
    EXECUTION_STARTED = "execution.started"
    ATTEMPT_FAILED = "attempt.failed"
    RETRY_SCHEDULED = "retry.scheduled"
    CANCELLATION_REQUESTED = "cancellation.requested"
    EXECUTION_CANCELLED = "execution.cancelled"
    RECOVERY_APPLIED = "recovery.applied"
    SUCCEEDED = "task.succeeded"
    FAILED = "task.failed"
    CANCELLED = "task.cancelled"
    DEADLINE_EXCEEDED = "deadline.exceeded"


class FailureKind(str, Enum):
    """Retry classification. See :mod:`jiro.runtime.errors` for the mapping."""

    #: Transient — worth retrying if budget remains (network blips, 429s).
    TRANSIENT = "transient"
    #: Bad input/config — retrying cannot help.
    PERMANENT = "permanent"
    #: An execution attempt exceeded its attempt timeout.
    TIMEOUT = "timeout"
    #: Cooperative cancellation. Never retried.
    CANCELLED = "cancelled"
    #: Storage or host failure. Retryable, but the runtime cannot proceed.
    INFRASTRUCTURE = "infrastructure"
    #: Anything unclassified from a handler. Retryable up to the attempt cap.
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class TaskLimits:
    """Validated execution limits for a single task.

    Frozen so a task's budget cannot be mutated after submission. All values
    have already passed :mod:`jiro.runtime.validation` clamping, so the worker
    may use them without re-checking.

    Attributes:
        max_attempts: total executions permitted, including the first.
        attempt_timeout: wall-clock cap for one attempt, in seconds.
        deadline: absolute epoch second by which the task must reach a terminal
            state, or ``None`` for no overall deadline.
        retry_backoff_base: first backoff delay in seconds.
        retry_backoff_max: ceiling for a single backoff delay.
        retry_jitter_ratio: fraction of the computed delay randomised, to stop
            workers synchronising their retries.
        max_result_bytes: result payloads larger than this are refused.
    """

    max_attempts: int = 3
    attempt_timeout: float = 60.0
    deadline: Optional[float] = None
    retry_backoff_base: float = 1.0
    retry_backoff_max: float = 60.0
    retry_jitter_ratio: float = 0.2
    max_result_bytes: int = 262_144

    def remaining_attempts(self, attempts: int) -> int:
        """Executions still permitted. Never negative."""
        return max(0, self.max_attempts - attempts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "attempt_timeout": self.attempt_timeout,
            "deadline": self.deadline,
            "retry_backoff_base": self.retry_backoff_base,
            "retry_backoff_max": self.retry_backoff_max,
            "retry_jitter_ratio": self.retry_jitter_ratio,
            "max_result_bytes": self.max_result_bytes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskLimits":
        return cls(
            max_attempts=int(data["max_attempts"]),
            attempt_timeout=float(data["attempt_timeout"]),
            deadline=(None if data.get("deadline") is None else float(data["deadline"])),
            retry_backoff_base=float(data["retry_backoff_base"]),
            retry_backoff_max=float(data["retry_backoff_max"]),
            retry_jitter_ratio=float(data["retry_jitter_ratio"]),
            max_result_bytes=int(data["max_result_bytes"]),
        )


@dataclass(frozen=True)
class TaskEvent:
    """One durable lifecycle event.

    ``metadata`` holds only small, non-sensitive scalars. The runtime strips
    payloads and anything that looks like a credential before it gets here.
    """

    task_id: str
    type: EventType
    ts: float
    attempt: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskRecord:
    """Immutable snapshot of a durable task row."""

    id: str
    type: str
    state: TaskState
    owner_key_id: Optional[str]
    idempotency_key: Optional[str]
    input: Dict[str, Any]
    result: Optional[Dict[str, Any]]
    result_ref: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    attempts: int
    limits: TaskLimits
    deadline_at: Optional[float]
    lease_owner: Optional[str]
    lease_expires_at: Optional[float]
    cancel_requested: bool
    available_at: Optional[float]
    created_at: float
    updated_at: float
    started_at: Optional[float]
    completed_at: Optional[float]

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    def deadline_exceeded(self, now: Optional[float] = None) -> bool:
        """True when an overall deadline has passed.

        Only meaningful before a terminal state is reached; ``recovery`` uses it
        to fail work that would otherwise execute forever.
        """
        if self.deadline_at is None or self.state.is_terminal:
            return False
        return (now if now is not None else time.time()) >= self.deadline_at

    def to_status(self) -> Dict[str, Any]:
        """Sanitised public representation.

        Deliberately omits ``lease_owner`` (internal worker identity) and any
        stack trace. Error information is limited to the already-sanitised
        ``error_code``/``error_message`` pair.
        """
        return {
            "id": self.id,
            "type": self.type,
            "state": self.state.value,
            "owner_key_id": self.owner_key_id,
            "idempotency_key": self.idempotency_key,
            "attempts": self.attempts,
            "max_attempts": self.limits.max_attempts,
            "attempt_timeout": self.limits.attempt_timeout,
            "deadline_at": to_iso(self.deadline_at),
            "cancel_requested": self.cancel_requested,
            "available_at": to_iso(self.available_at),
            "created_at": to_iso(self.created_at),
            "updated_at": to_iso(self.updated_at),
            "started_at": to_iso(self.started_at),
            "completed_at": to_iso(self.completed_at),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "has_result": self.result is not None or self.result_ref is not None,
        }

    def to_result(self) -> Dict[str, Any]:
        """Public representation including the result payload."""
        payload = self.to_status()
        payload["result"] = self.result
        payload["result_ref"] = self.result_ref
        return payload


__all__ = [
    "CLAIMABLE_STATES",
    "EventType",
    "FailureKind",
    "TERMINAL_STATES",
    "TaskEvent",
    "TaskLimits",
    "TaskRecord",
    "TaskState",
    "new_task_id",
    "to_iso",
]
