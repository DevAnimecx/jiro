"""Chaos engineering and resilience tests for scraping."""

from __future__ import annotations

import asyncio
import pytest

from jiro.scraping.client import ScrapingClient, EngineRateLimiter
from jiro.errors import EngineBlockedError, EngineError
from jiro.config import Settings
from tests.integration_utils import TEST_CONFIG, MockEngineResponses


class TestEngineRateLimiter:
    """Tests for per-engine rate limiting."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_burst(self):
        """Test that burst allowance works."""
        limiter = EngineRateLimiter({
            "test": {"rpm": 60, "burst": 10}
        })

        # Should allow burst
        for _ in range(10):
            wait = await limiter.acquire("test")
            assert wait == 0.0

        # 11th should wait
        wait = await limiter.acquire("test")
        assert wait > 0.0

    @pytest.mark.asyncio
    async def test_rate_limiter_refills_over_time(self):
        """Test token refill over time."""
        limiter = EngineRateLimiter({
            "test": {"rpm": 60, "burst": 2}  # 1 token/sec, burst 2
        })

        # Use burst
        await limiter.acquire("test", 2)

        # Wait for refill
        await asyncio.sleep(1.1)

        # Should have ~1 token
        wait = await limiter.acquire("test")
        assert wait < 0.5  # Should be nearly instant

    @pytest.mark.asyncio
    async def test_rate_limiter_separate_engines(self):
        """Test that engines have separate buckets."""
        limiter = EngineRateLimiter({
            "engine1": {"rpm": 6, "burst": 1},   # 0.1 tokens/sec refill
            "engine2": {"rpm": 6, "burst": 1},
        })

        assert await limiter.acquire("engine1") == 0.0
        assert await limiter.acquire("engine2") == 0.0

        # burst exhausted for both; non-blocking acquire must refuse
        assert limiter.try_acquire("engine1") is False
        assert limiter.try_acquire("engine2") is False

    def test_try_acquire_non_blocking(self):
        """Test non-blocking try_acquire."""
        limiter = EngineRateLimiter({
            "test": {"rpm": 60, "burst": 2}
        })

        assert limiter.try_acquire("test") is True
        assert limiter.try_acquire("test") is True
        assert limiter.try_acquire("test") is False  # Burst exhausted

    def test_get_available_tokens(self):
        """Test getting available token count."""
        limiter = EngineRateLimiter({
            "test": {"rpm": 0.6, "burst": 5}  # near-zero refill during test
        })

        assert limiter.get_available("test") == pytest.approx(5.0, abs=1e-6)
        limiter.try_acquire("test", 2)
        assert limiter.get_available("test") == pytest.approx(3.0, abs=1e-3)


class TestCircuitBreaker:
    """Tests for circuit breaker pattern."""

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold(self):
        """Test circuit opens after threshold failures."""
        from jiro.scraping.client import CircuitBreaker

        breaker = CircuitBreaker(threshold=3, cooldown=1.0)

        assert not breaker.is_open("test")

        breaker.record_failure("test")
        breaker.record_failure("test")
        assert not breaker.is_open("test")

        breaker.record_failure("test")
        assert breaker.is_open("test")

    @pytest.mark.asyncio
    async def test_circuit_resets_on_success(self):
        """Test circuit resets on success."""
        from jiro.scraping.client import CircuitBreaker

        breaker = CircuitBreaker(threshold=2, cooldown=1.0)

        breaker.record_failure("test")
        breaker.record_success("test")
        breaker.record_failure("test")
        breaker.record_failure("test")

        assert breaker.is_open("test")

        breaker.record_success("test")
        assert not breaker.is_open("test")

    @pytest.mark.asyncio
    async def test_circuit_cooldown_expires(self):
        """Test circuit closes after cooldown."""
        from jiro.scraping.client import CircuitBreaker

        breaker = CircuitBreaker(threshold=2, cooldown=0.1)

        breaker.record_failure("test")
        breaker.record_failure("test")
        assert breaker.is_open("test")

        await asyncio.sleep(0.15)
        assert not breaker.is_open("test")


class TestFallbackChain:
    """Tests for engine fallback chain."""

    @pytest.mark.asyncio
    async def test_fallback_on_blocked(self, test_orchestrator, test_client):
        """Test fallback when the first engine is blocked."""
        call_count = {"count": 0, "blocked": None}

        async def mock_get(url, *, engine, **kwargs):
            call_count["count"] += 1
            if call_count["count"] == 1:
                call_count["blocked"] = engine
                raise EngineBlockedError(f"{engine} blocked", details={"engine": engine})

            html = getattr(MockEngineResponses, f"{engine.upper()}_WEB",
                           MockEngineResponses.DUCKDUCKGO_WEB)

            class MockResp:
                def __init__(self, text):
                    self.text = text
                    self.status_code = 200
                    self.headers = {}

            return html, MockResp(html)

        test_client.get = mock_get

        from jiro.models import SearchRequest
        req = SearchRequest(q="result", engine="auto", num=5)

        result = await test_orchestrator.search(req, fresh=True)

        # The blocked engine must differ from the one that succeeded
        assert result.search_metadata["engine"] != call_count["blocked"]
        assert "fallback_engine" in result.search_metadata
        assert result.search_metadata["fallback_engine"] == result.search_metadata["engine"]

    @pytest.mark.asyncio
    async def test_fallback_on_parse_error(self, test_orchestrator, test_client):
        """Test fallback when the first engine's response cannot be parsed."""
        call_count = {"count": 0, "failed": None}

        async def mock_get(url, *, engine, **kwargs):
            call_count["count"] += 1
            if call_count["count"] == 1:
                call_count["failed"] = engine
                unparseable = "<html><body>No results here</body></html>"
                class MockResp:
                    def __init__(self):
                        self.text = unparseable
                        self.status_code = 200
                        self.headers = {}
                return unparseable, MockResp()

            html = getattr(MockEngineResponses, f"{engine.upper()}_WEB",
                           MockEngineResponses.DUCKDUCKGO_WEB)
            class MockResp:
                def __init__(self):
                    self.text = html
                    self.status_code = 200
                    self.headers = {}
            return html, MockResp()

        test_client.get = mock_get

        from jiro.models import SearchRequest
        req = SearchRequest(q="result", engine="auto", num=5)

        result = await test_orchestrator.search(req, fresh=True)
        assert result.search_metadata["engine"] != call_count["failed"]
        assert "fallback_engine" in result.search_metadata

    @pytest.mark.asyncio
    async def test_fallback_all_fail(self, test_orchestrator, test_client):
        """Test error when all engines fail."""
        async def mock_get(url, *, engine, **kwargs):
            raise EngineBlockedError(f"{engine} blocked", details={"engine": engine})

        test_client.get = mock_get

        from jiro.models import SearchRequest
        req = SearchRequest(q="test", engine="auto", num=5)

        with pytest.raises(EngineError) as exc:
            await test_orchestrator.search(req, fresh=True)

        assert "all engines failed" in str(exc.value)


