"""Pinterest scraper using JSON-LD structured data extraction."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.pinterest")


class PinterestScraper(BaseSocialScraper):
    """Pinterest scraper using JSON-LD structured data extraction."""
    
    platform = "pinterest"
    url_patterns = [
        "pinterest.com",
        "pin.it",
    ]
    supported_actions = ["pin", "profile", "board", "search"]
    rate_limit_rpm = 60
    requires_auth = False
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a Pinterest pin."""
        pin_id = self._extract_pin_id(url)
        if not pin_id:
            # Try extracting from URL directly
            html = await self._fetch_html(url)
            return self._extract_pin_from_html(html, url)
        
        # Try API first for better data
        try:
            return await self._scrape_pin_api(pin_id, url)
        except Exception:
            pass
        
        # Fallback to HTML parsing
        html = await self._fetch_html(url)
        return self._extract_pin_from_html(html, url)
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a Pinterest profile."""
        username = username.lstrip("@")
        
        url = f"https://pinterest.com/{username}"
        html = await self._fetch_html(url)
        return self._extract_profile_from_html(html, url)
    
    async def scrape_board(self, username: str, board_slug: str) -> List[SocialPost]:
        """Scrape pins from a board."""
        url = f"https://pinterest.com/{username}/{board_slug}"
        html = await self._fetch_html(url)
        return self._extract_board_pins(html, username, board_slug)
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search Pinterest pins."""
        # Pinterest search requires JavaScript rendering
        # Use basic HTML search
        url = f"https://pinterest.com/search/pins/?q={query}"
        html = await self._fetch_html(url)
        return self._extract_search_results(html, query, limit)
    
    def _extract_pin_id(self, url: str) -> Optional[str]:
        """Extract pin ID from Pinterest URL."""
        patterns = [
            r"pinterest\.com/pin/(\d+)",
            r"pin\.it/([\w]+)",
            r"pinterest\.com/[\w-]+/(\d+)",  # board pin
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    async def _scrape_pin_api(self, pin_id: str, url: str) -> SocialPost:
        """Scrape pin via Pinterest API (unofficial)."""
        api_url = f"https://www.pinterest.com/resource/PinResource/get/?source_url=%2Fpin%2F{pin_id}%2F&data=%7B%22options%22%3A%7B%22pin_id%22%3A%22{pin_id}%22%2C%22field_set_key%22%3A%22unauth_react%22%7D%2C%22context%22%3A%7B%7D%7D"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
        }
        
        resp = await self.client.get(api_url, headers=headers)
        data = resp.json()
        
        pin_data = data.get("resource_response", {}).get("data", {})
        if not pin_data:
            raise ValueError("Pin not found via API")
        
        return self._normalize_pin_api(pin_data, url)
    
    def _normalize_pin_api(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize pin from API response."""
        # Pinterest API returns complex nested data
        # Extract key fields
        pin_id = data.get("id", "")
        title = data.get("title", "") or data.get("rich_summary", {}).get("display_description", "")
        description = data.get("description", "") or data.get("closeup_unified_description", "")
        text = title or description
        
        # Author info
        creator = data.get("creator", {}) or data.get("owner", {})
        author = {
            "username": creator.get("username", ""),
            "display_name": creator.get("full_name", "") or creator.get("username", ""),
            "avatar": creator.get("image_xlarge_url", "") or creator.get("profile_image", ""),
            "verified": creator.get("verified_identity", False),
            "followers": creator.get("follower_count"),
        }
        
        # Engagement
        engagement = {
            "likes": data.get("like_count"),
            "comments": data.get("comment_count"),
            "saves": data.get("repin_count"),
        }
        
        # Media
        media = []
        images = data.get("images", {})
        if images:
            orig = images.get("orig", {})
            if orig.get("url"):
                media.append({
                    "type": "image",
                    "url": orig["url"],
                    "width": orig.get("width"),
                    "height": orig.get("height"),
                })
        
        videos = data.get("videos", {})
        if videos:
            video_list = videos.get("video_list", {})
            for key, video in video_list.items():
                if video.get("url"):
                    media.append({
                        "type": "video",
                        "url": video["url"],
                        "width": video.get("width"),
                        "height": video.get("height"),
                        "duration": video.get("duration"),
                    })
        
        return build_post(
            platform="pinterest",
            post_type="pin",
            url=url,
            id=str(pin_id),
            text=text,
            timestamp=data.get("created_at"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _extract_pin_from_html(self, html: str, url: str) -> SocialPost:
        """Extract pin data from HTML using JSON-LD."""
        # Extract JSON-LD
        json_ld_data = self._extract_json_ld(html)
        
        pin_data = None
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if "ImageObject" in types or "Product" in types:
                    pin_data = data
                    break
        
        if not pin_data:
            # Try meta tags
            pin_data = self._extract_meta_tags(html)
        
        if not pin_data:
            raise ValueError("Could not extract pin data from Pinterest page")
        
        return self._normalize_pin_html(pin_data, url)
    
    def _extract_profile_from_html(self, html: str, url: str) -> SocialProfile:
        """Extract profile data from HTML."""
        json_ld_data = self._extract_json_ld(html)
        
        profile_data = None
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if "Person" in types or "ProfilePage" in types:
                    profile_data = data
                    break
        
        if not profile_data:
            profile_data = self._extract_meta_tags(html)
        
        if not profile_data:
            raise ValueError("Could not extract profile data from Pinterest page")
        
        return self._normalize_profile_html(profile_data, url)
    
    def _extract_board_pins(self, html: str, username: str, board_slug: str) -> List[SocialPost]:
        """Extract pins from a board page."""
        # Board page has multiple pins
        json_ld_data = self._extract_json_ld(html)
        
        pins = []
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if "ImageObject" in types or "ItemList" in types:
                    # Could be a list of pins
                    if "itemListElement" in data:
                        for item in data["itemListElement"]:
                            pin_url = item.get("url", "")
                            if pin_url:
                                try:
                                    pin = self._normalize_pin_html(item, pin_url)
                                    pins.append(pin)
                                except Exception:
                                    continue
                    elif "ImageObject" in types:
                        try:
                            pin = self._normalize_pin_html(data, data.get("url", url))
                            pins.append(pin)
                        except Exception:
                            continue
        
        return pins
    
    def _extract_search_results(self, html: str, query: str, limit: int) -> List[SocialPost]:
        """Extract pins from search results."""
        json_ld_data = self._extract_json_ld(html)
        
        pins = []
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if "ItemList" in types and "itemListElement" in data:
                    for item in data["itemListElement"][:limit]:
                        try:
                            pin = self._normalize_pin_html(item.get("item", item), item.get("url", ""))
                            pins.append(pin)
                        except Exception:
                            continue
        
        return pins[:limit]
    
    def _extract_json_ld(self, html: str) -> List[Dict[str, Any]]:
        """Extract JSON-LD structured data from HTML."""
        results = []
        
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
        """Extract Open Graph and Pinterest meta tags."""
        data = {}
        
        # Open Graph tags
        og_pattern = r'<meta[^>]*property=["\']og:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(og_pattern, html, re.IGNORECASE):
            data[f"og:{match.group(1)}"] = match.group(2)
        
        # Pinterest specific tags
        pinterest_pattern = r'<meta[^>]*name=["\']pinterest:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(pinterest_pattern, html, re.IGNORECASE):
            data[f"pinterest:{match.group(1)}"] = match.group(2)
        
        # Twitter Card tags
        twitter_pattern = r'<meta[^>]*name=["\']twitter:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(twitter_pattern, html, re.IGNORECASE):
            data[f"twitter:{match.group(1)}"] = match.group(2)
        
        return data
    
    def _normalize_pin_html(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize pin from HTML extraction."""
        author = {
            "username": data.get("author", {}).get("name", "") if isinstance(data.get("author"), dict) else data.get("pinterest:username", ""),
            "display_name": data.get("author", {}).get("name", "") if isinstance(data.get("author"), dict) else data.get("pinterest:username", ""),
            "avatar": data.get("author", {}).get("image", "") if isinstance(data.get("author"), dict) else "",
        }
        
        engagement = {
            "likes": normalize_number(data.get("interactionStatistic", {}).get("userInteractionCount")) if isinstance(data.get("interactionStatistic"), dict) else None,
            "saves": normalize_number(data.get("pinterest:repin_count")),
        }
        
        media = []
        if data.get("image"):
            images = data["image"] if isinstance(data["image"], list) else [data["image"]]
            for img in images:
                img_url = img if isinstance(img, str) else img.get("url", "") or img.get("contentUrl", "")
                if img_url:
                    media.append({
                        "type": "image",
                        "url": img_url,
                        "width": img.get("width") if isinstance(img, dict) else None,
                        "height": img.get("height") if isinstance(img, dict) else None,
                    })
        
        text = data.get("text", "") or data.get("description", "") or data.get("og:description", "") or data.get("pinterest:description", "")
        
        pin_id = data.get("@id", "") or data.get("pinterest:pin_id", "") or url.split("/")[-1]
        
        return build_post(
            platform="pinterest",
            post_type="pin",
            url=url,
            id=str(pin_id),
            text=text,
            timestamp=data.get("datePublished") or data.get("dateCreated"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile_html(self, data: Dict[str, Any], url: str) -> SocialProfile:
        """Normalize profile from HTML extraction."""
        username = url.split("/")[-1].rstrip("/")
        
        author = {
            "username": username,
            "display_name": data.get("name", "") or data.get("og:title", "").replace(" on Pinterest", ""),
            "avatar": data.get("image", "") or data.get("og:image", ""),
            "verified": False,
            "followers": normalize_number(data.get("interactionStatistic", {}).get("userInteractionCount")) if isinstance(data.get("interactionStatistic"), dict) else None,
            "bio": data.get("description", "") or data.get("og:description", ""),
        }
        
        return build_profile(
            platform="pinterest",
            username=username,
            url=url,
            profile_data={"author": author, "engagement": {}, "id": data.get("@id", "")},
        )
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract pin ID, username, or board from Pinterest URL."""
        pin_id = self._extract_pin_id(url)
        if pin_id:
            return pin_id
        
        # Profile: pinterest.com/username
        match = re.search(r"pinterest\.com/([^/?]+)/?$", url)
        if match:
            return match.group(1)
        
        # Board: pinterest.com/username/board-slug
        match = re.search(r"pinterest\.com/([^/]+)/([^/?]+)", url)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
        
        # Short URL: pin.it/xxx
        match = re.search(r"pin\.it/([\w]+)", url)
        if match:
            return match.group(1)
        
        return None


# Register the scraper
registry.register(PinterestScraper)