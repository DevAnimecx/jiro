"""Composition helpers for wiring memory into a running application.

Keeps :mod:`jiro.server` free of memory construction details and keeps the
memory package free of any dependency on the runtime. The only coupling is two
plain callables handed across the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from jiro.db import Database
from jiro.log import get_logger
from jiro.memory.config import MemoryConfig
from jiro.memory.embeddings import build_adapter
from jiro.memory.errors import MemoryValidationError
from jiro.memory.outbox import MemoryOutbox
from jiro.memory.runtime_integration import TaskMemoryBridge
from jiro.memory.service import MemoryService
from jiro.memory.sqlite_repo import SqlMemoryRepository
from jiro.memory.types import MemoryScope, ScopeKind

log = get_logger("jiro.memory.app")


@dataclass
class MemoryStack:
    """The three memory objects an application needs."""

    service: MemoryService
    outbox: MemoryOutbox
    bridge: TaskMemoryBridge


async def build_memory_service(settings: Any, db: Database) -> Optional[MemoryStack]:
    """Construct the memory stack from project settings.

    Returns ``None`` when memory is disabled or misconfigured, so a bad memory
    config never prevents the API from starting — the failure is logged and the
    rest of the application is unaffected. This is a deliberate availability
    choice: memory is an enhancement, not a hard dependency of search.

    Raises:
        Nothing. Configuration problems are logged, not raised.
    """
    try:
        config = MemoryConfig.from_settings(settings)
    except Exception as exc:
        log.error("memory configuration invalid; memory disabled", extra={
            "error": str(exc)[:300],
        })
        return None

    if not config.enabled:
        log.info("memory disabled by configuration")
        return None

    try:
        await db.verify_schema(["memories", "memory_facts", "memory_procedures",
                                "memory_provenance", "memory_outbox"])
    except Exception as exc:
        log.error("memory schema incomplete; memory disabled", extra={
            "error": str(exc)[:300],
        })
        return None

    repo = SqlMemoryRepository(db)
    service = MemoryService(repo, config, embeddings=build_adapter(config))
    outbox = MemoryOutbox(repo, config)
    bridge = TaskMemoryBridge(service, outbox, enabled=config.auto_episodes)
    try:
        await service.initialize()
    except Exception:
        log.exception("memory initialisation failed; memory disabled")
        return None
    return MemoryStack(service=service, outbox=outbox, bridge=bridge)


def episode_hook(bridge: Optional[TaskMemoryBridge]) -> Optional[Callable]:
    """Build the terminal-task hook that queues an episode.

    Returns a coroutine function taking a ``TaskRecord``. The write is queued to
    the durable outbox rather than performed inline, so a task's terminal state
    can never be delayed or failed by memory.
    """
    if bridge is None:
        return None

    async def hook(record: Any) -> None:
        try:
            await bridge.enqueue_episode(record)
        except Exception:
            # Best-effort by contract. The task has already reached a terminal
            # state; a memory problem must not change that.
            log.warning("failed to queue task episode", extra={
                "task_id": getattr(record, "id", "?"),
            })

    return hook


# --------------------------------------------------------------- task handlers

MEMORY_RECALL = "memory_recall"
MEMORY_REMEMBER = "memory_remember"
MEMORY_FORGET = "memory_forget"


def _require_service(service: Optional[MemoryService]) -> MemoryService:
    if service is None:
        raise MemoryValidationError(
            "memory is not available in this process",
            details={"hint": "check memory.enabled and the startup log"},
        )
    return service


#: Scope kinds a memory *task* may read from. A task may always read its own
#: task scope; anything wider is still constrained to the submitting key, so
#: this widens recall without crossing a tenant boundary.
READABLE_SCOPE_KINDS = (ScopeKind.TASK, ScopeKind.SESSION, ScopeKind.KEY,
                        ScopeKind.GLOBAL)


def _trusted_owner(ctx: Any) -> Optional[str]:
    """The key that submitted this task.

    Read from the durable task record via ``TaskContext.owner_key_id``, never
    from the task payload. A payload field naming another key would let a
    caller read or write another tenant's memory, so the handlers do not accept
    one at all.
    """
    owner = getattr(ctx, "owner_key_id", None)
    return str(owner) if owner else None


def _own_task_scope(ctx: Any) -> MemoryScope:
    """The scope a memory task writes to: its own, and only its own.

    Scope kind and scope id are not caller-selectable. Allowing a payload to
    name ``global`` would let any task write into memory every other task
    reads, which is precisely the leak the task scope exists to prevent.
    """
    return MemoryScope(kind=ScopeKind.TASK, scope_id=str(ctx.task_id),
                       owner_key_id=_trusted_owner(ctx))


def _read_scope(ctx: Any) -> MemoryScope:
    """The scope a memory task recalls from."""
    owner = _trusted_owner(ctx)
    raw_kind = ctx.input.get("scope_kind") or ScopeKind.TASK.value
    try:
        kind = ScopeKind(str(raw_kind))
    except ValueError as exc:
        raise MemoryValidationError(
            f"unknown scope kind '{raw_kind}'",
            details={"known": [k.value for k in ScopeKind]},
        ) from exc
    if kind not in READABLE_SCOPE_KINDS:
        raise MemoryValidationError(
            f"scope kind '{kind.value}' is not readable by memory tasks",
            details={"readable": [k.value for k in READABLE_SCOPE_KINDS]},
        )
    scope_id = ctx.input.get("scope_id") or ctx.task_id
    return MemoryScope(kind=kind, scope_id=str(scope_id), owner_key_id=owner)


async def _memory_recall(service: MemoryService, ctx: Any) -> Dict[str, Any]:
    """Recall memories relevant to a query."""
    query = ctx.input.get("query") or ctx.input.get("text") or ""
    if not isinstance(query, str) or not query.strip():
        raise MemoryValidationError("'query' must be a non-empty string")
    scope = _read_scope(ctx)
    limit = int(ctx.input.get("limit") or 5)
    hits = await service.retrieve(query.strip(), scope=scope, limit=limit)
    return {
        "query": query.strip(),
        "scope": scope.key,
        "count": len(hits),
        "memories": [
            {
                "id": h.record.id,
                "type": h.record.memory_type.value,
                "title": h.record.title,
                "score": round(h.score, 4),
                "match_reason": h.match_reason,
                "score_breakdown": {
                    k: round(v, 4) for k, v in h.score_breakdown.items()
                },
                "verification": h.record.verification.value,
                "sources": [p.source_id for p in h.provenance][:3],
            }
            for h in hits
        ],
    }


async def _memory_remember(service: MemoryService, ctx: Any) -> Dict[str, Any]:
    """Store working memory for the current task."""
    text = ctx.input.get("text")
    if not isinstance(text, str) or not text.strip():
        raise MemoryValidationError("'text' must be a non-empty string")
    scope = _own_task_scope(ctx)
    record, created = await service.remember(
        {"text": text, "source_task_id": ctx.task_id},
        scope=scope,
        title=str(ctx.input.get("title") or "")[:200],
        importance=ctx.input.get("importance"),
        idempotent=bool(ctx.input.get("idempotent")),
    )
    return {"memory_id": record.id, "created": created, "state": record.status.value,
            "scope": scope.key}


async def _memory_forget(service: MemoryService, ctx: Any) -> Dict[str, Any]:
    """Delete this task's working memory."""
    scope = _own_task_scope(ctx)
    removed = await service.forget_scope(scope=scope)
    return {"removed": removed, "scope": scope.key}


