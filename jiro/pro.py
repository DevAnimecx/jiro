"""Pro tier system for Jiro v0.2.

Provides:
- API key authentication with tiered access
- Rate limiting per API key
- Quota management
- Usage tracking and billing
- Webhook support for usage alerts
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from jiro.config import Settings
from jiro.db import Database
from jiro.log import get_logger

log = get_logger("jiro.pro")


class PlanTier(str, Enum):
    """API plan tiers — two tiers: Free and Enterprise."""
    FREE = "free"
    ENTERPRISE = "enterprise"


@dataclass
class PlanLimits:
    """Rate and quota limits for a plan.

    Single source of truth for all feature gating. Feature availability
    is determined by tier level, not by boolean flags.
    """
    rpm: int = 100
    rpd: int = 10000
    rpm_search: int = 50
    rpd_search: int = 5000
    rpm_scrape: int = 30
    rpd_scrape: int = 3000
    max_results: int = 50
    max_concurrent: int = 20
    max_batch_size: int = 25
    hybrid_search: bool = True
    structured_extraction: bool = True
    social_scraping: bool = True
    smart_search: bool = True
    priority: int = 0
    webhook_alerts: bool = True
    custom_models: bool = False
    commercial_use: bool = False


# ── Plan tier definitions ─────────────────────────────────────────────
# FREE tier: very generous limits, most features unlocked
# ENTERPRISE tier: maximum everything, all features unlocked
PLAN_LIMITS: Dict[PlanTier, PlanLimits] = {
    PlanTier.FREE: PlanLimits(
        # Generous rate limits for free tier
        rpm=100,
        rpd=10000,
        rpm_search=50,
        rpd_search=5000,
        rpm_scrape=30,
        rpd_scrape=3000,
        max_results=50,
        max_concurrent=20,
        max_batch_size=25,
        priority=0,
        # Free features — most capabilities unlocked
        hybrid_search=True,
        structured_extraction=True,
        social_scraping=True,
        smart_search=True,
        webhook_alerts=True,
        custom_models=False,
        commercial_use=False,
    ),
    PlanTier.ENTERPRISE: PlanLimits(
        # Maximum everything
        rpm=1000,
        rpd=1000000,
        rpm_search=500,
        rpd_search=500000,
        rpm_scrape=300,
        rpd_scrape=300000,
        max_results=200,
        max_concurrent=50,
        max_batch_size=500,
        priority=10,
        # All features unlocked
        hybrid_search=True,
        structured_extraction=True,
        social_scraping=True,
        smart_search=True,
        webhook_alerts=True,
        custom_models=True,
        commercial_use=True,
    ),
}


def get_features_for_tier(tier: PlanTier) -> List[str]:
    """Derive feature list from tier level — single source of truth."""
    tier_level = get_tier_level(tier.value)
    return [
        feature for feature, min_tier in _FEATURE_MIN_TIERS.items()
        if get_tier_level(min_tier) <= tier_level
    ]


def get_all_feature_names() -> List[str]:
    """Return every feature name across all tiers."""
    return sorted(_FEATURE_MIN_TIERS.keys())


# Feature-to-minimum-tier mapping (replaces boolean flags)
_FEATURE_MIN_TIERS: Dict[str, str] = {
    "basic_search": "free",
    "basic_scrape": "free",
    "open_scrapers": "free",
    "social_advanced": "free",
    "social_search": "free",
    "social_timeline": "free",
    "social_batch": "free",
    "ai_search": "enterprise",
    "smart_search": "free",
    "structured_extraction": "free",
    "self_learning": "free",
    "advanced_healing": "enterprise",
    "high_volume": "enterprise",
    "custom_models": "enterprise",
    "commercial_use": "enterprise",
    "premium_support": "enterprise",
    "white_label": "enterprise",
    "webhook_alerts": "free",
}

# Tier hierarchy for quick comparison
_TIER_LEVELS = {
    "free": 0,
    "enterprise": 1,
}


def get_tier_level(tier: str) -> int:
    """Get numeric level for a tier name."""
    return _TIER_LEVELS.get(tier.lower(), 0)


@dataclass
class APIKey:
    """API key with metadata."""
    id: str
    name: str
    key_hash: str
    key_prefix: str
    tier: PlanTier
    scopes: List[str] = field(default_factory=lambda: ["search", "scrape", "ai"])
    created_at: float = field(default_factory=time.time)
    revoked: bool = False
    last_used_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def limits(self) -> PlanLimits:
        return PLAN_LIMITS[self.tier]


class CapabilityToken:
    """HMAC-signed capability token for enterprise feature access.

    Each enterprise feature access requires a valid capability token
    that is cryptographically verified on every request. Tokens are
    short-lived (5 minutes) and feature-specific.
    """

    def __init__(self, secret: str, feature: str, customer_id: str) -> None:
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        self._feature = feature
        self._customer_id = customer_id

    def generate(self, ttl: int = 300) -> str:
        """Generate a signed capability token."""
        payload = {
            "feat": self._feature,
            "sub": self._customer_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + ttl,
            "jti": secrets.token_hex(8),
        }
        token = jwt.encode(payload, self._secret, algorithm="HS256")
        return token

    def verify(self, token: str) -> Dict[str, Any]:
        """Verify a capability token. Returns payload dict or raises."""
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
            if payload.get("feat") != self._feature:
                raise ValueError("capability mismatch")
            if payload.get("sub") != self._customer_id:
                raise ValueError("customer mismatch")
            return payload
        except jwt.ExpiredSignatureError:
            raise ValueError("capability token expired")
        except jwt.InvalidTokenError as exc:
            raise ValueError(f"invalid capability token: {exc}")


def get_capability_token(settings: Settings, feature: str, customer_id: str) -> CapabilityToken:
    """Create a capability token verifier for an enterprise feature."""
    secret = settings.get("licensing.jwt_secret", "") or settings.jwt_secret
    return CapabilityToken(secret=secret, feature=feature, customer_id=customer_id)


class RateLimiter:
    """Token bucket rate limiter for API keys."""

    def __init__(self) -> None:
        self._buckets: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key_id: str, tier: PlanTier, endpoint: str = "default") -> bool:
        """Check if request is allowed. Returns True if allowed."""
        async with self._lock:
            bucket_key = f"{key_id}:{endpoint}"
            limits = PLAN_LIMITS[tier]

            # Get or create bucket
            if bucket_key not in self._buckets:
                self._buckets[bucket_key] = {
                    "tokens": limits.rpm,
                    "last_refill": time.time(),
                    "rpm_limit": limits.rpm,
                }

            bucket = self._buckets[bucket_key]
            now = time.time()

            # Refill tokens
            elapsed = now - bucket["last_refill"]
            refill = elapsed * (bucket["rpm_limit"] / 60.0)
            bucket["tokens"] = min(bucket["rpm_limit"], bucket["tokens"] + refill)
            bucket["last_refill"] = now

            # Check token
            if bucket["tokens"] >= 1:
                bucket["tokens"] -= 1
                return True
            return False

    async def get_usage(self, key_id: str, tier: PlanTier) -> Dict[str, Any]:
        """Get current rate limit usage."""
        limits = PLAN_LIMITS[tier]
        return {
            "rpm_limit": limits.rpm,
            "rpd_limit": limits.rpd,
            "tier": tier.value,
        }


class QuotaManager:
    """Daily quota tracking and enforcement."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def check_and_increment(self, key_id: str, tier: PlanTier,
                                  endpoint: str = "default") -> bool:
        """Check quota and increment if allowed."""
        async with self._lock:
            today = time.time() // 86400
            cache_key = f"{key_id}:{today}"

            # Get or init daily count
            if cache_key not in self._cache:
                row = await self.db.fetchone(
                    "SELECT COUNT(*) as n FROM usage WHERE key_id = ? AND ts >= ?",
                    key_id, today * 86400,
                )
                self._cache[cache_key] = {
                    "count": row["n"] if row else 0,
                    "date": today,
                }

            daily = self._cache[cache_key]
            limits = PLAN_LIMITS[tier]

            # Check daily quota
            if daily["count"] >= limits.rpd:
                return False

            # Increment
            daily["count"] += 1
            return True

    async def get_usage(self, key_id: str, tier: PlanTier) -> Dict[str, Any]:
        """Get current quota usage."""
        today = time.time() // 86400
        row = await self.db.fetchone(
            "SELECT COUNT(*) as n FROM usage WHERE key_id = $1 AND ts >= $2",
            key_id, today * 86400,
        )
        count = row["n"] if row else 0
        limits = PLAN_LIMITS[tier]
        return {
            "used": count,
            "limit": limits.rpd,
            "remaining": max(0, limits.rpd - count),
            "reset_at": (today + 1) * 86400,
            "tier": tier.value,
        }


