"""System endpoints: /health, /engines, /metrics."""

from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from jiro import __version__
from jiro.server.deps import get_cache, get_orchestrator

router = APIRouter(tags=["system"])

_STARTED_AT = time.time()


@router.get("/", summary="Service index")
async def index(request: Request) -> Dict[str, Any]:
    return {
        "name": "Jiro Search API",
        "version": __version__,
        "tagline": "Local-first, AI-native search & scraping API",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
        "endpoints": {
            "search": "/search.json?engine=google&q=hello&num=5",
            "scrape": "POST /scrape",
            "ai_search": "POST /ai/search",
            "engines": "/engines",
            "metrics": "/metrics",
        },
    }


@router.get("/health", summary="Health check")
async def health(request: Request, cache: Any = Depends(get_cache)) -> Dict[str, Any]:
    stats = await cache.stats()
    db = request.app.state.db
    db_ok = db is not None
    return {
        "status": "ok",
        "version": __version__,
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "cache": stats,
        "database": "ok" if db_ok else "unavailable",
        "engines_configured": request.app.state.settings.engines,
    }


@router.get("/engines", summary="List supported engines & types")
async def engines(orchestrator: Any = Depends(get_orchestrator)) -> Dict[str, Any]:
    return {"engines": await orchestrator.engines_info()}


@router.get("/metrics", summary="Prometheus-style metrics")
async def metrics(request: Request) -> str:
    db = request.app.state.db
    usage = await db.usage_summary()
    stats = await request.app.state.cache.stats()
    lines = [
        "# HELP jiro_requests_total Total API requests served.",
        "# TYPE jiro_requests_total counter",
        f'jiro_requests_total{{}} {usage.get("requests", 0)}',
        "# HELP jiro_cache_entries Number of cache entries.",
        "# TYPE jiro_cache_entries gauge",
        f'jiro_cache_entries{{}} {stats.get("entries", 0)}',
        "# HELP jiro_tokens_total Tokens consumed via LLM.",
        "# TYPE jiro_tokens_total counter",
        f'jiro_tokens_total{{}} {usage.get("tokens_in", 0) + usage.get("tokens_out", 0)}',
        "# HELP jiro_uptime_seconds Process uptime.",
        "# TYPE jiro_uptime_seconds gauge",
        f'jiro_uptime_seconds{{}} {round(time.time() - _STARTED_AT, 1)}',
    ]
    # per-engine success/failure counters (in-memory, since server start)
    registry = request.app.state.scraper_client
    for engine in sorted(getattr(registry.breaker, "_failures", {})):
        lines.append(f'jiro_engine_failures{{engine="{engine}"}} '
                     f'{registry.breaker._failures.get(engine, 0)}')
    if hasattr(registry.breaker, "_open_until"):
        for engine, until in registry.breaker._open_until.items():
            lines.append(f'jiro_engine_circuit_open{{engine="{engine}"}} 1')
    return "\n".join(lines) + "\n"
