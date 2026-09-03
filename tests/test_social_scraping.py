"""Tests for Phase 2: Social Media Scraping (12 platforms)"""

from __future__ import annotations

import pytest

from jiro.scraping.social import (
    registry,
    router as social_router,
    parse_social_url,
    auto_detect,
    detect_action,
    build_post,
    build_profile,
    build_search,
)
from jiro.scraping.social.base import (
    BaseSocialScraper,
    SocialPost,
    SocialProfile,
    SocialScrapeError,
    RateLimitError,
    AuthRequiredError,
    NotFoundError,
)
from jiro.scraping.social.reddit import RedditScraper
from jiro.scraping.social.hackernews import HackerNewsScraper
from jiro.scraping.social.youtube import YouTubeScraper
from jiro.scraping.social.bluesky import BlueskyScraper
from jiro.scraping.social.twitter import TwitterScraper
from jiro.scraping.social.threads import ThreadsScraper
from jiro.scraping.social.instagram import InstagramScraper
from jiro.scraping.social.tiktok import TikTokScraper
from jiro.scraping.social.linkedin import LinkedInScraper
from jiro.scraping.social.facebook import FacebookScraper
from jiro.scraping.social.telegram import TelegramScraper
from jiro.scraping.social.pinterest import PinterestScraper


# ── Registry Tests ─────────────────────────────────────────────────────────────

class TestSocialRegistry:
    def test_all_12_platforms_registered(self):
        platforms = [p["platform"] for p in registry.list_platforms()]
        expected = [
            "reddit", "hackernews", "youtube", "bluesky",
            "twitter", "threads", "instagram", "tiktok",
            "linkedin", "facebook", "telegram", "pinterest",
        ]
        for p in expected:
            assert p in platforms, f"Missing platform: {p}"
        assert len(platforms) == 12

    def test_get_scraper_class(self):
        scraper_class = registry.get("reddit")
        assert scraper_class == RedditScraper
        
        scraper_class = registry.get("twitter")
        assert scraper_class == TwitterScraper

    def test_find_by_url(self):
        # Test URL detection
        assert registry.find_by_url("https://reddit.com/r/python/comments/abc/") == RedditScraper
        assert registry.find_by_url("https://twitter.com/user/status/123") == TwitterScraper
        assert registry.find_by_url("https://youtube.com/watch?v=abc") == YouTubeScraper
        assert registry.find_by_url("https://bsky.app/profile/user/post/123") == BlueskyScraper


# ── Router Tests ───────────────────────────────────────────────────────────────

class TestSocialRouter:
    def test_auto_detect_platform(self):
        assert auto_detect("https://reddit.com/r/python/") == "reddit"
        assert auto_detect("https://twitter.com/user/status/123") == "twitter"
        assert auto_detect("https://x.com/user/status/123") == "twitter"
        assert auto_detect("https://youtube.com/watch?v=abc") == "youtube"
        assert auto_detect("https://youtu.be/abc") == "youtube"
        assert auto_detect("https://threads.net/@user/post/123") == "threads"
        assert auto_detect("https://instagram.com/p/abc/") == "instagram"
        assert auto_detect("https://tiktok.com/@user/video/123") == "tiktok"
        assert auto_detect("https://linkedin.com/in/user") == "linkedin"
        assert auto_detect("https://facebook.com/user/posts/123") == "facebook"
        assert auto_detect("https://t.me/channel/123") == "telegram"
        assert auto_detect("https://pinterest.com/pin/123") == "pinterest"
        assert auto_detect("https://news.ycombinator.com/item?id=123") == "hackernews"
        assert auto_detect("https://bsky.app/profile/user/post/123") == "bluesky"
        assert auto_detect("https://unknown.com/path") is None

    def test_detect_action(self):
        assert detect_action("reddit", "https://reddit.com/r/python/comments/abc/title/") == "post"
        assert detect_action("reddit", "https://reddit.com/r/python/") == "subreddit"
        assert detect_action("twitter", "https://twitter.com/user/status/123") == "post"
        assert detect_action("twitter", "https://twitter.com/user/") == "profile"
        assert detect_action("youtube", "https://youtube.com/watch?v=abc") == "video"
        assert detect_action("youtube", "https://youtube.com/@channel") == "channel"
        assert detect_action("instagram", "https://instagram.com/p/abc/") == "post"
        assert detect_action("instagram", "https://instagram.com/reel/abc/") == "post"

    def test_parse_social_url(self):
        result = parse_social_url("https://twitter.com/user/status/123456")
        assert result["platform"] == "twitter"
        assert result["action"] == "post"
        assert result["identifier"] == "123456"
        
        result = parse_social_url("https://reddit.com/r/python/")
        assert result["platform"] == "reddit"
        assert result["action"] == "subreddit"
        
        result = parse_social_url("https://bsky.app/profile/user.bsky.social/post/abc123")
        assert result["platform"] == "bluesky"
        assert result["action"] == "post"


