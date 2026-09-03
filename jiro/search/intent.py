"""Intent classifier for smart routing (/v1/smart endpoint).

Lightweight rule-based + pattern classifier (<1ms, no LLM needed).
Routes natural language queries to appropriate action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.search.intent")


class IntentType(Enum):
    """Types of intents the classifier can detect."""
    SCRAPE = "scrape"                    # URL in input
    SOCIAL_PROFILE = "social_profile"    # @username
    SOCIAL_REDDIT = "social_reddit"      # reddit, r/
    SOCIAL_TWITTER = "social_twitter"    # twitter, x.com, tweets
    SOCIAL_INSTAGRAM = "social_instagram" # instagram, ig
    SOCIAL_TIKTOK = "social_tiktok"      # tiktok
    SOCIAL_YOUTUBE = "social_youtube"    # youtube, yt
    SOCIAL_TELEGRAM = "social_telegram"  # telegram, t.me
    SOCIAL_THREADS = "social_threads"    # threads
    SOCIAL_BLUESKY = "social_bluesky"    # bluesky
    SOCIAL_LINKEDIN = "social_linkedin"  # linkedin
    SOCIAL_FACEBOOK = "social_facebook"  # facebook
    SOCIAL_PINTEREST = "social_pinterest" # pinterest
    SOCIAL_HACKERNEWS = "social_hackernews" # hackernews
    RESEARCH_ANSWER = "research_answer"   # what is, how to, best, compare
    NEWS_SEARCH = "news_search"           # news, today, latest, breaking
    TRENDING = "trending"                 # trending, viral
    MONITOR_SETUP = "monitor_setup"       # monitor, track, alert
    SEARCH = "search"                     # default
    
    
# Map intent to platform for social intents
INTENT_TO_PLATFORM = {
    IntentType.SOCIAL_REDDIT: "reddit",
    IntentType.SOCIAL_TWITTER: "twitter",
    IntentType.SOCIAL_INSTAGRAM: "instagram",
    IntentType.SOCIAL_TIKTOK: "tiktok",
    IntentType.SOCIAL_YOUTUBE: "youtube",
    IntentType.SOCIAL_TELEGRAM: "telegram",
    IntentType.SOCIAL_THREADS: "threads",
    IntentType.SOCIAL_BLUESKY: "bluesky",
    IntentType.SOCIAL_LINKEDIN: "linkedin",
    IntentType.SOCIAL_FACEBOOK: "facebook",
    IntentType.SOCIAL_PINTEREST: "pinterest",
    IntentType.SOCIAL_HACKERNEWS: "hackernews",
}


@dataclass
class IntentResult:
    """Result of intent classification."""
    intent: IntentType
    confidence: float
    platform: Optional[str] = None
    action: Optional[str] = None
    extracted: Optional[Dict[str, Any]] = None


class IntentClassifier:
    """Rule-based intent classifier for natural language queries."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._compile_patterns()
    
    def _compile_patterns(self) -> None:
        """Pre-compile all regex patterns."""
        
        # URL pattern
        self.url_pattern = re.compile(
            r'https?://[^\s]+|www\.[^\s]+\.[^\s]+|[a-z0-9-]+\.[a-z]{2,}(?:/[^\s]*)?',
            re.IGNORECASE
        )
        
        # Social handle patterns
        self.twitter_handle = re.compile(r'@(\w{1,15})')
        self.mention_pattern = re.compile(r'@(\w+)')
        
        # Intent patterns (ordered by priority)
        self.patterns: List[Tuple[IntentType, List[re.Pattern], float]] = [
            # URL detection (highest priority for scrape)
            (IntentType.SCRAPE, [self.url_pattern], 1.0),
            
            # Social platform specific (by @handle or platform name)
            (IntentType.SOCIAL_TWITTER, [
                re.compile(r'\b(twitter|x\.com|tweet|tweets)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_REDDIT, [
                re.compile(r'\b(reddit|r/\w+)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_INSTAGRAM, [
                re.compile(r'\b(instagram|ig)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_TIKTOK, [
                re.compile(r'\b(tiktok)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_YOUTUBE, [
                re.compile(r'\b(youtube|yt\b)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_TELEGRAM, [
                re.compile(r'\b(telegram|t\.me)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_THREADS, [
                re.compile(r'\b(threads)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_BLUESKY, [
                re.compile(r'\b(bluesky|bsky)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_LINKEDIN, [
                re.compile(r'\b(linkedin)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_FACEBOOK, [
                re.compile(r'\b(facebook|fb)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_PINTEREST, [
                re.compile(r'\b(pinterest)\b', re.IGNORECASE),
            ], 0.95),
            (IntentType.SOCIAL_HACKERNEWS, [
                re.compile(r'\b(hackernews|hacker news|hn)\b', re.IGNORECASE),
            ], 0.95),
            
            # Research/Answer patterns
            (IntentType.RESEARCH_ANSWER, [
                re.compile(r'^(what|who|where|when|why|how)\s', re.IGNORECASE),
                re.compile(r'\b(what is|who is|how to|how do|best|top|compare|vs|versus|review|guide|tutorial)\b', re.IGNORECASE),
            ], 0.9),
            
            # News/Freshness patterns
            (IntentType.NEWS_SEARCH, [
                re.compile(r'\b(news|today|latest|breaking|just in|recent|this week|this month)\b', re.IGNORECASE),
            ], 0.9),
            
            # Trending patterns
            (IntentType.TRENDING, [
                re.compile(r'\b(trending|viral|popular|hot right now)\b', re.IGNORECASE),
            ], 0.85),
            
            # Monitor/Alert patterns
            (IntentType.MONITOR_SETUP, [
                re.compile(r'\b(monitor|track|alert|notify|watch)\b', re.IGNORECASE),
            ], 0.85),
        ]
        
        # Action patterns within platforms
        self.action_patterns: Dict[IntentType, Dict[str, List[re.Pattern]]] = {
            IntentType.SOCIAL_TWITTER: {
                "profile": [re.compile(r'(profile|user|account)')],
                "timeline": [re.compile(r'(timeline|tweets|feed|posts)')],
                "search": [re.compile(r'(search|find|look for)')],
            },
            IntentType.SOCIAL_REDDIT: {
                "subreddit": [re.compile(r'(subreddit|r/|community)')],
                "post": [re.compile(r'(post|thread|comment)')],
                "search": [re.compile(r'(search|find)')],
            },
            IntentType.SOCIAL_YOUTUBE: {
                "video": [re.compile(r'(video|watch)')],
                "channel": [re.compile(r'(channel|creator)')],
                "search": [re.compile(r'(search|find)')],
            },
        }
    
    def classify(self, query: str) -> IntentResult:
        """Classify the intent of a query."""
        query_lower = query.lower().strip()
        
        # Check for @username (social profile)
        handle_match = self.twitter_handle.search(query)
        if handle_match:
            # Check if it's a platform-specific handle
            platform = self._detect_platform_from_context(query_lower)
            if platform:
                intent = self._platform_to_social_intent(platform)
                return IntentResult(
                    intent=intent,
                    confidence=0.95,
                    platform=platform,
                    action="profile",
                    extracted={"username": handle_match.group(1)},
                )
            # Default to Twitter for bare @handle
            return IntentResult(
                intent=IntentType.SOCIAL_PROFILE,
                confidence=0.8,
                platform="twitter",
                action="profile",
                extracted={"username": handle_match.group(1)},
            )
        
        # Check URL
        url_match = self.url_pattern.search(query)
        if url_match:
            url = url_match.group(0)
            platform = self._detect_platform_from_url(url)
            if platform:
                action = self._detect_action_from_url(platform, url)
                return IntentResult(
                    intent=IntentType.SCRAPE,
                    confidence=1.0,
                    platform=platform,
                    action=action or "post",
                    extracted={"url": url},
                )
            # Generic URL
            return IntentResult(
                intent=IntentType.SCRAPE,
                confidence=1.0,
                action="post",
                extracted={"url": url},
            )
        
        # Check patterns in order
        for intent_type, patterns, confidence in self.patterns:
            for pattern in patterns:
                if pattern.search(query_lower):
                    platform = None
                    action = None
                    extracted = None
                    
                    if intent_type in INTENT_TO_PLATFORM:
                        platform = INTENT_TO_PLATFORM[intent_type]
                        action = "post"
                    elif intent_type == IntentType.NEWS_SEARCH:
                        platform = "google"  # Best for news
                    elif intent_type == IntentType.RESEARCH_ANSWER:
                        platform = None  # Will use AI search
                    
                    return IntentResult(
                        intent=intent_type,
                        confidence=confidence,
                        platform=platform,
                        action=action,
                        extracted=extracted,
                    )
        
        # Default to search
        return IntentResult(
            intent=IntentType.SEARCH,
            confidence=0.5,
        )
    
    def _detect_platform_from_context(self, query: str) -> Optional[str]:
        """Detect platform from context clues."""
        for platform_name in ["twitter", "x.com", "instagram", "tiktok", "youtube", 
                              "reddit", "linkedin", "facebook", "telegram", 
                              "threads", "bluesky", "pinterest", "hackernews"]:
            if platform_name in query:
                return platform_name
        return None
    
    def _detect_platform_from_url(self, url: str) -> Optional[str]:
        """Detect platform from URL."""
        url_lower = url.lower()
        
        if "twitter.com" in url_lower or "x.com" in url_lower:
            return "twitter"
        elif "reddit.com" in url_lower:
            return "reddit"
        elif "youtube.com" in url_lower or "youtu.be" in url_lower:
            return "youtube"
        elif "instagram.com" in url_lower or "instagr.am" in url_lower:
            return "instagram"
        elif "tiktok.com" in url_lower:
            return "tiktok"
        elif "linkedin.com" in url_lower:
            return "linkedin"
        elif "facebook.com" in url_lower or "fb.com" in url_lower or "fb.watch" in url_lower:
            return "facebook"
        elif "t.me" in url_lower or "telegram.me" in url_lower:
            return "telegram"
        elif "threads.net" in url_lower:
            return "threads"
        elif "bsky.app" in url_lower or "bluesky.app" in url_lower:
            return "bluesky"
        elif "pinterest.com" in url_lower or "pin.it" in url_lower:
            return "pinterest"
        elif "news.ycombinator.com" in url_lower or "hackernews.com" in url_lower:
            return "hackernews"
        return None
    
    def _detect_action_from_url(self, platform: str, url: str) -> Optional[str]:
        """Detect action type from URL for a platform."""
        if platform not in self.action_patterns:
            return "post"
        
        for action, patterns in self.action_patterns[platform].items():
            for pattern in patterns:
                if pattern.search(url):
                    return action
        
        # Default actions per platform
        defaults = {
            "twitter": "post",
            "reddit": "post",
            "youtube": "video",
            "instagram": "post",
            "tiktok": "video",
        }
        return defaults.get(platform, "post")
    
    def _platform_to_social_intent(self, platform: str) -> IntentType:
        """Map platform name to social intent type."""
        mapping = {
            "twitter": IntentType.SOCIAL_TWITTER,
            "x.com": IntentType.SOCIAL_TWITTER,
            "reddit": IntentType.SOCIAL_REDDIT,
            "youtube": IntentType.SOCIAL_YOUTUBE,
            "instagram": IntentType.SOCIAL_INSTAGRAM,
            "ig": IntentType.SOCIAL_INSTAGRAM,
            "tiktok": IntentType.SOCIAL_TIKTOK,
            "linkedin": IntentType.SOCIAL_LINKEDIN,
            "facebook": IntentType.SOCIAL_FACEBOOK,
            "fb": IntentType.SOCIAL_FACEBOOK,
            "telegram": IntentType.SOCIAL_TELEGRAM,
            "t.me": IntentType.SOCIAL_TELEGRAM,
            "threads": IntentType.SOCIAL_THREADS,
            "bluesky": IntentType.SOCIAL_BLUESKY,
            "bsky": IntentType.SOCIAL_BLUESKY,
            "pinterest": IntentType.SOCIAL_PINTEREST,
            "hackernews": IntentType.SOCIAL_HACKERNEWS,
            "hn": IntentType.SOCIAL_HACKERNEWS,
        }
        return mapping.get(platform.lower(), IntentType.SOCIAL_PROFILE)


# Convenience function
_classifier: Optional[IntentClassifier] = None


def get_classifier(settings: Settings) -> IntentClassifier:
    """Get or create the global intent classifier."""
    global _classifier
    if _classifier is None:
        _classifier = IntentClassifier(settings)
    return _classifier


def classify_intent(query: str, settings: Settings) -> IntentResult:
    """Convenience function to classify intent."""
    classifier = get_classifier(settings)
    return classifier.classify(query)