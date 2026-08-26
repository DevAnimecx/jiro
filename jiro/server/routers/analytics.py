"""Analytics & observability endpoints: /analytics, /analytics/engines,
/analytics/latency, /analytics/errors, /analytics/usage, /alerts.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, Request

from jiro.alerts import AlertManager
from jiro.analytics import AnalyticsEngine
from jiro.auth import AuthContext
from jiro.server.deps import (
    get_auth_context,
    record_usage,
)

router = APIRouter(tags=["analytics"])


def _get_analytics(request: Request) -> AnalyticsEngine:
    """Get or create the analytics engine (attached to app state)."""
    if not hasattr(request.app.state, "_analytics"):
        request.app.state._analytics = AnalyticsEngine(request.app.state.db)
    return request.app.state._analytics


def _get_alerts(request: Request) -> AlertManager:
    """Get or create the alert manager (attached to app state)."""
    if not hasattr(request.app.state, "_alert_manager"):
        request.app.state._alert_manager = AlertManager()
    return request.app.state._alert_manager


# ── Dashboard ────────────────────────────────────────────────────────────

@router.get("/analytics", summary="Full analytics dashboard")
async def analytics_dashboard(
    request: Request,
    since_hours: float = Query(24, ge=0, description="Hours to look back"),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Complete analytics dashboard with engine metrics, latency, errors, and usage patterns."""
    request.state.auth = ctx
    since = time.time() - (since_hours * 3600) if since_hours > 0 else 0
    analytics = _get_analytics(request)
    data = await analytics.dashboard(since=since)
    await record_usage(request, endpoint="/analytics", status=200)
    return data


# ── Engine Metrics ───────────────────────────────────────────────────────

@router.get("/analytics/engines", summary="Per-engine performance metrics")
async def analytics_engines(
    request: Request,
    since_hours: float = Query(24, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    since = time.time() - (since_hours * 3600) if since_hours > 0 else 0
    analytics = _get_analytics(request)
    data = await analytics.engine_metrics(since=since)
    await record_usage(request, endpoint="/analytics/engines", status=200)
    return data


# ── Latency Percentiles ─────────────────────────────────────────────────

@router.get("/analytics/latency", summary="Latency percentiles (p50, p95, p99)")
async def analytics_latency(
    request: Request,
    since_hours: float = Query(24, ge=0),
    engine: Optional[str] = Query(None, description="Filter by engine"),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    since = time.time() - (since_hours * 3600) if since_hours > 0 else 0
    analytics = _get_analytics(request)
    data = await analytics.latency_percentiles(since=since, engine=engine)
    await record_usage(request, endpoint="/analytics/latency", status=200)
    return data


# ── Error Rates ──────────────────────────────────────────────────────────

@router.get("/analytics/errors", summary="Error rates by status code and engine")
async def analytics_errors(
    request: Request,
    since_hours: float = Query(24, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    since = time.time() - (since_hours * 3600) if since_hours > 0 else 0
    analytics = _get_analytics(request)
    data = await analytics.error_rates(since=since)
    await record_usage(request, endpoint="/analytics/errors", status=200)
    return data


# ── Usage Patterns ───────────────────────────────────────────────────────

@router.get("/analytics/usage", summary="Usage patterns: top queries, peak hours, engine popularity")
async def analytics_usage(
    request: Request,
    since_hours: float = Query(168, ge=0, description="Hours to look back (default 7 days)"),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    since = time.time() - (since_hours * 3600) if since_hours > 0 else 0
    analytics = _get_analytics(request)
    data = await analytics.usage_patterns(since=since)
    await record_usage(request, endpoint="/analytics/usage", status=200)
    return data


# ── Alerts ───────────────────────────────────────────────────────────────

@router.get("/alerts", summary="List recent alerts")
async def list_alerts(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None, description="info | warning | critical"),
    engine: Optional[str] = Query(None),
    unacknowledged_only: bool = Query(False),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    alerts = _get_alerts(request)
    items = alerts.list_alerts(
        limit=limit, severity=severity, engine=engine,
        unacknowledged_only=unacknowledged_only,
    )
    await record_usage(request, endpoint="/alerts", status=200)
    return {"alerts": items, "count": len(items)}


@router.get("/alerts/summary", summary="Alert summary counts")
async def alerts_summary(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    alerts = _get_alerts(request)
    data = alerts.summary()
    await record_usage(request, endpoint="/alerts/summary", status=200)
    return data


@router.post("/alerts/{alert_id}/acknowledge", summary="Acknowledge an alert")
async def acknowledge_alert(
    request: Request,
    alert_id: str,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    alerts = _get_alerts(request)
    ok = alerts.acknowledge(alert_id)
    await record_usage(request, endpoint="/alerts/acknowledge", status=200)
    return {"acknowledged": ok, "alert_id": alert_id}


@router.delete("/alerts", summary="Clear all alerts")
async def clear_alerts(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    alerts = _get_alerts(request)
    n = alerts.clear()
    await record_usage(request, endpoint="/alerts/clear", status=200)
    return {"cleared": n}


# ── Audit Log ────────────────────────────────────────────────────────────

@router.get("/audit", summary="Recent audit log entries")
async def audit_recent(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    method: Optional[str] = Query(None, description="GET | POST | DELETE"),
    path_prefix: Optional[str] = Query(None, description="Filter by path prefix"),
    status_min: Optional[int] = Query(None, description="Min status code (e.g. 400 for errors)"),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    audit = getattr(request.app.state, "_audit_logger", None)
    if audit is None:
        return {"entries": [], "count": 0, "message": "audit logging not initialized"}
    entries = audit.recent(limit=limit, method=method,
                           path_prefix=path_prefix, status_min=status_min)
    await record_usage(request, endpoint="/audit", status=200)
    return {"entries": entries, "count": len(entries)}


@router.get("/audit/summary", summary="Audit log summary statistics")
async def audit_summary(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    audit = getattr(request.app.state, "_audit_logger", None)
    if audit is None:
        return {"total_requests": 0, "entries_stored": 0}
    data = audit.summary()
    await record_usage(request, endpoint="/audit/summary", status=200)
    return data
