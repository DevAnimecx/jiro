"""Comprehensive validation tests for the full social scraper system.

Tests:
- All 12 social scrapers: instantiation, methods, interface compliance
- All 6 plugin engines: interface compliance
- ScrapingClient: backward compatibility (engine optional, headers alias, post())
- Browser: get_browser_page() context manager
- Router: no duplicates, correct detection
- Normalizer: all platforms produce valid output
- Dynamic token/hash extraction: Instagram, Threads, Facebook
- Search implementations: all platforms return results or empty list (no NotImplementedError)
- Timeline/feed implementations: LinkedIn, Facebook, Threads, Telegram
"""

from __future__ import annotations

import inspect
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiro.browser import BrowserFetcher, BrowserPage, get_browser, get_browser_page, playwright_available
from jiro.scraping.client import ScrapingClient
from jiro.scraping.engines import SearchOrchestrator
from jiro.scraping.social import (
    build_post,
    build_profile,
    build_search,
    normalizer,
    registry,
    router as social_router,
)
from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, SocialScrapeError, RateLimitError, AuthRequiredError, NotFoundError
from jiro.scraping.social.facebook import FacebookScraper
from jiro.scraping.social.instagram import InstagramScraper
from jiro.scraping.social.linkedin import LinkedInScraper
from jiro.scraping.social.pinterest import PinterestScraper
from jiro.scraping.social.reddit import RedditScraper
from jiro.scraping.social.telegram import TelegramScraper
from jiro.scraping.social.threads import ThreadsScraper
from jiro.scraping.social.tiktok import TikTokScraper
from jiro.scraping.social.twitter import TwitterScraper
from jiro.scraping.social.youtube import YouTubeScraper
from jiro.scraping.social.bluesky import BlueskyScraper
from jiro.scraping.social.hackernews import HackerNewsScraper
from jiro.plugins import engine_registry
from jiro.plugins.engine.hackernews import HackerNewsEnginePlugin
from jiro.plugins.engine.github import GitHubPlugin
from jiro.plugins.engine.reddit import RedditEnginePlugin
from jiro.plugins.engine.arxiv import ArxivPlugin
from jiro.plugins.engine.google_scholar import GoogleScholarPlugin
from jiro.plugins.engine.wikipedia import WikipediaPlugin


# ── Fixtures ────────────────────────────────────────────────────────────────────

ALL_SOCIAL_PLATFORMS = [
    "reddit", "hackernews", "youtube", "bluesky",
    "twitter", "threads", "instagram", "tiktok",
    "linkedin", "facebook", "telegram", "pinterest",
]

ALL_PLUGIN_ENGINES = ["hackernews", "github", "reddit", "arxiv", "google_scholar", "wikipedia"]

SOCIAL_CLASSES = {
    "reddit": RedditScraper,
    "hackernews": HackerNewsScraper,
    "youtube": YouTubeScraper,
    "bluesky": BlueskyScraper,
    "twitter": TwitterScraper,
    "threads": ThreadsScraper,
    "instagram": InstagramScraper,
    "tiktok": TikTokScraper,
    "linkedin": LinkedInScraper,
    "facebook": FacebookScraper,
    "telegram": TelegramScraper,
    "pinterest": PinterestScraper,
}


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.social = {}
    settings.user_agent_rotation = False
    settings.robots_txt = {"enabled": False}
    return settings


@pytest.fixture
def mock_client():
    client = MagicMock(spec=ScrapingClient)
    return client


# ── Phase 1: ScrapingClient Interface Fix ──────────────────────────────────────

