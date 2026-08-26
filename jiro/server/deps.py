"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends, Request

from jiro.auth import AuthContext, AuthManager, build_auth_context
from jiro.audit import ComplianceLogger
from jiro.cache import CacheManager
from jiro.config import Settings
from jiro.db import Database
from jiro.errors import ForbiddenError
from jiro.scraping.client import ScrapingClient
from jiro.scraping.engines import SearchOrchestrator


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_cache(request: Request) -> CacheManager:
    return request.app.state.cache


def get_client(request: Request) -> ScrapingClient:
    return request.app.state.scraper_client


def get_orchestrator(request: Request) -> SearchOrchestrator:
    return request.app.state.orchestrator


def get_auth_manager(request: Request) -> AuthManager:
    return request.app.state.auth


def get_llm(request: Request) -> Any:
    return request.app.state.llm


def get_agent(request: Request) -> Any:
    return request.app.state.agent


def get_audit_logger_dep(request: Request) -> ComplianceLogger:
    return request.app.state.audit_logger


async def get_auth_context(
    request: Request,
    auth: AuthManager = Depends(get_auth_manager),
) -> AuthContext:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return AuthContext(bucket=f"ip:{request.client.host if request.client else 'anon'}")
    ctx = await build_auth_context(request, auth, require=True)
    # rate limit after identity resolution (Redis-backed across workers)
    await auth.check_rate_limit_async(ctx.bucket)
    return ctx


async def optional_auth_context(
    request: Request,
    auth: AuthManager = Depends(get_auth_manager),
) -> AuthContext:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return AuthContext(bucket=f"ip:{request.client.host if request.client else 'anon'}")
    return await build_auth_context(request, auth, require=False)


def require_scope(scope: str = "search"):
    async def _dep(
        ctx: AuthContext = Depends(get_auth_context),
        auth: AuthManager = Depends(get_auth_manager),
    ) -> AuthContext:
        if ctx.record is not None:
            await auth.authorize(ctx.record, scope=scope)
        elif scope != "search":  # anonymous only gets search
            raise ForbiddenError(f"scope '{scope}' requires authentication")
        return ctx

    return _dep


async def record_usage(request: Request, *, endpoint: str, status: int = 200,
                       latency_ms: float = 0.0, engine: str | None = None,
                       query: str | None = None, cached: bool = False,
                       tokens: Dict[str, int] | None = None) -> None:
    """Best-effort usage accounting (never raises into the request path)."""
    try:
        settings: Settings = request.app.state.settings
        ctx: AuthContext = getattr(request.state, "auth", None) or AuthContext()
        db: Database = request.app.state.db
        q = query if settings.log_queries() else None
        await db.usage_add(
            key_id=ctx.key_id, endpoint=endpoint, engine=engine, query=q,
            status=status, latency_ms=latency_ms, cached=cached,
            tokens_in=(tokens or {}).get("in", 0),
            tokens_out=(tokens or {}).get("out", 0),
        )
    except Exception:
        pass
