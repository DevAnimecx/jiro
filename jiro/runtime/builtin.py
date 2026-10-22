"""Handlers for the task types the ``/jobs`` API already exposes.

These let the existing jobs API be re-pointed at the durable runtime without
changing what a caller submits or receives. Each one is a thin adapter over the
same collaborators the jobs router already used, so no behaviour is
reimplemented and no result shape changes.

They are registered by :func:`register_builtin_handlers` during application
startup, capturing their collaborators then. No user-supplied code is ever
executed here.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Mapping

from jiro.log import get_logger
from jiro.runtime.errors import ValidationError
from jiro.runtime.handlers import HandlerRegistry, TaskContext
from jiro.runtime.validation import Field, TaskSchema

log = get_logger("jiro.runtime.builtin")

#: Matches the `urls[:50]` cap the jobs router has always applied.
MAX_BATCH_URLS = 50


def _service(task_type: str, ctx: TaskContext, attr: str) -> Any:
    """Fetch a collaborator captured at registration time.

    Raises:
        ValidationError: when the collaborator is absent, which means the
            runtime started without wiring what this handler needs. Failing the
            task is the honest outcome.
    """
    services = ctx.services
    collaborator = getattr(services, attr, None) if services is not None else None
    if collaborator is None:
        raise ValidationError(
            f"task type '{task_type}' is not available in this process",
            details={"missing": attr},
        )
    return collaborator


def _require_query(task_type: str, payload: Mapping[str, Any]) -> str:
    query = payload.get("query") or ""
    if not isinstance(query, str) or not query.strip():
        raise ValidationError(f"'{task_type}' requires a non-empty 'query'")
    return query.strip()


def _optional_int(payload: Mapping[str, Any], key: str) -> Any:
    """Return an int override, or ``None`` to let the agent apply its default.

    The schema has already bounds-checked the value; this only narrows the type
    for the agent call.
    """
    value = payload.get(key)
    return None if value is None else int(value)


def _as_urls(payload: Mapping[str, Any]) -> List[str]:
    """Validate and bound the URL list for batch scraping."""
    raw = payload.get("urls") or []
    if not isinstance(raw, list):
        raise ValidationError(
            "'urls' must be a list of strings", details={"received": type(raw).__name__}
        )
    urls: List[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise ValidationError("'urls' must contain non-empty strings")
        urls.append(entry.strip())
    if not urls:
        raise ValidationError("'urls' must not be empty")
    if len(urls) > MAX_BATCH_URLS:
        raise ValidationError(
            f"batch size exceeds the maximum of {MAX_BATCH_URLS}",
            details={"received": len(urls), "max": MAX_BATCH_URLS},
        )
    return urls


async def ai_search_handler(ctx: TaskContext) -> Dict[str, Any]:
    """Run the existing agentic research flow for one job."""
    agent = _service("ai_search", ctx, "agent")
    from jiro.models import AISearchRequest

    body = AISearchRequest(query=_require_query("ai_search", ctx.input))
    result = await agent.research(
        body.query,
        max_sources=_optional_int(ctx.input, "max_sources") or body.max_sources,
        provider=body.llm_provider,
        model=body.llm_model,
    )
    return result if isinstance(result, dict) else {"result": result}


async def ai_agent_handler(ctx: TaskContext) -> Dict[str, Any]:
    """Run the existing multi-step agent flow for one job."""
    agent = _service("ai_agent", ctx, "agent")
    from jiro.models import AgentRequest

    body = AgentRequest(goal=ctx.input.get("goal") or _require_query("ai_agent", ctx.input))
    result = await agent.run_agent(
        body.goal,
        max_steps=_optional_int(ctx.input, "max_steps") or body.max_steps,
        max_sources=_optional_int(ctx.input, "max_sources") or body.max_sources,
        max_sources_per_step=body.max_sources_per_step,
        refine=body.refine,
        provider=body.llm_provider,
        model=body.llm_model,
    )
    return result if isinstance(result, dict) else {"result": result}


async def batch_scrape_handler(ctx: TaskContext) -> Dict[str, Any]:
    """Scrape a bounded list of URLs.

    Serial by design: the client's concurrency budget is global, and fanning
    out inside a task would let one submission bypass it.
    """
    client = _service("batch_scrape", ctx, "scraper_client")
    from jiro.extract import scrape_url

    urls = _as_urls(ctx.input)
    fmt = ctx.input.get("format", "markdown")

    results: List[Dict[str, Any]] = []
    for url in urls:
        if await ctx.cancel_requested():
            # Stop early rather than report a partial batch as a success. The
            # worker records the real cancelled state.
            raise asyncio.CancelledError("batch scrape cancelled")
        try:
            page = await scrape_url(url, client, fmt=fmt, include_metadata=True)
            results.append(page if isinstance(page, dict) else {"url": url})
        except Exception as exc:
            # A single bad URL must not fail the whole batch, matching the
            # behaviour the jobs router has always had.
            results.append({"url": url, "error": type(exc).__name__})
            log.warning("batch scrape url failed", extra={"url": url})

    return {"count": len(results), "results": results}


#: Narrow allow-list schemas. A field a handler does not use is not accepted,
#: so a typo is reported instead of silently ignored.
AI_SEARCH_SCHEMA = TaskSchema(
    "ai_search",
    [
        Field("query", "str", required=True, max_length=2000, max_bytes=8000),
        Field("max_sources", "int", min_value=1, max_value=50, default=10),
        Field("llm_provider", "str", default="", max_length=64),
        Field("llm_model", "str", default="", max_length=128),
    ],
)

AI_AGENT_SCHEMA = TaskSchema(
    "ai_agent",
    [
        Field("goal", "str", max_length=2000, max_bytes=8000),
        Field("query", "str", max_length=2000, max_bytes=8000),
        Field("max_steps", "int", min_value=1, max_value=20, default=5),
        Field("max_sources", "int", min_value=1, max_value=50, default=10),
        Field("refine", "bool", default=False),
        Field("llm_provider", "str", default="", max_length=64),
        Field("llm_model", "str", default="", max_length=128),
    ],
)

BATCH_SCRAPE_SCHEMA = TaskSchema(
    "batch_scrape",
    [
        Field("urls", "list", required=True, item_type="str", max_length=MAX_BATCH_URLS),
        Field("format", "str", choices=("markdown", "text", "html"), default="markdown"),
    ],
)


def register_builtin_handlers(registry: HandlerRegistry, *, services: Any) -> HandlerRegistry:
    """Register the ``/jobs`` task types on ``registry``.

    Args:
        registry: the runtime's handler registry.
        services: object exposing ``agent`` and ``scraper_client`` — normally the
            FastAPI ``app.state`` container, so handlers use the same
            collaborators the jobs router already used.

    Returns:
        The same registry, for chaining.
    """
    bound_services = services
    for name, handler, schema in (
        ("ai_search", ai_search_handler, AI_SEARCH_SCHEMA),
        ("ai_agent", ai_agent_handler, AI_AGENT_SCHEMA),
        ("batch_scrape", batch_scrape_handler, BATCH_SCRAPE_SCHEMA),
    ):
        if registry.has(name):
            continue
        registry.register(
            name,
            _bind(handler, bound_services),
            schema,
            description=f"Built-in {name} task backed by the Jiro agent/scraper",
        )
    return registry


def _bind(handler: Callable[[TaskContext], Any], services: Any) -> Callable:
    """Bind startup collaborators into a handler's :class:`TaskContext`.

    Binding happens once, at registration, rather than per attempt: the
    collaborators are process-wide singletons and re-resolving them on every
    execution would only add a failure mode.
    """

    async def bound(ctx: TaskContext) -> Dict[str, Any]:
        ctx = _with_services(ctx, services)
        return await handler(ctx)

    bound.__name__ = handler.__name__
    bound.__doc__ = handler.__doc__
    return bound


def _with_services(ctx: TaskContext, services: Any) -> TaskContext:
    """``dataclasses.replace`` on a frozen context — no mutation in place."""
    from dataclasses import replace

    return replace(ctx, services=services)


__all__ = [
    "AI_AGENT_SCHEMA",
    "AI_SEARCH_SCHEMA",
    "BATCH_SCRAPE_SCHEMA",
    "MAX_BATCH_URLS",
    "register_builtin_handlers",
]
