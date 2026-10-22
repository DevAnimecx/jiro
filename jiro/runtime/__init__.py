"""Jiro durable task runtime.

A bounded, persistent, cancellable execution substrate. Tasks are validated
against a registered handler's schema, persisted before acceptance is reported,
executed by a fixed pool of workers under explicit time and attempt budgets,
and reconciled on restart.

Guarantees
----------
* **Durable acceptance** — :meth:`TaskRuntime.submit` does not return until the
  row and its lifecycle event are committed.
* **At-least-once execution** — a task interrupted by a crash is re-executed
  after recovery. Exactly-once external side effects are not achievable from a
  database alone; handlers performing non-idempotent work must key on
  ``TaskContext.idempotency_key``.
* **Bounded execution** — simultaneous handler executions never exceed
  ``runtime.max_concurrency``.
* **Honest cancellation** — a task is only reported ``cancelled`` once its
  handler has actually stopped.

Not in scope for this phase: cognitive memory, agent swarms, autonomous goal
generation, and federation.

Typical use::

    from jiro.runtime import HandlerRegistry, TaskRuntime, Field, TaskSchema

    registry = HandlerRegistry()
    registry.register(
        "greet",
        greet,                                  # async def greet(ctx) -> dict
        TaskSchema("greet", [Field("name", "str", required=True)]),
    )
    runtime = await TaskRuntime.create(settings, db, registry)
    await runtime.start()
"""

from __future__ import annotations

from jiro.runtime.config import RuntimeConfig
from jiro.runtime.errors import (
    FailureKind,
    InvalidTransition,
    TaskError,
    TaskLimitExceeded,
    TaskNotFound,
    TaskValidationError,
    UnknownTaskType,
    classify_failure,
    sanitize_error,
)
from jiro.runtime.handlers import HandlerRegistry, HandlerSpec, TaskContext, TaskHandler
from jiro.runtime.lifecycle import (
    TRANSITIONS,
    can_transition,
    describe,
    validate_transition,
)
from jiro.runtime.recovery import RecoveryManager, RecoveryReport
from jiro.runtime.repository import NewTask, TaskRepository
from jiro.runtime.retry import RetryDecision, RetryPolicy
from jiro.runtime.runtime import TaskRuntime
from jiro.runtime.sqlite_repo import SqlTaskRepository
from jiro.runtime.types import (
    EventType,
    TaskEvent,
    TaskLimits,
    TaskRecord,
    TaskState,
)
from jiro.runtime.validation import Field, TaskSchema, validate_input
from jiro.runtime.worker import WorkerPool

__all__ = [
    "EventType",
    "FailureKind",
    "Field",
    "HandlerRegistry",
    "HandlerSpec",
    "InvalidTransition",
    "NewTask",
    "RecoveryManager",
    "RecoveryReport",
    "RetryDecision",
    "RetryPolicy",
    "RuntimeConfig",
    "SqlTaskRepository",
    "TRANSITIONS",
    "TaskContext",
    "TaskError",
    "TaskEvent",
    "TaskHandler",
    "TaskLimits",
    "TaskLimitExceeded",
    "TaskNotFound",
    "TaskRecord",
    "TaskRepository",
    "TaskRuntime",
    "TaskSchema",
    "TaskState",
    "TaskValidationError",
    "UnknownTaskType",
    "WorkerPool",
    "can_transition",
    "classify_failure",
    "describe",
    "sanitize_error",
    "validate_input",
    "validate_transition",
]
