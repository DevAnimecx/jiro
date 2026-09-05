"""Self-healing layer for social scrapers.

Automatically recovers from common scraping failures:
- Stale query hashes (Instagram, Threads, Facebook)
- Broken CSS/XPath selectors
- Blocked engines / CAPTCHA / rate limits
- Windows file locks
- Missing/invalid credentials
- DNS failures, SSL errors, HTTP 5xx, proxy failures
- Empty or malformed results

Usage: wrap scraper calls with ``heal(scraper, method, *args, **kwargs)``
or ``await heal_async(scraper, method, *args, **kwargs)`` for async scrapers.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("jiro.scraping.social.healing")

# How many times to retry a healed call before giving up.
MAX_HEAL_RETRIES = 2
# Base cooldown after a scraper is marked blocked (seconds).
BLOCK_COOLDOWN = 120.0
# Jitter range added to cooldowns to prevent thundering herd.
BLOCK_JITTER = 15.0


class HealingStats:
    """Thread-safe tracking of heal attempts and outcomes per scraper."""

    def __init__(self) -> None:
        self.attempts: Dict[str, int] = {}
        self.successes: Dict[str, int] = {}
        self.failures: Dict[str, int] = {}
        self.blocked_until: Dict[str, float] = {}
        self.last_error: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def record_attempt(self, platform: str) -> None:
        async with self._lock:
            self.attempts[platform] = self.attempts.get(platform, 0) + 1

    async def record_success(self, platform: str) -> None:
        async with self._lock:
            self.successes[platform] = self.successes.get(platform, 0) + 1
            self.last_error.pop(platform, None)

    async def record_failure(self, platform: str, error: str) -> None:
        async with self._lock:
            self.failures[platform] = self.failures.get(platform, 0) + 1
            self.last_error[platform] = str(error)[:200]

    async def mark_blocked(self, platform: str, cooldown: float = BLOCK_COOLDOWN) -> None:
        jitter = random.uniform(0, BLOCK_JITTER)
        async with self._lock:
            self.blocked_until[platform] = time.time() + cooldown + jitter

    def is_blocked(self, platform: str) -> bool:
        return self.blocked_until.get(platform, 0) > time.time()

    def unblock(self, platform: str) -> None:
        self.blocked_until.pop(platform, None)

    def success_rate(self, platform: str) -> float:
        total = self.successes.get(platform, 0) + self.failures.get(platform, 0)
        if total == 0:
            return 1.0
        return self.successes.get(platform, 0) / total

    # Sync variants for use in synchronous code (no event loop available)
    def record_attempt_sync(self, platform: str) -> None:
        self.attempts[platform] = self.attempts.get(platform, 0) + 1

    def record_success_sync(self, platform: str) -> None:
        self.successes[platform] = self.successes.get(platform, 0) + 1
        self.last_error.pop(platform, None)

    def record_failure_sync(self, platform: str, error: str) -> None:
        self.failures[platform] = self.failures.get(platform, 0) + 1
        self.last_error[platform] = str(error)[:200]

    def mark_blocked_sync(self, platform: str, cooldown: float = BLOCK_COOLDOWN) -> None:
        jitter = random.uniform(0, BLOCK_JITTER)
        self.blocked_until[platform] = time.time() + cooldown + jitter


# Global stats instance
_stats = HealingStats()


class StaleHashError(Exception):
    """Raised when a dynamic token/query hash is rejected by the platform."""


class SelectorError(Exception):
    """Raised when all CSS/XPath selectors fail to extract data."""


class EngineBlockedError(Exception):
    """Raised when an engine returns a bot-wall or CAPTCHA."""


class EmptyResultError(Exception):
    """Raised when scraping returns empty or malformed data after healing."""


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


def _is_dns_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "socket.gaierror", "name resolution", "dns", "getaddrinfo",
        "temporary failure in name resolution",
    ))


def _is_ssl_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "ssl", "certificate", "cert verify failed", "tls",
        "sslcertverificationerror",
    ))


def _is_http_5xx(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "500", "502", "503", "504", "service unavailable",
        "internal server error", "bad gateway", "gateway timeout",
    ))


def _is_proxy_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "proxy", "tunnel connection failed", "could not resolve host",
    ))


def _heal_stale_hash(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Force re-extraction of dynamic hashes and retry."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing stale hash for %s.%s", platform, getattr(method, "__name__", method))

    for attr in ("_query_hashes", "_cached_hashes", "_fb_lsd", "_dynamic_tokens",
                 "_query_hash", "_x_instagram_ajax", "_x_csrftoken"):
        if hasattr(scraper, attr):
            try:
                setattr(scraper, attr, {})
            except Exception:
                pass

    for attr in ("_cached_response", "_last_response", "_cache", "_page_cache"):
        if hasattr(scraper, attr):
            try:
                setattr(scraper, attr, None)
            except Exception:
                pass

    if hasattr(scraper, "_extract_query_hashes"):
        try:
            scraper._extract_query_hashes()  # type: ignore[attr-defined]
        except Exception:
            pass

    return method(*args, **kwargs)


def _heal_selector(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Try alternative selectors and retry."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing selector for %s.%s", platform, getattr(method, "__name__", method))

    for attr in ("_alt_selectors", "_fallback_selectors", "_selector_variants",
                 "_css_selectors", "_xpath_selectors"):
        if hasattr(scraper, attr):
            try:
                current = getattr(scraper, attr, [])
                if isinstance(current, list) and len(current) > 1:
                    rotated = current[1:] + current[:1]
                    setattr(scraper, attr, rotated)
            except Exception:
                pass

    if hasattr(scraper, "use_browser_fallback"):
        try:
            kwargs["use_browser"] = True
        except Exception:
            pass

    return method(*args, **kwargs)


async def _heal_blocked(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Wait out cooldown with jitter and retry with rotated fingerprint."""
    platform = getattr(scraper, "platform", "unknown")
    cooldown = BLOCK_COOLDOWN

    if hasattr(scraper, "client") and hasattr(scraper.client, "breaker"):
        try:
            cb_cooldown = getattr(scraper.client.breaker, "cooldown", BLOCK_COOLDOWN)
            cooldown = max(cooldown, cb_cooldown)
        except Exception:
            pass

    log.info("healing blocked engine %s (cooldown %.1fs)", platform, cooldown)
    await _stats.mark_blocked(platform, cooldown)

    # Cap wait at 5s for CLI responsiveness; use asyncio.sleep so cancellation works
    wait = min(cooldown, 5.0)
    try:
        await asyncio.sleep(wait)
    except asyncio.CancelledError:
        log.info("heal blocked cancelled for %s", platform)
        raise

    if hasattr(scraper, "client"):
        try:
            fp = scraper.client.fingerprint
            fp.next_profile()
            fp.next_geo_headers()
        except Exception:
            pass

    return method(*args, **kwargs)