# ── Normalizer Tests ────────────────────────────────────────────────────────────

class TestSocialNormalizer:
    def test_build_post(self):
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

    def test_build_profile(self):
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

    def test_build_search(self):
        search = build_search(
            platform="twitter",
            posts=[
                {"platform": "twitter", "type": "post", "url": "https://twitter.com/1", "data": {}},
                {"platform": "twitter", "type": "post", "url": "https://twitter.com/2", "data": {}},
            ],
            query="test query",
        )
        
        assert search["platform"] == "twitter"
        assert search["query"] == "test query"
        assert search["total"] == 2


# ── Platform-Specific Tests ────────────────────────────────────────────────────

@pytest.fixture
def mock_settings():
    """Create mock settings for scraper tests."""
    from unittest.mock import MagicMock
    settings = MagicMock()
    settings.social = {}
    return settings


class TestRedditScraper:
    def test_extract_identifier(self, mock_settings):
        scraper = RedditScraper(None, mock_settings)
        assert scraper.extract_identifier("https://reddit.com/r/python/comments/abc123/title/") == "abc123"
        assert scraper.extract_identifier("https://reddit.com/user/username") == "username"
        assert scraper.extract_identifier("https://reddit.com/r/python") == "python"
    
    def test_extract_identifier_class(self):
        assert RedditScraper.extract_identifier_class("https://reddit.com/r/python/comments/abc123/title/") == "abc123"
        assert RedditScraper.extract_identifier_class("https://reddit.com/user/username") == "username"
        assert RedditScraper.extract_identifier_class("https://reddit.com/r/python") == "python"


class TestHackerNewsScraper:
    def test_extract_identifier(self, mock_settings):
        scraper = HackerNewsScraper(None, mock_settings)
        assert scraper.extract_identifier("https://news.ycombinator.com/item?id=12345") == "12345"
        assert scraper.extract_identifier("https://news.ycombinator.com/item/12345") == "12345"
    
    def test_extract_identifier_class(self):
        assert HackerNewsScraper.extract_identifier_class("https://news.ycombinator.com/item?id=12345") == "12345"
        assert HackerNewsScraper.extract_identifier_class("https://news.ycombinator.com/item/12345") == "12345"


