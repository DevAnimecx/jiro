"""Property-based tests using Hypothesis."""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st, settings, assume
from hypothesis.strategies import composite

from jiro.scraping.client import EngineRateLimiter
from jiro.scraping.engines import SearchOrchestrator
from jiro.models import SearchRequest
from jiro.config import Settings
from tests.integration_utils import TEST_CONFIG


# Property-based test configuration
PROPERTY_TEST_SETTINGS = settings(max_examples=100, deadline=None)


class TestRateLimiterProperties:
    """Property-based tests for rate limiter."""

    @given(
        rpm=st.integers(min_value=1, max_value=1000),
        burst=st.integers(min_value=1, max_value=100),
        requests=st.integers(min_value=1, max_value=200),
    )
    @settings(max_examples=50)
    def test_rate_limiter_never_allows_more_than_burst_immediately(self, rpm, burst, requests):
        """Property: rate limiter never allows more than burst tokens immediately."""
        limiter = EngineRateLimiter({"test": {"rpm": rpm, "burst": burst}})

        allowed = sum(1 for _ in range(requests) if limiter.try_acquire("test"))
        assert allowed <= burst

    @given(
        rpm=st.integers(min_value=10, max_value=1000),
        burst=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=30)
    def test_rate_limiter_eventually_allows_all(self, rpm, burst):
        """Property: given enough elapsed time, all requested tokens become available."""
        import time

        limiter = EngineRateLimiter({"test": {"rpm": rpm, "burst": burst}})

        # Exhaust the burst immediately
        allowed = 0
        while limiter.try_acquire("test"):
            allowed += 1
        assert allowed == burst

        # Simulate enough wall-clock time passing for the full burst to refill
        bucket = limiter._buckets["test"]
        wait_seconds = burst * 60.0 / rpm + 1.0
        bucket["last_refill"] = time.time() - wait_seconds

        # Now the whole burst should be acquirable again
        for _ in range(burst):
            assert limiter.try_acquire("test")

    @given(
        engines=st.lists(st.sampled_from(["google", "bing", "duckduckgo"]), min_size=1, max_size=5, unique=True),
    )
    @settings(max_examples=20)
    def test_separate_buckets_per_engine(self, engines):
        """Property: each engine has independent rate limit bucket."""
        engine_limits = {e: {"rpm": 60, "burst": 5} for e in engines}
        limiter = EngineRateLimiter(engine_limits)

        # Exhaust burst for each engine
        for engine in engines:
            for _ in range(5):
                assert limiter.try_acquire(engine)

        # All should be exhausted
        for engine in engines:
            assert not limiter.try_acquire(engine)


class TestSearchRequestProperties:
    """Property-based tests for search request validation."""

    @given(
        q=st.text(min_size=1, max_size=500).filter(lambda x: not x.isspace()),
        engine=st.sampled_from(["google", "bing", "duckduckgo", "brave", "youtube",
                                "amazon", "ebay", "yandex", "baidu", "auto"]),
        type=st.sampled_from(["web", "images", "news", "videos", "shopping", "places"]),
        num=st.integers(min_value=1, max_value=100),
        start=st.integers(min_value=0, max_value=1000),
        location=st.text(min_size=1, max_size=50),
        language=st.text(min_size=2, max_size=10),
    )
    @settings(max_examples=50)
    def test_search_request_validation(self, q, engine, type, num, start, location, language):
        """Property: SearchRequest validates all valid inputs."""
        req = SearchRequest(
            q=q, engine=engine, type=type, num=num, start=start,
            location=location, language=language
        )
        assert req.q == q
        assert req.engine == engine
        assert req.type == type
        assert req.num == num
        assert req.start == start

    @given(
        q=st.text(min_size=501, max_size=1000),
    )
    @settings(max_examples=10)
    def test_search_request_rejects_long_query(self, q):
        """Property: SearchRequest rejects queries over max length."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SearchRequest(q=q, engine="google", num=10)

    @given(
        num=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=20)
    def test_search_request_num_in_range(self, num):
        """Property: SearchRequest accepts any num within the valid [1, 100] range."""
        req = SearchRequest(q="test", engine="google", num=num)
        assert req.num == num

    @given(
        num=st.one_of(st.integers(max_value=0), st.integers(min_value=101)),
    )
    @settings(max_examples=20)
    def test_search_request_rejects_out_of_range_num(self, num):
        """Property: SearchRequest rejects num outside [1, 100]."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SearchRequest(q="test", engine="google", num=num)


