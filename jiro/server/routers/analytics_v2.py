"""Analytics endpoints for query trends, usage patterns, and anomaly detection."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request

from jiro.auth import AuthContext
from jiro.server.deps import get_auth_context, get_settings, record_usage

router = APIRouter(tags=["analytics"])


@router.get("/analytics/trends", summary="Trending queries and topics")
async def get_trends(
    request: Request,
    top_n: int = Query(10, ge=1, le=100),
    window_hours: int = Query(24, ge=1, le=168),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    from jiro.analytics import get_analytics
    analytics = get_analytics()
    trends = analytics.get_trending(top_n, window_hours)
    await record_usage(request, endpoint="/analytics/trends", status=200)
    return {"trends": trends, "window_hours": window_hours}


@router.get("/analytics/popular", summary="Most popular queries")
async def get_popular(
    request: Request,
    top_n: int = Query(20, ge=1, le=100),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    from jiro.analytics import get_analytics
    analytics = get_analytics()
    popular = analytics.get_popular_queries(top_n)
    await record_usage(request, endpoint="/analytics/popular", status=200)
    return {"popular": popular}


@router.get("/analytics/engines", summary="Engine usage distribution")
async def get_engine_distribution(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    from jiro.analytics import get_analytics
    analytics = get_analytics()
    dist = analytics.get_engine_distribution()
    await record_usage(request, endpoint="/analytics/engines", status=200)
    return {"distribution": dist}


@router.get("/analytics/user/{user_id}", summary="Per-user analytics")
async def get_user_analytics(
    request: Request,
    user_id: str,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    from jiro.analytics import get_analytics
    analytics = get_analytics()
    stats = analytics.get_user_stats(user_id)
    await record_usage(request, endpoint="/analytics/user", status=200)
    return stats


@router.get("/analytics/volume", summary="Hourly query volume")
async def get_volume(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    from jiro.analytics import get_analytics
    analytics = get_analytics()
    volume = analytics.get_hourly_volume(hours)
    await record_usage(request, endpoint="/analytics/volume", status=200)
    return {"volume": volume, "hours": hours}


@router.get("/analytics/anomalies", summary="Detected anomalies and alerts")
async def get_anomalies(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    from jiro.analytics import get_analytics
    analytics = get_analytics()
    anomalies = analytics.get_anomalies(limit)
    await record_usage(request, endpoint="/analytics/anomalies", status=200)
    return {"anomalies": anomalies, "count": len(anomalies)}


@router.get("/analytics/summary", summary="Analytics summary overview")
async def get_summary(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    from jiro.analytics import get_analytics
    analytics = get_analytics()
    summary = analytics.get_summary()
    await record_usage(request, endpoint="/analytics/summary", status=200)
    return summary
