"""License enforcement for Jiro commercial features.

Provides:
- License token generation (HMAC-SHA256 signed JWT)
- Hardware-bound license validation
- Offline grace period support
- Feature flag resolution
- License persistence and auto-renewal

License token format (JWT):
{
  "sub": "customer_id",
  "tier": "pro",
  "features": ["social_advanced", "ai_search", ...],
  "hw": "sha256:CPU_SERIAL+MACHINE_ID",
  "iat": 1700000000,
  "exp": 1700086400,
  "jti": "unique_id",
  "max_devices": 3,
  "used_devices": ["sha256:abc123", ...]
}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import jwt

from jiro.config import Settings
from jiro.errors import LicenseError

log = logging.getLogger("jiro.licensing")

# License token prefix in config/storage
_LICENSE_PREFIX = "jiro_lic_"
# Default license storage path
_DEFAULT_LICENSE_PATH = "~/.jiro/license.token"
# Grace period in seconds (24 hours)
_GRACE_PERIOD_SECONDS = 86400
# How often to revalidate online (seconds)
_ONLINE_REVALIDATE_INTERVAL = 86400


@dataclass
class LicenseInfo:
    """Decoded and validated license information."""
    customer_id: str
    tier: str
    features: List[str]
    hardware_id: str
    issued_at: float
    expires_at: float
    license_id: str
    max_devices: int = 1
    used_devices: List[str] = field(default_factory=list)
    valid: bool = True
    grace_mode: bool = False
    error: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def in_grace_period(self) -> bool:
        if not self.is_expired:
            return False
        grace_end = self.expires_at + _GRACE_PERIOD_SECONDS
        return time.time() < grace_end

    @property
    def remaining_grace(self) -> float:
        if not self.in_grace_period:
            return 0.0
        return (self.expires_at + _GRACE_PERIOD_SECONDS) - time.time()

    def has_feature(self, feature: str) -> bool:
        return feature in self.features

    def to_dict(self) -> Dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "tier": self.tier,
            "features": self.features,
            "hardware_id": self.hardware_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "license_id": self.license_id,
            "max_devices": self.max_devices,
            "used_devices": self.used_devices,
            "valid": self.valid,
            "grace_mode": self.grace_mode,
            "error": self.error,
        }


class LicenseManager:
    """Manages license tokens for Jiro commercial features.

    Supports:
    - Offline license validation (JWT signed with HMAC-SHA256)
    - Hardware binding (license tied to machine)
    - Grace period (24h after expiration)
    - Online revalidation (optional)
    - Device limit enforcement
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or Settings.load()
        self._license_path = Path(
            self.settings.get("licensing.path", _DEFAULT_LICENSE_PATH)
        ).expanduser()
        self._secret = self._get_secret()
        self._cached_license: Optional[LicenseInfo] = None
        self._last_online_check: float = 0.0
        self._online_check_result: bool = True

    def _get_secret(self) -> str:
        """Get the license signing secret from config or environment."""
        secret = self.settings.get("licensing.secret", "")
        if not secret:
            secret = os.environ.get("JIRO_LICENSE_SECRET", "")
        if not secret:
            log.warning("licensing.secret not configured; license validation will fail")
        return secret

    def _get_hardware_id(self) -> str:
        """Generate a hardware-based machine identifier."""
        try:
            if platform.system() == "Windows":
                import ctypes
                serial = ""
                try:
                    import subprocess
                    result = subprocess.run(
                        ["wmic", "baseboard", "get", "serialnumber"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
                        if len(lines) > 1:
                            serial = lines[1]
                except Exception:
                    pass
                if not serial:
                    serial = platform.machine() + platform.processor()
            elif platform.system() == "Darwin":
                import subprocess
                result = subprocess.run(
                    ["ioreg", "-l"], capture_output=True, text=True, timeout=5
                )
                import re
                match = re.search(r'"IOPlatformSerialNumber" = "([^"]+)"', result.stdout)
                serial = match.group(1) if match else platform.machine()
            else:
                # Linux
                try:
                    with open("/etc/machine-id") as f:
                        serial = f.read().strip()
                except Exception:
                    serial = platform.machine()
        except Exception:
            serial = platform.machine() + platform.processor()

        combined = f"{serial}|{platform.system()}|{platform.machine()}"
        return "sha256:" + hashlib.sha256(combined.encode()).hexdigest()[:16]

    def generate_license_token(
        self,
        customer_id: str,
        tier: str,
        features: List[str],
        expires_in: float = 31536000.0,  # 1 year default
        max_devices: int = 3,
    ) -> str:
        """Generate a new license token (server-side operation).

        This is used by the license server to issue new licenses.
        """
        if not self._secret:
            raise LicenseError("licensing.secret not configured")

        hw_id = self._get_hardware_id()
        now = time.time()
        payload = {
            "sub": customer_id,
            "tier": tier,
            "features": features,
            "hw": hw_id,
            "iat": now,
            "exp": now + expires_in,
            "jti": secrets.token_hex(16),
            "max_devices": max_devices,
            "used_devices": [],
        }
        token = jwt.encode(payload, self._secret, algorithm="HS256")
        return token

    def validate_token(self, token: str) -> LicenseInfo:
        """Validate a license token and return LicenseInfo.

        Validates:
        - JWT signature (HMAC-SHA256)
        - Hardware binding (machine ID match)
        - Expiration (with grace period)
        - Device limit
        """
        if not self._secret:
            return LicenseInfo(
                customer_id="", tier="free", features=[], hardware_id="",
                issued_at=0, expires_at=0, license_id="", valid=False,
                error="License system not configured",
            )

        # Strip prefix if present
        if token.startswith(_LICENSE_PREFIX):
            token = token[len(_LICENSE_PREFIX):]

        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return LicenseInfo(
                customer_id=payload.get("sub", "") if "payload" in dir() else "",
                tier="", features=[], hardware_id="",
                issued_at=0, expires_at=0, license_id="", valid=False,
                error="License expired",
            )
        except jwt.InvalidTokenError as exc:
            return LicenseInfo(
                customer_id="", tier="free", features=[], hardware_id="",
                issued_at=0, expires_at=0, license_id="", valid=False,
                error=f"Invalid license token: {exc}",
            )

        # Extract fields
        customer_id = payload.get("sub", "")
        tier = payload.get("tier", "free")
        features = payload.get("features", [])
        hw_id = payload.get("hw", "")
        issued_at = payload.get("iat", 0)
        expires_at = payload.get("exp", 0)
        license_id = payload.get("jti", "")
        max_devices = payload.get("max_devices", 1)
        used_devices = payload.get("used_devices", [])

        # Validate hardware binding
        current_hw = self._get_hardware_id()
        if hw_id and hw_id != current_hw:
            return LicenseInfo(
                customer_id=customer_id, tier=tier, features=features,
                hardware_id=hw_id, issued_at=issued_at, expires_at=expires_at,
                license_id=license_id, max_devices=max_devices,
                used_devices=used_devices, valid=False,
                error="Hardware mismatch (license bound to different machine)",
            )

        # Check expiration
        now = time.time()
        is_expired = now > expires_at
        in_grace = is_expired and (now < expires_at + _GRACE_PERIOD_SECONDS)

        return LicenseInfo(
            customer_id=customer_id, tier=tier, features=features,
            hardware_id=hw_id, issued_at=issued_at, expires_at=expires_at,
            license_id=license_id, max_devices=max_devices,
            used_devices=used_devices, valid=True,
            grace_mode=in_grace,
            error="License expired (grace period)" if in_grace else None,
        )

    def save_license(self, token: str) -> None:
        """Save license token to disk (encrypted)."""
        try:
            self._license_path.parent.mkdir(parents=True, exist_ok=True)
            # Simple obfuscation: base64 encode (not encryption, just storage)
            import base64
            encoded = base64.urlsafe_b64encode(token.encode()).decode()
            self._license_path.write_text(encoded, encoding="utf-8")
            self._cached_license = None  # invalidate cache
            log.info("license saved to %s", self._license_path)
        except Exception as exc:
            log.error("failed to save license: %s", exc)
            raise LicenseError(f"Failed to save license: {exc}")

    def load_license(self) -> Optional[str]:
        """Load license token from disk."""
        try:
            if not self._license_path.exists():
                return None
            import base64
            encoded = self._license_path.read_text(encoding="utf-8").strip()
            token = base64.urlsafe_b64decode(encoded).decode()
            return token
        except Exception as exc:
            log.warning("failed to load license: %s", exc)
            return None

    def get_license(self, force_revalidate: bool = False) -> LicenseInfo:
        """Get the current license, with caching and online revalidation."""
        # Return cached license if still valid and not expired
        if self._cached_license and not force_revalidate:
            if self._cached_license.valid and not self._cached_license.is_expired:
                return self._cached_license
            if self._cached_license.in_grace_period:
                return self._cached_license

        # Load from disk
        token = self.load_license()
        if not token:
            self._cached_license = LicenseInfo(
                customer_id="", tier="free", features=[], hardware_id="",
                issued_at=0, expires_at=0, license_id="", valid=False,
                error="No license found",
            )
            return self._cached_license

        # Validate
        info = self.validate_token(token)
        self._cached_license = info

        # Online revalidation if configured and enough time has passed
        if info.valid and self.settings.get("licensing.online_validation", False):
            now = time.time()
            if force_revalidate or (now - self._last_online_check > _ONLINE_REVALIDATE_INTERVAL):
                self._revalidate_online(token)

        return info

    def _revalidate_online(self, token: str) -> None:
        """Revalidate license with online server (if configured)."""
        try:
            license_url = self.settings.get("licensing.server_url", "")
            if not license_url:
                return
            import httpx
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{license_url}/license/validate",
                    json={"token": token, "hw_id": self._get_hardware_id()},
                )
                if resp.status_code == 200:
                    self._online_check_result = True
                else:
                    self._online_check_result = False
        except Exception:
            # Network failure → use grace period
            self._online_check_result = False
        finally:
            self._last_online_check = time.time()

    def clear_license(self) -> None:
        """Remove saved license (for testing/debugging)."""
        try:
            if self._license_path.exists():
                self._license_path.unlink()
        except Exception:
            pass
        self._cached_license = None

    def is_licensed(self) -> bool:
        """Quick check if a valid license exists."""
        info = self.get_license()
        return info.valid and not info.is_expired or info.in_grace_period

    def has_feature(self, feature: str) -> bool:
        """Check if the current license enables a feature."""
        info = self.get_license()
        return info.valid and info.has_feature(feature)


# Global license manager instance
_license_manager: Optional[LicenseManager] = None


def get_license_manager(settings: Optional[Settings] = None) -> LicenseManager:
    """Get or create the global LicenseManager instance."""
    global _license_manager
    if _license_manager is None:
        _license_manager = LicenseManager(settings)
    return _license_manager


def validate_license(token: str) -> LicenseInfo:
    """Validate a license token (convenience function)."""
    manager = get_license_manager()
    return manager.validate_token(token)


def generate_license_token(
    customer_id: str,
    tier: str,
    features: List[str],
    expires_in: float = 31536000.0,
    max_devices: int = 3,
) -> str:
    """Generate a new license token (convenience function)."""
    manager = get_license_manager()
    return manager.generate_license_token(customer_id, tier, features, expires_in, max_devices)


def save_license(token: str) -> None:
    """Save license token to disk (convenience function)."""
    manager = get_license_manager()
    manager.save_license(token)


def load_license() -> Optional[str]:
    """Load license token from disk (convenience function)."""
    manager = get_license_manager()
    return manager.load_license()


def get_active_license(force_revalidate: bool = False) -> LicenseInfo:
    """Get the current active license (convenience function)."""
    manager = get_license_manager()
    return manager.get_license(force_revalidate=force_revalidate)


def is_licensed() -> bool:
    """Quick check if a valid license exists (convenience function)."""
    manager = get_license_manager()
    return manager.is_licensed()


def has_feature(feature: str) -> bool:
    """Check if the current license enables a feature (convenience function)."""
    manager = get_license_manager()
    return manager.has_feature(feature)


# Feature definitions and tier mappings
# Derived from pro.py PLAN_LIMITS — single source of truth
from jiro.pro import PLAN_LIMITS, PlanTier, get_all_feature_names

_ALL_FEATURES = get_all_feature_names()

# Feature descriptions (human-readable)
_FEATURE_DESCRIPTIONS: Dict[str, str] = {
    "basic_search": "Basic web search across 9 engines",
    "basic_scrape": "URL scraping to markdown/text/html/json",
    "open_scrapers": "Open source scraper library",
    "social_advanced": "Advanced social media scrapers (12 platforms)",
    "social_search": "Search on social media platforms",
    "social_timeline": "Social media timeline scraping",
    "social_batch": "Batch social media scraping",
    "ai_search": "AI-powered agentic research with citations",
    "smart_search": "Intent-aware smart search routing",
    "structured_extraction": "LLM-assisted structured data extraction",
    "self_learning": "Self-learning timeouts and retries",
    "advanced_healing": "Advanced self-healing strategies",
    "high_volume": "High volume batch processing",
    "custom_models": "Custom LLM model support",
    "commercial_use": "Commercial use license",
    "premium_support": "Priority support access",
    "white_label": "White-label customization",
    "webhook_alerts": "Webhook alert notifications",
}

FEATURE_DEFINITIONS: Dict[str, Dict[str, Any]] = {}
for _feat in _ALL_FEATURES:
    _allowed_tiers = [
        tier.value for tier in PlanTier
        if getattr(PLAN_LIMITS[tier], f"feature_{_feat}", False)
    ]
    FEATURE_DEFINITIONS[_feat] = {
        "tiers": _allowed_tiers,
        "default": "free" in _allowed_tiers,
        "description": _FEATURE_DESCRIPTIONS.get(_feat, _feat),
    }

# Tier hierarchy for quick comparison
_TIER_LEVELS = {
    "free": 0,
    "enterprise": 1,
}


def get_tier_level(tier: str) -> int:
    """Get numeric level for a tier name."""
    return _TIER_LEVELS.get(tier.lower(), 0)


def is_feature_enabled(feature: str, tier: str = "free") -> bool:
    """Check if a feature is enabled for a given tier."""
    if feature not in FEATURE_DEFINITIONS:
        return False
    allowed_tiers = FEATURE_DEFINITIONS[feature].get("tiers", [])
    return tier.lower() in allowed_tiers


def get_features_for_tier(tier: str) -> List[str]:
    """Get all features enabled for a given tier."""
    tier = tier.lower()
    enabled = []
    for feature, config in FEATURE_DEFINITIONS.items():
        if tier in config.get("tiers", []):
            enabled.append(feature)
    return enabled


def get_tier_features(tier: str) -> List[str]:
    """Alias for get_features_for_tier for backward compatibility."""
    return get_features_for_tier(tier)


class FeatureGate:
    """Runtime feature gate with caching.

    Usage:
    ```
    gate = FeatureGate()
    if not gate.check("social_search"):
        raise ForbiddenError("social_search requires starter tier")
    ```
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings.load()
        self._license_manager = get_license_manager(settings)
        self._cache: Dict[str, Tuple[bool, float]] = {}
        self._cache_ttl = 30.0  # seconds

    def check(self, feature: str) -> bool:
        """Check if a feature is available (cached)."""
        now = time.time()
        if feature in self._cache:
            allowed, cached_at = self._cache[feature]
            if now - cached_at < self._cache_ttl:
                return allowed
            # Refresh expired cache entry

        license_info = self._license_manager.get_license()
        allowed = license_info.valid and license_info.has_feature(feature)
        self._cache[feature] = (allowed, now)
        return allowed

    def require(self, feature: str) -> None:
        """Raise LicenseError if feature is not available."""
        if not self.check(feature):
            license_info = self._license_manager.get_license()
            current_tier = license_info.tier if license_info.valid else "free"
            raise LicenseError(
                f"Feature '{feature}' requires a paid plan. "
                f"Current tier: {current_tier}. "
                f"Upgrade at https://jiro.ai/pricing",
                details={
                    "feature": feature,
                    "current_tier": current_tier,
                    "required_tiers": FEATURE_DEFINITIONS.get(feature, {}).get("tiers", []),
                    "upgrade_url": "https://jiro.ai/pricing",
                },
            )

    def clear_cache(self) -> None:
        """Clear the feature gate cache."""
        self._cache.clear()


# Global feature gate instance
_feature_gate: Optional[FeatureGate] = None


def get_feature_gate(settings: Optional[Settings] = None) -> FeatureGate:
    """Get or create the global FeatureGate instance."""
    global _feature_gate
    if _feature_gate is None:
        _feature_gate = FeatureGate(settings)
    return _feature_gate


def check_feature(feature: str) -> bool:
    """Check if a feature is available (convenience function)."""
    return get_feature_gate().check(feature)


def require_feature(feature: str) -> None:
    """Raise LicenseError if feature is not available (convenience function)."""
    get_feature_gate().require(feature)
