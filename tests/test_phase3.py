"""Phase 3: Anti-Bot Hardening — tests for browser fingerprint rotation,
SQLite cookie persistence, proxy cost tracking, and enhanced bot detection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiro.browser import (
    BrowserFetcher,
    LOCALES,
    TIMEZONES,
    VIEWPORTS,
    _build_stealth_script,
    playwright_available,
)
from jiro.config import Settings
from jiro.proxy import ProxyManager
from jiro.scraping.client import (
    BLOCK_MARKERS,
    EngineCookieJar,
    _ADAPTIVE_DELAYS,
    CircuitBreaker,
)


# ── Browser fingerprint rotation ──────────────────────────────────────────

class TestBrowserFingerprint:
    def test_viewports_are_valid(self):
        for w, h in VIEWPORTS:
            assert w >= 1280
            assert h >= 720
            assert isinstance(w, int)
            assert isinstance(h, int)

    def test_timezones_are_strings(self):
        for tz in TIMEZONES:
            assert isinstance(tz, str)
            assert "/" in tz  # e.g. "America/New_York"

    def test_locales_are_valid(self):
        for loc in LOCALES:
            assert isinstance(loc, str)
            assert len(loc) >= 2

    def test_stealth_script_contains_webdriver(self):
        script = _build_stealth_script((1920, 1080), "America/New_York", "en-US")
        assert "navigator.webdriver" in script
        assert "undefined" in script

    def test_stealth_script_contains_screen(self):
        script = _build_stealth_script((1920, 1080), "America/New_York", "en-US")
        assert "screen" in script
        assert "1920" in script
        assert "1080" in script

    def test_stealth_script_contains_chrome_runtime(self):
        script = _build_stealth_script((1366, 768), "Europe/London", "en-GB")
        assert "window.chrome" in script
        assert "runtime" in script

    def test_stealth_script_contains_plugins(self):
        script = _build_stealth_script((1920, 1080), "America/New_York", "en-US")
        assert "plugins" in script

    def test_stealth_script_contains_permissions(self):
        script = _build_stealth_script((1920, 1080), "America/New_York", "en-US")
        assert "permissions" in script
        assert "notifications" in script

    def test_stealth_script_varies_viewport(self):
        script1 = _build_stealth_script((1920, 1080), "America/New_York", "en-US")
        script2 = _build_stealth_script((1366, 768), "Europe/London", "en-GB")
        assert "1920" in script1
        assert "1366" in script2


class TestBrowserFetcher:
    def test_init_defaults(self):
        fetcher = BrowserFetcher()
        assert fetcher.headless is True
        assert fetcher.timeout == 30.0
        assert fetcher._browser is None

    def test_next_fingerprint_returns_dict(self):
        fetcher = BrowserFetcher()
        fp = fetcher._next_fingerprint()
        assert "viewport" in fp
        assert "timezone" in fp
        assert "locale" in fp
        assert "width" in fp["viewport"]
        assert "height" in fp["viewport"]

    def test_fingerprint_rotates_viewports(self):
        fetcher = BrowserFetcher()
        fps = [fetcher._next_fingerprint() for _ in range(len(VIEWPORTS))]
        # All should be different (or at least cycle through them)
        unique_vps = set((fp["viewport"]["width"], fp["viewport"]["height"]) for fp in fps)
        assert len(unique_vps) > 1  # at least 2 different viewports used

    def test_playwright_available_returns_bool(self):
        result = playwright_available()
        assert isinstance(result, bool)


# ── SQLite cookie persistence ────────────────────────────────────────────

class TestEngineCookieJar:
    def test_init_default(self):
        jar = EngineCookieJar()
        assert jar._cookies == {}
        assert jar._db is None

    def test_init_with_db(self):
        db = MagicMock()
        jar = EngineCookieJar(db)
        assert jar._db is db

    def test_update_and_get(self):
        jar = EngineCookieJar()
        jar.update("google", ["foo=bar; Path=/", "baz=qux; Path=/"])
        cookies = jar.get_dict("google")
        assert cookies["foo"] == "bar"
        assert cookies["baz"] == "qux"

    def test_get_unknown_engine(self):
        jar = EngineCookieJar()
        assert jar.get_dict("unknown") == {}

    def test_clear_engine(self):
        jar = EngineCookieJar()
        jar.update("google", ["foo=bar"])
        jar.clear("google")
        assert jar.get_dict("google") == {}

    def test_clear_all(self):
        jar = EngineCookieJar()
        jar.update("google", ["foo=bar"])
        jar.update("bing", ["baz=qux"])
        jar.clear()
        assert jar.get_dict("google") == {}
        assert jar.get_dict("bing") == {}

    @pytest.mark.asyncio
    async def test_load_from_db(self):
        db = AsyncMock()
        db.cookie_load_all.return_value = {"google": {"foo": "bar"}}
        jar = EngineCookieJar(db)
        await jar.load()
        assert jar.get_dict("google") == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_save_to_db(self):
        db = AsyncMock()
        jar = EngineCookieJar(db)
        jar.update("google", ["foo=bar"])
        await jar.save("google")
        db.cookie_save.assert_called_once_with("google", {"foo": "bar"})

    @pytest.mark.asyncio
    async def test_load_db_error_ignored(self):
        db = AsyncMock()
        db.cookie_load_all.side_effect = Exception("db error")
        jar = EngineCookieJar(db)
        await jar.load()  # Should not raise
        assert jar.get_dict("google") == {}


# ── Proxy cost tracking ──────────────────────────────────────────────────

class TestProxyCostTracking:
    def test_record_cost(self):
        settings = Settings()
        pm = ProxyManager(settings)
        pm.record_cost("http://proxy1:8080", 0.001, 150.0)
        info = pm.cost_info("http://proxy1:8080")
        assert info["total_cost"] == 0.001
        assert info["total_requests"] == 1
        assert info["avg_latency_ms"] == 150.0

    def test_record_multiple_costs(self):
        settings = Settings()
        pm = ProxyManager(settings)
        pm.record_cost("http://proxy1:8080", 0.001, 100.0)
        pm.record_cost("http://proxy1:8080", 0.002, 200.0)
        info = pm.cost_info("http://proxy1:8080")
        assert info["total_cost"] == 0.003
        assert info["total_requests"] == 2
        assert info["avg_latency_ms"] == 150.0

    def test_cost_info_all_proxies(self):
        settings = Settings()
        pm = ProxyManager(settings)
        pm.record_cost("http://proxy1:8080", 0.001, 100.0)
        pm.record_cost("http://proxy2:8080", 0.002, 200.0)
        info = pm.cost_info()
        assert info["total_cost"] == 0.003
        assert info["total_requests"] == 2

    def test_cost_info_unknown_proxy(self):
        settings = Settings()
        pm = ProxyManager(settings)
        info = pm.cost_info("http://unknown:8080")
        assert info["total_cost"] == 0
        assert info["total_requests"] == 0

    def test_info_includes_cost(self):
        settings = Settings()
        pm = ProxyManager(settings)
        info = pm.info()
        assert "cost" in info


# ── Enhanced bot detection ───────────────────────────────────────────────

class TestEnhancedBotDetection:
    def test_block_markers_for_all_engines(self):
        engines = ["google", "bing", "duckduckgo", "brave", "youtube",
                   "amazon", "ebay", "yandex", "baidu"]
        for eng in engines:
            assert eng in BLOCK_MARKERS, f"Missing block markers for {eng}"
            assert len(BLOCK_MARKERS[eng]) >= 3

    def test_adaptive_delays_for_all_engines(self):
        engines = ["google", "bing", "duckduckgo", "brave", "youtube",
                   "amazon", "ebay", "yandex", "baidu"]
        for eng in engines:
            assert eng in _ADAPTIVE_DELAYS, f"Missing adaptive delays for {eng}"
            min_d, max_d = _ADAPTIVE_DELAYS[eng]
            assert min_d > 0
            assert max_d > min_d

    def test_google_markers_include_consent(self):
        assert "consent.google" in BLOCK_MARKERS["google"]

    def test_bing_markers_include_verify(self):
        assert "verify you are human" in BLOCK_MARKERS["bing"]

    def test_amazon_markers_include_captcha(self):
        assert "captcha" in BLOCK_MARKERS["amazon"]

    def test_youtube_markers_include_sign_in(self):
        assert "sign in" in BLOCK_MARKERS["youtube"]


# ── Circuit breaker ──────────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_threshold_and_cooldown(self):
        cb = CircuitBreaker(threshold=3, cooldown=60.0)
        assert cb.threshold == 3
        assert cb.cooldown == 60.0

    def test_record_success_resets_failures(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure("google")
        cb.record_failure("google")
        cb.record_success("google")
        assert cb._failures.get("google", 0) == 0

    def test_record_failure_increments(self):
        cb = CircuitBreaker(threshold=5)
        cb.record_failure("google")
        cb.record_failure("google")
        assert cb._failures["google"] == 2
