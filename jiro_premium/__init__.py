"""Jiro Premium - encrypted payload package for commercial features.

This package contains premium implementations that are encrypted at rest
and only decrypted in memory when a valid license is present.
"""

from __future__ import annotations

from typing import Any, Dict

from jiro.feature_flags import require as require_feature, is_enabled
from jiro.licensing import LicenseError

__all__ = [
    "PremiumFeature",
    "get_premium_implementations",
    "get_premium_scrapers",
]


class PremiumFeature:
    """Wrapper for premium features that validates license before use."""

    def __init__(self, feature: str, implementation: Any) -> None:
        self.feature = feature
        self._impl = implementation
        self._available = False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not is_enabled(self.feature):
            raise LicenseError(
                f"Premium feature '{self.feature}' requires a paid license. "
                f"Upgrade at https://jiro.ai/pricing",
                details={
                    "feature": self.feature,
                    "upgrade_url": "https://jiro.ai/pricing",
                },
            )
        return self._impl(*args, **kwargs)

    def is_available(self) -> bool:
        """Check if feature is available without triggering error."""
        return is_enabled(self.feature)


def get_premium_implementations() -> Dict[str, Any]:
    """Get all premium implementations with license validation.

    Returns a dict mapping feature names to PremiumFeature wrappers.
    """
    implementations: Dict[str, Any] = {}

    try:
        from jiro_premium.impl import (
            AdvancedSocialScrapers,
            AIEnhancedSearch,
            BatchProcessor,
            WhiteLabelConfig,
        )
        implementations["social_advanced"] = PremiumFeature("social_advanced", AdvancedSocialScrapers)
        implementations["ai_search"] = PremiumFeature("ai_search", AIEnhancedSearch)
        implementations["social_batch"] = PremiumFeature("social_batch", BatchProcessor)
        implementations["white_label"] = PremiumFeature("white_label", WhiteLabelConfig)
    except ImportError as exc:
        log = __import__("logging").getLogger("jiro.premium")
        log.warning("premium implementations not available: %s", exc)

    return implementations


def get_premium_scrapers() -> Dict[str, Any]:
    """Get premium social scrapers with license validation."""
    implementations = get_premium_implementations()
    scrapers: Dict[str, Any] = {}
    for name, impl in implementations.items():
        if name.startswith("social"):
            scrapers[name] = impl
    return scrapers


def load_premium() -> None:
    """Load and initialize premium package."""
    import logging
    log = logging.getLogger("jiro.premium")
    try:
        implementations = get_premium_implementations()
        log.info("loaded %d premium features", len(implementations))
    except Exception as exc:
        log.warning("premium load failed: %s", exc)
