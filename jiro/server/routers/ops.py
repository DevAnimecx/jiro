"""Operations endpoints: proxy status, proxy health check, CAPTCHA solving, config info."""

from __future__ import annotations

import base64
import time
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from jiro.auth import AuthContext
from jiro.captcha import CaptchaSolver
from jiro.errors import JiroPermissionError
from jiro.server.deps import get_auth_context, record_usage
from jiro.telemetry import get_metrics
from jiro.tracing import get_tracer

router = APIRouter(tags=["ops"])

# Base URL used to probe reachability of each search engine.
ENGINE_BASE_URLS = {
    "google": "https://www.google.com",
    "bing": "https://www.bing.com",
    "duckduckgo": "https://duckduckgo.com",
    "brave": "https://search.brave.com",
    "youtube": "https://www.youtube.com",
    "amazon": "https://www.amazon.com",
    "ebay": "https://www.ebay.com",
    "yandex": "https://yandex.com",
    "baidu": "https://www.baidu.com",
}


@router.get("/health/engines", summary="Probe reachability of configured engines")
async def engine_health(request: Request) -> Dict[str, Any]:
    """Lightweight reachability probe for each configured engine.

    Useful to detect that, e.g., Google is serving CAPTCHAs from a datacenter
    IP before relying on it in production. Performs a single GET per engine
    with a short timeout; failures are reported, never raised.
    """
    settings = request.app.state.settings
    engines = settings.engines or list(ENGINE_BASE_URLS)
    results = []
    for engine in engines:
        url = ENGINE_BASE_URLS.get(engine)
        if not url:
            results.append({"engine": engine, "reachable": None,
                            "note": "no probe URL known"})
            continue
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0),
                                         follow_redirects=True,
                                         trust_env=False) as c:
                resp = await c.get(url)
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            results.append({
                "engine": engine,
                "reachable": 200 <= resp.status_code < 400,
                "status_code": resp.status_code,
                "latency_ms": latency_ms,
            })
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            results.append({
                "engine": engine,
                "reachable": False,
                "error": str(exc)[:200],
                "latency_ms": latency_ms,
            })
    reachable = sum(1 for r in results if r.get("reachable"))
    await record_usage(request, endpoint="/health/engines", status=200)
    return {"total": len(results), "reachable": reachable,
            "engines": results}