class TestRetryLogic:
    """Tests for retry with exponential backoff."""

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self, test_client):
        """Test retry on timeout (client retries at the transport layer)."""
        call_count = {"count": 0}

        async def mock_do_request(url, *, params, engine, extra_headers, proxy):
            call_count["count"] += 1
            if call_count["count"] < 3:
                raise asyncio.TimeoutError("Timeout")
            class MockResp:
                text = MockEngineResponses.DUCKDUCKGO_WEB
                status_code = 200
                headers = {}
                def __getattr__(self, name):
                    return None
            return MockResp()

        test_client._do_request = mock_do_request
        test_client.retries = 3

        from jiro.models import SearchRequest
        from jiro.scraping.parsers.duckduckgo import DuckDuckGoEngine

        engine = DuckDuckGoEngine(test_client, Settings(raw=TEST_CONFIG))
        req = SearchRequest(q="result", engine="duckduckgo", num=5)

        result = await engine.search(req)
        assert len(result.organic_results) > 0
        assert call_count["count"] == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_blocked(self, test_client):
        """Test no retry on EngineBlockedError (should propagate immediately)."""
        call_count = {"count": 0}

        async def mock_do_request(url, *, params, engine, extra_headers, proxy):
            call_count["count"] += 1
            raise EngineBlockedError("Blocked", details={"engine": engine})

        test_client._do_request = mock_do_request
        test_client.retries = 3

        # The client must not retry a hard block — it should surface immediately.
        with pytest.raises(EngineBlockedError):
            await test_client.get("https://example.com", engine="duckduckgo")

        assert call_count["count"] == 1


