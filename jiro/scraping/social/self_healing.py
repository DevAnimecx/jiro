"""Self-healing layer for social scrapers.

Automatically recovers from common scraping failures:
- Stale query hashes (Instagram, Threads, Facebook)
- Broken CSS/XPath selectors
- Blocked engines (circuit breaker integration)
- Windows file locks
- Missing/invalid credentials

Usage: wrap scraper calls with ``heal(scraper, method, *args, **kwargs)``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("jiro.scraping.social.healing")

# How many times to retry a healed call before giving up.
MAX_HEAL_RETRIES = 2
# Cooldown after a scraper is marked blocked (seconds).
BLOCK_COOLDOWN = 120.0


class HealingStats:
    """Track heal attempts and outcomes per scraper."""

    def __init__(self) -> None:
        self.attempts: Dict[str, int] = {}
        self.successes: Dict[str, int] = {}
        self.failures: Dict[str, int] = {}
        self.blocked_until: Dict[str, float] = {}
        self.last_error: Dict[str, str] = {}

    def record_attempt(self, platform: str) -> None:
        self.attempts[platform] = self.attempts.get(platform, 0) + 1

    def record_success(self, platform: str) -> None:
        self.successes[platform] = self.successes.get(platform, 0) + 1
        self.last_error.pop(platform, None)

    def record_failure(self, platform: str, error: str) -> None:
        self.failures[platform] = self.failures.get(platform, 0) + 1
        self.last_error[platform] = str(error)[:200]

    def mark_blocked(self, platform: str, cooldown: float = BLOCK_COOLDOWN) -> None:
        self.blocked_until[platform] = time.time() + cooldown

    def is_blocked(self, platform: str) -> bool:
        return self.blocked_until.get(platform, 0) > time.time()

    def unblock(self, platform: str) -> None:
        self.blocked_until.pop(platform, None)

    def success_rate(self, platform: str) -> float:
        total = self.successes.get(platform, 0) + self.failures.get(platform, 0)
        if total == 0:
            return 1.0
        return self.successes.get(platform, 0) / total


# Global stats instance
_stats = HealingStats()


class StaleHashError(Exception):
    """Raised when a dynamic token/query hash is rejected by the platform."""


class SelectorError(Exception):
    """Raised when all CSS/XPath selectors fail to extract data."""


class EngineBlockedError(Exception):
    """Raised when an engine returns a bot-wall or CAPTCHA."""


def _is_stale_hash_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "query_hash", "hash", "invalid parameter", "bad request",
        "parameter error", "missing parameter", "graphql",
    ))


def _is_selector_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "selector", "xpath", "css", "not found", "element",
        "parse", "extract", "no data",
    ))


def _is_blocked_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "captcha", "blocked", "403", "429", "rate limit",
        "bot", "verify", "challenge", "anomaly",
    ))


def _is_file_lock_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "winerror 32", "winerror 10022", "permission denied",
        "file in use", "being used by another process",
        "resource temporarily unavailable",
    ))


def _heal_stale_hash(scraper: Any, method: str, args: tuple, kwargs: dict) -> Any:
    """Force re-extraction of dynamic hashes and retry."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing stale hash for %s.%s", platform, method)

    # Clear cached hashes if the scraper supports it
    for attr in ("_query_hashes", "_cached_hashes", "_fb_lsd", "_dynamic_tokens"):
        if hasattr(scraper, attr):
            try:
                setattr(scraper, attr, {})
            except Exception:
                pass

    # Force re-fetch by clearing any cached response
    for attr in ("_cached_response", "_last_response", "_cache"):
        if hasattr(scraper, attr):
            try:
                setattr(scraper, attr, None)
            except Exception:
                pass

    # Re-initialize the scraper's token extractors
    if hasattr(scraper, "_extract_query_hashes"):
        try:
            scraper._extract_query_hashes()  # type: ignore[attr-defined]
        except Exception:
            pass

    return method(*args, **kwargs)


