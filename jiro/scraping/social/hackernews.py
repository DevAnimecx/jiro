"""Hacker News scraper using Firebase REST API."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.hackernews")


class HackerNewsScraper(BaseSocialScraper):
    """Hacker News scraper using Firebase REST API (no auth required)."""
    
    platform = "hackernews"
    url_patterns = [
        "news.ycombinator.com",
        "hackernews.com",
    ]
    supported_actions = ["post", "profile", "story", "comment", "top", "new", "best", "ask", "show", "job"]
    rate_limit_rpm = 100
    requires_auth = False
    
    BASE_URL = "https://hacker-news.firebaseio.com/v0"
    HN_URL = "https://news.ycombinator.com"
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a Hacker News story/comment."""
        item_id = self.extract_identifier(url)
        if not item_id:
            raise ValueError("Could not extract item ID from URL")
        
        return await self._get_item(item_id, url)
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a Hacker News user profile."""
        url = f"{self.BASE_URL}/user/{username}.json"
        data = await self._fetch_json(url)
        
        if not data:
            raise ValueError("User not found")
        
        return self._normalize_profile(data, username)
    
    async def get_top_stories(self, limit: int = 25) -> List[SocialPost]:
        """Get top stories."""
        url = f"{self.BASE_URL}/topstories.json"
        ids = await self._fetch_json(url)
        
        return await self._get_items_batch(ids[:limit])
    
    async def get_new_stories(self, limit: int = 25) -> List[SocialPost]:
        """Get new stories."""
        url = f"{self.BASE_URL}/newstories.json"
        ids = await self._fetch_json(url)
        
        return await self._get_items_batch(ids[:limit])
    
    async def get_best_stories(self, limit: int = 25) -> List[SocialPost]:
        """Get best stories."""
        url = f"{self.BASE_URL}/beststories.json"
        ids = await self._fetch_json(url)
        
        return await self._get_items_batch(ids[:limit])
    
    async def get_ask_stories(self, limit: int = 25) -> List[SocialPost]:
        """Get Ask HN stories."""
        url = f"{self.BASE_URL}/askstories.json"
        ids = await self._fetch_json(url)
        
        return await self._get_items_batch(ids[:limit])
    
    async def get_show_stories(self, limit: int = 25) -> List[SocialPost]:
        """Get Show HN stories."""
        url = f"{self.BASE_URL}/showstories.json"
        ids = await self._fetch_json(url)
        
        return await self._get_items_batch(ids[:limit])
    
    async def get_job_stories(self, limit: int = 25) -> List[SocialPost]:
        """Get Job stories."""
        url = f"{self.BASE_URL}/jobstories.json"
        ids = await self._fetch_json(url)
        
        return await self._get_items_batch(ids[:limit])
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search Hacker News (uses Algolia API)."""
        # Use Algolia HN Search API
        url = f"https://hn.algolia.com/api/v1/search?query={query}&hitsPerPage={limit}"
        data = await self._fetch_json(url)
        
        hits = data.get("hits", [])
        results = []
        for hit in hits:
            item_id = hit.get("objectID")
            if item_id:
                try:
                    item = await self._get_item(item_id, f"{self.HN_URL}/item?id={item_id}")
                    results.append(item)
                except Exception:
                    log.debug("silenced fallback", exc_info=True)
        
        return results
    
    async def _get_items_batch(self, ids: List[int]) -> List[SocialPost]:
        """Fetch multiple items in parallel."""
        tasks = [self._get_item(str(id), f"{self.HN_URL}/item?id={id}") for id in ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        posts = []
        for r in results:
            if isinstance(r, SocialPost):
                posts.append(r)
            elif isinstance(r, Exception):
                log.warning("Failed to fetch HN item", extra={"error": str(r)})
        
        return posts
    
    async def _get_item(self, item_id: str, url: str) -> SocialPost:
        """Get a single item by ID."""
        api_url = f"{self.BASE_URL}/item/{item_id}.json"
        data = await self._fetch_json(api_url)
        
        if not data:
            raise ValueError(f"Item {item_id} not found")
        
        return self._normalize_item(data, url)
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract item ID from HN URL."""
        import re
        # item?id=12345
        match = re.search(r"[?&]id=(\d+)", url)
        if match:
            return match.group(1)
        # /item/12345
        match = re.search(r"/item/(\d+)", url)
        if match:
            return match.group(1)
        return None
    
    @classmethod
    def extract_identifier_class(cls, url: str) -> Optional[str]:
        """Class method to extract identifier without instantiation."""
        import re
        match = re.search(r"[?&]id=(\d+)", url)
        if match:
            return match.group(1)
        match = re.search(r"/item/(\d+)", url)
        if match:
            return match.group(1)
        return None
    
    def _normalize_item(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize HN item (story, comment, job, poll, etc.)."""
        item_type = data.get("type", "story")
        
        author = {
            "username": data.get("by", "[deleted]"),
            "display_name": data.get("by", "[deleted]"),
            "avatar": "",
        }
        
        # Determine engagement based on type
        if item_type == "story":
            engagement = {
                "likes": data.get("score"),
                "comments": len(data.get("kids", [])),
            }
        elif item_type == "comment":
            engagement = {
                "likes": None,  # Comments don't have scores in API
                "replies": len(data.get("kids", [])),
            }
        elif item_type == "poll":
            engagement = {
                "likes": data.get("score"),
                "votes": sum(opt.get("score", 0) for opt in data.get("parts", [])),
            }
        else:
            engagement = {}
        
        media = []
        if data.get("url"):
            media.append({"type": "link", "url": data["url"]})
        
        text = data.get("text", "") or data.get("title", "")
        if item_type == "comment":
            text = data.get("text", "")
        
        return build_post(
            platform="hackernews",
            post_type=item_type,
            url=url,
            id=str(data.get("id", "")),
            text=text,
            timestamp=data.get("time"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile(self, data: Dict[str, Any], username: str) -> SocialProfile:
        """Normalize HN user profile."""
        author = {
            "username": data.get("id", username),
            "display_name": data.get("id", username),
            "avatar": "",
            "verified": False,
            "followers": None,
            "bio": data.get("about", ""),
            "location": "",
            "joined_date": data.get("created"),
        }
        
        engagement = {
            "karma": data.get("karma"),
            "submitted_count": len(data.get("submitted", [])),
        }
        
        return build_profile(
            platform="hackernews",
            username=username,
            url=f"{self.HN_URL}/user?id={username}",
            profile_data={"author": author, "engagement": engagement, "id": data.get("id")},
        )


# Register the scraper
registry.register(HackerNewsScraper)