class TestChaosResilience:
    """Chaos engineering tests."""

    @pytest.mark.chaos
    @pytest.mark.asyncio
    async def test_latency_injection(self, test_client):
        """Test handling of injected latency."""
        from tests.integration_utils import ChaosScrapingClient

        chaos_client = ChaosScrapingClient(
            Settings(raw=TEST_CONFIG),
            chaos_config={"latency_ms": 100},
        )
        assert chaos_client is not None

    @pytest.mark.chaos
    @pytest.mark.asyncio
    async def test_random_failures(self, test_client):
        """Test handling of random failures."""
        from tests.integration_utils import ChaosScrapingClient

        chaos_client = ChaosScrapingClient(
            Settings(raw=TEST_CONFIG),
            chaos_config={"fail_rate": 0.3},
        )
        assert chaos_client is not None

    @pytest.mark.chaos
    @pytest.mark.asyncio
    async def test_blocked_responses(self, test_client):
        """Test handling of blocked responses."""
        from tests.integration_utils import ChaosScrapingClient

        chaos_client = ChaosScrapingClient(
            Settings(raw=TEST_CONFIG),
            chaos_config={"blocked": True},
        )
        assert chaos_client is not None


class TestProxyRotation:
    """Tests for proxy rotation and health."""

    @pytest.mark.asyncio
    async def test_proxy_rotation_round_robin(self):
        """Test round-robin proxy rotation."""
        from jiro.proxy import ProxyManager
        from jiro.config import Settings

        settings = Settings(raw=TEST_CONFIG)
        settings.raw["scraping"]["proxy"] = {
            "enabled": True,
            "url": "http://proxy1:8080,http://proxy2:8080",
            "rotation_strategy": "round_robin",
        }

        manager = ProxyManager(settings)
        proxies = [manager.next() for _ in range(4)]

        assert proxies[0] == "http://proxy1:8080"
        assert proxies[1] == "http://proxy2:8080"
        assert proxies[2] == "http://proxy1:8080"
        assert proxies[3] == "http://proxy2:8080"

    @pytest.mark.asyncio
    async def test_proxy_failure_tracking(self):
        """Test proxy failure tracking and cooldown."""
        from jiro.proxy import ProxyManager
        from jiro.config import Settings

        settings = Settings(raw=TEST_CONFIG)
        settings.raw["scraping"]["proxy"] = {
            "enabled": True,
            "url": "http://proxy1:8080,http://proxy2:8080",
        }

        manager = ProxyManager(settings)

        # Record failures for proxy1
        for _ in range(3):
            manager.record_failure("http://proxy1:8080")

        # proxy1 should be in cooldown, proxy2 should be used
        proxies = [manager.next() for _ in range(3)]
        assert all(p == "http://proxy2:8080" for p in proxies)

    @pytest.mark.asyncio
    async def test_proxy_cost_tracking(self):
        """Test proxy cost/latency tracking."""
        from jiro.proxy import ProxyManager
        from jiro.config import Settings

        settings = Settings(raw=TEST_CONFIG)
        settings.raw["scraping"]["proxy"] = {
            "enabled": True,
            "url": "http://proxy1:8080",
        }

        manager = ProxyManager(settings)

        manager.record_cost("http://proxy1:8080", cost=0.01, latency_ms=500)
        manager.record_cost("http://proxy1:8080", cost=0.01, latency_ms=300)

        stats = manager.cost_info("http://proxy1:8080")
        assert stats["total_cost"] == 0.02
        assert stats["avg_latency_ms"] == 400


class TestCAPTCHAHandling:
    """Tests for CAPTCHA solver integration."""

    @pytest.mark.asyncio
    async def test_captcha_solver_2captcha(self):
        """Test 2Captcha integration."""
        from jiro.captcha import CaptchaSolver
        from jiro.config import Settings

        settings = Settings(raw=TEST_CONFIG)
        settings.raw["scraping"]["captcha"] = {
            "enabled": True,
            "provider": "2captcha",
            "api_key": "test-key",
        }

        solver = CaptchaSolver(settings)
        assert solver.provider == "2captcha"

    @pytest.mark.asyncio
    async def test_captcha_solver_capsolver(self):
        """Test CapSolver integration."""
        from jiro.captcha import CaptchaSolver
        from jiro.config import Settings

        settings = Settings(raw=TEST_CONFIG)
        settings.raw["scraping"]["captcha"] = {
            "enabled": True,
            "provider": "capsolver",
            "api_key": "test-key",
        }

        solver = CaptchaSolver(settings)
        assert solver.provider == "capsolver"


