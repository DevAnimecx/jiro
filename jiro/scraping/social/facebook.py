"""Facebook scraper using public GraphQL endpoints (requires cookies for full access)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.facebook")


class FacebookScraper(BaseSocialScraper):
    """Facebook scraper using public GraphQL endpoints (cookies required for most data)."""
    
    platform = "facebook"
    url_patterns = [
        "facebook.com",
        "fb.com",
        "fb.watch",
    ]
    supported_actions = ["post", "profile", "page", "group", "video", "photo", "search", "timeline"]
    rate_limit_rpm = 20
    requires_auth = True
    
    GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        self.sessionid = settings.social.get("facebook", {}).get("sessionid", "") if settings else ""
        self.c_user = settings.social.get("facebook", {}).get("c_user", "") if settings else ""
        self._lsd_token: Optional[str] = None
    
    def _get_headers(self, lsd_token: Optional[str] = None) -> Dict[str, str]:
        """Get headers for Facebook requests."""
        token = lsd_token or self._lsd_token or ""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        }
        if token:
            headers["X-FB-LSD"] = token
        if self.c_user and self.sessionid:
            headers["Cookie"] = f"c_user={self.c_user}; xs={self.sessionid}"
        return headers

    async def _ensure_lsd_token(self, url: str) -> None:
        """Fetch X-FB-LSD token from Facebook page HTML."""
        if self._lsd_token:
            return
        try:
            html = await self._fetch_html(url, engine=self.platform)
            self._lsd_token = self._extract_lsd_token(html)
            if self._lsd_token:
                log.debug("Extracted X-FB-LSD token from Facebook page")
        except Exception as exc:
            log.debug("Could not extract X-FB-LSD token: %s", exc)

    @staticmethod
    def _extract_lsd_token(html: str) -> Optional[str]:
        """Extract X-FB-LSD token from Facebook HTML."""
        # Pattern 1: hidden input field
        m = re.search(r'name="lsd"\s+value="([^"]+)"', html)
        if m:
            return m.group(1)
        # Pattern 2: in JS/config data
        m = re.search(r'"lsd"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1)
        # Pattern 3: __RELAY_INIT__ or similar
        m = re.search(r'LSD:\s*\[\s*["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
        return None
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a Facebook post."""
        post_id = self._extract_post_id(url)
        if not post_id:
            raise ValueError("Could not extract post ID from URL")
        await self._ensure_lsd_token(url)
        
        # Try to extract from JSON-LD in HTML
        html = await self._fetch_html(url)
        return self._extract_from_html(html, url)
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a Facebook profile/page."""
        username = username.lstrip("@").rstrip("/")
        url = f"https://facebook.com/{username}"
        await self._ensure_lsd_token(url)
        html = await self._fetch_html(url)
        return self._extract_profile_from_html(html, url)
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search Facebook (requires auth)."""
        # Try GraphQL search with dynamic LSD token
        search_url = f"https://www.facebook.com/search/posts/?q={query}"
        await self._ensure_lsd_token(search_url)
        
        headers = self._get_headers()
        if not self._lsd_token:
            return []
        
        try:
            # Use Facebook's public search page
            html = await self._fetch_html(search_url)
            results = self._parse_search_results(html, limit)
            if results:
                return results
        except Exception:
            log.debug("silenced fallback", exc_info=True)
        
        return []

    def _parse_search_results(self, html: str, limit: int) -> List[SocialPost]:
        """Parse Facebook search results from HTML."""
        results = []
        
        # Try JSON-LD extraction
        json_ld_data = self._extract_json_ld(html)
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if any(t in ["SocialMediaPosting", "Article", "BlogPosting"] for t in types):
                    results.append(self._normalize_post(data, data.get("url", "")))
        
        # Try __comet_data extraction from Facebook's internal data
        comet_match = re.search(r'__comet_data\s*=\s*({.+?});\s*\n', html, re.DOTALL)
        if comet_match:
            try:
                comet_data = json.loads(comet_match.group(1))
                # Parse comet data for search results
                for key, val in comet_data.items():
                    if isinstance(val, dict) and "data" in val:
                        results.extend(self._parse_comet_search(val["data"], limit))
            except Exception:
                log.debug("silenced fallback", exc_info=True)
        
        return results[:limit]

    def _parse_comet_search(self, data: Any, limit: int) -> List[SocialPost]:
        """Parse Facebook Comet internal search data."""
        results = []
        try:
            if isinstance(data, dict):
                for edge in data.get("edges", [])[:limit]:
                    node = edge.get("node", {})
                    if node.get("story"):
                        post = node["story"]
                        results.append(build_post(
                            platform="facebook",
                            post_type="post",
                            url=post.get("url", ""),
                            id=post.get("id", ""),
                            text=post.get("message", {}).get("text", "") if isinstance(post.get("message"), dict) else str(post.get("message", "")),
                            timestamp=post.get("timestamp", {}).get("time", 0),
                            author={
                                "username": post.get("actors", [{}])[0].get("name", "") if post.get("actors") else "",
                            },
                            engagement={
                                "likes": post.get("like_count", 0),
                                "comments": post.get("comment_count", 0),
                                "shares": post.get("share_count", 0),
                            },
                            media=[],
                        ))
        except Exception:
            log.debug("silenced fallback", exc_info=True)
        return results
    
    def _extract_from_html(self, html: str, url: str) -> SocialPost:
        """Extract post data from HTML using JSON-LD and meta tags."""
        # Extract JSON-LD
        json_ld_data = self._extract_json_ld(html)
        
        # Look for SocialMediaPosting or Article
        post_data = None
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if any(t in ["SocialMediaPosting", "Article", "VideoObject"] for t in types):
                    post_data = data
                    break
        
        if not post_data:
            post_data = self._extract_meta_tags(html)
        
        if not post_data:
            raise ValueError("Could not extract post data from Facebook page")
        
        return self._normalize_post(post_data, url)
    
    def _extract_profile_from_html(self, html: str, url: str) -> SocialProfile:
        """Extract profile/page data from HTML."""
        json_ld_data = self._extract_json_ld(html)
        
        # Look for Person, ProfilePage, or Organization
        profile_data = None
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if any(t in ["Person", "ProfilePage", "Organization"] for t in types):
                    profile_data = data
                    break
        
        if not profile_data:
            profile_data = self._extract_meta_tags(html)
        
        if not profile_data:
            raise ValueError("Could not extract profile data from Facebook page")
        
        return self._normalize_profile(profile_data, url)
    
    def _extract_json_ld(self, html: str) -> List[Dict[str, Any]]:
        """Extract JSON-LD structured data from HTML."""
        results = []
        
        # Find all script tags with type="application/ld+json"
        pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
        
        for match in matches:
            try:
                data = json.loads(match.strip())
                if isinstance(data, list):
                    results.extend(data)
                else:
                    results.append(data)
            except json.JSONDecodeError:
                continue
        
        return results
    
    def _extract_meta_tags(self, html: str) -> Dict[str, Any]:
        """Extract Open Graph and Facebook meta tags."""
        data = {}
        
        # Open Graph tags
        og_pattern = r'<meta[^>]*property=["\']og:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(og_pattern, html, re.IGNORECASE):
            data[f"og:{match.group(1)}"] = match.group(2)
        
        # Facebook specific tags
        fb_pattern = r'<meta[^>]*property=["\']fb:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(fb_pattern, html, re.IGNORECASE):
            data[f"fb:{match.group(1)}"] = match.group(2)
        
        # Twitter Card tags
        twitter_pattern = r'<meta[^>]*name=["\']twitter:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(twitter_pattern, html, re.IGNORECASE):
            data[f"twitter:{match.group(1)}"] = match.group(2)
        
        # Standard meta tags
        meta_pattern = r'<meta[^>]*name=["\']([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(meta_pattern, html, re.IGNORECASE):
            data[match.group(1)] = match.group(2)
        
        # Look for fb:profile_id or fb:page_id
        profile_id_pattern = r'"profile_id"\s*:\s*(\d+)'
        match = re.search(profile_id_pattern, html)
        if match:
            data["fb:profile_id"] = match.group(1)
        
        page_id_pattern = r'"page_id"\s*:\s*(\d+)'
        match = re.search(page_id_pattern, html)
        if match:
            data["fb:page_id"] = match.group(1)
        
        return data
    
    def _normalize_post(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize Facebook post."""
        author = {
            "username": data.get("author", {}).get("name", "") if isinstance(data.get("author"), dict) else data.get("author", ""),
            "display_name": data.get("author", {}).get("name", "") if isinstance(data.get("author"), dict) else data.get("author", ""),
            "avatar": data.get("author", {}).get("image", "") if isinstance(data.get("author"), dict) else data.get("og:image", ""),
        }
        
        engagement = {
            "likes": normalize_number(data.get("interactionStatistic", {}).get("userInteractionCount")) if isinstance(data.get("interactionStatistic"), dict) else None,
            "comments": None,
            "shares": None,
        }
        
        media = []
        # Images from og:image
        og_images = data.get("og:image")
        if og_images:
            images = og_images if isinstance(og_images, list) else [og_images]
            for img in images:
                media.append({"type": "image", "url": img})
        
        # Video from og:video
        if data.get("og:video"):
            videos = data["og:video"] if isinstance(data["og:video"], list) else [data["og:video"]]
            for vid in videos:
                media.append({"type": "video", "url": vid})
        
        return build_post(
            platform="facebook",
            post_type="post",
            url=url,
            id=data.get("@id", "") or data.get("fb:post_id", "") or url.split("/")[-1],
            text=data.get("text", "") or data.get("description", "") or data.get("og:description", ""),
            timestamp=data.get("datePublished") or data.get("dateCreated"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile(self, data: Dict[str, Any], url: str) -> SocialProfile:
        """Normalize Facebook profile/page."""
        # Extract username from URL
        username = url.split("/")[-1].rstrip("/")
        
        author = {
            "username": username,
            "display_name": data.get("name", "") or data.get("og:title", ""),
            "avatar": data.get("image", "") or data.get("og:image", ""),
            "verified": data.get("isVerified", False) or data.get("verified", False),
            "followers": normalize_number(data.get("followers_count") or data.get("fb:followers_count")),
            "bio": data.get("description", "") or data.get("og:description", ""),
            "location": data.get("location", {}).get("name", "") if isinstance(data.get("location"), dict) else "",
        }
        
        return build_profile(
            platform="facebook",
            username=username,
            url=url,
            profile_data={"author": author, "engagement": {}, "id": data.get("@id", "") or data.get("fb:profile_id") or data.get("fb:page_id")},
        )
    
    async def scrape_timeline(self, username: str, limit: int = 20) -> List[SocialPost]:
        """Get recent posts from a Facebook page."""
        page_url = f"https://facebook.com/{username.lstrip('@')}"
        await self._ensure_lsd_token(page_url)
        html = await self._fetch_html(page_url, engine=self.platform)
        return self._parse_page_posts(html, page_url, limit)

    def _parse_page_posts(self, html: str, base_url: str, limit: int) -> List[SocialPost]:
        """Parse page posts from Facebook HTML."""
        results = []
        
        # Try JSON-LD
        json_ld_data = self._extract_json_ld(html)
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if any(t in ["SocialMediaPosting", "Article", "BlogPosting"] for t in types):
                    results.append(self._normalize_post(data, data.get("url", base_url)))
        
        # Try __comet_data
        comet_match = re.search(r'__comet_data\s*=\s*({.+?});\s*\n', html, re.DOTALL)
        if comet_match:
            try:
                comet_data = json.loads(comet_match.group(1))
                for key, val in comet_data.items():
                    if isinstance(val, dict) and "data" in val:
                        posts = self._parse_comet_search(val["data"], limit)
                        results.extend(posts)
            except Exception:
                log.debug("silenced fallback", exc_info=True)
        
        return results[:limit]
    
    def _extract_post_id(self, url: str) -> Optional[str]:
        """Extract post ID from Facebook URL."""
        patterns = [
            r"facebook\.com/.*/posts/(\d+)",
            r"facebook\.com/.*/videos/(\d+)",
            r"facebook\.com/.*/photos/(\d+)",
            r"fb\.watch/(\w+)",
            r"story_fbid=(\d+)",
            r"fbid=(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract post ID or username from Facebook URL."""
        post_id = self._extract_post_id(url)
        if post_id:
            return post_id
        
        # Profile/page
        match = re.search(r"facebook\.com/([^/?]+)", url)
        if match:
            return match.group(1)
        
        match = re.search(r"fb\.com/([^/?]+)", url)
        if match:
            return match.group(1)
        
        return None


# Register the scraper
registry.register(FacebookScraper)