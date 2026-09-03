"""Unified output normalizer for social media data."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from jiro.log import get_logger

log = get_logger("jiro.scraping.social.normalizer")


class SocialNormalizer:
    """Normalizes social media data to unified schema."""
    
    @staticmethod
    def normalize_timestamp(ts: Any, platform: str = "") -> str:
        """Normalize various timestamp formats to ISO 8601 UTC."""
        if not ts:
            return datetime.utcnow().isoformat() + "Z"
        
        # Already a string in ISO format
        if isinstance(ts, str):
            ts = ts.strip()
            # Try to parse common formats
            formats = [
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%a %b %d %H:%M:%S %z %Y",  # Twitter format
                "%a, %d %b %Y %H:%M:%S %Z",  # RSS format
            ]
            for fmt in formats:
                try:
                    dt = datetime.strptime(ts, fmt)
                    return dt.isoformat() + "Z"
                except ValueError:
                    continue
            
            # If it looks like ISO already, return as-is
            if "T" in ts and ("Z" in ts or "+" in ts or ts.count(":") >= 2):
                return ts
            
            # Unix timestamp string
            if ts.isdigit():
                return datetime.utcfromtimestamp(int(ts)).isoformat() + "Z"
            
            return datetime.utcnow().isoformat() + "Z"
        
        # Unix timestamp (int/float)
        if isinstance(ts, (int, float)):
            # Handle milliseconds
            if ts > 1e12:
                ts = ts / 1000
            return datetime.utcfromtimestamp(ts).isoformat() + "Z"
        
        return datetime.utcnow().isoformat() + "Z"
    
    @staticmethod
    def normalize_number(val: Any) -> Optional[int]:
        """Normalize various number formats to int."""
        if val is None:
            return None
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(val)
        if isinstance(val, str):
            # Handle abbreviated numbers like "1.2K", "3.5M"
            val = val.strip().upper()
            multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
            for suffix, mult in multipliers.items():
                if val.endswith(suffix):
                    try:
                        return int(float(val[:-1]) * mult)
                    except ValueError:
                        pass
            # Remove commas
            val = val.replace(",", "")
            try:
                return int(float(val))
            except ValueError:
                return None
        return None
    
    @staticmethod
    def extract_hashtags(text: str) -> List[str]:
        """Extract hashtags from text."""
        if not text:
            return []
        return [tag.lower() for tag in re.findall(r"#(\w+)", text)]
    
    @staticmethod
    def extract_mentions(text: str) -> List[str]:
        """Extract @mentions from text."""
        if not text:
            return []
        return [mention.lower() for mention in re.findall(r"@(\w+)", text)]
    
    @staticmethod
    def clean_text(text: str) -> str:
        """Clean and normalize text content."""
        if not text:
            return ""
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text)
        # Remove zero-width chars
        text = text.replace("\u200b", "").replace("\ufeff", "")
        return text.strip()
    
    @staticmethod
    def normalize_url(url: str, platform: str = "") -> str:
        """Normalize URL to canonical form."""
        if not url:
            return ""
        url = url.strip()
        # Remove tracking parameters
        tracking_params = [
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "ref", "ref_src", "ref_url",
            "share_id", "shared_from", "source",
        ]
        parsed = urlparse(url)
        if parsed.query:
            params = []
            for param in parsed.query.split("&"):
                key = param.split("=")[0].lower()
                if key not in tracking_params:
                    params.append(param)
            query = "&".join(params) if params else ""
        else:
            query = ""
        
        normalized = parsed._replace(query=query).geturl()
        # Remove trailing slash for consistency (except for homepage)
        if normalized.endswith("/") and parsed.path != "/":
            normalized = normalized[:-1]
        return normalized
    
    @staticmethod
    def build_normalized_post(
        platform: str,
        post_type: str,
        url: str,
        id: str,
        text: str,
        timestamp: Any,
        author: Dict[str, Any],
        engagement: Dict[str, Any],
        media: List[Dict[str, Any]] = None,
        **extra
    ) -> Dict[str, Any]:
        """Build normalized post dictionary."""
        return {
            "platform": platform,
            "type": post_type,
            "url": SocialNormalizer.normalize_url(url, platform),
            "data": {
                "id": str(id),
                "text": SocialNormalizer.clean_text(text),
                "timestamp": SocialNormalizer.normalize_timestamp(timestamp, platform),
                "author": {
                    "username": author.get("username", ""),
                    "display_name": author.get("display_name", ""),
                    "avatar": SocialNormalizer.normalize_url(author.get("avatar", ""), platform),
                    "verified": bool(author.get("verified", False)),
                    "followers": SocialNormalizer.normalize_number(author.get("followers")),
                    "profile_url": SocialNormalizer.normalize_url(author.get("profile_url", ""), platform),
                    "bio": SocialNormalizer.clean_text(author.get("bio", "")),
                    "location": SocialNormalizer.clean_text(author.get("location", "")),
                    "joined_date": SocialNormalizer.normalize_timestamp(author.get("joined_date"), platform) if author.get("joined_date") else "",
                },
                "engagement": {
                    "likes": SocialNormalizer.normalize_number(engagement.get("likes")),
                    "comments": SocialNormalizer.normalize_number(engagement.get("comments")),
                    "shares": SocialNormalizer.normalize_number(engagement.get("shares")),
                    "views": SocialNormalizer.normalize_number(engagement.get("views")),
                    "saves": SocialNormalizer.normalize_number(engagement.get("saves")),
                    "retweets": SocialNormalizer.normalize_number(engagement.get("retweets")),
                    "replies": SocialNormalizer.normalize_number(engagement.get("replies")),
                    "reposts": SocialNormalizer.normalize_number(engagement.get("reposts")),
                    "quotes": SocialNormalizer.normalize_number(engagement.get("quotes")),
                    "bookmarks": SocialNormalizer.normalize_number(engagement.get("bookmarks")),
                },
                "media": [
                    {
                        "type": m.get("type", "image"),
                        "url": SocialNormalizer.normalize_url(m.get("url", ""), platform),
                        "thumbnail": SocialNormalizer.normalize_url(m.get("thumbnail", ""), platform),
                        "width": m.get("width"),
                        "height": m.get("height"),
                        "duration": m.get("duration"),
                        "alt_text": SocialNormalizer.clean_text(m.get("alt_text", "")),
                    }
                    for m in (media or [])
                ],
                "hashtags": SocialNormalizer.extract_hashtags(text),
                "mentions": SocialNormalizer.extract_mentions(text),
            },
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "credits_charged": extra.get("credits_charged", 2),
        }
    
    @staticmethod
    def build_normalized_profile(
        platform: str,
        username: str,
        url: str,
        profile_data: Dict[str, Any],
        **extra
    ) -> Dict[str, Any]:
        """Build normalized profile dictionary."""
        author = profile_data.get("author", profile_data)
        engagement = profile_data.get("engagement", {})
        
        return {
            "platform": platform,
            "type": "profile",
            "url": SocialNormalizer.normalize_url(url, platform),
            "data": {
                "id": profile_data.get("id", username),
                "username": username,
                "display_name": author.get("display_name", ""),
                "avatar": SocialNormalizer.normalize_url(author.get("avatar", ""), platform),
                "verified": bool(author.get("verified", False)),
                "followers": SocialNormalizer.normalize_number(author.get("followers")),
                "following": SocialNormalizer.normalize_number(author.get("following")),
                "posts_count": SocialNormalizer.normalize_number(author.get("posts_count") or author.get("statuses_count")),
                "bio": SocialNormalizer.clean_text(author.get("bio", "")),
                "location": SocialNormalizer.clean_text(author.get("location", "")),
                "website": SocialNormalizer.normalize_url(author.get("website", ""), platform),
                "joined_date": SocialNormalizer.normalize_timestamp(author.get("joined_date"), platform) if author.get("joined_date") else "",
                "profile_url": SocialNormalizer.normalize_url(url, platform),
            },
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "credits_charged": extra.get("credits_charged", 3),
        }
    
    @staticmethod
    def build_normalized_search_result(
        platform: str,
        posts: List[Dict[str, Any]],
        query: str,
        **extra
    ) -> Dict[str, Any]:
        """Build normalized search results."""
        return {
            "platform": platform,
            "type": "search",
            "query": query,
            "results": posts,
            "total": len(posts),
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "credits_charged": extra.get("credits_charged", 8),
        }


# Convenience functions
normalize_timestamp = SocialNormalizer.normalize_timestamp
normalize_number = SocialNormalizer.normalize_number
normalize_url = SocialNormalizer.normalize_url
clean_text = SocialNormalizer.clean_text
extract_hashtags = SocialNormalizer.extract_hashtags
extract_mentions = SocialNormalizer.extract_mentions
build_post = SocialNormalizer.build_normalized_post
build_profile = SocialNormalizer.build_normalized_profile
build_search = SocialNormalizer.build_normalized_search_result