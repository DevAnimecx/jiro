"""Handler registration and the execution context handed to handlers.

The runtime executes **only** handlers that were explicitly registered in
process by trusted code. There is no path by which a submitted task can name a
callable, import a module, or evaluate a string. That is the primary security
boundary of this subsystem.

Handlers must be coroutine functions. A synchronous callable is rejected at
registration time rather than being wrapped in a thread, because a thread cannot
be cancelled — wrapping one would let the runtime report ``cancelled`` while the
handler kept running.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from jiro.runtime.errors import TaskValidationError, UnknownTaskType
from jiro.runtime.types import TaskLimits
from jiro.runtime.validation import TaskSchema


@dataclass(frozen=True)
class TaskContext:
    """Everything a handler is allowed to know about its own execution.

    Attributes:
        task_id: stable identity of the task, safe to use as an idempotency key
            for a downstream provider.
        task_type: the registered type name.
        input: the validated task payload. Already schema-checked and
            normalised, so a handler never re-validates and never sees a field
            it did not declare.
        attempt: 1-based attempt number. Increases across retries.
        idempotency_key: caller-supplied key, or ``None``. A handler performing
            a non-idempotent external operation must key on this value so an
            at-least-once re-execution does not duplicate the effect.
        deadline_at: absolute epoch second the task must finish by, if bounded.
        limits: the validated limits governing this attempt.
        owner_key_id: the authenticated key that submitted the task, taken from
            the durable task record and never from the task payload. Handlers
            that write owner-scoped data must use this value rather than any
            caller-supplied ``owner_key_id`` in ``input``, which is untrusted
            and could name a different key.
        runtime: owning :class:`~jiro.runtime.runtime.TaskRuntime`, for the
            narrow cases where a handler legitimately needs to check runtime
            state (see :meth:`cancel_requested`).
        services: trusted in-process collaborators captured when the handler was
            registered. Only built-in handlers use this; third-party handlers
            should take what they need from module scope or their own closure
            rather than reaching through a runtime-injected object graph.
    """

    task_id: str
    task_type: str
    input: Dict[str, Any]
    attempt: int
    idempotency_key: Optional[str]
    deadline_at: Optional[float]
    limits: TaskLimits
    owner_key_id: Optional[str] = None
    runtime: Any = None
    services: Any = None

    async def cancel_requested(self) -> bool:
        """Whether cancellation has been requested for this task.

        Handlers that run long external operations should poll this and return
        promptly. A handler that ignores it is still terminated when its
        attempt timeout fires — but only then.
        """
        if self.runtime is None:
            return False
        return await self.runtime.is_cancel_requested(self.task_id)

    async def sleep(self, seconds: float) -> None:
        """Interruptible sleep, honouring cancellation of the surrounding task."""
        await asyncio.sleep(seconds)


#: Signature of a task handler.
TaskHandler = Callable[["TaskContext"], Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class HandlerSpec:
    """A registered handler and its input schema."""

    type: str
    handler: TaskHandler
    schema: TaskSchema
    description: str = ""
    #: When true, this handler may submit further tasks (e.g. a fan-out batch).
    #: Child submissions are still bounded by the global concurrency limit.
    allows_children: bool = False


def is_async_callable(fn: Any) -> bool:
    """Whether ``fn`` will return an awaitable when called.

    ``inspect.iscoroutinefunction`` alone rejects a perfectly valid handler
    written as a class with ``async def __call__``, or a decorated function
    whose wrapper hides the coroutine marker. Both are async, so both are
    accepted.
    """
    if inspect.iscoroutinefunction(fn):
        return True
    call = getattr(fn, "__call__", None)
    return call is not None and inspect.iscoroutinefunction(call)


class HandlerRegistry:
    """Explicit, closed registry of executable task types.

    Registration happens once, during application startup, by trusted code.
    There is no unregister/load-from-path path, so the set of executable code
    is fixed for the lifetime of the process.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, HandlerSpec] = {}

    def register(
        self,
        type_name: str,
        handler: TaskHandler,
        schema: TaskSchema,
        *,
        description: str = "",
        allows_children: bool = False,
    ) -> HandlerSpec:
        """Register an async handler for ``type_name``.

        Args:
            type_name: the task type callers submit. Must match ``schema.name``.
            handler: an ``async def handler(ctx) -> dict`` callable.
            schema: input schema used to validate every submission.
            description: human-readable summary for CLI/MCP help.
            allows_children: whether this handler may submit child tasks.

        Returns:
            The stored :class:`HandlerSpec`.

        Raises:
            TaskValidationError: if the callable is not a coroutine function,
                the names disagree, or the type is already registered.
        """
        if not isinstance(type_name, str) or not type_name.strip():
            raise TaskValidationError("task type must be a non-empty string")
        if type_name in self._handlers:
            raise TaskValidationError(
                f"task type '{type_name}' is already registered",
                details={"type": type_name},
            )
        if not is_async_callable(handler):
            # Rejected rather than adapted: a sync handler run in a thread
            # cannot be cancelled, so wrapping it would produce a runtime that
            # reports cancellation while work continues.
            raise TaskValidationError(
                f"handler for '{type_name}' must be an async function; "
                "synchronous handlers cannot be cancelled safely",
                details={"type": type_name},
            )
        if not isinstance(schema, TaskSchema):
            raise TaskValidationError(
                f"handler for '{type_name}' requires a TaskSchema",
                details={"type": type_name},
            )
        if schema.name != type_name:
            raise TaskValidationError(
                f"schema name '{schema.name}' does not match task type '{type_name}'",
                details={"type": type_name, "schema": schema.name},
            )

        spec = HandlerSpec(
            type=type_name,
            handler=handler,
            schema=schema,
            description=description,
            allows_children=allows_children,
        )
        self._handlers[type_name] = spec
        return spec

    def get(self, type_name: str) -> HandlerSpec:
        """Look up a handler.

        Raises:
            UnknownTaskType: if nothing is registered under that name.
        """
        try:
            return self._handlers[type_name]
        except KeyError as exc:
            raise UnknownTaskType(
                f"unknown task type '{type_name}'",
                details={"type": type_name, "available": self.type_names()},
            ) from exc

    def has(self, type_name: str) -> bool:
        return type_name in self._handlers

    def type_names(self) -> List[str]:
        return sorted(self._handlers)

    def schemas(self) -> Dict[str, TaskSchema]:
        return {name: spec.schema for name, spec in self._handlers.items()}

    def describe(self) -> Mapping[str, Dict[str, Any]]:
        """Introspection payload for CLI and MCP tool listings."""
        return {
            name: {
                "description": spec.description,
                "schema": spec.schema.describe(),
                "allows_children": spec.allows_children,
            }
            for name, spec in self._handlers.items()
        }

    def __len__(self) -> int:
        return len(self._handlers)


__all__ = [
    "HandlerRegistry",
    "HandlerSpec",
    "TaskContext",
    "TaskHandler",
]