async def _heal_file_lock(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Wait for file lock to release and retry with retries."""
    import os
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing file lock for %s.%s", platform, getattr(method, "__name__", method))

    for attempt in range(3):
        try:
            await asyncio.sleep(2.0 * (attempt + 1))
            if os.name == "nt":
                for attr in ("_temp_path", "_cache_path", "_lock_path", "_db_path"):
                    if hasattr(scraper, attr):
                        try:
                            path = getattr(scraper, attr)
                            if path and os.path.exists(path):
                                os.remove(path)
                        except PermissionError:
                            if attempt < 2:
                                continue
                            raise
                        except OSError:
                            pass
            return method(*args, **kwargs)
        except PermissionError:
            if attempt >= 2:
                raise
            continue
        except asyncio.CancelledError:
            raise

    return method(*args, **kwargs)


async def _heal_credentials(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Re-authenticate / refresh tokens and retry."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing credentials for %s.%s", platform, getattr(method, "__name__", method))

    for attr in ("_auth_token", "_cookies", "_session", "_csrf_token",
                 "_sessionid", "_access_token"):
        if hasattr(scraper, attr):
            try:
                setattr(scraper, attr, None)
            except Exception:
                pass

    if hasattr(scraper, "login") or hasattr(scraper, "authenticate"):
        try:
            login_fn = getattr(scraper, "login", None) or getattr(scraper, "authenticate")
            if callable(login_fn):
                result = login_fn()
                if asyncio.iscoroutine(result):
                    await result
        except Exception:
            pass

    return method(*args, **kwargs)


async def _heal_dns(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Retry with delay after DNS failure."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing DNS for %s.%s", platform, getattr(method, "__name__", method))
    try:
        await asyncio.sleep(2.0)
    except asyncio.CancelledError:
        raise
    return method(*args, **kwargs)


async def _heal_ssl(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Retry with relaxed SSL verification (if supported)."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing SSL for %s.%s", platform, getattr(method, "__name__", method))

    if hasattr(scraper, "client") and hasattr(scraper.client, "_request_curl"):
        try:
            kwargs["verify"] = False
        except Exception:
            pass

    try:
        await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        raise
    return method(*args, **kwargs)


async def _heal_http_5xx(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Exponential backoff with jitter for server errors."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing HTTP 5xx for %s.%s", platform, getattr(method, "__name__", method))

    backoff = min(2.0 * (1 + random.uniform(0, 1)), 10.0)
    try:
        await asyncio.sleep(backoff)
    except asyncio.CancelledError:
        raise
    return method(*args, **kwargs)


async def _heal_proxy(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Rotate proxy and retry."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing proxy for %s.%s", platform, getattr(method, "__name__", method))

    if hasattr(scraper, "client") and hasattr(scraper.client, "proxies"):
        try:
            scraper.client.proxies._index += 1
        except Exception:
            pass

    try:
        await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        raise
    return method(*args, **kwargs)


async def _heal_empty_result(scraper: Any, method: Callable, args: tuple, kwargs: dict) -> Any:
    """Validate result is non-empty; re-scrape if empty."""
    platform = getattr(scraper, "platform", "unknown")
    log.info("healing empty result for %s.%s", platform, getattr(method, "__name__", method))

    try:
        await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        raise
    return method(*args, **kwargs)


def _validate_result(result: Any) -> bool:
    """Check if a scrape result is meaningful (non-empty, well-formed)."""
    if result is None:
        return False
    if isinstance(result, list) and len(result) == 0:
        return False
    if isinstance(result, dict) and not result:
        return False
    if isinstance(result, str) and not result.strip():
        return False
    return True


async def heal_async(scraper: Any, method: Callable, *args: Any, **kwargs: Any) -> Any:
    """Call ``method`` with automatic healing on failure (async version).

    Retries up to ``MAX_HEAL_RETRIES`` times, applying increasingly
    aggressive recovery strategies based on the error type. Supports
    cancellation via ``asyncio.CancelledError``.
    """
    platform = getattr(scraper, "platform", "unknown")

    if _stats.is_blocked(platform):
        raise EngineBlockedError(
            f"{platform} is temporarily blocked (cooldown active)"
        )

    last_exc = None
    for attempt in range(1 + MAX_HEAL_RETRIES):
        await _stats.record_attempt(platform)
        try:
            result = method(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            if not _validate_result(result):
                raise EmptyResultError(f"empty result from {platform}.{getattr(method, '__name__', method)}")
            await _stats.record_success(platform)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc
            await _stats.record_failure(platform, str(exc))

            try:
                if _is_stale_hash_error(exc):
                    result = _heal_stale_hash(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if _validate_result(result):
                        await _stats.record_success(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after stale-hash healing for {platform}")

                elif _is_selector_error(exc):
                    result = _heal_selector(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if _validate_result(result):
                        await _stats.record_success(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after selector healing for {platform}")

                elif _is_blocked_error(exc):
                    result = await _heal_blocked(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if _validate_result(result):
                        await _stats.record_success(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after blocked healing for {platform}")

                elif _is_file_lock_error(exc):
                    result = await _heal_file_lock(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if _validate_result(result):
                        await _stats.record_success(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after file-lock healing for {platform}")

                elif _is_dns_error(exc):
                    result = await _heal_dns(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if _validate_result(result):
                        await _stats.record_success(platform)
                        return result
                    last_exc = exc

                elif _is_ssl_error(exc):
                    result = await _heal_ssl(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if _validate_result(result):
                        await _stats.record_success(platform)
                        return result
                    last_exc = exc

                elif _is_http_5xx(exc):
                    result = await _heal_http_5xx(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if _validate_result(result):
                        await _stats.record_success(platform)
                        return result
                    last_exc = exc

                elif _is_proxy_error(exc):
                    result = await _heal_proxy(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if _validate_result(result):
                        await _stats.record_success(platform)
                        return result
                    last_exc = exc

                elif "auth" in str(exc).lower() or "token" in str(exc).lower():
                    result = await _heal_credentials(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if _validate_result(result):
                        await _stats.record_success(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after credential healing for {platform}")

            except asyncio.CancelledError:
                raise
            except Exception as heal_exc:
                last_exc = heal_exc
                log.warning("heal attempt failed for %s: %s", platform, heal_exc)

    raise last_exc or EngineBlockedError(f"{platform}: max heal retries exceeded")


def heal(scraper: Any, method: Callable, *args: Any, **kwargs: Any) -> Any:
    """Call ``method`` with automatic healing on failure (sync version).

    For async scrapers, prefer ``heal_async()``.
    """
    platform = getattr(scraper, "platform", "unknown")

    if _stats.is_blocked(platform):
        raise EngineBlockedError(
            f"{platform} is temporarily blocked (cooldown active)"
        )

    last_exc = None
    for attempt in range(1 + MAX_HEAL_RETRIES):
        _stats.record_attempt_sync(platform)
        try:
            result = method(*args, **kwargs)
            if asyncio.iscoroutine(result):
                raise RuntimeError(
                    "heal() cannot await coroutines; use heal_async() in async code"
                )
            if not _validate_result(result):
                raise EmptyResultError(f"empty result from {platform}.{getattr(method, '__name__', method)}")
            _stats.record_success_sync(platform)
            return result
        except Exception as exc:
            last_exc = exc
            _stats.record_failure_sync(platform, str(exc))

            try:
                if _is_stale_hash_error(exc):
                    result = _heal_stale_hash(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        raise RuntimeError("heal() cannot await coroutines; use heal_async()")
                    if _validate_result(result):
                        _stats.record_success_sync(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after stale-hash healing for {platform}")
                elif _is_selector_error(exc):
                    result = _heal_selector(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        raise RuntimeError("heal() cannot await coroutines; use heal_async()")
                    if _validate_result(result):
                        _stats.record_success_sync(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after selector healing for {platform}")
                elif _is_blocked_error(exc):
                    result = _heal_blocked(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        raise RuntimeError("heal() cannot await coroutines; use heal_async()")
                    if _validate_result(result):
                        _stats.record_success_sync(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after blocked healing for {platform}")
                elif _is_file_lock_error(exc):
                    result = _heal_file_lock(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        raise RuntimeError("heal() cannot await coroutines; use heal_async()")
                    if _validate_result(result):
                        _stats.record_success_sync(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after file-lock healing for {platform}")
                elif _is_dns_error(exc):
                    result = _heal_dns(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        raise RuntimeError("heal() cannot await coroutines; use heal_async()")
                    if _validate_result(result):
                        _stats.record_success_sync(platform)
                        return result
                    last_exc = exc
                elif _is_ssl_error(exc):
                    result = _heal_ssl(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        raise RuntimeError("heal() cannot await coroutines; use heal_async()")
                    if _validate_result(result):
                        _stats.record_success_sync(platform)
                        return result
                    last_exc = exc
                elif _is_http_5xx(exc):
                    result = _heal_http_5xx(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        raise RuntimeError("heal() cannot await coroutines; use heal_async()")
                    if _validate_result(result):
                        _stats.record_success_sync(platform)
                        return result
                    last_exc = exc
                elif _is_proxy_error(exc):
                    result = _heal_proxy(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        raise RuntimeError("heal() cannot await coroutines; use heal_async()")
                    if _validate_result(result):
                        _stats.record_success_sync(platform)
                        return result
                    last_exc = exc
                elif "auth" in str(exc).lower() or "token" in str(exc).lower():
                    result = _heal_credentials(scraper, method, args, kwargs)
                    if asyncio.iscoroutine(result):
                        raise RuntimeError("heal() cannot await coroutines; use heal_async()")
                    if _validate_result(result):
                        _stats.record_success_sync(platform)
                        return result
                    last_exc = EmptyResultError(f"empty result after credential healing for {platform}")
            except RuntimeError:
                raise
            except Exception as heal_exc:
                last_exc = heal_exc
                log.warning("heal attempt failed for %s: %s", platform, heal_exc)

    raise last_exc or EngineBlockedError(f"{platform}: max heal retries exceeded")


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