class TestSearchOrchestratorProperties:
    """Property-based tests for search orchestrator."""

    @given(
        engine=st.sampled_from(["google", "bing", "duckduckgo", "auto"]),
    )
    @settings(max_examples=10)
    def test_available_engines_includes_requested(self, engine):
        """Property: available_engines includes requested engine first."""
        settings = Settings(raw=TEST_CONFIG)
        from jiro.scraping.client import ScrapingClient
        from jiro.cache import CacheManager

        client = ScrapingClient(settings)
        cache = CacheManager(memory=True, ttl=60)
        orchestrator = SearchOrchestrator(settings, client, cache)

        engines = orchestrator.available_engines(engine)
        if engine != "auto":
            assert engines[0] == engine

    @given(
        fallback_order=st.lists(
            st.sampled_from(["google", "bing", "duckduckgo", "brave"]),
            min_size=2, max_size=4, unique=True
        ),
    )
    @settings(max_examples=20)
    def test_fallback_order_respected(self, fallback_order):
        """Property: fallback order is respected in available_engines."""
        settings = Settings(raw=TEST_CONFIG)
        settings.raw["scraping"]["fallback_order"] = fallback_order
        settings.raw["scraping"]["engines"] = fallback_order

        from jiro.scraping.client import ScrapingClient
        from jiro.cache import CacheManager

        client = ScrapingClient(settings)
        cache = CacheManager(memory=True, ttl=60)
        orchestrator = SearchOrchestrator(settings, client, cache)

        engines = orchestrator.available_engines("auto")
        assert engines == fallback_order


class TestCacheKeyProperties:
    """Property-based tests for cache key generation."""

    @given(
        q1=st.text(min_size=1, max_size=100),
        q2=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=50)
    def test_cache_key_different_for_different_queries(self, q1, q2):
        """Property: different queries produce different cache keys."""
        assume(q1 != q2)

        from jiro.cache import CacheManager
        cache = CacheManager(memory=True, ttl=60)

        key1 = cache.make_key("search", "google", q1, "web", 10, 0, "us", "en", "off", "any", "desktop", "us", "en")
        key2 = cache.make_key("search", "google", q2, "web", 10, 0, "us", "en", "off", "any", "desktop", "us", "en")

        assert key1 != key2

    @given(
        q=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=20)
    def test_cache_key_deterministic(self, q):
        """Property: cache key is deterministic for same inputs."""
        from jiro.cache import CacheManager
        cache = CacheManager(memory=True, ttl=60)

        key1 = cache.make_key("search", "google", q, "web", 10, 0, "us", "en", "off", "any", "desktop", "us", "en")
        key2 = cache.make_key("search", "google", q, "web", 10, 0, "us", "en", "off", "any", "desktop", "us", "en")

        assert key1 == key2

    # Map of param name -> positional slot in make_key (after "search","google","query")
    PARAM_INDEX = {
        "engine": 0, "type": 1, "num": 2, "start": 3, "location": 4,
        "language": 5, "safe": 6, "time_range": 7, "device": 8, "gl": 9, "hl": 10,
    }
    ORIGINAL = ["google", "web", 10, 0, "us", "en", "off", "any", "desktop", "us", "en"]

    @given(
        param_changes=st.lists(
            st.tuples(
                st.sampled_from(list(PARAM_INDEX)),
                st.one_of(
                    st.sampled_from(["google", "bing", "duckduckgo"]),
                    st.sampled_from(["web", "images", "news"]),
                    st.integers(1, 100),
                    st.integers(0, 1000),
                    st.text(min_size=1, max_size=20),
                    st.text(min_size=2, max_size=10),
                    st.sampled_from(["off", "medium", "high"]),
                    st.sampled_from(["any", "day", "week", "month", "year"]),
                    st.sampled_from(["desktop", "mobile"]),
                    st.text(min_size=2, max_size=5),
                    st.text(min_size=2, max_size=5),
                )
            ),
            min_size=1, max_size=5
        ),
    )
    @settings(max_examples=30)
    def test_cache_key_changes_with_params(self, param_changes):
        """Property: cache key changes when any parameter changes."""
        from jiro.cache import CacheManager
        cache = CacheManager(memory=True, ttl=60)

        base = list(self.ORIGINAL)
        changed = False
        for param, value in param_changes:
            idx = self.PARAM_INDEX[param]
            assume(value != self.ORIGINAL[idx])
            base[idx] = value
            changed = True

        assume(changed)
        key1 = cache.make_key("search", "google", "test query", *self.ORIGINAL)
        key2 = cache.make_key("search", "google", "test query", *base)

        assert key1 != key2


