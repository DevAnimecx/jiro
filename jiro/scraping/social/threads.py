"""Threads scraper using public GraphQL endpoints."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.threads")


class ThreadsScraper(BaseSocialScraper):
    """Threads scraper using public GraphQL API (no auth required for public data)."""
    
    platform = "threads"
    url_patterns = [
        "threads.net",
    ]
    supported_actions = ["post", "profile", "replies", "search", "timeline"]
    rate_limit_rpm = 60
    requires_auth = False
    
    GRAPHQL_URL = "https://www.threads.net/api/graphql"
    
    # Default query hashes (used as fallback)
    DEFAULT_QUERY_HASHES = {
        "post": "5672115639492503",
        "profile": "17851374694183129",
        "user_posts": "17849115430193904",
    }
    
    _cached_hashes: Dict[str, str] = {}
    
    @property
    def QUERY_HASHES(self) -> Dict[str, str]:
        """Return query hashes, fetching dynamic ones if not cached."""
        if not self._cached_hashes:
            self._cached_hashes = dict(self.DEFAULT_QUERY_HASHES)
        return self._cached_hashes

    async def _ensure_query_hashes(self) -> None:
        """Fetch latest query hashes from Threads page if cache is empty."""
        if self._cached_hashes:
            return
        try:
            html = await self._fetch_html("https://www.threads.net/", engine=self.platform)
            found = self._parse_query_hashes_from_page(html)
            if found:
                self._cached_hashes.update(found)
                log.info("Extracted %d dynamic query hashes from Threads", len(found))
        except Exception as exc:
            log.debug("Could not fetch dynamic query hashes: %s", exc)

    @staticmethod
    def _parse_query_hashes_from_page(html: str) -> Dict[str, str]:
        """Extract query hashes from Threads page source."""
        hashes = {}
        patterns = re.findall(r'queryId["\s:=]+([a-f0-9]{16,})', html, re.IGNORECASE)
        seen = set()
        for h in patterns:
            h = h.lower()
            if h not in seen and 16 <= len(h) <= 32:
                seen.add(h)
                hashes[f"dynamic_{len(hashes)}"] = h
        return hashes
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a Threads post."""
        post_id = self._extract_post_id(url)
        if not post_id:
            raise ValueError("Could not extract post ID from URL")
        
        return await self._get_post(post_id, url)
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a Threads profile."""
        # Remove @ if present
        username = username.lstrip("@")
        
        return await self._get_profile(username)
    
    async def get_user_posts(self, username: str, limit: int = 25) -> List[SocialPost]:
        """Get posts from a user."""
        username = username.lstrip("@")
        await self._ensure_query_hashes()
        
        variables = {
            "username": username,
            "first": limit,
        }
        
        data = await self._graphql_request(self.QUERY_HASHES["user_posts"], variables)
        
        user = data.get("data", {}).get("user", {})
        posts = user.get("threads", {}).get("edges", [])
        
        results = []
        for edge in posts[:limit]:
            post_data = edge.get("node", {})
            if post_data:
                post_url = f"https://threads.net/@{username}/post/{post_data.get('code', '')}"
                results.append(self._normalize_post(post_data, post_url))
        
        return results
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search Threads posts (public search)."""
        await self._ensure_query_hashes()
        
        variables = {
            "query": query,
            "first": limit,
        }
        
        try:
            data = await self._graphql_request(self.QUERY_HASHES.get("search", self.QUERY_HASHES.get("user_posts")), variables)
            posts = data.get("data", {}).get("search_results", {}).get("edges", [])
            
            results = []
            for edge in posts[:limit]:
                post_data = edge.get("node", {})
                if post_data:
                    post_url = f"https://threads.net/@{post_data.get('owner', {}).get('username', '')}/post/{post_data.get('code', '')}"
                    results.append(self._normalize_post(post_data, post_url))
            
            return results
        except Exception as exc:
            log.debug("Threads search failed: %s", exc)
            return []
    
    # Search query hash (derived from profile hash as fallback)
    # Threads search uses same query hash as user posts in many versions
    
    async def _get_post(self, post_id: str, url: str) -> SocialPost:
        """Get a single post by ID."""
        await self._ensure_query_hashes()
        variables = {"shortcode": post_id}
        data = await self._graphql_request(self.QUERY_HASHES["post"], variables)
        
        post = data.get("data", {}).get("xdt_shortcode_media", {})
        if not post:
            raise ValueError("Post not found")
        
        return self._normalize_post(post, url)
    
    async def _get_profile(self, username: str) -> SocialProfile:
        """Get profile by username."""
        await self._ensure_query_hashes()
        variables = {"username": username}
        data = await self._graphql_request(self.QUERY_HASHES["profile"], variables)
        
        user = data.get("data", {}).get("user", {})
        if not user:
            raise ValueError("Profile not found")
        
        return self._normalize_profile(user, username)
    
    async def _graphql_request(self, query_hash: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Make GraphQL request to Threads API."""
        params = {
            "query_hash": query_hash,
            "variables": json.dumps(variables),
        }
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-IG-App-ID": "936619743392459",  # Threads app ID
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        
        text, resp = await self.client.post(self.GRAPHQL_URL, engine=self.platform, data=params, extra_headers=headers)
        if resp.status_code == 429:
            raise self.RateLimitError("threads")
        resp.raise_for_status()
        
        return resp.json()
    
    def _normalize_post(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize Threads post."""
        author_data = data.get("user", {}) or data.get("owner", {})
        
        author = {
            "username": author_data.get("username", ""),
            "display_name": author_data.get("full_name", ""),
            "avatar": author_data.get("profile_pic_url", ""),
            "verified": author_data.get("is_verified", False),
            "followers": author_data.get("follower_count"),
        }
        
        engagement = {
            "likes": data.get("like_count"),
            "replies": data.get("comment_count"),
            "reposts": data.get("repost_count", 0),
            "quotes": data.get("quote_count", 0),
        }
        
        media = []
        if data.get("__typename") == "XDTGraphVideo":
            media.append({
                "type": "video",
                "url": data.get("video_url", ""),
                "thumbnail": data.get("display_url", ""),
                "duration": data.get("video_duration"),
            })
        elif data.get("display_url"):
            media.append({
                "type": "image",
                "url": data.get("display_url", ""),
                "thumbnail": data.get("thumbnail_url", ""),
            })
        
        # Handle carousel
        for child in data.get("edge_sidecar_to_children", {}).get("edges", []):
            child_node = child.get("node", {})
            if child_node.get("display_url"):
                media.append({
                    "type": "image",
                    "url": child_node.get("display_url", ""),
                    "thumbnail": child_node.get("thumbnail_url", ""),
                })
        
        return build_post(
            platform="threads",
            post_type="post",
            url=url,
            id=data.get("code", "") or data.get("id", ""),
            text=data.get("caption", {}).get("text", "") if data.get("caption") else "",
            timestamp=data.get("taken_at"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile(self, data: Dict[str, Any], username: str) -> SocialProfile:
        """Normalize Threads profile."""
        author = {
            "username": data.get("username", username),
            "display_name": data.get("full_name", ""),
            "avatar": data.get("profile_pic_url", ""),
            "verified": data.get("is_verified", False),
            "followers": data.get("follower_count"),
            "following": data.get("following_count"),
            "posts_count": data.get("media_count"),
            "bio": data.get("biography", ""),
            "location": "",
        }
        
        return build_profile(
            platform="threads",
            username=username,
            url=f"https://threads.net/@{username}",
            profile_data={"author": author, "engagement": {}, "id": data.get("id")},
        )
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract post ID or username from Threads URL."""
        # Post: threads.net/@username/post/POST_ID
        match = re.search(r"@([^/]+)/post/([^/]+)", url)
        if match:
            return match.group(2)
        
        # Profile: threads.net/@username
        match = re.search(r"threads\.net/@([^/?]+)", url)
        if match:
            return match.group(1)
        
        return None
    
    async def scrape_timeline(self, username: str, limit: int = 20) -> List[SocialPost]:
        """Get posts from a user's timeline (alias for get_user_posts)."""
        return await self.get_user_posts(username, limit)
    
    @classmethod
    def extract_identifier_class(cls, url: str) -> Optional[str]:
        """Class method to extract identifier without instantiation."""
        # Post: threads.net/@username/post/POST_ID
        match = re.search(r"@([^/]+)/post/([^/]+)", url)
        if match:
            return match.group(2)
        
        # Profile: threads.net/@username
        match = re.search(r"threads\.net/@([^/?]+)", url)
        if match:
            return match.group(1)
        
        return None


# Register the scraper
registry.register(ThreadsScraper)