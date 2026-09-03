"""Tests for Jiro v0.2 Pro tier system."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from jiro.pro import (
    ProManager, PlanTier, PlanLimits, RateLimiter, QuotaManager,
    PLAN_LIMITS, APIKey,
)


class TestPlanLimits:
    """Test plan tier configurations."""

    def test_free_tier_limits(self):
        limits = PLAN_LIMITS[PlanTier.FREE]
        assert limits.rpm == 10
        assert limits.rpd == 100
        assert limits.hybrid_search is False
        assert limits.structured_extraction is False
        assert limits.social_scraping is True
        assert limits.smart_search is False

    def test_starter_tier_limits(self):
        limits = PLAN_LIMITS[PlanTier.STARTER]
        assert limits.rpm == 60
        assert limits.rpd == 5000
        assert limits.hybrid_search is True
        assert limits.structured_extraction is True
        assert limits.social_scraping is True
        assert limits.smart_search is True
        assert limits.webhook_alerts is True

    def test_pro_tier_limits(self):
        limits = PLAN_LIMITS[PlanTier.PRO]
        assert limits.rpm == 300
        assert limits.rpd == 50000
        assert limits.max_results == 50
        assert limits.max_concurrent == 10
        assert limits.custom_models is True

    def test_enterprise_tier_limits(self):
        limits = PLAN_LIMITS[PlanTier.ENTERPRISE]
        assert limits.rpm == 1000
        assert limits.rpd == 500000
        assert limits.max_results == 100
        assert limits.max_concurrent == 20
        assert limits.priority == 3

    def test_all_tiers_have_limits(self):
        for tier in PlanTier:
            assert tier in PLAN_LIMITS

    def test_tiers_are_ordered_by_limits(self):
        tiers = [PlanTier.FREE, PlanTier.STARTER, PlanTier.PRO, PlanTier.ENTERPRISE]
        for i in range(len(tiers) - 1):
            current = PLAN_LIMITS[tiers[i]]
            next_tier = PLAN_LIMITS[tiers[i + 1]]
            assert current.rpm < next_tier.rpm
            assert current.rpd < next_tier.rpd


class TestRateLimiter:
    """Test rate limiter functionality."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_requests(self):
        limiter = RateLimiter()
        result = await limiter.check("key1", PlanTier.FREE, "search")
        assert result is True

    @pytest.mark.asyncio
    async def test_rate_limiter_tracks_usage(self):
        limiter = RateLimiter()
        await limiter.check("key1", PlanTier.FREE, "search")
        usage = await limiter.get_usage("key1", PlanTier.FREE)
        assert usage["tier"] == "free"
        assert usage["rpm_limit"] == 10


class TestAPIKey:
    """Test API key data structure."""

    def test_api_key_creation(self):
        key = APIKey(
            id="test_id",
            name="Test Key",
            key_hash="abc123",
            key_prefix="jiro_test",
            tier=PlanTier.FREE,
        )
        assert key.id == "test_id"
        assert key.tier == PlanTier.FREE
        assert key.revoked is False

    def test_api_key_limits(self):
        key = APIKey(
            id="test_id",
            name="Test Key",
            key_hash="abc123",
            key_prefix="jiro_test",
            tier=PlanTier.PRO,
        )
        limits = key.limits
        assert limits.rpm == 300
        assert limits.rpd == 50000


class TestProManager:
    """Test ProManager functionality."""

    def test_generate_api_key(self):
        key, key_hash, prefix = ProManager.generate_api_key()
        assert key.startswith("jiro_")
        assert len(key) > 20
        assert len(key_hash) == 64  # SHA-256
        assert prefix == key[:12]

    def test_generate_unique_keys(self):
        key1, _, _ = ProManager.generate_api_key()
        key2, _, _ = ProManager.generate_api_key()
        assert key1 != key2

    def test_plan_tier_enum(self):
        assert PlanTier.FREE.value == "free"
        assert PlanTier.STARTER.value == "starter"
        assert PlanTier.PRO.value == "pro"
        assert PlanTier.ENTERPRISE.value == "enterprise"


class TestPlanInfo:
    """Test plan information retrieval."""

    def test_all_plans_have_required_fields(self):
        for tier, limits in PLAN_LIMITS.items():
            assert limits.rpm > 0
            assert limits.rpd > 0
            assert limits.max_results > 0
            assert limits.max_concurrent > 0

    def test_enterprise_has_highest_limits(self):
        enterprise = PLAN_LIMITS[PlanTier.ENTERPRISE]
        for tier in PlanTier:
            if tier != PlanTier.ENTERPRISE:
                other = PLAN_LIMITS[tier]
                assert enterprise.rpm >= other.rpm
                assert enterprise.rpd >= other.rpd


class TestQuotaManager:
    """Test quota tracking (mock DB)."""

    def test_quota_manager_initialization(self):
        # Test that QuotaManager can be created with mock DB
        class MockDB:
            async def fetchone(self, *args, **kwargs):
                return {"n": 0}
            async def execute(self, *args, **kwargs):
                pass

        manager = QuotaManager(MockDB())
        assert manager.db is not None
        assert manager._cache == {}