async def admin_or_open(request: Request,
                        ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Require admin only when auth is enabled; open otherwise (local use)."""
    request.state.auth = ctx
    if request.app.state.settings.auth_enabled and ctx.record is None:
        raise JiroPermissionError("admin role required")
    if request.app.state.settings.auth_enabled and ctx.role != "admin":
        raise JiroPermissionError("admin role required")
    return ctx


@router.get("/proxy/status", summary="BYOK proxy configuration status")
async def proxy_status(
    request: Request,
    ctx: AuthContext = Depends(admin_or_open),
) -> Dict[str, Any]:
    client = request.app.state.scraper_client
    return {"proxy": client.proxies.info()}


@router.post("/proxy/health", summary="Check proxy connectivity (tests each endpoint)")
async def proxy_health_check(
    request: Request,
    ctx: AuthContext = Depends(admin_or_open),
) -> Dict[str, Any]:
    client = request.app.state.scraper_client
    pm = client.proxies
    urls = pm.configured_urls()
    if not urls:
        return {"endpoints": [], "message": "no proxies configured"}

    results = []
    for url in urls:
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                proxy=url, timeout=httpx.Timeout(10.0),
                follow_redirects=True,
            ) as test_client:
                resp = await test_client.get("https://httpbin.org/ip")
                latency_ms = round((time.perf_counter() - start) * 1000, 1)
                healthy = resp.status_code == 200
                results.append({
                    "endpoint": pm._redact(url),
                    "healthy": healthy,
                    "status_code": resp.status_code,
                    "latency_ms": latency_ms,
                })
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            results.append({
                "endpoint": pm._redact(url),
                "healthy": False,
                "error": str(exc)[:200],
                "latency_ms": latency_ms,
            })

    healthy_count = sum(1 for r in results if r["healthy"])
    await record_usage(request, endpoint="/proxy/health", status=200)
    return {
        "total": len(results),
        "healthy": healthy_count,
        "unhealthy": len(results) - healthy_count,
        "endpoints": results,
    }


@router.get("/captcha/status", summary="BYOK CAPTCHA solver status")
async def captcha_status(
    request: Request,
    ctx: AuthContext = Depends(admin_or_open),
) -> Dict[str, Any]:
    solver: CaptchaSolver = request.app.state.captcha
    return {
        "enabled": solver.enabled,
        "provider": solver.provider,
        "ready": solver.ready,
    }


@router.post("/captcha/solve", summary="Solve a CAPTCHA image (base64 in JSON body)")
async def captcha_solve(
    request: Request,
    body: Dict[str, Any],
    ctx: AuthContext = Depends(admin_or_open),
) -> Dict[str, Any]:
    solver: CaptchaSolver = request.app.state.captcha
    if not solver.ready:
        return {"error": "captcha solver not configured",
                "hint": "set scraping.captcha.enabled=true and provider/api_key"}
    b64 = body.get("image", "")
    if not b64:
        return {"error": "provide 'image' as base64"}
    try:
        data = base64.b64decode(b64)
    except Exception:
        return {"error": "invalid base64 image"}
    if len(data) > 2_000_000:
        return {"error": "image too large"}
    text = await solver.solve_image(data)
    await record_usage(request, endpoint="/captcha/solve", status=200)
    return {"solved": True, "text": text, "provider": solver.provider}


@router.get("/metrics", response_class=PlainTextResponse, summary="Prometheus metrics")
async def prometheus_metrics(request: Request) -> PlainTextResponse:
    metrics = get_metrics()
    content = metrics.render()
    return PlainTextResponse(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/health", summary="Comprehensive health check")
async def health_check(request: Request) -> Dict[str, Any]:
    tracer = get_tracer()
    with tracer.span("health_check"):
        settings = request.app.state.settings
        db = request.app.state.db
        checks = {"status": "healthy", "checks": {}}

        # Database check
        try:
            start = time.perf_counter()
            await db.fetchone("SELECT 1")
            db_ms = round((time.perf_counter() - start) * 1000, 2)
            checks["checks"]["database"] = {"status": "healthy", "latency_ms": db_ms}
        except Exception as exc:
            checks["checks"]["database"] = {"status": "unhealthy", "error": str(exc)[:200]}
            checks["status"] = "degraded"

        # Cache check
        cache = getattr(request.app.state, "cache", None)
        if cache:
            try:
                start = time.perf_counter()
                await cache.set("_health", "ok", ttl=10)
                cache_ms = round((time.perf_counter() - start) * 1000, 2)
                checks["checks"]["cache"] = {"status": "healthy", "latency_ms": cache_ms}
            except Exception as exc:
                checks["checks"]["cache"] = {"status": "unhealthy", "error": str(exc)[:200]}
                checks["status"] = "degraded"
        else:
            checks["checks"]["cache"] = {"status": "not_configured"}

        # Auth check
        auth = getattr(request.app.state, "auth", None)
        if auth:
            checks["checks"]["auth"] = {"status": "healthy", "enabled": settings.auth_enabled}
        else:
            checks["checks"]["auth"] = {"status": "not_configured"}

        # Uptime
        checks["uptime_seconds"] = round(time.time() - _server_start_time, 2)
        checks["version"] = settings.get("version", "0.2.8")

    return checks


_server_start_time = time.time()


@router.get("/health/live", summary="Liveness probe")
async def liveness() -> Dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready", summary="Readiness probe")
async def readiness(request: Request) -> Dict[str, str]:
    db = request.app.state.db
    try:
        await db.fetchone("SELECT 1")
        return {"status": "ready"}
    except Exception:
        return {"status": "not_ready"}
