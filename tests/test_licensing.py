"""Tests for jiro.licensing and jiro.feature_flags."""

from __future__ import annotations

import pytest

from jiro.errors import LicenseError
from jiro.feature_flags import (
    FEATURE_DEFINITIONS,
    FeatureFlags,
    get_current_tier,
    get_disabled,
    get_enabled,
    get_features_for_tier,
    get_tier_for_feature,
    is_enabled,
    require,
    validate_tier_access,
)
from jiro.licensing import FEATURE_DEFINITIONS as LIC_FEATURES
from jiro.pro import PlanTier


class TestFeatureDefinitions:
    def test_feature_definitions_exist(self):
        assert len(FEATURE_DEFINITIONS) > 0

    def test_basic_search_is_free(self):
        feat = FEATURE_DEFINITIONS["basic_search"]
        assert feat["default"] is True
        assert "free" in feat["tiers"]

    def test_ai_search_requires_enterprise(self):
        feat = FEATURE_DEFINITIONS["ai_search"]
        assert feat["default"] is False
        assert "enterprise" in feat["tiers"]
        assert "free" not in feat["tiers"]

    def test_all_features_have_required_fields(self):
        for name, feat in FEATURE_DEFINITIONS.items():
            assert "default" in feat
            assert "tiers" in feat
            assert "description" in feat
            assert isinstance(feat["tiers"], list)
            assert len(feat["tiers"]) > 0


class TestFeatureFlags:
    def test_free_tier_has_many_features(self):
        features = get_features_for_tier("free")
        assert "basic_search" in features
        assert "basic_scrape" in features
        assert "open_scrapers" in features
        assert "social_advanced" in features
        assert "social_search" in features
        assert "social_timeline" in features
        assert "smart_search" in features
        assert "structured_extraction" in features
        assert "ai_search" not in features

    def test_enterprise_tier_has_all_features(self):
        features = get_features_for_tier("enterprise")
        assert "ai_search" in features
        assert "white_label" in features
        assert "self_learning" in features
        assert "advanced_healing" in features
        for name in FEATURE_DEFINITIONS:
            assert name in features

    def test_tier_for_feature(self):
        assert get_tier_for_feature("basic_search") == "free"
        assert get_tier_for_feature("smart_search") == "free"
        assert get_tier_for_feature("ai_search") == "enterprise"
        assert get_tier_for_feature("white_label") == "enterprise"

    def test_validate_tier_access(self):
        assert validate_tier_access("free", "basic_search") is True
        assert validate_tier_access("free", "smart_search") is True
        assert validate_tier_access("free", "ai_search") is False
        assert validate_tier_access("enterprise", "ai_search") is True

    def test_get_enabled_free(self):
        flags = FeatureFlags()
        flags.set_override("basic_search", True)
        flags.set_override("basic_scrape", True)
        flags.set_override("open_scrapers", True)
        enabled = flags.get_enabled_features()
        assert "basic_search" in enabled

    def test_get_disabled_free(self):
        flags = FeatureFlags()
        disabled = flags.get_disabled_features()
        assert "ai_search" in disabled
        assert "white_label" in disabled

    def test_is_enabled_with_override(self):
        flags = FeatureFlags()
        flags.set_override("ai_search", True)
        assert flags.is_enabled("ai_search") is True
        flags.clear_overrides()
        assert flags.is_enabled("ai_search") is False

    def test_require_raises_for_disabled_feature(self):
        flags = FeatureFlags()
        with pytest.raises(LicenseError):
            flags.require("ai_search")

    def test_require_passes_for_enabled_feature(self):
        flags = FeatureFlags()
        flags.set_override("basic_search", True)
        flags.require("basic_search")  # should not raise

    def test_convenience_functions(self):
        from jiro.feature_flags import _flags
        _flags.set_override("test_feature", True)
        assert is_enabled("test_feature") is True
        _flags.require("test_feature")
        _flags.clear_overrides()

    def test_get_current_tier_free(self):
        tier = get_current_tier()
        assert tier == "free"

    def test_get_enabled_free_tier(self):
        enabled = get_enabled()
        assert "basic_search" in enabled
        assert "basic_scrape" in enabled

    def test_get_disabled_free_tier(self):
        disabled = get_disabled()
        assert "ai_search" in disabled


class TestPlanLimits:
    def test_free_plan_limits(self):
        from jiro.pro import PLAN_LIMITS, PlanTier
        limits = PLAN_LIMITS[PlanTier.FREE]
        assert limits.rpm == 100
        assert limits.max_concurrent == 20
        assert limits.feature_ai_search is False
        assert limits.feature_social_batch is True
        assert limits.feature_self_learning is True

    def test_enterprise_plan_limits(self):
        from jiro.pro import PLAN_LIMITS, PlanTier
        limits = PLAN_LIMITS[PlanTier.ENTERPRISE]
        assert limits.rpm == 1000
        assert limits.feature_ai_search is True
        assert limits.feature_white_label is True
        assert limits.feature_self_learning is True

    def test_all_tiers_have_feature_flags(self):
        from jiro.pro import PLAN_LIMITS
        for tier in PlanTier:
            limits = PLAN_LIMITS[tier]
            assert hasattr(limits, "feature_basic_search")
            assert hasattr(limits, "feature_ai_search")
            assert hasattr(limits, "feature_white_label")

    def test_only_two_tiers_exist(self):
        assert list(PlanTier) == [PlanTier.FREE, PlanTier.ENTERPRISE]


class TestPremiumPackage:
    def test_premium_imports(self):
        from jiro_premium import get_premium_implementations, get_premium_scrapers
        impls = get_premium_implementations()
        assert "ai_search" in impls
        assert "social_batch" in impls

    def test_premium_feature_wrapper(self):
        from jiro_premium import PremiumFeature, get_premium_implementations
        impls = get_premium_implementations()
        feat = impls.get("ai_search")
        assert isinstance(feat, PremiumFeature)

    def test_premium_scrapers_list(self):
        from jiro_premium import get_premium_scrapers
        scrapers = get_premium_scrapers()
        assert isinstance(scrapers, dict)
