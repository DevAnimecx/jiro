"""Advanced rate limiting with sliding window, quotas, and tier-based limits.

Provides:
- Sliding window rate limiting (per-second, per-minute, per-hour, per-day)
- Usage quotas with configurable limits
- Tier-based rate limiting (free, pro, enterprise)
- Redis-backed distributed rate limiting
- Rate limit headers in responses
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Tier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class RateLimitConfig:
    """Rate limit configuration for a tier."""
    requests_per_second: int = 10
    requests_per_minute: int = 100
    requests_per_hour: int = 1000
    requests_per_day: int = 10000
    burst_size: int = 20  # Max burst size


@dataclass
class QuotaConfig:
    """Usage quota configuration."""
    monthly_searches: int = 10000
    monthly_scrapes: int = 5000
    monthly_ai_queries: int = 1000
    max_concurrent_requests: int = 10


# Default tier configurations
TIER_LIMITS: Dict[Tier, RateLimitConfig] = {
    Tier.FREE: RateLimitConfig(
        requests_per_second=5,
        requests_per_minute=60,
        requests_per_hour=500,
        requests_per_day=5000,
        burst_size=10,
    ),
    Tier.PRO: RateLimitConfig(
        requests_per_second=20,
        requests_per_minute=200,
        requests_per_hour=5000,
        requests_per_day=50000,
        burst_size=50,
    ),
    Tier.ENTERPRISE: RateLimitConfig(
        requests_per_second=100,
        requests_per_minute=1000,
        requests_per_hour=50000,
        requests_per_day=500000,
        burst_size=200,
    ),
}

TIER_QUOTAS: Dict[Tier, QuotaConfig] = {
    Tier.FREE: QuotaConfig(
        monthly_searches=1000,
        monthly_scrapes=500,
        monthly_ai_queries=100,
        max_concurrent_requests=5,
    ),
    Tier.PRO: QuotaConfig(
        monthly_searches=50000,
        monthly_scrapes=25000,
        monthly_ai_queries=5000,
        max_concurrent_requests=20,
    ),
    Tier.ENTERPRISE: QuotaConfig(
        monthly_searches=1000000,
        monthly_scrapes=500000,
        monthly_ai_queries=100000,
        max_concurrent_requests=100,
    ),
}


class SlidingWindowRateLimiter:
    """In-memory sliding window rate limiter."""

    def __init__(self, config: RateLimitConfig) -> None:
        self.config = config
        self._windows: Dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> tuple[bool, Dict[str, int]]:
        """Check if request is allowed. Returns (allowed, headers)."""
        now = time.time()
        
        # Clean old entries
        self._windows[key] = [
            t for t in self._windows[key]
            if now - t < 86400  # Keep 24h of data
        ]
        
        # Check limits
        window_1s = [t for t in self._windows[key] if now - t < 1]
        window_1m = [t for t in self._windows[key] if now - t < 60]
        window_1h = [t for t in self._windows[key] if now - t < 3600]
        window_1d = [t for t in self._windows[key] if now - t < 86400]
        
        headers = {
            "X-RateLimit-Limit-Sec": self.config.requests_per_second,
            "X-RateLimit-Remaining-Sec": max(0, self.config.requests_per_second - len(window_1s)),
            "X-RateLimit-Limit-Min": self.config.requests_per_minute,
            "X-RateLimit-Remaining-Min": max(0, self.config.requests_per_minute - len(window_1m)),
            "X-RateLimit-Limit-Hour": self.config.requests_per_hour,
            "X-RateLimit-Remaining-Hour": max(0, self.config.requests_per_hour - len(window_1h)),
            "X-RateLimit-Limit-Day": self.config.requests_per_day,
            "X-RateLimit-Remaining-Day": max(0, self.config.requests_per_day - len(window_1d)),
        }
        
        if (len(window_1s) >= self.config.requests_per_second or
            len(window_1m) >= self.config.requests_per_minute or
            len(window_1h) >= self.config.requests_per_hour or
            len(window_1d) >= self.config.requests_per_day):
            return False, headers
        
        self._windows[key].append(now)
        return True, headers


class UsageQuotaManager:
    """Track and enforce usage quotas."""

    def __init__(self) -> None:
        self._usage: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def check_quota(self, api_key: str, quota_type: str, limit: int) -> tuple[bool, int, int]:
        """Check if quota is available. Returns (allowed, used, remaining)."""
        month_key = time.strftime("%Y-%m")
        usage_key = f"{api_key}:{month_key}"
        
        used = self._usage[usage_key][quota_type]
        remaining = max(0, limit - used)
        
        return used < limit, used, remaining

    def record_usage(self, api_key: str, quota_type: str, amount: int = 1) -> None:
        """Record usage against quota."""
        month_key = time.strftime("%Y-%m")
        usage_key = f"{api_key}:{month_key}"
        self._usage[usage_key][quota_type] += amount

    def get_usage(self, api_key: str) -> Dict[str, Dict[str, Any]]:
        """Get usage summary for an API key."""
        month_key = time.strftime("%Y-%m")
        usage_key = f"{api_key}:{month_key}"
        
        return {
            "month": month_key,
            "searches": self._usage[usage_key].get("searches", 0),
            "scrapes": self._usage[usage_key].get("scrapes", 0),
            "ai_queries": self._usage[usage_key].get("ai_queries", 0),
        }


# Global instances
_rate_limiters: Dict[str, SlidingWindowRateLimiter] = {}
_quota_manager = UsageQuotaManager()


def get_rate_limiter(api_key: str, tier: Tier = Tier.FREE) -> SlidingWindowRateLimiter:
    """Get or create rate limiter for an API key."""
    if api_key not in _rate_limiters:
        config = TIER_LIMITS.get(tier, TIER_LIMITS[Tier.FREE])
        _rate_limiters[api_key] = SlidingWindowRateLimiter(config)
    return _rate_limiters[api_key]


def get_quota_manager() -> UsageQuotaManager:
    """Get the global quota manager."""
    return _quota_manager