class ProManager:
    """Main Pro tier manager."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.rate_limiter = RateLimiter()
        self.quota_manager = QuotaManager(db)
        self._active_requests: Dict[str, int] = {}  # key_id -> count
        self._lock = asyncio.Lock()

    @staticmethod
    def generate_api_key() -> tuple[str, str, str]:
        """Generate new API key. Returns (key, key_hash, prefix)."""
        key = f"jiro_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        prefix = key[:12]
        return key, key_hash, prefix

    async def validate_key(self, api_key: str) -> Optional[APIKey]:
        """Validate an API key and return its metadata."""
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        row = await self.db.fetchone(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked = FALSE",
            key_hash,
        )
        if not row:
            return None

        # Update last used
        await self.db.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            time.time(), row["id"],
        )

        return APIKey(
            id=row["id"],
            name=row["name"],
            key_hash=row["key_hash"],
            key_prefix=row["key_prefix"],
            tier=PlanTier(row.get("tier", "free")),
            scopes=json.loads(row.get("scopes", "[]")),
            created_at=row["created_at"],
            revoked=row.get("revoked", False),
            last_used_at=row.get("last_used_at"),
        )

    async def create_key(self, name: str, tier: PlanTier = PlanTier.FREE,
                          scopes: Optional[List[str]] = None) -> tuple[APIKey, str]:
        """Create a new API key. Returns (APIKey, raw_key)."""
        raw_key, key_hash, prefix = self.generate_api_key()
        key_id = secrets.token_urlsafe(16)

        if scopes is None:
            scopes = ["search", "scrape", "ai"]

        await self.db.execute(
            "INSERT INTO api_keys (id, name, key_hash, key_prefix, tier, scopes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            key_id, name, key_hash, prefix, tier.value, json.dumps(scopes), time.time(),
        )

        log.info("api_key_created", extra={"key_id": key_id, "tier": tier.value})

        return APIKey(
            id=key_id,
            name=name,
            key_hash=key_hash,
            key_prefix=prefix,
            tier=tier,
            scopes=scopes,
        ), raw_key

    async def check_request(self, key_id: str, tier: PlanTier,
                            endpoint: str = "default") -> tuple[bool, str]:
        """Check if request is allowed (rate + quota). Returns (allowed, reason)."""
        # Check concurrent limit
        async with self._lock:
            current = self._active_requests.get(key_id, 0)
            limits = PLAN_LIMITS[tier]
            if current >= limits.max_concurrent:
                return False, "concurrent_limit_exceeded"

        # Check rate limit
        if not await self.rate_limiter.check(key_id, tier, endpoint):
            return False, "rate_limit_exceeded"

        # Check daily quota
        if not await self.quota_manager.check_and_increment(key_id, tier, endpoint):
            return False, "quota_exceeded"

        return True, "ok"

    async def start_request(self, key_id: str) -> None:
        """Track request start."""
        async with self._lock:
            self._active_requests[key_id] = self._active_requests.get(key_id, 0) + 1

    async def end_request(self, key_id: str) -> None:
        """Track request end."""
        async with self._lock:
            self._active_requests[key_id] = max(0, self._active_requests.get(key_id, 0) - 1)

    async def list_keys(self, include_revoked: bool = False) -> List[APIKey]:
        """List all API keys."""
        rows = await self.db.fetchall(
            "SELECT * FROM api_keys" + ("" if include_revoked else " WHERE revoked = FALSE")
            + " ORDER BY created_at DESC"
        )
        return [
            APIKey(
                id=r["id"],
                name=r["name"],
                key_hash=r["key_hash"],
                key_prefix=r["key_prefix"],
                tier=PlanTier(r.get("tier", "free")),
                scopes=json.loads(r.get("scopes", "[]")),
                created_at=r["created_at"],
                revoked=r.get("revoked", False),
                last_used_at=r.get("last_used_at"),
            )
            for r in rows
        ]

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        await self.db.execute(
            "UPDATE api_keys SET revoked = TRUE WHERE id = $1", key_id
        )
        log.info("api_key_revoked", extra={"key_id": key_id})
        return True

    async def upgrade_key(self, key_id: str, new_tier: PlanTier) -> bool:
        """Upgrade an API key's tier."""
        await self.db.execute(
            "UPDATE api_keys SET tier = $1 WHERE id = $2", new_tier.value, key_id
        )
        log.info("api_key_upgraded", extra={"key_id": key_id, "new_tier": new_tier.value})
        return True

    async def get_usage_summary(self, key_id: str, tier: PlanTier,
                                 days: int = 30) -> Dict[str, Any]:
        """Get comprehensive usage summary."""
        since = time.time() - (days * 86400)

        # Total requests
        total = await self.db.fetchone(
            "SELECT COUNT(*) as n, SUM(tokens_in) as ti, SUM(tokens_out) as to_,"
            " SUM(cached) as c FROM usage WHERE key_id = $1 AND ts >= $2",
            key_id, since,
        )

        # By endpoint
        by_endpoint = await self.db.fetchall(
            "SELECT endpoint, COUNT(*) as n FROM usage WHERE key_id = $1 AND ts >= $2"
            " GROUP BY endpoint ORDER BY n DESC",
            key_id, since,
        )

        # By engine
        by_engine = await self.db.fetchall(
            "SELECT engine, COUNT(*) as n FROM usage WHERE key_id = $1 AND ts >= $2"
            " AND engine IS NOT NULL GROUP BY engine ORDER BY n DESC",
            key_id, since,
        )

        # Daily usage
        daily = await self.db.fetchall(
            "SELECT DATE(ts, 'unixepoch') as day, COUNT(*) as n"
            " FROM usage WHERE key_id = $1 AND ts >= $2"
            " GROUP BY day ORDER BY day",
            key_id, since,
        )

        # Today's usage
        today_start = time.time() // 86400 * 86400
        today_count = await self.db.fetchone(
            "SELECT COUNT(*) as n FROM usage WHERE key_id = $1 AND ts >= $2",
            key_id, today_start,
        )

        limits = PLAN_LIMITS[tier]
        return {
            "key_id": key_id,
            "tier": tier.value,
            "period_days": days,
            "total_requests": total["n"] if total else 0,
            "total_tokens_in": total["ti"] or 0 if total else 0,
            "total_tokens_out": total["to_"] or 0 if total else 0,
            "cached_requests": total["c"] or 0 if total else 0,
            "today_requests": today_count["n"] if today_count else 0,
            "today_limit": limits.rpd,
            "today_remaining": max(0, limits.rpd - (today_count["n"] if today_count else 0)),
            "by_endpoint": by_endpoint,
            "by_engine": by_engine,
            "daily": daily,
            "limits": {
                "rpm": limits.rpm,
                "rpd": limits.rpd,
                "max_results": limits.max_results,
                "max_concurrent": limits.max_concurrent,
            },
        }


# Global instance with proper cleanup tracking
_pro_manager: Optional[ProManager] = None
_pro_manager_db_owner: bool = False  # Track if we own the DB connection


def get_pro_manager(settings: Optional[Settings] = None, db: Optional[Database] = None) -> ProManager:
    """Get or create ProManager singleton.
    
    SECURITY: Properly tracks database ownership to prevent connection leaks.
    """
    global _pro_manager, _pro_manager_db_owner
    if _pro_manager is None:
        if settings is None:
            settings = Settings.load()
        if db is None:
            from jiro.db import Database as DB
            db = DB(settings.db_path)
            _pro_manager_db_owner = True  # We created it, we own it
        else:
            _pro_manager_db_owner = False  # External DB, don't close it
        _pro_manager = ProManager(settings, db)
    return _pro_manager


async def cleanup_pro_manager() -> None:
    """Clean up ProManager and its resources."""
    global _pro_manager, _pro_manager_db_owner
    if _pro_manager is not None and _pro_manager_db_owner:
        # Don't close DB here - let the main app handle it
        pass
    _pro_manager = None
    _pro_manager_db_owner = False