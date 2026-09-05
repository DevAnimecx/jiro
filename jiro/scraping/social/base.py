"""Base classes and normalized schema for social media scraping."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from jiro.config import Settings
from jiro.scraping.client import ScrapingClient
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.base")


@dataclass
class SocialAuthor:
    """Normalized author information."""
    username: str = ""
    display_name: str = ""
    avatar: str = ""
    verified: bool = False
    followers: Optional[int] = None
    profile_url: str = ""
    bio: str = ""
    location: str = ""
    joined_date: str = ""


@dataclass
class SocialEngagement:
    """Normalized engagement metrics."""
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    views: Optional[int] = None
    saves: Optional[int] = None
    retweets: Optional[int] = None
    replies: Optional[int] = None
    reposts: Optional[int] = None
    quotes: Optional[int] = None
    bookmarks: Optional[int] = None


@dataclass
class SocialMedia:
    """Normalized media attachment."""
    type: str = ""  # image, video, gif, audio
    url: str = ""
    thumbnail: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    alt_text: str = ""


@dataclass
class SocialPost:
    """Normalized social media post."""
    platform: str
    type: str  # post, profile, comment, video, story, reel, etc.
    url: str
    data: Dict[str, Any] = field(default_factory=dict)
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    credits_charged: int = 2
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "platform": self.platform,
            "type": self.type,
            "url": self.url,
            "data": self.data,
            "scraped_at": self.scraped_at,
            "credits_charged": self.credits_charged,
        }


@dataclass
class SocialProfile:
    """Normalized social media profile."""
    platform: str
    username: str
    url: str
    data: Dict[str, Any] = field(default_factory=dict)
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    credits_charged: int = 3


class SocialScrapeError(Exception):
    """Error during social scraping."""
    def __init__(self, message: str, platform: str = "", code: str = "scrape_failed", retryable: bool = True):
        super().__init__(message)
        self.platform = platform
        self.code = code
        self.retryable = retryable


class RateLimitError(SocialScrapeError):
    """Rate limit exceeded."""
    def __init__(self, platform: str, reset_after: Optional[float] = None):
        super().__init__(f"Rate limit exceeded for {platform}", platform, "rate_limited", retryable=True)
        self.reset_after = reset_after


class AuthRequiredError(SocialScrapeError):
    """Authentication required."""
    def __init__(self, platform: str):
        super().__init__(f"Authentication required for {platform}", platform, "auth_required", retryable=False)


class NotFoundError(SocialScrapeError):
    """Content not found."""
    def __init__(self, platform: str, url: str):
        super().__init__(f"Content not found: {url}", platform, "not_found", retryable=False)


class BaseSocialScraper(ABC):
    """Base class for all social media scrapers."""
    
    # Platform identifier
    platform: str = ""
    
    # URL patterns this scraper handles
    url_patterns: List[str] = []
    
    # Supported actions
    supported_actions: List[str] = ["post", "profile", "search"]
    
    # Rate limits (requests per minute)
    rate_limit_rpm: int = 30
    
    # Requires authentication
    requires_auth: bool = False
    
    def __init__(self, client: ScrapingClient = None, settings: Settings = None) -> None:
        self.client = client
        self.settings = settings
        self.social_config = settings.social.get(self.platform, {}) if settings else {}
        self._rate_limit_lock = asyncio.Lock()
        self._last_request = 0.0
    
    @abstractmethod
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a single post/tweet/video."""
        pass
    
    @abstractmethod
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a user profile."""
        pass
    
    async def scrape_timeline(self, username: str, limit: int = 20) -> List[SocialPost]:
        """Scrape user timeline (optional override)."""
        raise NotImplementedError(f"{self.platform} doesn't support timeline scraping")
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search for posts (optional override)."""
        raise NotImplementedError(f"{self.platform} doesn't support search")
    
    def can_handle(self, url: str) -> bool:
        """Check if this scraper can handle the URL."""
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in self.url_patterns)
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract username/post ID from URL. Override in subclass."""
        return None
    
    @classmethod
    def extract_identifier_class(cls, url: str) -> Optional[str]:
        """Class method to extract identifier without instantiation."""
        return None
    
    @classmethod
    def can_handle_class(cls, url: str) -> bool:
        """Class method to check if URL can be handled without instantiation."""
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in cls.url_patterns)
    
    async def _rate_limit(self) -> None:
        """Apply rate limiting."""
        async with self._rate_limit_lock:
            import time
            now = time.monotonic()
            elapsed = now - self._last_request
            min_interval = 60.0 / self.rate_limit_rpm
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last_request = time.monotonic()
    
    async def _fetch_json(self, url: str, **kwargs) -> Dict[str, Any]:
        """Fetch JSON with rate limiting."""
        await self._rate_limit()
        kwargs.setdefault("engine", self.platform)
        if "headers" in kwargs:
            kwargs["extra_headers"] = kwargs.pop("headers")
        text, resp = await self.client.get(url, **kwargs)
        if resp.status_code == 429:
            raise RateLimitError(self.platform)
        if resp.status_code == 404:
            raise NotFoundError(self.platform, url)
        resp.raise_for_status()
        return resp.json()
    
    async def _fetch_html(self, url: str, **kwargs) -> str:
        """Fetch HTML with rate limiting."""
        await self._rate_limit()
        kwargs.setdefault("engine", self.platform)
        if "headers" in kwargs:
            kwargs["extra_headers"] = kwargs.pop("headers")
        text, resp = await self.client.get(url, **kwargs)
        if resp.status_code == 429:
            raise RateLimitError(self.platform)
        if resp.status_code == 404:
            raise NotFoundError(self.platform, url)
        resp.raise_for_status()
        return resp.text
    
    def _normalize_engagement(self, raw: Dict[str, Any]) -> SocialEngagement:
        """Normalize engagement metrics from platform-specific format."""
        return SocialEngagement(
            likes=raw.get("likes") or raw.get("like_count") or raw.get("favorite_count"),
            comments=raw.get("comments") or raw.get("comment_count") or raw.get("reply_count"),
            shares=raw.get("shares") or raw.get("share_count") or raw.get("retweet_count"),
            views=raw.get("views") or raw.get("view_count") or raw.get("play_count"),
            saves=raw.get("saves") or raw.get("bookmark_count"),
            retweets=raw.get("retweets") or raw.get("retweet_count"),
            replies=raw.get("replies") or raw.get("reply_count"),
            reposts=raw.get("reposts") or raw.get("repost_count"),
            quotes=raw.get("quotes") or raw.get("quote_count"),
            bookmarks=raw.get("bookmarks") or raw.get("bookmark_count"),
        )
    
    def _normalize_author(self, raw: Dict[str, Any]) -> SocialAuthor:
        """Normalize author from platform-specific format."""
        return SocialAuthor(
            username=raw.get("username") or raw.get("screen_name") or raw.get("author", {}).get("username", ""),
            display_name=raw.get("display_name") or raw.get("name") or raw.get("author", {}).get("display_name", ""),
            avatar=raw.get("avatar") or raw.get("profile_image_url") or raw.get("author", {}).get("avatar", ""),
            verified=raw.get("verified") or raw.get("is_verified", False),
            followers=raw.get("followers") or raw.get("followers_count") or raw.get("author", {}).get("followers"),
            profile_url=raw.get("profile_url") or raw.get("url", ""),
            bio=raw.get("bio") or raw.get("description") or raw.get("author", {}).get("bio", ""),
            location=raw.get("location") or raw.get("author", {}).get("location", ""),
            joined_date=raw.get("joined_date") or raw.get("created_at") or raw.get("author", {}).get("joined_date", ""),
        )
    
    def _normalize_media(self, raw: List[Dict[str, Any]]) -> List[SocialMedia]:
        """Normalize media attachments."""
        media = []
        for m in raw:
            media.append(SocialMedia(
                type=m.get("type", "image"),
                url=m.get("url") or m.get("media_url") or m.get("video_url", ""),
                thumbnail=m.get("thumbnail") or m.get("preview_image_url", ""),
                width=m.get("width"),
                height=m.get("height"),
                duration=m.get("duration"),
                alt_text=m.get("alt_text") or m.get("alt", ""),
            ))
        return media
    
    def _build_post_data(
        self,
        id: str,
        text: str,
        timestamp: str,
        author: SocialAuthor,
        engagement: SocialEngagement,
        media: List[SocialMedia],
        hashtags: List[str] = None,
        mentions: List[str] = None,
        **extra
    ) -> Dict[str, Any]:
        """Build normalized post data dict."""
        return {
            "id": id,
            "text": text,
            "timestamp": timestamp,
            "author": {
                "username": author.username,
                "display_name": author.display_name,
                "avatar": author.avatar,
                "verified": author.verified,
                "followers": author.followers,
                "profile_url": author.profile_url,
                "bio": author.bio,
                "location": author.location,
                "joined_date": author.joined_date,
            },
            "engagement": {
                "likes": engagement.likes,
                "comments": engagement.comments,
                "shares": engagement.shares,
                "views": engagement.views,
                "saves": engagement.saves,
                "retweets": engagement.retweets,
                "replies": engagement.replies,
                "reposts": engagement.reposts,
                "quotes": engagement.quotes,
                "bookmarks": engagement.bookmarks,
            },
            "media": [
                {
                    "type": m.type,
                    "url": m.url,
                    "thumbnail": m.thumbnail,
                    "width": m.width,
                    "height": m.height,
                    "duration": m.duration,
                    "alt_text": m.alt_text,
                }
                for m in media
            ],
            "hashtags": hashtags or [],
            "mentions": mentions or [],
            **extra
        }


class SocialScraperRegistry:
    """Registry for social media scrapers."""
    
    def __init__(self) -> None:
        self._scrapers: Dict[str, type] = {}
        self._instances: Dict[str, BaseSocialScraper] = {}
    
    def register(self, scraper_class: type) -> type:
        """Register a scraper class."""
        if not scraper_class.platform:
            raise ValueError("Scraper must define platform")
        self._scrapers[scraper_class.platform] = scraper_class
        log.info(f"Registered social scraper: {scraper_class.platform}")
        return scraper_class
    
    def get(self, platform: str) -> type:
        """Get scraper class by platform."""
        if platform not in self._scrapers:
            raise ValueError(f"Unknown platform: {platform}. Available: {list(self._scrapers.keys())}")
        return self._scrapers[platform]
    
    def get_instance(self, platform: str, client: ScrapingClient, settings: Settings) -> BaseSocialScraper:
        """Get or create scraper instance."""
        if platform not in self._instances:
            scraper_class = self.get(platform)
            self._instances[platform] = scraper_class(client, settings)
        return self._instances[platform]
    
    def find_by_url(self, url: str) -> Optional[type]:
        """Find scraper that can handle the URL."""
        for scraper_class in self._scrapers.values():
            # Create a temporary instance to check can_handle
            # Use classmethod if available
            if hasattr(scraper_class, 'can_handle_class'):
                if scraper_class.can_handle_class(url):
                    return scraper_class
            else:
                instance = scraper_class()
                if instance.can_handle(url):
                    return scraper_class
        return None
    
    def list_platforms(self) -> List[Dict[str, Any]]:
        """List all registered platforms."""
        return [
            {
                "platform": cls.platform,
                "url_patterns": cls.url_patterns,
                "supported_actions": cls.supported_actions,
                "requires_auth": cls.requires_auth,
                "rate_limit_rpm": cls.rate_limit_rpm,
            }
            for cls in self._scrapers.values()
        ]


# Global registry
registry = SocialScraperRegistry()