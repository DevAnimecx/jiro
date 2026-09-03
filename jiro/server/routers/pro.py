"""Pro tier API endpoints for Jiro v0.2.

Provides:
- API key management (create, list, revoke, upgrade)
- Usage tracking and analytics
- Rate limit status
- Plan information
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jiro.auth import get_auth_context, AuthContext
from jiro.config import Settings
from jiro.db import Database
from jiro.log import get_logger
from jiro.pro import ProManager, PlanTier, PLAN_LIMITS

log = get_logger("jiro.pro.router")

router = APIRouter(prefix="/v1/pro", tags=["pro"])


# Request/Response models

class CreateKeyRequest(BaseModel):
    name: str = Field(..., description="Friendly name for the API key")
    tier: str = Field("free", description="Plan tier: free, starter, pro, enterprise")
    scopes: List[str] = Field(default=["search", "scrape", "ai"], description="Allowed scopes")


class CreateKeyResponse(BaseModel):
    id: str
    name: str
    tier: str
    key: str = Field(..., description="Full API key (shown only once)")
    key_prefix: str
    scopes: List[str]
    created_at: float


class UpgradeKeyRequest(BaseModel):
    tier: str = Field(..., description="New plan tier: starter, pro, enterprise")


class KeyInfo(BaseModel):
    id: str
    name: str
    tier: str
    key_prefix: str
    scopes: List[str]
    created_at: float
    revoked: bool
    last_used_at: Optional[float] = None


class UsageSummary(BaseModel):
    key_id: str
    tier: str
    period_days: int
    total_requests: int
    total_tokens_in: int
    total_tokens_out: int
    cached_requests: int
    today_requests: int
    today_limit: int
    today_remaining: int
    by_endpoint: List[Dict[str, Any]]
    by_engine: List[Dict[str, Any]]
    daily: List[Dict[str, Any]]
    limits: Dict[str, Any]


class PlanInfo(BaseModel):
    name: str
    rpm: int
    rpd: int
    max_results: int
    max_concurrent: int
    hybrid_search: bool
    structured_extraction: bool
    social_scraping: bool
    smart_search: bool
    webhook_alerts: bool
    custom_models: bool
    price_monthly: Optional[float] = None


class RateLimitStatus(BaseModel):
    key_id: str
    tier: str
    rpm_limit: int
    rpd_limit: int
    concurrent_limit: int
    concurrent_current: int


class QuotaStatus(BaseModel):
    key_id: str
    tier: str
    used: int
    limit: int
    remaining: int
    reset_at: float


# Helper to get ProManager

async def _get_pro(request: Request) -> ProManager:
    settings = Settings.load()
    db = Database(settings.db_path)
    await db.connect()
    return ProManager(settings, db)


# Endpoints

@router.post("/keys", response_model=CreateKeyResponse)
async def create_api_key(
    req: CreateKeyRequest,
    auth: AuthContext = Depends(get_auth_context),
    pro: ProManager = Depends(_get_pro),
):
    """Create a new API key."""
    try:
        tier = PlanTier(req.tier)
    except ValueError:
        raise HTTPException(400, f"Invalid tier: {req.tier}")

    api_key, raw_key = await pro.create_key(req.name, tier, req.scopes)

    return CreateKeyResponse(
        id=api_key.id,
        name=api_key.name,
        tier=api_key.tier.value,
        key=raw_key,
        key_prefix=api_key.key_prefix,
        scopes=api_key.scopes,
        created_at=api_key.created_at,
    )


@router.get("/keys", response_model=List[KeyInfo])
async def list_api_keys(
    include_revoked: bool = Query(False),
    auth: AuthContext = Depends(get_auth_context),
    pro: ProManager = Depends(_get_pro),
):
    """List all API keys."""
    keys = await pro.list_keys(include_revoked)
    return [
        KeyInfo(
            id=k.id,
            name=k.name,
            tier=k.tier.value,
            key_prefix=k.key_prefix,
            scopes=k.scopes,
            created_at=k.created_at,
            revoked=k.revoked,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]


@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    auth: AuthContext = Depends(get_auth_context),
    pro: ProManager = Depends(_get_pro),
):
    """Revoke an API key."""
    success = await pro.revoke_key(key_id)
    if not success:
        raise HTTPException(404, "Key not found")
    return {"status": "revoked", "key_id": key_id}


@router.put("/keys/{key_id}/upgrade")
async def upgrade_api_key(
    key_id: str,
    req: UpgradeKeyRequest,
    auth: AuthContext = Depends(get_auth_context),
    pro: ProManager = Depends(_get_pro),
):
    """Upgrade an API key's tier."""
    try:
        new_tier = PlanTier(req.tier)
    except ValueError:
        raise HTTPException(400, f"Invalid tier: {req.tier}")

    success = await pro.upgrade_key(key_id, new_tier)
    if not success:
        raise HTTPException(404, "Key not found")

    return {"status": "upgraded", "key_id": key_id, "new_tier": new_tier.value}