def register_memory_handlers(registry: Any, *, service: Optional[MemoryService]) -> Any:
    """Register the memory task types.

    Returns the registry. When memory is unavailable the types are still
    registered, and each handler fails with a clear message rather than an
    ``unknown task type`` — the difference matters when diagnosing a submission
    that failed.

    The service is captured in each closure rather than looked up on the runtime
    at call time, so a handler cannot be pointed at a different memory stack by
    anything that happens to run in the same process.
    """

    def bind(handler: Callable) -> Callable:
        async def bound(ctx: Any) -> Dict[str, Any]:
            return await handler(_require_service(service), ctx)

        bound.__name__ = handler.__name__
        return bound

    from jiro.runtime.validation import Field, TaskSchema

    # Note the absence of an ``owner_key_id`` field on every schema: identity
    # comes from the authenticated submission, so a caller cannot name it.
    registry.register(
        MEMORY_RECALL,
        bind(_memory_recall),
        TaskSchema(MEMORY_RECALL, [
            Field("query", "str", required=True, max_length=2000),
            Field("limit", "int", min_value=1, max_value=50, default=5),
            Field("scope_kind", "str", default="task", max_length=16),
            Field("scope_id", "str", default="", max_length=128),
        ]),
        description="Recall durable memories relevant to a query",
    )
    registry.register(
        MEMORY_REMEMBER,
        bind(_memory_remember),
        TaskSchema(MEMORY_REMEMBER, [
            Field("text", "str", required=True, max_length=8000),
            Field("title", "str", default="", max_length=200),
            Field("importance", "float", min_value=0.0, max_value=1.0),
            Field("idempotent", "bool", default=False),
        ]),
        description="Store working memory for the current task",
    )
    registry.register(
        MEMORY_FORGET,
        bind(_memory_forget),
        TaskSchema(MEMORY_FORGET, []),
        description="Delete this task's working memory",
    )
    return registry


__all__ = [
    "MEMORY_FORGET",
    "MEMORY_RECALL",
    "MEMORY_REMEMBER",
    "MemoryStack",
    "build_memory_service",
    "episode_hook",
    "register_memory_handlers",
]