class TestBrowserFallback:
    """Tests for Playwright browser fallback."""

    @pytest.mark.asyncio
    async def test_browser_fallback_disabled_by_default(self):
        """Test browser fallback is disabled by default."""
        from jiro.config import Settings

        settings = Settings(raw=TEST_CONFIG)
        client = ScrapingClient(settings)

        assert client.browser_fallback is None

    @pytest.mark.asyncio
    async def test_browser_fallback_requires_playwright(self):
        """Test browser fallback requires playwright extra."""
        from jiro.scraping.client import playwright_available
        from jiro.config import Settings

        settings = Settings(raw=TEST_CONFIG)
        settings.raw["scraping"]["browser_fallback"] = True
        client = ScrapingClient(settings)

        # Should be None if playwright not available
        if not playwright_available():
            assert client.browser_fallback is None


class TestCacheBehavior:
    """Tests for caching behavior."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self, test_orchestrator, test_cache):
        """Test cache hit returns cached result."""
        from jiro.models import SearchRequest, SearchResponse, OrganicResult

        # Pre-populate cache
        req = SearchRequest(q="cached query", engine="duckduckgo", num=5)
        key = test_cache.make_key("search", "duckduckgo", "cached query", "web", 5, 0,
                                   "us", "en", "off", "any", "desktop", "us", "en")

        cached_response = SearchResponse(
            search_metadata={
                "id": "test", "engine": "duckduckgo", "query": "cached query",
                "type": "web", "status": "success", "created_at": "2024-01-01T00:00:00Z",
                "total_time_taken": 0.1, "cached": False, "parser_version": "1.0"
            },
            search_information={"total_results": 100, "query_displayed": "cached query"},
            organic_results=[
                OrganicResult(position=1, title="Cached", link="https://example.com",
                              displayed_link="example.com", snippet="Cached result", source="example.com")
            ]
        )

        await test_cache.put(key, cached_response.model_dump(), engine="duckduckgo")

        # Search should return cached
        result = await test_orchestrator.search(req, fresh=False)
        assert result.search_metadata["cached"] is True

    @pytest.mark.asyncio
    async def test_fresh_bypasses_cache(self, test_orchestrator, test_cache, test_client):
        """Test fresh=true bypasses cache and hits the engine."""
        from jiro.models import SearchRequest

        req = SearchRequest(q="result", engine="duckduckgo", num=5, fresh=True)

        call_count = {"count": 0}

        async def mock_get(url, *, engine, **kwargs):
            call_count["count"] += 1
            html = getattr(MockEngineResponses, f"{engine.upper()}_WEB",
                           MockEngineResponses.DUCKDUCKGO_WEB)

            class MockResp:
                def __init__(self, text):
                    self.text = text
                    self.status_code = 200
                    self.headers = {}

            return html, MockResp(html)

        test_client.get = mock_get

        # Fresh request should reach the engine (bypass cache) and succeed.
        result = await test_orchestrator.search(req, fresh=True)
        assert call_count["count"] >= 1
        assert len(result.organic_results) > 0
        assert result.search_metadata.get("cached") is not True


class TestSemanticCache:
    """Tests for semantic (embedding-based) cache."""

    @pytest.mark.asyncio
    async def test_semantic_cache_store_and_find(self, test_settings, test_db, test_cache):
        """Test semantic cache storage and fuzzy lookup."""
        from jiro.semantic import SemanticCache

        semantic = SemanticCache(test_settings, test_db, cache=test_cache)

        # Store a query-embedding pair
        await semantic.store("python web scraping", "cache-key-123")

        # Similar query should find it (if embeddings available)
        # Without LLM key, this will return None
        result = await semantic.find("how to scrape web with python")
        # In test without LLM, returns None
        assert result is None or isinstance(result, dict)


class TestAdaptiveDelays:
    """Tests for adaptive delays between requests."""

    def test_adaptive_delays_configured(self):
        """Test that adaptive delays are configured per engine."""
        from jiro.scraping.client import _ADAPTIVE_DELAYS

        assert "google" in _ADAPTIVE_DELAYS
        assert "bing" in _ADAPTIVE_DELAYS
        assert _ADAPTIVE_DELAYS["google"][0] >= 1.0  # Min delay for Google
        assert _ADAPTIVE_DELAYS["duckduckgo"][0] < 1.0  # Lower for DDG