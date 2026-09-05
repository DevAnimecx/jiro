"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends, Request

from jiro.auth import AuthContext, build_auth_context
from jiro.errors import AuthError, JiroPermissionError
from jiro.audit import ComplianceLogger
from jiro.cache import CacheManager
from jiro.config import Settings
from jiro.db import Database
from jiro.errors import ForbiddenError, LicenseError
from jiro.feature_flags import get_feature_gate
from jiro.licensing import FEATURE_DEFINITIONS
from jiro.pro import PLAN_LIMITS, PlanLimits, PlanTier
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
    """Return LLM instance, with per-request overrides from headers if present.

    jiro-cloud passes LLM config via X-LLM-* headers so the admin panel
    can manage API keys centrally without touching jiro-search config.
    """
    base_llm = request.app.state.llm
    headers = request.headers

    trusted = headers.get("x-trusted-proxy", "").lower() == "jiro-cloud"
    if not trusted:
        return base_llm

    api_key = headers.get("x-llm-api-key")
    provider = headers.get("x-llm-provider")
    model = headers.get("x-llm-model")
    base_url = headers.get("x-llm-base-url")

    if not any([api_key, provider, model, base_url]):
        return base_llm

    from jiro.ai.llm import LLM

    settings = request.app.state.settings
    overridden_config = dict(settings.llm)
    if api_key:
        overridden_config["api_key"] = api_key
    if provider:
        overridden_config["provider"] = provider
    if model:
        overridden_config["model"] = model
    if base_url:
        overridden_config["base_url"] = base_url

    class _OverrideSettings:
        def __init__(self, llm_cfg: dict):
            self.llm = llm_cfg
            self.scraping = settings.scraping
            self.cache = settings.cache

    return LLM(_OverrideSettings(overridden_config))


def get_agent(request: Request) -> Any:
    return request.app.state.agent


def get_audit_logger_dep(request: Request) -> ComplianceLogger:
    return request.app.state.audit_logger


async def get_auth_context(
    request: Request,
    auth = Depends(get_auth_manager),
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
    auth = Depends(get_auth_manager),
) -> AuthContext:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return AuthContext(bucket=f"ip:{request.client.host if request.client else 'anon'}")
    return await build_auth_context(request, auth, require=False)


def require_scope(scope: str = "search"):
    async def _dep(
        ctx: AuthContext = Depends(get_auth_context),
        auth = Depends(get_auth_manager),
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


# ---------------------------------------------------------------------------
# License / feature enforcement
# ---------------------------------------------------------------------------

def require_feature(feature: str):
    """Dependency that gates an endpoint on a licensed feature.

    Usage::

        @router.post("/social/search")
        async def social_search(
            ...,
            ctx: AuthContext = Depends(require_feature("social_search")),
            ...
        ):
            ...
    """
    async def _dep(
        ctx: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        # Anonymous users: generous free features (no auth required)
        if ctx.record is None:
            anon_free = {
                "basic_search", "basic_scrape", "open_scrapers",
                "social_advanced", "social_search", "social_timeline",
                "smart_search", "structured_extraction", "webhook_alerts",
                "social_batch", "self_learning",
            }
            if feature not in anon_free:
                raise LicenseError(
                    f"'{feature}' requires authentication. "
                    f"Get your free API key at https://jiro.ai/dashboard",
                    details={
                        "feature": feature,
                        "required_tiers": FEATURE_DEFINITIONS.get(feature, {}).get("tiers", []),
                        "login_url": "https://jiro.ai/dashboard",
                    },
                )
            return ctx

        tier = PlanTier(ctx.record.get("tier", "free"))
        limits = PLAN_LIMITS[tier]
        feature_flag = getattr(limits, f"feature_{feature}", False)
        if not feature_flag:
            raise LicenseError(
                f"'{feature}' requires an Enterprise plan. "
                f"Current tier: {tier.value}. "
                f"Upgrade at https://jiro.ai/pricing",
                details={
                    "feature": feature,
                    "current_tier": tier.value,
                    "required_tiers": FEATURE_DEFINITIONS.get(feature, {}).get("tiers", []),
                    "upgrade_url": "https://jiro.ai/pricing",
                },
            )
        return ctx

    return _dep


def require_tier(minimum_tier: str):
    """Dependency that gates an endpoint on a minimum plan tier.

    For enterprise endpoints, this also validates the HMAC license token
    from the ``Authorization: License <token>`` header when present.

    Usage::

        @router.post("/enterprise/tenants")
        async def create_tenant(
            ...,
            ctx: AuthContext = Depends(require_tier("enterprise")),
            ...
        ):
            ...
    """
    async def _dep(
        request: Request,
        ctx: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        # Check if a license token is provided (deep lock for enterprise)
        license_token = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("license "):
            license_token = auth_header[8:].strip()

        if ctx.record is None:
            # Allow license-token-only auth for enterprise endpoints
            if license_token and minimum_tier == "enterprise":
                from jiro.licensing import get_license_manager
                manager = get_license_manager()
                info = manager.validate_token(license_token)
                if info.valid and info.tier == "enterprise":
                    # Create a synthetic auth context from the license
                    ctx = AuthContext(
                        bucket=f"license:{info.customer_id}",
                        key_id=f"lic:{info.license_id[:16]}",
                        role="admin" if "admin" in info.features else "user",
                    )
                    return ctx
            raise LicenseError(
                f"'{minimum_tier}' tier or higher required. "
                f"Get your free API key at https://jiro.ai/dashboard",
                details={"required_tier": minimum_tier},
            )

        current_tier = ctx.record.get("tier", "free")
        if get_tier_level(current_tier) < get_tier_level(minimum_tier):
            raise LicenseError(
                f"'{minimum_tier}' tier or higher required. "
                f"Current tier: {current_tier}. "
                f"Upgrade at https://jiro.ai/pricing",
                details={
                    "current_tier": current_tier,
                    "required_tier": minimum_tier,
                    "upgrade_url": "https://jiro.ai/pricing",
                },
            )
        return ctx

    return _dep


def get_tier_level(tier: str) -> int:
    """Get numeric level for a tier name."""
    from jiro.licensing import _TIER_LEVELS
    return _TIER_LEVELS.get(tier.lower(), 0)


def require_admin_dep(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """FastAPI dependency that requires admin role."""
    if ctx.record is None or ctx.record.get("role") != "admin":
        raise JiroPermissionError("admin role required")
    return ctx


def require_permission(permission: str):
    """FastAPI dependency factory that requires a specific RBAC permission.
    
    Usage::
        @router.delete("/users/{user_id}")
        async def delete_user(
            user_id: str,
            ctx: AuthContext = Depends(require_permission("users:delete")),
        ):
            ...
    """
    async def _dep(
        ctx: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        if ctx.record is None:
            raise AuthError("authentication required")
        
        from jiro.rbac import get_rbac_manager
        from jiro.db import Database
        from jiro.config import Settings
        
        db = Database(Settings.load().db_path)
        rbac = get_rbac_manager(db, Settings.load())
        identity = ctx.key_id or ""
        
        if not await rbac.has_permission(identity, permission):
            raise JiroPermissionError(
                f"permission denied: {permission}",
                details={"required": permission, "identity": identity},
            )
        return ctx
    
    return _dep

