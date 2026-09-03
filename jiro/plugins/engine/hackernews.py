"""Hacker News search engine plugin."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from jiro.config import Settings
from jiro.plugins import BaseEnginePlugin
from jiro.scraping.client import ScrapingClient


class HackerNewsEnginePlugin(BaseEnginePlugin):
    """Hacker News search engine plugin using Firebase API."""
    
    name = "hackernews"
    type = "engine"
    version = "1.0"
    author = "Jiro Team"
    description = "Hacker News stories and comments search"
    homepage = "https://news.ycombinator.com"
    min_jiro_version = "0.2.0"
    
    supported_types = ["web", "story", "comment", "top", "new", "best", "ask", "show", "job"]
    rate_limit_rpm = 100
    requires_proxy = False
    
    BASE_URL = "https://hacker-news.firebaseio.com/v0"
    HN_URL = "https://news.ycombinator.com"
    
    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        super().__init__(client, settings)
    
    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Search Hacker News using Algolia API."""
        # Use Algolia HN Search for better results
        import asyncio
        
        search_type = kwargs.get("type", "story")  # story, comment, poll, etc.
        
        params = {
            "query": query,
            "hitsPerPage": min(kwargs.get("max_results", 25), 100),
            "tags": search_type,
        }
        
        if kwargs.get("author"):
            params["tags"] = f"{search_type},author_{kwargs['author']}"
        
        url = "https://hn.algolia.com/api/v1/search"
        
        try:
            resp = await self.client.get(url, params=params)
            data = resp.json()
            return self._parse_algolia_results(data.get("hits", []))
        except Exception as e:
            raise RuntimeError(f"Hacker News search failed: {e}")
    
    async def get_top_stories(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Get top stories."""
        url = f"{self.BASE_URL}/topstories.json"
        ids = await self.client.get(url)
        return await self._get_items_batch(ids[:limit])
    
    async def get_new_stories(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Get new stories."""
        url = f"{self.BASE_URL}/newstories.json"
        ids = await self.client.get(url)
        return await self._get_items_batch(ids[:limit])
    
    async def get_best_stories(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Get best stories."""
        url = f"{self.BASE_URL}/beststories.json"
        ids = await self.client.get(url)
        return await self._get_items_batch(ids[:limit])
    
    async def scrape(self, url: str) -> Dict[str, Any]:
        """Scrape a Hacker News item."""
        # Extract item ID from URL
        import re
        match = re.search(r"[?&]id=(\d+)", url)
        if not match:
            match = re.search(r"/item/(\d+)", url)
        if not match:
            raise ValueError("Invalid Hacker News URL")
        
        item_id = match.group(1)
        api_url = f"{self.BASE_URL}/item/{item_id}.json"
        data = await self.client.get(api_url)
        return self._normalize_item(data, url)
    
    def _parse_algolia_results(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse Algolia search results."""
        results = []
        for hit in hits:
            item_type = hit.get("type", "story")
            
            result = {
                "title": hit.get("title", "") or hit.get("story_title", "") or "",
                "link": hit.get("url", "") or f"{self.HN_URL}/item?id={hit.get('objectID', '')}",
                "snippet": hit.get("story_text", "")[:300] if hit.get("story_text") else hit.get("comment_text", "")[:300] if hit.get("comment_text") else "",
                "author": hit.get("author", ""),
                "points": hit.get("points", 0),
                "num_comments": hit.get("num_comments", 0),
                "created_at": hit.get("created_at", ""),
                "object_id": hit.get("objectID", ""),
                "type": item_type,
                "source": "hackernews",
            }
            results.append(result)
        return results
    
    async def _get_items_batch(self, ids: List[int]) -> List[Dict[str, Any]]:
        """Fetch multiple items in parallel."""
        import asyncio
        tasks = [self.client.get(f"{self.BASE_URL}/item/{id}.json") for id in ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        items = []
        for r in results:
            if isinstance(r, dict):
                items.append(self._normalize_item(r, f"{self.HN_URL}/item?id={r.get('id')}"))
        return items
    
    def _normalize_item(self, data: Dict[str, Any], url: str) -> Dict[str, Any]:
        """Normalize HN item."""
        item_type = data.get("type", "story")
        
        return {
            "title": data.get("title", "") or data.get("text", "")[:100] or "",
            "link": url,
            "snippet": data.get("text", "")[:300] if data.get("text") else "",
            "author": data.get("by", ""),
            "points": data.get("score", 0),
            "num_comments": len(data.get("kids", [])),
            "created_at": data.get("time", 0),
            "type": item_type,
            "source": "hackernews",
            "item_id": data.get("id"),
        }


# Register
from jiro.plugins import engine_registry
engine_registry.register(HackerNewsEnginePlugin)