@router.get("/usage", response_model=UsageSummary)
async def get_usage(
    key_id: str = Query(..., description="API key ID"),
    days: int = Query(30, ge=1, le=365),
    auth: AuthContext = Depends(get_auth_context),
    pro: ProManager = Depends(_get_pro),
):
    """Get usage summary for an API key."""
    keys = await pro.list_keys()
    key = next((k for k in keys if k.id == key_id), None)
    if not key:
        raise HTTPException(404, "Key not found")

    return await pro.get_usage_summary(key_id, key.tier, days)


@router.get("/rate-limit", response_model=RateLimitStatus)
async def get_rate_limit(
    auth: AuthContext = Depends(get_auth_context),
    pro: ProManager = Depends(_get_pro),
):
    """Get current rate limit status for the authenticated key."""
    tier = auth.tier if hasattr(auth, "tier") else PlanTier.FREE
    limits = PLAN_LIMITS[tier]

    return RateLimitStatus(
        key_id=auth.key_id if hasattr(auth, "key_id") else "anonymous",
        tier=tier.value,
        rpm_limit=limits.rpm,
        rpd_limit=limits.rpd,
        concurrent_limit=limits.max_concurrent,
        concurrent_current=pro._active_requests.get(auth.key_id, 0) if hasattr(auth, "key_id") else 0,
    )


@router.get("/quota", response_model=QuotaStatus)
async def get_quota(
    auth: AuthContext = Depends(get_auth_context),
    pro: ProManager = Depends(_get_pro),
):
    """Get current quota status for the authenticated key."""
    tier = auth.tier if hasattr(auth, "tier") else PlanTier.FREE
    key_id = auth.key_id if hasattr(auth, "key_id") else "anonymous"

    usage = await pro.quota_manager.get_usage(key_id, tier)
    return QuotaStatus(**usage)


@router.get("/plans", response_model=List[PlanInfo])
async def list_plans():
    """List all available plans with pricing."""
    plans = []
    prices = {
        PlanTier.FREE: 0.0,
        PlanTier.STARTER: 29.0,
        PlanTier.PRO: 99.0,
        PlanTier.ENTERPRISE: 499.0,
    }

    for tier, limits in PLAN_LIMITS.items():
        plans.append(PlanInfo(
            name=tier.value,
            rpm=limits.rpm,
            rpd=limits.rpd,
            max_results=limits.max_results,
            max_concurrent=limits.max_concurrent,
            hybrid_search=limits.hybrid_search,
            structured_extraction=limits.structured_extraction,
            social_scraping=limits.social_scraping,
            smart_search=limits.smart_search,
            webhook_alerts=limits.webhook_alerts,
            custom_models=limits.custom_models,
            price_monthly=prices.get(tier),
        ))

    return plans


@router.get("/tier/{tier}", response_model=PlanInfo)
async def get_plan_info(tier: str):
    """Get detailed info for a specific plan tier."""
    try:
        plan_tier = PlanTier(tier)
    except ValueError:
        raise HTTPException(400, f"Invalid tier: {tier}")

    limits = PLAN_LIMITS[plan_tier]
    prices = {
        PlanTier.FREE: 0.0,
        PlanTier.STARTER: 29.0,
        PlanTier.PRO: 99.0,
        PlanTier.ENTERPRISE: 499.0,
    }

    return PlanInfo(
        name=plan_tier.value,
        rpm=limits.rpm,
        rpd=limits.rpd,
        max_results=limits.max_results,
        max_concurrent=limits.max_concurrent,
        hybrid_search=limits.hybrid_search,
        structured_extraction=limits.structured_extraction,
        social_scraping=limits.social_scraping,
        smart_search=limits.smart_search,
        webhook_alerts=limits.webhook_alerts,
        custom_models=limits.custom_models,
        price_monthly=prices.get(plan_tier),
    )