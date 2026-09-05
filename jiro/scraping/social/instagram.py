"""Instagram scraper using public GraphQL endpoints (requires sessionid for full access)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.instagram")


class InstagramScraper(BaseSocialScraper):
    """Instagram scraper using public GraphQL endpoints (sessionid optional for public posts)."""
    
    platform = "instagram"
    url_patterns = [
        "instagram.com",
        "instagr.am",
    ]
    supported_actions = ["post", "profile", "reel", "story", "tag", "highlights"]
    rate_limit_rpm = 30
    requires_auth = True  # Better with sessionid
    
    GRAPHQL_URL = "https://www.instagram.com/graphql/query/"
    FEED_URL = "https://www.instagram.com/api/v1/feed/user/"
    
    # Default query hashes (used as fallback)
    DEFAULT_QUERY_HASHES = {
        "post": "b3055c01b4b222b8a47db1c2817f37ba",
        "profile": "7c16654f22e81d40e2b2c1a7c8e9f0a1",
        "user_posts": "69cba40317214236af40e7efa697781d",
        "reels": "5a3f3b5c3e4c3b5a3f3b5c3e4c3b5a3f",
    }
    
    _cached_hashes: Dict[str, str] = {}
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        self.sessionid = settings.social.get("instagram", {}).get("sessionid", "") if settings else ""
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for Instagram requests."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.sessionid:
            headers["Cookie"] = f"sessionid={self.sessionid}"
        return headers

    @property
    def QUERY_HASHES(self) -> Dict[str, str]:
        """Return query hashes, fetching dynamic ones if not cached."""
        if not self._cached_hashes:
            self._cached_hashes = dict(self.DEFAULT_QUERY_HASHES)
        return self._cached_hashes

    async def _ensure_query_hashes(self) -> None:
        """Fetch latest query hashes from Instagram if cache is empty."""
        if self._cached_hashes:
            return
        try:
            html = await self._fetch_html("https://www.instagram.com/", engine=self.platform)
            found = self._parse_query_hashes_from_page(html)
            if found:
                self._cached_hashes.update(found)
                log.info("Extracted %d dynamic query hashes from Instagram", len(found))
        except Exception as exc:
            log.debug("Could not fetch dynamic query hashes: %s", exc)

    @staticmethod
    def _parse_query_hashes_from_page(html: str) -> Dict[str, str]:
        """Extract query hashes from Instagram page source."""
        hashes = {}
        patterns = re.findall(r'queryId["\s:=]+([a-f0-9]{32})', html, re.IGNORECASE)
        seen = set()
        for h in patterns:
            h = h.lower()
            if h not in seen and len(h) == 32:
                seen.add(h)
                hashes[f"dynamic_{len(hashes)}"] = h
        return hashes
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape an Instagram post/reel."""
        shortcode = self._extract_shortcode(url)
        if not shortcode:
            raise ValueError("Could not extract shortcode from URL")
        
        return await self._get_post(shortcode, url)
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape an Instagram profile."""
        username = username.lstrip("@")
        
        return await self._get_profile(username)
    
    async def get_user_posts(self, username: str, limit: int = 25) -> List[SocialPost]:
        """Get posts from a user."""
        username = username.lstrip("@")
        
        # Get user ID first
        profile = await self._get_profile(username)
        user_id = profile.data.get("id")
        
        if not user_id:
            raise ValueError("Could not get user ID")
        
        return await self._get_user_feed(user_id, limit)
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search Instagram (limited without auth)."""
        # Public web search
        url = f"https://www.instagram.com/web/search/topsearch/"
        params = {"query": query, "context": "blended"}
        headers = self._get_headers()
        
        try:
            text, resp = await self.client.get(url, engine=self.platform, params=params, extra_headers=headers)
            data = resp.json()
            
            results = []
            for user in data.get("users", [])[:limit]:
                user_info = user.get("user", {})
                if user_info.get("username"):
                    try:
                        posts = await self.get_user_posts(user_info["username"], 1)
                        results.extend(posts)
                    except Exception:
                        log.debug("silenced fallback", exc_info=True)
                        continue
            
            return results
        except Exception as exc:
            log.debug("Instagram search failed: %s", exc)
            return []
    
    async def _get_post(self, shortcode: str, url: str) -> SocialPost:
        """Get a single post by shortcode."""
        await self._ensure_query_hashes()
        variables = {"shortcode": shortcode}
        data = await self._graphql_request(self.QUERY_HASHES["post"], variables)
        
        post = data.get("data", {}).get("xdt_shortcode_media", {})
        if not post:
            raise ValueError("Post not found")
        
        return self._normalize_post(post, url)
    
    async def _get_profile(self, username: str) -> SocialProfile:
        """Get profile by username."""
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        headers = self._get_headers()
        
        text, resp = await self.client.get(url, engine=self.platform, extra_headers=headers)
        if resp.status_code == 404:
            raise ValueError("Profile not found")
        resp.raise_for_status()
        
        data = resp.json()
        user = data.get("data", {}).get("user", {})
        
        if not user:
            raise ValueError("Profile not found")
        
        return self._normalize_profile(user, username)
    
    async def _get_user_feed(self, user_id: str, limit: int) -> List[SocialPost]:
        """Get user feed posts."""
        url = f"{self.FEED_URL}{user_id}/"
        params = {"count": limit}
        headers = self._get_headers()
        
        text, resp = await self.client.get(url, engine=self.platform, params=params, extra_headers=headers)
        data = resp.json()
        
        items = data.get("items", [])
        results = []
        for item in items[:limit]:
            post_url = f"https://instagram.com/p/{item.get('code', '')}"
            results.append(self._normalize_feed_item(item, post_url))
        
        return results
    
    async def _graphql_request(self, query_hash: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Make GraphQL request to Instagram."""
        params = {
            "query_hash": query_hash,
            "variables": json.dumps(variables),
        }
        
        headers = self._get_headers()
        text, resp = await self.client.get(self.GRAPHQL_URL, engine=self.platform, params=params, extra_headers=headers)
        if resp.status_code == 429:
            raise self.RateLimitError("instagram")
        resp.raise_for_status()
        
        return resp.json()
    
    def _normalize_post(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize Instagram post."""
        author_data = data.get("owner", {})
        
        author = {
            "username": author_data.get("username", ""),
            "display_name": author_data.get("full_name", ""),
            "avatar": author_data.get("profile_pic_url", ""),
            "verified": author_data.get("is_verified", False),
            "followers": author_data.get("edge_followed_by", {}).get("count"),
        }
        
        engagement = {
            "likes": data.get("edge_media_preview_like", {}).get("count"),
            "comments": data.get("edge_media_to_comment", {}).get("count"),
        }
        
        media = []
        if data.get("is_video"):
            media.append({
                "type": "video",
                "url": data.get("video_url", ""),
                "thumbnail": data.get("display_url", ""),
                "duration": data.get("video_duration"),
            })
        else:
            media.append({
                "type": "image",
                "url": data.get("display_url", ""),
                "thumbnail": data.get("thumbnail_url", ""),
            })
        
        # Carousel
        for edge in data.get("edge_sidecar_to_children", {}).get("edges", []):
            node = edge.get("node", {})
            if node.get("display_url"):
                media.append({
                    "type": "video" if node.get("is_video") else "image",
                    "url": node.get("video_url", "") or node.get("display_url", ""),
                    "thumbnail": node.get("display_url", ""),
                    "duration": node.get("video_duration"),
                })
        
        caption_edges = data.get("edge_media_to_caption", {}).get("edges", [])
        caption = caption_edges[0].get("node", {}).get("text", "") if caption_edges else ""
        
        return build_post(
            platform="instagram",
            post_type="reel" if data.get("is_video") else "post",
            url=url,
            id=data.get("shortcode", ""),
            text=caption,
            timestamp=data.get("taken_at_timestamp"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_feed_item(self, item: Dict[str, Any], url: str) -> SocialPost:
        """Normalize feed item (simpler format)."""
        author_data = item.get("user", {})
        
        author = {
            "username": author_data.get("username", ""),
            "display_name": author_data.get("full_name", ""),
            "avatar": author_data.get("profile_pic_url", ""),
            "verified": author_data.get("is_verified", False),
            "followers": None,
        }
        
        engagement = {
            "likes": item.get("like_count"),
            "comments": item.get("comment_count"),
        }
        
        media = []
        if item.get("media_type") == 2:  # Video
            media.append({
                "type": "video",
                "url": item.get("video_url", ""),
                "thumbnail": item.get("image_versions2", {}).get("candidates", [{}])[0].get("url", ""),
            })
        else:
            candidates = item.get("image_versions2", {}).get("candidates", [])
            if candidates:
                media.append({
                    "type": "image",
                    "url": candidates[0].get("url", ""),
                    "thumbnail": candidates[0].get("url", ""),
                })
        
        caption = item.get("caption", {}).get("text", "") if item.get("caption") else ""
        
        return build_post(
            platform="instagram",
            post_type="post",
            url=url,
            id=item.get("code", "") or item.get("id", ""),
            text=caption,
            timestamp=item.get("taken_at"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile(self, data: Dict[str, Any], username: str) -> SocialProfile:
        """Normalize Instagram profile."""
        author = {
            "username": data.get("username", username),
            "display_name": data.get("full_name", ""),
            "avatar": data.get("profile_pic_url_hd", "") or data.get("profile_pic_url", ""),
            "verified": data.get("is_verified", False),
            "followers": data.get("edge_followed_by", {}).get("count"),
            "following": data.get("edge_follow", {}).get("count"),
            "posts_count": data.get("edge_owner_to_timeline_media", {}).get("count"),
            "bio": data.get("biography", ""),
            "location": "",
            "joined_date": None,
        }
        
        return build_profile(
            platform="instagram",
            username=username,
            url=f"https://instagram.com/{username}",
            profile_data={"author": author, "engagement": {}, "id": data.get("id")},
        )
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract shortcode or username from Instagram URL."""
        shortcode = self._extract_shortcode(url)
        if shortcode:
            return shortcode
        
        # Profile
        match = re.search(r"instagram\.com/([^/?]+)", url)
        if match:
            return match.group(1)
        
        return None
    
    def _extract_shortcode(self, url: str) -> Optional[str]:
        """Extract shortcode from Instagram URL."""
        patterns = [
            r"instagram\.com/p/([^/?]+)",
            r"instagram\.com/reel/([^/?]+)",
            r"instagram\.com/tv/([^/?]+)",
            r"instagr\.am/p/([^/?]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None


# Register the scraper
registry.register(InstagramScraper)