class TestYouTubeScraper:
    def test_extract_video_id(self, mock_settings):
        scraper = YouTubeScraper(None, mock_settings)
        assert scraper._extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert scraper._extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert scraper._extract_video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert scraper._extract_video_id("https://youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    
    def test_extract_identifier_class(self):
        assert YouTubeScraper.extract_identifier_class("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert YouTubeScraper.extract_identifier_class("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert YouTubeScraper.extract_identifier_class("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert YouTubeScraper.extract_identifier_class("https://youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert YouTubeScraper.extract_identifier_class("https://youtube.com/channel/UC123") == "UC123"


class TestBlueskyScraper:
    def test_extract_identifier(self, mock_settings):
        scraper = BlueskyScraper(None, mock_settings)
        assert scraper.extract_identifier("https://bsky.app/profile/user.bsky.social/post/abc123") == "user.bsky.social/post/abc123"
        assert scraper.extract_identifier("https://bsky.app/profile/user.bsky.social") == "user.bsky.social"
    
    def test_extract_identifier_class(self):
        assert BlueskyScraper.extract_identifier_class("https://bsky.app/profile/user.bsky.social/post/abc123") == "user.bsky.social/post/abc123"
        assert BlueskyScraper.extract_identifier_class("https://bsky.app/profile/user.bsky.social") == "user.bsky.social"


class TestTwitterScraper:
    def test_extract_tweet_id(self, mock_settings):
        scraper = TwitterScraper(None, mock_settings)
        assert scraper._extract_tweet_id("https://twitter.com/user/status/123456789") == "123456789"
        assert scraper._extract_tweet_id("https://x.com/user/status/123456789") == "123456789"
        assert scraper._extract_tweet_id("https://twitter.com/username") is None
    
    def test_extract_identifier_class(self):
        assert TwitterScraper.extract_identifier_class("https://twitter.com/user/status/123456789") == "123456789"
        assert TwitterScraper.extract_identifier_class("https://x.com/user/status/123456789") == "123456789"
        assert TwitterScraper.extract_identifier_class("https://twitter.com/username") == "username"


class TestThreadsScraper:
    def test_extract_identifier(self, mock_settings):
        scraper = ThreadsScraper(None, mock_settings)
        assert scraper.extract_identifier("https://threads.net/@user/post/abc123") == "abc123"
        assert scraper.extract_identifier("https://threads.net/@user") == "user"
    
    def test_extract_identifier_class(self):
        assert ThreadsScraper.extract_identifier_class("https://threads.net/@user/post/abc123") == "abc123"
        assert ThreadsScraper.extract_identifier_class("https://threads.net/@user") == "user"


class TestInstagramScraper:
    def test_extract_shortcode(self):
        scraper = InstagramScraper(None, None)
        assert scraper._extract_shortcode("https://instagram.com/p/ABC123/") == "ABC123"
        assert scraper._extract_shortcode("https://instagram.com/reel/ABC123/") == "ABC123"
        assert scraper._extract_shortcode("https://instagram.com/tv/ABC123/") == "ABC123"
        assert scraper._extract_shortcode("https://instagram.com/user") is None


class TestTikTokScraper:
    def test_extract_video_id(self):
        scraper = TikTokScraper(None, None)
        assert scraper._extract_video_id("https://tiktok.com/@user/video/123456789") == "123456789"
        assert scraper._extract_video_id("https://vm.tiktok.com/abc123") == "abc123"
        assert scraper._extract_video_id("https://vt.tiktok.com/abc123") == "abc123"


class TestLinkedInScraper:
    def test_extract_identifier(self):
        scraper = LinkedInScraper(None, None)
        assert scraper.extract_identifier("https://linkedin.com/in/username") == "username"
        assert scraper.extract_identifier("https://linkedin.com/company/company-name") == "company-name"
        assert scraper.extract_identifier("https://linkedin.com/feed/update/urn:li:activity:123") == "urn:li:activity:123"


class TestFacebookScraper:
    def test_extract_identifier(self):
        scraper = FacebookScraper(None, None)
        assert scraper.extract_identifier("https://facebook.com/user/posts/123") == "123"
        assert scraper.extract_identifier("https://fb.watch/abc123") == "abc123"
        assert scraper.extract_identifier("https://facebook.com/username") == "username"


class TestTelegramScraper:
    def test_extract_identifier(self):
        scraper = TelegramScraper(None, None)
        assert scraper.extract_identifier("https://t.me/channel/123") == "channel/123"
        assert scraper.extract_identifier("https://t.me/channel") == "channel"


class TestPinterestScraper:
    def test_extract_identifier(self):
        scraper = PinterestScraper(None, None)
        assert scraper.extract_identifier("https://pinterest.com/pin/123456") == "123456"
        assert scraper.extract_identifier("https://pinterest.com/username") == "username"
        assert scraper.extract_identifier("https://pinterest.com/username/board-slug") == "username/board-slug"
        assert scraper.extract_identifier("https://pin.it/abc123") == "abc123"


# ── Error Handling Tests ────────────────────────────────────────────────────────

class TestErrorHandling:
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])