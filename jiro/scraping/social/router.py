"""URL detection and platform routing for social media scraping."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from jiro.log import get_logger
from jiro.scraping.social.base import registry, BaseSocialScraper

log = get_logger("jiro.scraping.social.router")


# Platform URL patterns for auto-detection
# Order matters: more specific patterns first
PLATFORM_PATTERNS = [
    ("bluesky", [
        r"bsky\.app",
        r"bluesky\.app",
    ]),
    ("threads", [
        r"threads\.net",
    ]),
    ("instagram", [
        r"instagram\.com",
        r"instagr\.am",
    ]),
    ("tiktok", [
        r"tiktok\.com",
        r"vm\.tiktok\.com",
        r"vt\.tiktok\.com",
    ]),
    ("youtube", [
        r"youtube\.com",
        r"youtu\.be",
        r"youtube-nocookie\.com",
    ]),
    ("reddit", [
        r"reddit\.com",
        r"old\.reddit\.com",
        r"www\.reddit\.com",
    ]),
    ("hackernews", [
        r"news\.ycombinator\.com",
        r"hackernews\.com",
    ]),
    ("telegram", [
        r"t\.me",
        r"telegram\.me",
        r"telegram\.org",
    ]),
    ("pinterest", [
        r"pinterest\.com",
        r"pin\.it",
    ]),
    ("linkedin", [
        r"linkedin\.com",
    ]),
    ("facebook", [
        r"facebook\.com",
        r"fb\.com",
        r"fb\.watch",
    ]),
    ("twitter", [
        r"twitter\.com",
        r"x\.com",
        r"fxtwitter\.com",
        r"vxtwitter\.com",
        r"t\.co",
    ]),
    ("pinterest", [
        r"pinterest\.com",
        r"pin\.it",
    ]),
    ("hackernews", [
        r"news\.ycombinator\.com",
        r"hackernews\.com",
    ]),
    ("bluesky", [
        r"bsky\.app",
        r"bluesky\.app",
    ]),
]


# Action patterns within platforms
ACTION_PATTERNS = {
    "twitter": {
        "post": [r"twitter\.com/\w+/status/\d+", r"x\.com/\w+/status/\d+"],
        "profile": [r"twitter\.com/\w+/?$", r"x\.com/\w+/?$"],
        "list": [r"twitter\.com/\w+/lists/\d+"],
    },
    "threads": {
        "post": [r"threads\.net/@[\w.]+/post/[\w-]+"],
        "profile": [r"threads\.net/@[\w.]+/?$"],
    },
    "instagram": {
        "post": [r"instagram\.com/p/[\w-]+", r"instagram\.com/reel/[\w-]+", r"instagram\.com/tv/[\w-]+"],
        "profile": [r"instagram\.com/[\w.]+/?$"],
        "tag": [r"instagram\.com/explore/tags/[\w-]+"],
    },
    "tiktok": {
        "post": [r"tiktok\.com/@[\w.]+/video/\d+"],
        "profile": [r"tiktok\.com/@[\w.]+/?$"],
        "tag": [r"tiktok\.com/tag/[\w-]+"],
    },
    "youtube": {
        "video": [r"youtube\.com/watch\?v=", r"youtu\.be/"],
        "channel": [r"youtube\.com/channel/", r"youtube\.com/c/", r"youtube\.com/@"],
        "playlist": [r"youtube\.com/playlist\?list="],
        "shorts": [r"youtube\.com/shorts/"],
    },
    "reddit": {
        "post": [r"reddit\.com/r/\w+/comments/\w+/"],
        "subreddit": [r"reddit\.com/r/\w+/?$"],
        "profile": [r"reddit\.com/user/\w+", r"reddit\.com/u/\w+"],
        "search": [r"reddit\.com/search"],
    },
    "linkedin": {
        "post": [r"linkedin\.com/feed/update/"],
        "profile": [r"linkedin\.com/in/"],
        "company": [r"linkedin\.com/company/"],
    },
    "facebook": {
        "post": [r"facebook\.com/\w+/posts/", r"facebook\.com/permalink\.php"],
        "profile": [r"facebook\.com/\w+/?$"],
        "page": [r"facebook\.com/pages/"],
    },
    "telegram": {
        "channel": [r"t\.me/[\w]+/?$", r"t\.me/s/[\w]+"],
        "post": [r"t\.me/[\w]+/\d+"],
    },
    "pinterest": {
        "pin": [r"pinterest\.com/pin/\d+"],
        "profile": [r"pinterest\.com/[\w.]+/?$"],
        "board": [r"pinterest\.com/[\w.]+/[\w-]+"],
    },
    "hackernews": {
        "post": [r"news\.ycombinator\.com/item\?id=\d+"],
        "user": [r"news\.ycombinator\.com/user?id=\w+"],
    },
    "bluesky": {
        "post": [r"bsky\.app/profile/[\w.]+/post/[\w-]+"],
        "profile": [r"bsky\.app/profile/[\w.]+"],
    },
}


class SocialRouter:
    """Routes URLs to appropriate social media scraper."""
    
    def __init__(self) -> None:
        self._compile_patterns()
    
    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns."""
        self._platform_patterns = [
            (platform, [re.compile(p, re.IGNORECASE) for p in patterns])
            for platform, patterns in PLATFORM_PATTERNS
        ]
        self._action_patterns = {
            platform: {
                action: [re.compile(p, re.IGNORECASE) for p in patterns]
                for action, patterns in actions.items()
            }
            for platform, actions in ACTION_PATTERNS.items()
        }
    
    def detect_platform(self, url: str) -> Optional[str]:
        """Detect platform from URL."""
        for platform, patterns in self._platform_patterns:
            for pattern in patterns:
                if pattern.search(url):
                    return platform
        return None
    
    def detect_action(self, platform: str, url: str) -> Optional[str]:
        """Detect action type from URL for a given platform."""
        if platform not in self._action_patterns:
            return None
        
        for action, patterns in self._action_patterns[platform].items():
            for pattern in patterns:
                if pattern.search(url):
                    return action
        return None
    
    def detect(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """Detect both platform and action from URL."""
        platform = self.detect_platform(url)
        if not platform:
            return None, None
        action = self.detect_action(platform, url)
        return platform, action
    
    def get_scraper(self, url: str) -> Optional[BaseSocialScraper]:
        """Get scraper instance for URL."""
        platform = self.detect_platform(url)
        if not platform:
            return None
        # Note: Instance creation requires client and settings
        # This returns the class, caller must instantiate
        return registry.get(platform)
    
    def extract_identifier(self, platform: str, url: str) -> Optional[str]:
        """Extract username or post ID from URL."""
        scrapers = registry._scrapers
        if platform in scrapers:
            return scrapers[platform].extract_identifier_class(url)
        return None


# Singleton router
router = SocialRouter()


def auto_detect(url: str) -> Optional[str]:
    """Convenience function to detect platform from URL."""
    return router.detect_platform(url)


def detect_action(platform: str, url: str) -> Optional[str]:
    """Convenience function to detect action from URL."""
    return router.detect_action(platform, url)


def parse_social_url(url: str) -> Dict[str, Any]:
    """Parse social URL and return structured info."""
    platform, action = router.detect(url)
    identifier = None
    if platform:
        identifier = router.extract_identifier(platform, url)
    
    return {
        "platform": platform,
        "action": action,
        "identifier": identifier,
        "url": url,
    }