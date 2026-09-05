"""Feature flag system for Jiro commercial licensing.

Provides runtime feature availability checks based on license tier.
Integrates with jiro.licensing for token validation and jiro.pro for plan limits.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from jiro.config import Settings
from jiro.errors import LicenseError
from jiro.licensing import (
    FEATURE_DEFINITIONS,
    LicenseInfo,
    get_active_license,
    get_feature_gate,
    is_feature_enabled,
)


class FeatureFlags:
    """Runtime feature flag resolver with tier awareness.

    Checks license token against tier definitions to determine
    which features are available for the current session.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or Settings.load()
        self._gate = get_feature_gate(settings)
        self._overrides: Set[str] = set()  # for testing

    def is_enabled(self, feature: str) -> bool:
        """Check if a feature is currently enabled."""
        if feature in self._overrides:
            return True
        return self._gate.check(feature)

    def require(self, feature: str) -> None:
        """Raise LicenseError if feature is not available."""
        if feature in self._overrides:
            return
        self._gate.require(feature)

    def get_enabled_features(self) -> List[str]:
        """Get all currently enabled features."""
        license_info = get_active_license()
        if not license_info.valid:
            return [f for f in FEATURE_DEFINITIONS if FEATURE_DEFINITIONS[f]["default"]]
        return [f for f in FEATURE_DEFINITIONS if license_info.has_feature(f)]

    def get_disabled_features(self) -> List[str]:
        """Get all currently disabled features."""
        license_info = get_active_license()
        if not license_info.valid:
            return [f for f in FEATURE_DEFINITIONS if not FEATURE_DEFINITIONS[f]["default"]]
        return [f for f in FEATURE_DEFINITIONS if not license_info.has_feature(f)]

    def get_tier(self) -> str:
        """Get the current license tier."""
        license_info = get_active_license()
        if not license_info.valid:
            return "free"
        return license_info.tier

    def set_override(self, feature: str, enabled: bool = True) -> None:
        """Override a feature flag (testing only)."""
        if enabled:
            self._overrides.add(feature)
        else:
            self._overrides.discard(feature)

    def clear_overrides(self) -> None:
        """Clear all test overrides."""
        self._overrides.clear()


# Tier-to-feature mapping — derived from pro.py PLAN_LIMITS (single source of truth)
from jiro.pro import PLAN_LIMITS, PlanTier, get_features_for_tier as _get_features_for_tier

_TIER_FEATURES: Dict[str, List[str]] = {
    tier.value: _get_features_for_tier(tier)
    for tier in PlanTier
}


def get_features_for_tier(tier: str) -> List[str]:
    """Get all features available for a given tier."""
    return _TIER_FEATURES.get(tier.lower(), _TIER_FEATURES["free"])


def get_tier_for_feature(feature: str) -> Optional[str]:
    """Get the lowest tier that supports a feature."""
    for tier, features in _TIER_FEATURES.items():
        if feature in features:
            return tier
    return None


def validate_tier_access(tier: str, feature: str) -> bool:
    """Check if a tier has access to a feature."""
    return feature in _TIER_FEATURES.get(tier.lower(), [])


# Global instance
_flags = FeatureFlags()


def is_enabled(feature: str) -> bool:
    """Check if a feature is enabled (convenience function)."""
    return _flags.is_enabled(feature)


def require(feature: str) -> None:
    """Raise LicenseError if feature is not available (convenience function)."""
    _flags.require(feature)


def get_current_tier() -> str:
    """Get current license tier (convenience function)."""
    return _flags.get_tier()


def get_enabled() -> List[str]:
    """Get all enabled features (convenience function)."""
    return _flags.get_enabled_features()


def get_disabled() -> List[str]:
    """Get all disabled features (convenience function)."""
    return _flags.get_disabled_features()