def _heal_selector(scraper: Any, method: str, args: tuple, kwargs: dict) -> Any:
    """Try alternative selectors and retry."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing selector for %s.%s", platform, method)

    # Cycle through alternative selectors if available
    for attr in ("_alt_selectors", "_fallback_selectors", "_selector_variants"):
        if hasattr(scraper, attr):
            try:
                current = getattr(scraper, attr, [])
                if isinstance(current, list) and len(current) > 1:
                    # Rotate to next selector variant
                    rotated = current[1:] + current[:1]
                    setattr(scraper, attr, rotated)
            except Exception:
                pass

    # Try with browser fallback if available
    if hasattr(scraper, "use_browser_fallback"):
        try:
            kwargs["use_browser"] = True
        except Exception:
            pass

    return method(*args, **kwargs)


def _heal_blocked(scraper: Any, method: str, args: tuple, kwargs: dict) -> Any:
    """Wait out cooldown and retry with rotated fingerprint."""
    platform = getattr(scraper, "platform", "unknown")
    cooldown = BLOCK_COOLDOWN
    if hasattr(scraper, "breaker") and hasattr(scraper.breaker, "cooldown"):
        cooldown = scraper.breaker.cooldown

    log.info("healing blocked engine %s (cooldown %.1fs)", platform, cooldown)
    _stats.mark_blocked(platform, cooldown)
    time.sleep(min(cooldown, 5.0))  # Cap wait at 5s for CLI responsiveness

    # Rotate browser fingerprint if available
    if hasattr(scraper, "client") and hasattr(scraper.client, "fingerprint"):
        try:
            scraper.client.fingerprint.next_profile()
            scraper.client.fingerprint.next_geo_headers()
        except Exception:
            pass

    return method(*args, **kwargs)


def _heal_file_lock(scraper: Any, method: str, args: tuple, kwargs: dict) -> Any:
    """Wait for file lock to release and retry."""
    import os
    import time as _time
    log.info("healing file lock for %s.%s", getattr(scraper, "platform", "unknown"), method)
    _time.sleep(2.0)  # Give the locking process time to finish

    # On Windows, try to clear temp file locks
    if os.name == "nt":
        for attr in ("_temp_path", "_cache_path", "_lock_path"):
            if hasattr(scraper, attr):
                try:
                    path = getattr(scraper, attr)
                    if path and os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass

    return method(*args, **kwargs)


def _heal_credentials(scraper: Any, method: str, args: tuple, kwargs: dict) -> Any:
    """Re-authenticate / refresh tokens and retry."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing credentials for %s.%s", platform, method)

    # Clear auth state
    for attr in ("_auth_token", "_cookies", "_session", "_csrf_token"):
        if hasattr(scraper, attr):
            try:
                setattr(scraper, attr, None)
            except Exception:
                pass

    # Re-login if the scraper supports it
    if hasattr(scraper, "login") or hasattr(scraper, "authenticate"):
        try:
            login_fn = getattr(scraper, "login", None) or getattr(scraper, "authenticate")
            if callable(login_fn):
                result = login_fn()
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
        except Exception:
            pass

    return method(*args, **kwargs)


def heal(scraper: Any, method: Callable, *args: Any, **kwargs: Any) -> Any:
    """Call ``method`` with automatic healing on failure.

    Retries up to ``MAX_HEAL_RETRIES`` times, applying increasingly
    aggressive recovery strategies based on the error type.
    """
    platform = getattr(scraper, "platform", "unknown")

    if _stats.is_blocked(platform):
        raise EngineBlockedError(
            f"{platform} is temporarily blocked (cooldown active)"
        )

    last_exc = None
    for attempt in range(1 + MAX_HEAL_RETRIES):
        _stats.record_attempt(platform)
        try:
            result = method(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
            _stats.record_success(platform)
            return result
        except Exception as exc:
            last_exc = exc
            _stats.record_failure(platform, str(exc))

            if _is_stale_hash_error(exc):
                try:
                    return _heal_stale_hash(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc

            elif _is_selector_error(exc):
                try:
                    return _heal_selector(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc

            elif _is_blocked_error(exc):
                try:
                    return _heal_blocked(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc

            elif _is_file_lock_error(exc):
                try:
                    return _heal_file_lock(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc

            elif "auth" in str(exc).lower() or "token" in str(exc).lower():
                try:
                    return _heal_credentials(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc

    raise last_exc or RuntimeError(f"{platform}: max heal retries exceeded")


async def heal_async(scraper: Any, method: Callable, *args: Any, **kwargs: Any) -> Any:
    """Async version of ``heal()`` for use in async scrapers."""
    platform = getattr(scraper, "platform", "unknown")

    if _stats.is_blocked(platform):
        raise EngineBlockedError(
            f"{platform} is temporarily blocked (cooldown active)"
        )

    last_exc = None
    for attempt in range(1 + MAX_HEAL_RETRIES):
        _stats.record_attempt(platform)
        try:
            result = method(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            _stats.record_success(platform)
            return result
        except Exception as exc:
            last_exc = exc
            _stats.record_failure(platform, str(exc))

            if _is_stale_hash_error(exc):
                try:
                    return _heal_stale_hash(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc
            elif _is_selector_error(exc):
                try:
                    return _heal_selector(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc
            elif _is_blocked_error(exc):
                try:
                    return _heal_blocked(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc
            elif _is_file_lock_error(exc):
                try:
                    return _heal_file_lock(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc
            elif "auth" in str(exc).lower() or "token" in str(exc).lower():
                try:
                    return _heal_credentials(scraper, method, args, kwargs)
                except Exception as heal_exc:
                    last_exc = heal_exc

    raise last_exc or RuntimeError(f"{platform}: max heal retries exceeded")


def get_stats() -> Dict[str, Any]:
    """Return healing statistics for monitoring."""
    return {
        "attempts": dict(_stats.attempts),
        "successes": dict(_stats.successes),
        "failures": dict(_stats.failures),
        "blocked": {p: _stats.blocked_until[p] for p in _stats.blocked_until},
        "last_errors": dict(_stats.last_error),
        "success_rates": {
            p: _stats.success_rate(p) for p in set(
                list(_stats.successes.keys()) + list(_stats.failures.keys())
            )
        },
    }


def reset_stats() -> None:
    """Reset all healing statistics."""
    global _stats
    _stats = HealingStats()


def unblock_all() -> None:
    """Unblock all platforms (admin operation)."""
    _stats.blocked_until.clear()
