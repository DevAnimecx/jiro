"""Operations endpoints: proxy status, proxy health check, CAPTCHA solving, config info."""

from __future__ import annotations

import base64
import time
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, Request

from jiro.auth import AuthContext
from jiro.captcha import CaptchaSolver
from jiro.errors import PermissionError
from jiro.server.deps import get_auth_context, record_usage

router = APIRouter(tags=["ops"])


async def admin_or_open(request: Request,
                        ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Require admin only when auth is enabled; open otherwise (local use)."""
    request.state.auth = ctx
    if request.app.state.settings.auth_enabled and ctx.record is None:
        raise PermissionError("admin role required")
    if request.app.state.settings.auth_enabled and ctx.role != "admin":
        raise PermissionError("admin role required")
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