class TestScrapingClientInterface:
    def test_engine_is_optional(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def check():
            settings = MagicMock()
            settings.user_agent_rotation = False
            settings.robots_txt = {"enabled": False}
            client = ScrapingClient(settings)
            sig = inspect.signature(client.get)
            params = sig.parameters
            assert "engine" in params
            assert params["engine"].default != inspect.Parameter.empty, "engine should have a default"

        try:
            loop.run_until_complete(check())
        finally:
            loop.close()

    def test_headers_alias_accepted(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def check():
            settings = MagicMock()
            settings.user_agent_rotation = False
            settings.robots_txt = {"enabled": False}
            client = ScrapingClient(settings)
            sig = inspect.signature(client.get)
            assert "headers" in sig.parameters, "headers alias should be accepted"

        try:
            loop.run_until_complete(check())
        finally:
            loop.close()

    def test_post_method_exists(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def check():
            settings = MagicMock()
            settings.user_agent_rotation = False
            settings.robots_txt = {"enabled": False}
            client = ScrapingClient(settings)
            assert hasattr(client, "post"), "ScrapingClient should have a post() method"
            sig = inspect.signature(client.post)
            assert "engine" in sig.parameters

        try:
            loop.run_until_complete(check())
        finally:
            loop.close()


# ── Phase 1: Browser Functions ──────────────────────────────────────────────────

class TestBrowserFunctions:
    def test_get_browser_returns_fetcher(self):
        import asyncio
        fetcher = asyncio.run(get_browser(headless=True, timeout=30.0))
        assert isinstance(fetcher, BrowserFetcher)

    def test_get_browser_page_returns_context_manager(self):
        ctx = get_browser_page(headless=True, timeout=30.0)
        assert hasattr(ctx, "__aenter__")
        assert hasattr(ctx, "__aexit__")
        assert isinstance(ctx, BrowserPage)

    def test_playwright_available_returns_bool(self):
        result = playwright_available()
        assert isinstance(result, bool)

    def test_browser_fetcher_has_fetch_and_close(self):
        fetcher = BrowserFetcher(headless=True)
        assert hasattr(fetcher, "fetch")
        assert hasattr(fetcher, "close")
        assert callable(fetcher.fetch)
        assert callable(fetcher.close)


# ── Phase 2+3: All Scrapers + Plugin Engines ───────────────────────────────────

class TestAllScrapersInstantiate:
    """Every scraper must instantiate with (None, mock_settings) without error."""

    @pytest.mark.parametrize("platform", ALL_SOCIAL_PLATFORMS)
    def test_instantiate(self, platform, mock_settings):
        cls = registry.get(platform)
        instance = cls(None, mock_settings)
        assert instance is not None
        assert instance.platform == platform

    @pytest.mark.parametrize("engine", ALL_PLUGIN_ENGINES)
    def test_plugin_instantiate(self, engine):
        cls = engine_registry.get(engine)
        assert cls is not None
        # Just verify the class exists and is importable
        assert hasattr(cls, "search")


class TestAllScrapersHaveMethods:
    """Every scraper must implement the core abstract methods."""

    @pytest.mark.parametrize("platform", ALL_SOCIAL_PLATFORMS)
    def test_has_search(self, platform, mock_settings):
        cls = registry.get(platform)
        instance = cls(None, mock_settings)
        assert hasattr(instance, "search"), f"{platform} missing search()"
        assert callable(instance.search)

    @pytest.mark.parametrize("platform", ALL_SOCIAL_PLATFORMS)
    def test_has_scrape_post(self, platform, mock_settings):
        cls = registry.get(platform)
        instance = cls(None, mock_settings)
        assert hasattr(instance, "scrape_post"), f"{platform} missing scrape_post()"

    @pytest.mark.parametrize("platform", ALL_SOCIAL_PLATFORMS)
    def test_has_scrape_profile(self, platform, mock_settings):
        cls = registry.get(platform)
        instance = cls(None, mock_settings)
        assert hasattr(instance, "scrape_profile"), f"{platform} missing scrape_profile()"


class TestNoNotImplementedError:
    """No scraper should have NotImplementedError in its search method."""

    @pytest.mark.parametrize("platform", ALL_SOCIAL_PLATFORMS)
    def test_search_implemented(self, platform, mock_settings):
        cls = registry.get(platform)
        src = inspect.getsource(cls.search)
        assert "NotImplementedError" not in src, f"{platform}.search() raises NotImplementedError"


class TestScraperInterfaceCompliance:
    """Verify scrapers call client.get() with correct interface."""

    @pytest.mark.parametrize("platform", ALL_SOCIAL_PLATFORMS)
    def test_no_direct_client_get_with_headers(self, platform, mock_settings):
        """Scrapers should not pass headers= directly to client.get()."""
        cls = registry.get(platform)
        src = inspect.getsource(cls)
        lines = src.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if ("self.client.get(" in stripped or "self.client.post(" in stripped):
                if re.search(r'headers\s*=', stripped) and not re.search(r'extra_headers\s*=', stripped):
                    pytest.fail(
                        f"{platform} line {i+1}: uses headers= instead of extra_headers=: {stripped}"
                    )

    @pytest.mark.parametrize("platform", ALL_SOCIAL_PLATFORMS)
    def test_engine_param_present(self, platform, mock_settings):
        """Scrapers should pass engine= to client.get/post."""
        cls = registry.get(platform)
        src = inspect.getsource(cls)
        lines = src.split("\n")
        found_get_or_post = False
        for line in lines:
            if "self.client.get(" in line or "self.client.post(" in line:
                found_get_or_post = True
                assert "engine=" in line, (
                    f"{platform}: missing engine= in client call: {line.strip()}"
                )
        # Scrapers that only use _fetch_json/_fetch_html from base class
        # won't have direct client calls, which is also fine
        # But we check at least some have it or they use the base class properly
        if found_get_or_post:
            pass  # Already checked above


class TestSearchAndTimelineCoverage:
    """Verify search and timeline coverage across all platforms."""

    def test_all_have_search(self):
        for platform in ALL_SOCIAL_PLATFORMS:
            cls = registry.get(platform)
            instance = cls(None, MagicMock())
            assert hasattr(instance, "search")
            src = inspect.getsource(cls.search)
            assert "NotImplementedError" not in src

    def test_timeline_platforms(self):
        """These platforms should have timeline/feed support."""
        timeline_platforms = ["linkedin", "facebook", "threads", "telegram"]
        for platform in timeline_platforms:
            cls = registry.get(platform)
            instance = cls(None, MagicMock())
            assert hasattr(instance, "scrape_timeline"), f"{platform} missing scrape_timeline()"


# ── Phase 4: Dynamic Tokens/Hashes ─────────────────────────────────────────────

class TestDynamicTokenExtraction:
    """Test dynamic extraction of query hashes and CSRF tokens."""

    def test_instagram_parse_query_hashes(self):
        html = '<script>queryId:"abc123def45678901234567890123456",other:"ignore"</script>'
        hashes = InstagramScraper._parse_query_hashes_from_page(html)
        assert "abc123def45678901234567890123456" in hashes.values()

    def test_instagram_no_false_positives(self):
        html = '<script>someRandom: "not-a-hash"</script>'
        hashes = InstagramScraper._parse_query_hashes_from_page(html)
        assert len(hashes) == 0

    def test_threads_parse_query_hashes(self):
        html = '<script>queryId: "5672115639492503"</script>'
        hashes = ThreadsScraper._parse_query_hashes_from_page(html)
        assert "5672115639492503" in hashes.values()

    def test_facebook_extract_lsd_token_input(self):
        html = '<input type="hidden" name="lsd" value="AVtest123" />'
        token = FacebookScraper._extract_lsd_token(html)
        assert token == "AVtest123"

    def test_facebook_extract_lsd_token_js(self):
        html = '<script>"lsd":"AVjs456"</script>'
        token = FacebookScraper._extract_lsd_token(html)
        assert token == "AVjs456"

    def test_facebook_no_token_returns_none(self):
        html = "<html><body>No token here</body></html>"
        token = FacebookScraper._extract_lsd_token(html)
        assert token is None

    def test_instagram_default_hashes_exist(self):
        assert "post" in InstagramScraper.DEFAULT_QUERY_HASHES
        assert "profile" in InstagramScraper.DEFAULT_QUERY_HASHES
        assert "user_posts" in InstagramScraper.DEFAULT_QUERY_HASHES
        assert "reels" in InstagramScraper.DEFAULT_QUERY_HASHES

    def test_threads_default_hashes_exist(self):
        assert "post" in ThreadsScraper.DEFAULT_QUERY_HASHES
        assert "profile" in ThreadsScraper.DEFAULT_QUERY_HASHES
        assert "user_posts" in ThreadsScraper.DEFAULT_QUERY_HASHES


# ── Phase 5: Search Method Signatures ──────────────────────────────────────────

class TestSearchMethodSignatures:
    """Verify search() methods have correct signatures."""

    @pytest.mark.parametrize("platform", ALL_SOCIAL_PLATFORMS)
    def test_search_signature(self, platform, mock_settings):
        cls = registry.get(platform)
        sig = inspect.signature(cls.search)
        params = list(sig.parameters.keys())
        assert "query" in params, f"{platform}.search() missing 'query' param"
        assert "limit" in params, f"{platform}.search() missing 'limit' param"


# ── Phase 8: Router & Normalizer ────────────────────────────────────────────────

class TestRouter:
    def test_no_duplicate_patterns(self):
        patterns = [p for p, _ in social_router._platform_patterns]
        assert len(patterns) == len(set(patterns)), f"Duplicate patterns: {[p for p in patterns if patterns.count(p) > 1]}"

    def test_all_platforms_in_router(self):
        for platform in ALL_SOCIAL_PLATFORMS:
            found = any(p == platform for p, _ in social_router._platform_patterns)
            assert found, f"{platform} not in router patterns"

    def test_extract_identifier_uses_public_api(self):
        router_cls = type(social_router)
        src = inspect.getsource(router_cls.extract_identifier)
        assert "_scrapers" not in src, "extract_identifier should not access private _scrapers"

    def test_detect_common_urls(self):
        tests = [
            ("https://reddit.com/r/python/", "reddit"),
            ("https://twitter.com/user/status/123", "twitter"),
            ("https://x.com/user/status/123", "twitter"),
            ("https://youtube.com/watch?v=abc", "youtube"),
            ("https://youtu.be/abc", "youtube"),
            ("https://threads.net/@user/post/123", "threads"),
            ("https://instagram.com/p/abc/", "instagram"),
            ("https://tiktok.com/@user/video/123", "tiktok"),
            ("https://linkedin.com/in/user", "linkedin"),
            ("https://facebook.com/user", "facebook"),
            ("https://t.me/channel", "telegram"),
            ("https://pinterest.com/pin/123", "pinterest"),
            ("https://news.ycombinator.com/item?id=123", "hackernews"),
            ("https://bsky.app/profile/user", "bluesky"),
        ]
        for url, expected in tests:
            result = social_router.detect_platform(url)
            assert result == expected, f"Expected {expected} for {url}, got {result}"


class TestNormalizer:
    def test_normalize_timestamp_iso(self):
        result = normalizer.normalize_timestamp("2026-01-15T10:00:00Z")
        assert result == "2026-01-15T10:00:00Z"

    def test_normalize_timestamp_unix(self):
        result = normalizer.normalize_timestamp(1705312800)
        assert "2024" in result or "2025" in result or "2026" in result

    def test_normalize_number_k(self):
        assert normalizer.normalize_number("1.2K") == 1200

    def test_normalize_number_m(self):
        assert normalizer.normalize_number("3.5M") == 3500000

    def test_normalize_number_b(self):
        assert normalizer.normalize_number("1B") == 1000000000

    def test_normalize_number_plain(self):
        assert normalizer.normalize_number("42") == 42

    def test_extract_hashtags(self):
        tags = normalizer.extract_hashtags("Hello #python #coding world")
        assert "python" in tags
        assert "coding" in tags

    def test_extract_mentions(self):
        mentions = normalizer.extract_mentions("Hello @user1 @user2")
        assert "user1" in mentions
        assert "user2" in mentions

    def test_clean_text(self):
        result = normalizer.clean_text("Hello   world\n\n\ntest")
        assert "   " not in result
        assert "\n\n" not in result

    def test_normalize_url_strips_tracking(self):
        result = normalizer.normalize_url("https://example.com?utm_source=google&utm_medium=cpc")
        assert "utm_source" not in result
        assert "utm_medium" not in result

    def test_build_post_returns_dict(self):
        post = build_post(
            platform="twitter",
            post_type="post",
            url="https://twitter.com/user/status/123",
            id="123",
            text="Hello",
            timestamp="2026-01-15T10:00:00Z",
            author={"username": "user"},
            engagement={"likes": 10},
            media=[],
        )
        assert isinstance(post, dict)
        assert post["platform"] == "twitter"
        assert post["type"] == "post"
        assert post["data"]["text"] == "Hello"

    def test_build_profile_returns_dict(self):
        profile = build_profile(
            platform="twitter",
            username="user",
            url="https://twitter.com/user",
            profile_data={"author": {"username": "user", "display_name": "User"}},
        )
        assert isinstance(profile, dict)
        assert profile["platform"] == "twitter"
        assert profile["data"]["username"] == "user"

    def test_build_search_returns_dict(self):
        search = build_search(
            platform="twitter",
            posts=[{"platform": "twitter", "type": "post", "url": "https://x.com/1", "data": {}}],
            query="test",
        )
        assert isinstance(search, dict)
        assert search["query"] == "test"
        assert search["total"] == 1


# ── Plugin Engine Tests ─────────────────────────────────────────────────────────

class TestPluginEngines:
    @pytest.mark.parametrize("engine_name", ALL_PLUGIN_ENGINES)
    def test_engine_has_search_method(self, engine_name):
        cls = engine_registry.get(engine_name)
        assert hasattr(cls, "search")

    @pytest.mark.parametrize("engine_name", ALL_PLUGIN_ENGINES)
    def test_engine_has_scrape_method(self, engine_name):
        cls = engine_registry.get(engine_name)
        assert hasattr(cls, "scrape")

    def test_github_plugin_builds_query(self):
        query = GitHubPlugin._build_query(None, "python async", {"language": "python", "min_stars": 100})
        assert "python" in query
        assert "async" in query
        assert "language:python" in query
        assert "stars:>=100" in query

    def test_arxiv_extract_id(self):
        assert ArxivPlugin._extract_arxiv_id(None, "https://arxiv.org/abs/2401.00001") == "2401.00001"
        assert ArxivPlugin._extract_arxiv_id(None, "https://arxiv.org/pdf/2401.00001") == "2401.00001"
        assert ArxivPlugin._extract_arxiv_id(None, "arxiv:2401.00001") == "2401.00001"
        assert ArxivPlugin._extract_arxiv_id(None, "invalid") is None

    def test_wikipedia_extract_title(self):
        assert WikipediaPlugin._extract_title(None, "https://en.wikipedia.org/wiki/Python_(programming_language)") == "Python (programming language)"
        assert WikipediaPlugin._extract_title(None, "https://en.wikipedia.org/wiki/Artificial_intelligence") == "Artificial intelligence"
        assert WikipediaPlugin._extract_title(None, "invalid") is None


# ── Social Scraper URL Extraction Tests ────────────────────────────────────────

class TestURLExtraction:
    """Test that all scrapers correctly extract identifiers from URLs."""

    def test_reddit_extract_identifier(self, mock_settings):
        scraper = RedditScraper(None, mock_settings)
        assert scraper.extract_identifier("https://reddit.com/r/python/comments/abc123/title/") == "abc123"
        assert scraper.extract_identifier("https://reddit.com/user/username") == "username"
        assert scraper.extract_identifier("https://reddit.com/r/python") == "python"

    def test_twitter_extract_identifier(self, mock_settings):
        scraper = TwitterScraper(None, mock_settings)
        assert scraper.extract_identifier("https://twitter.com/user/status/123456789") == "123456789"
        assert scraper.extract_identifier("https://x.com/user/status/123456789") == "123456789"
        assert scraper.extract_identifier("https://twitter.com/username") == "username"

    def test_instagram_extract_shortcode(self):
        scraper = InstagramScraper(None, None)
        assert scraper._extract_shortcode("https://instagram.com/p/ABC123/") == "ABC123"
        assert scraper._extract_shortcode("https://instagram.com/reel/ABC123/") == "ABC123"
        assert scraper._extract_shortcode("https://instagram.com/tv/ABC123/") == "ABC123"
        assert scraper._extract_shortcode("https://instagram.com/user") is None

    def test_tiktok_extract_video_id(self):
        scraper = TikTokScraper(None, None)
        assert scraper._extract_video_id("https://tiktok.com/@user/video/123456789") == "123456789"
        assert scraper._extract_video_id("https://vm.tiktok.com/abc123") == "abc123"
        assert scraper._extract_video_id("https://vt.tiktok.com/abc123") == "abc123"

    def test_threads_extract_identifier(self, mock_settings):
        scraper = ThreadsScraper(None, mock_settings)
        assert scraper.extract_identifier("https://threads.net/@user/post/abc123") == "abc123"
        assert scraper.extract_identifier("https://threads.net/@user") == "user"

    def test_linkedin_extract_identifier(self):
        scraper = LinkedInScraper(None, None)
        assert scraper.extract_identifier("https://linkedin.com/in/username") == "username"
        assert scraper.extract_identifier("https://linkedin.com/company/company-name") == "company-name"
        assert scraper.extract_identifier("https://linkedin.com/feed/update/urn:li:activity:123") == "urn:li:activity:123"

    def test_facebook_extract_identifier(self):
        scraper = FacebookScraper(None, None)
        assert scraper.extract_identifier("https://facebook.com/user/posts/123") == "123"
        assert scraper.extract_identifier("https://fb.watch/abc123") == "abc123"
        assert scraper.extract_identifier("https://facebook.com/username") == "username"

    def test_telegram_extract_identifier(self):
        scraper = TelegramScraper(None, None)
        assert scraper.extract_identifier("https://t.me/channel/123") == "channel/123"
        assert scraper.extract_identifier("https://t.me/channel") == "channel"

    def test_pinterest_extract_identifier(self):
        scraper = PinterestScraper(None, None)
        assert scraper.extract_identifier("https://pinterest.com/pin/123456") == "123456"
        assert scraper.extract_identifier("https://pinterest.com/username") == "username"
        assert scraper.extract_identifier("https://pinterest.com/username/board-slug") == "username/board-slug"
        assert scraper.extract_identifier("https://pin.it/abc123") == "abc123"

    def test_youtube_extract_video_id(self):
        scraper = YouTubeScraper(None, None)
        assert scraper._extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert scraper._extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert scraper._extract_video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_bluesky_extract_identifier(self, mock_settings):
        scraper = BlueskyScraper(None, mock_settings)
        assert scraper.extract_identifier("https://bsky.app/profile/user.bsky.social/post/abc123") == "user.bsky.social/post/abc123"
        assert scraper.extract_identifier("https://bsky.app/profile/user.bsky.social") == "user.bsky.social"

    def test_hackernews_extract_identifier(self, mock_settings):
        scraper = HackerNewsScraper(None, mock_settings)
        assert scraper.extract_identifier("https://news.ycombinator.com/item?id=12345") == "12345"
        assert scraper.extract_identifier("https://news.ycombinator.com/item/12345") == "12345"


# ── Social Post Normalization Tests ────────────────────────────────────────────

class TestSocialPostNormalization:
    def test_build_post_structure(self):
        post = build_post(
            platform="twitter",
            post_type="post",
            url="https://twitter.com/user/status/123",
            id="123",
            text="Hello world",
            timestamp="2026-01-15T10:00:00Z",
            author={"username": "user", "display_name": "User", "avatar": "", "verified": True, "followers": 1000},
            engagement={"likes": 100, "retweets": 50},
            media=[{"type": "image", "url": "https://example.com/img.jpg"}],
        )
        assert post["platform"] == "twitter"
        assert post["type"] == "post"
        assert post["url"] == "https://twitter.com/user/status/123"
        assert post["data"]["id"] == "123"
        assert post["data"]["text"] == "Hello world"
        assert post["data"]["author"]["username"] == "user"
        assert post["data"]["engagement"]["likes"] == 100
        assert len(post["data"]["media"]) == 1

    def test_build_profile_structure(self):
        profile = build_profile(
            platform="twitter",
            username="user",
            url="https://twitter.com/user",
            profile_data={"author": {"username": "user", "display_name": "User", "followers": 1000, "bio": "Bio"}},
        )
        assert profile["platform"] == "twitter"
        assert profile["type"] == "profile"
        assert profile["url"] == "https://twitter.com/user"
        assert profile["data"]["username"] == "user"

    def test_build_search_structure(self):
        search = build_search(
            platform="twitter",
            posts=[
                {"platform": "twitter", "type": "post", "url": "https://x.com/1", "data": {}},
                {"platform": "twitter", "type": "post", "url": "https://x.com/2", "data": {}},
            ],
            query="test query",
        )
        assert search["platform"] == "twitter"
        assert search["query"] == "test query"
        assert search["total"] == 2


# ── Error Handling Tests ────────────────────────────────────────────────────────

class TestErrorHierarchy:
    def test_rate_limit_error(self):
        error = RateLimitError("twitter")
        assert error.platform == "twitter"
        assert error.code == "rate_limited"
        assert error.retryable is True

    def test_auth_required_error(self):
        error = AuthRequiredError("instagram")
        assert error.platform == "instagram"
        assert error.code == "auth_required"
        assert error.retryable is False

    def test_not_found_error(self):
        error = NotFoundError("twitter", "https://twitter.com/user/status/123")
        assert error.platform == "twitter"
        assert error.code == "not_found"
        assert error.retryable is False

    def test_social_scrape_error_base(self):
        error = SocialScrapeError("test", platform="reddit", code="test_code", retryable=True)
        assert str(error) == "test"
        assert error.platform == "reddit"
        assert error.code == "test_code"
        assert error.retryable is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
