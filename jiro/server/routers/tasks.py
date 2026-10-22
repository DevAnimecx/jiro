"""Durable task endpoints.

Thin transport adapters: every route resolves the caller's identity, delegates
to :class:`~jiro.runtime.runtime.TaskRuntime`, and maps the runtime's exceptions
onto Jiro's existing HTTP error contract. No SQL, no scheduling logic, and no
state transitions happen here.

Authorization
-------------
Every route requires an authenticated caller, and every lookup is scoped to
``ctx.key_id``. The runtime raises ``TaskNotFound`` both for a task that does
not exist and for one owned by somebody else, so this surface cannot be used to
discover another user's task ids.

Known limitation: ``key_id`` identifies an **API key**, not a person. A user
holding three keys has three task namespaces. There is no user/account table in
Jiro yet; see docs/adr/0004.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request

from jiro.auth import AuthContext
from jiro.models import TaskCreate, TaskEventOut, TaskOut, TaskResultOut
from jiro.runtime.runtime import TaskRuntime
from jiro.runtime.types import TaskState
from jiro.server.deps import get_auth_context, record_usage

router = APIRouter(tags=["tasks"])


def get_runtime(request: Request) -> TaskRuntime:
    """The process-wide durable runtime, created during application startup."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:  # pragma: no cover - only if startup was bypassed
        from jiro.errors import ConfigError

        raise ConfigError("durable task runtime is not initialised")
    return runtime


def _owner(ctx: AuthContext) -> Optional[str]:
    """The identity tasks are filed under.

    An anonymous caller (auth disabled) gets ``None``, which is its own
    namespace — reachable only by anonymous callers.
    """
    return ctx.key_id if ctx.is_authenticated else None


def _to_out(record: Any, *, with_result: bool = False) -> Dict[str, Any]:
    status = record.to_result() if with_result else record.to_status()
    if not with_result:
        status.pop("result", None)
        status.pop("result_ref", None)
    return status


@router.post(
    "/tasks",
    response_model=TaskOut,
    status_code=202,
    summary="Submit a durable task",
)
async def submit_task(
    request: Request,
    body: TaskCreate,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Validate and durably persist a task.

    Returns 202 only after the task row and its lifecycle events are committed,
    so a 202 means the work will survive a restart.
    """
    request.state.auth = ctx
    runtime = get_runtime(request)

    limits = {
        key: value
        for key, value in (
            ("max_attempts", body.max_attempts),
            ("attempt_timeout_seconds", body.attempt_timeout_seconds),
            ("deadline_in_seconds", body.deadline_in_seconds),
        )
        if value is not None
    }

    record = await runtime.submit(
        body.type,
        body.payload,
        owner_key_id=_owner(ctx),
        idempotency_key=body.idempotency_key,
        limits=limits or None,
    )
    await record_usage(request, endpoint="/tasks", status=202)
    return _to_out(record)


@router.get("/tasks", response_model=List[TaskOut], summary="List your tasks")
async def list_tasks(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    state: Optional[str] = Query(None, description="Filter by lifecycle state"),
    ctx: AuthContext = Depends(get_auth_context),
) -> List[Dict[str, Any]]:
    """Newest first, scoped to the caller. Never returns another user's tasks."""
    request.state.auth = ctx
    runtime = get_runtime(request)

    states = None
    if state:
        try:
            states = [TaskState(state)]
        except ValueError as exc:
            from jiro.errors import ValidationError

            raise ValidationError(
                f"unknown task state '{state}'",
                details={"known": [s.value for s in TaskState]},
            ) from exc

    records = await runtime.list(
        owner_key_id=_owner(ctx), limit=limit, offset=offset, states=states
    )
    return [_to_out(r) for r in records]


@router.get(
    "/tasks/{task_id}",
    response_model=TaskResultOut,
    summary="Get task status and result",
)
async def get_task(
    task_id: str,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Status of one of the caller's tasks, including a stored result."""
    request.state.auth = ctx
    runtime = get_runtime(request)
    record = await runtime.get(task_id, owner_key_id=_owner(ctx))
    return _to_out(record, with_result=True)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=TaskOut,
    summary="Cancel a task",
)
async def cancel_task(
    task_id: str,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Request cancellation and report the resulting state.

    The response is the truth. A running cooperative task still reports
    ``running`` with ``cancel_requested: true`` until its handler unwinds; the
    runtime never claims a stop that has not happened. Repeating the call on an
    already-terminal task is a safe no-op.
    """
    request.state.auth = ctx
    runtime = get_runtime(request)
    record = await runtime.cancel(task_id, owner_key_id=_owner(ctx))
    await record_usage(request, endpoint="/tasks/cancel", status=200)
    return _to_out(record)


@router.get(
    "/tasks/{task_id}/events",
    response_model=List[TaskEventOut],
    summary="List a task's lifecycle events",
)
async def task_events(
    task_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    ctx: AuthContext = Depends(get_auth_context),
) -> List[Dict[str, Any]]:
    """Durable audit trail: submitted, claimed, retried, cancelled, recovered."""
    request.state.auth = ctx
    runtime = get_runtime(request)
    events = await runtime.events(task_id, owner_key_id=_owner(ctx), limit=limit)
    return [
        {
            "task_id": e.task_id,
            "type": e.type.value,
            "ts": e.ts,
            "attempt": e.attempt,
            "metadata": e.metadata,
        }
        for e in events
    ]


@router.get("/tasks/runtime/stats", summary="Runtime worker and queue state")
async def runtime_stats(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Operational snapshot for the authenticated caller."""
    request.state.auth = ctx
    runtime = get_runtime(request)
    depth = await runtime.queue_depth()
    return {
        **runtime.stats(),
        "queue_depth": depth,
        "last_recovery": (
            runtime.last_recovery_report.to_dict() if runtime.last_recovery_report else None
        ),
    }


__all__ = ["router"]
