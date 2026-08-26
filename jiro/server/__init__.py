"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from jiro import __version__
from jiro.ai.agent import Agent
from jiro.ai.llm import LLM
from jiro.auth import AuthManager
from jiro.audit import AuditLogger, AuditMiddleware, ComplianceLogger
from jiro.cache import CacheManager
from jiro.compliance import ComplianceManager
from jiro.config import Settings
from jiro.db import Database
from jiro.errors import JiroError
from jiro.log import get_logger, setup_logging
from jiro.mcp import JiroMCPServer
from jiro.mcp_http import SessionStore, create_mcp_router, mcp_session_cleanup_loop
from jiro.scraping.client import ScrapingClient
from jiro.scraping.engines import SearchOrchestrator

log = get_logger("jiro.server")


def create_app(settings: Optional[Settings] = None,
               config_path: Optional[str] = None) -> FastAPI:
    settings = settings or Settings.load(config_path)
    setup_logging(level=settings.logging.get("level", "info"),
                  file=settings.logging.get("file", ""))
    log.info("jiro starting", extra={"version": __version__})

    # --- startup security guard -------------------------------------------
    # Refuse to expose an *unauthenticated* API on a non-loopback interface.
    host = settings.host
    is_loopback = host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")
    if not settings.auth_enabled and not settings.server_insecure:
        if not is_loopback:
            raise RuntimeError(
                "Refusing to start: authentication is disabled (auth.enabled: false) "
                f"but the server is bound to a non-loopback host ({host}). Either "
                "enable auth, bind to 127.0.0.1, or pass `jiro serve --insecure` in a "
                "trusted sandbox."
            )
        log.warning(
            "authentication is DISABLED — the API is open to anyone who can reach "
            f"{host}:{settings.port}. Enable auth (auth.enabled: true) before sharing "
            "this instance. See https://github.com/DevAnimecx/jiro#security."
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # -- resources ------------------------------------------------------
        db = Database(settings.db_path)
        await db.connect()
        redis_cache = None
        if settings.cache_type == "redis":
            from jiro.redis_cache import RedisCache
            redis_cache = RedisCache(
                settings.get("cache.url", "redis://localhost:6379/0"),
                ttl=settings.cache_ttl,
            )
        cache = CacheManager(
            db if settings.cache_type == "sqlite" else None,
            memory=settings.cache_type == "memory",
            redis=redis_cache,
            ttl=settings.cache_ttl,
        )
        scraper_client = ScrapingClient(settings)
        auth = AuthManager(settings, db)
        try:
            auth.validate_security_config()
        except Exception as exc:  # ConfigError → hard fail before serving
            log.error("security configuration invalid", extra={"error": str(exc)})
            raise
        from jiro.semantic import SemanticCache

        semantic = SemanticCache(settings, db, cache=cache)
        orchestrator = SearchOrchestrator(settings, scraper_client, cache,
                                          semantic=semantic)
        llm = LLM(settings)
        agent = Agent(settings, orchestrator, _scrape_for_agent(scraper_client), llm)
        compliance = ComplianceManager(settings, db)
        compliance_audit = ComplianceLogger(settings)

        app.state.db = db
        app.state.cache = cache
        app.state.scraper_client = scraper_client
        app.state.auth = auth
        app.state.orchestrator = orchestrator
        app.state.llm = llm
        app.state.agent = agent
        app.state.compliance_manager = compliance
        app.state.audit_logger = compliance_audit
        app.state.settings = settings

        from jiro.captcha import CaptchaSolver
        from jiro.jobs import JobManager

        app.state.captcha = CaptchaSolver(settings)
        app.state.jobs = JobManager(db)

        # MCP over HTTP: shared protocol engine + session store
        mcp_server = JiroMCPServer(settings)
        await mcp_server.start()
        app.state.mcp_server = mcp_server
        app.state.mcp_sessions = SessionStore()
        cleanup_task = asyncio.create_task(
            mcp_session_cleanup_loop(app.state.mcp_sessions))

        log.info("jiro ready", extra={
            "host": settings.host, "port": settings.port,
            "engines": settings.engines, "cache": settings.cache_type,
            "auth_enabled": settings.auth_enabled,
        })

        # --- datacenter scraping caveat ------------------------------------
        if settings.default_engine == "google" and not settings.proxy.get("enabled"):
            log.warning(
                "default engine is 'google' but no proxy is configured; from datacenter "
                "IPs Google serves CAPTCHAs and Jiro will fall back to other engines. "
                "Add a BYOK residential proxy (scraping.proxy) for reliable Google access."
            )
        try:
            yield
        finally:
            cleanup_task.cancel()
            await mcp_server.stop()
            await scraper_client.close()
            await db.close()

    app = FastAPI(
        title="Jiro Search API",
        description=(
            "Local-first, AI-native web search & scraping API — a drop-in, "
            "self-hosted alternative to SerpAPI.\n\n"
            "Endpoints: `/search` (web search), `/scrape` (URL extraction), "
            "`/ai/search` (agentic research), `/api-keys` (team keys). "
            "See the `/openapi.json` schema for full details."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "Jiro", "url": "https://github.com/DevAnimecx/jiro"},
        license_info={"name": "MIT"},
    )

    _add_middleware(app, settings)
    _add_error_handlers(app)
    _mount_routers(app)
    return app


def _scrape_for_agent(client: ScrapingClient):
    """Adapter so the Agent can scrape pages through the shared HTTP client."""

    async def _scrape(url: str) -> dict:
        from jiro.extract import scrape_url
        return await scrape_url(url, client, fmt="markdown", include_metadata=False)

    return _scrape


def _add_middleware(app: FastAPI, settings: Settings) -> None:
    # CORS middleware
    if settings.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=settings.cors.get("allow_methods", ["*"]),
            allow_headers=settings.cors.get("allow_headers", ["*"]),
        )

    # Audit logging middleware
    request_audit = AuditLogger(max_entries=10000)
    app.state.request_audit = request_audit
    app.state._audit_logger = request_audit  # legacy alias used by analytics router
    app.add_middleware(AuditMiddleware, audit_logger=request_audit)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except JiroError as exc:
            response = JSONResponse(status_code=exc.status_code,
                                    content=exc.to_dict())
        except Exception as exc:  # pragma: no cover - safety net
            log.exception("unhandled error", extra={"request_id": request_id})
            response = JSONResponse(status_code=500, content={
                "error": "internal server error", "error_code": "internal_error",
            })
        latency = (time.perf_counter() - started)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Jiro-Version"] = __version__
        auth_ctx = getattr(request.state, "auth", None)
        log_extra = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": round(latency, 1),
        }
        if auth_ctx is not None and getattr(auth_ctx, "is_authenticated", False):
            log_extra["key_id"] = auth_ctx.key_id
            log_extra["role"] = auth_ctx.role
        eng = request.query_params.get("engine")
        if eng:
            log_extra["engine"] = eng
        log.info("request", extra=log_extra)
        return response


def _add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(JiroError)
    async def jiro_error_handler(request: Request, exc: JiroError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


def _mount_routers(app: FastAPI) -> None:
    from jiro.server.routers import admin, ai, analytics, compliance, jobs, ops, plugins, scrape, search, stream, system

    app.include_router(system.router)
    app.include_router(search.router)
    app.include_router(scrape.router)
    app.include_router(ai.router)
    app.include_router(stream.router)
    app.include_router(admin.router)
    app.include_router(jobs.router)
    app.include_router(ops.router)
    app.include_router(analytics.router)
    app.include_router(compliance.router)
    app.include_router(plugins.router)
    # MCP remote transports (Streamable HTTP + legacy SSE)
    app.include_router(create_mcp_router())