class TestURLNormalizationProperties:
    """Property-based tests for URL normalization."""

    @given(
        url=st.from_regex(r"https?://[a-z0-9.-]+\.[a-z]{2,}(?:/[^?#]*)?(?:\?[^#]*)?(?:#.*)?", fullmatch=True),
    )
    @settings(max_examples=50)
    def test_normalize_url_preserves_valid_urls(self, url):
        """Property: valid URLs are preserved (not Bing redirect)."""
        from jiro.scraping.client import normalize_url

        # Skip Bing redirect URLs
        if "bing.com/ck/a" in url:
            assume(False)

        result = normalize_url(url)
        assert result == url or result.startswith("http")

    @given(
        encoded=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=20)
    def test_normalize_url_handles_bing_redirect(self, encoded):
        """Property: Bing redirect URLs are handled without raising."""
        from jiro.scraping.client import normalize_url

        url = f"https://www.bing.com/ck/a?u=a1{encoded}"
        result = normalize_url(url)
        # Should either return original or decoded
        assert isinstance(result, str)
        assert len(result) > 0


class TestParserSelectorsProperties:
    """Property-based tests for parser selector resilience."""

    @given(
        selectors=st.lists(
            st.sampled_from(["div", "span", "a", "p", "h1", "h2", "h3", "ul", "li",
                             "section", "article", ".result", "#main", "cite", "table"]),
            min_size=1, max_size=10
        ),
        html_content=st.text(min_size=100, max_size=5000),
    )
    @settings(max_examples=20)
    def test_first_many_returns_first_match(self, selectors, html_content):
        """Property: _first_many returns first selector that matches."""
        from selectolax.parser import HTMLParser
        from jiro.scraping.parsers.google import GoogleEngine

        tree = HTMLParser(html_content)

        result = GoogleEngine._first_many(tree, selectors)

        # Should return first non-empty match (or [] when none match)
        expected: list = []
        for sel in selectors:
            nodes = tree.css(sel)
            if nodes:
                expected = nodes
                break

        assert result == expected


class TestConfigMergeProperties:
    """Property-based tests for config merging."""

    @given(
        base=st.fixed_dictionaries({
            "a": st.integers(),
            "b": st.text(),
            "c": st.booleans(),
        }),
        override=st.fixed_dictionaries({
            "a": st.integers(),
            "b": st.text(),
            "c": st.booleans(),
        }),
    )
    @settings(max_examples=50)
    def test_deep_merge_override_wins(self, base, override):
        """Property: override values win in deep merge."""
        from jiro.config import deep_merge

        result = deep_merge(base, override)

        assert result["a"] == override["a"]
        assert result["b"] == override["b"]
        assert result["c"] == override["c"]

    @given(
        base=st.fixed_dictionaries({
            "nested": st.fixed_dictionaries({"x": st.integers(), "y": st.text()}),
        }),
        override=st.fixed_dictionaries({
            "nested": st.fixed_dictionaries({"x": st.integers()}),
        }),
    )
    @settings(max_examples=30)
    def test_deep_merge_preserves_unoverridden_nested(self, base, override):
        """Property: deep merge preserves nested keys not in override."""
        from jiro.config import deep_merge

        result = deep_merge(base, override)

        assert result["nested"]["x"] == override["nested"]["x"]
        assert result["nested"]["y"] == base["nested"]["y"]


class TestAuthProperties:
    """Property-based tests for auth."""

    @given(
        api_key=st.text(min_size=40, max_size=50),
    )
    @settings(max_examples=20)
    def test_api_key_hash_deterministic(self, api_key):
        """Property: API key hashing is deterministic."""
        from jiro.auth import hash_key

        h1 = hash_key(api_key)
        h2 = hash_key(api_key)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    @given(
        api_key1=st.text(min_size=40, max_size=50),
        api_key2=st.text(min_size=40, max_size=50),
    )
    @settings(max_examples=30)
    def test_api_key_hash_collision_resistant(self, api_key1, api_key2):
        """Property: different keys produce different hashes (with high probability)."""
        assume(api_key1 != api_key2)

        from jiro.auth import hash_key

        h1 = hash_key(api_key1)
        h2 = hash_key(api_key2)
        assert h1 != h2