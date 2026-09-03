"""Reddit search engine plugin."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from jiro.config import Settings
from jiro.plugins import BaseEnginePlugin
from jiro.scraping.client import ScrapingClient


class RedditEnginePlugin(BaseEnginePlugin):
    """Reddit search engine plugin using public .json endpoints."""
    
    name = "reddit"
    type = "engine"
    version = "1.0"
    author = "Jiro Team"
    description = "Reddit search - posts, comments, subreddits"
    homepage = "https://reddit.com"
    min_jiro_version = "0.2.0"
    
    supported_types = ["web", "post", "comment", "subreddit", "user"]
    rate_limit_rpm = 60
    requires_proxy = False
    
    BASE_URL = "https://www.reddit.com"
    
    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        super().__init__(client, settings)
    
    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Search Reddit using public .json endpoints."""
        search_type = kwargs.get("type", "post")  # post, comment, subreddit, user
        subreddit = kwargs.get("subreddit")
        
        params = {
            "q": query,
            "limit": min(kwargs.get("max_results", 25), 100),
            "sort": kwargs.get("sort", "relevance"),
            "t": kwargs.get("time", "all"),  # hour, day, week, month, year, all
        }
        
        if search_type == "subreddit":
            url = f"{self.BASE_URL}/subreddits/search.json"
        elif subreddit:
            url = f"{self.BASE_URL}/r/{subreddit}/search.json"
            params["restrict_sr"] = "1"
        else:
            url = f"{self.BASE_URL}/search.json"
        
        headers = {
            "User-Agent": "Jiro/0.2 (+https://github.com/DevAnimecx/jiro)",
        }
        
        try:
            resp = await self.client.get(url, params=params, headers=headers)
            data = resp.json()
            return self._parse_search_results(data, search_type)
        except Exception as e:
            raise RuntimeError(f"Reddit search failed: {e}")
    
    async def scrape(self, url: str) -> Dict[str, Any]:
        """Scrape a Reddit post/comment."""
        # Convert to .json endpoint
        if not url.endswith(".json"):
            if not url.endswith("/"):
                url += "/"
            url += ".json"
        
        try:
            resp = await self.client.get(url)
            data = resp.json()
            return self._parse_post_data(data, url)
        except Exception as e:
            raise RuntimeError(f"Reddit scrape failed: {e}")
    
    async def get_subreddit_posts(self, subreddit: str, sort: str = "hot", limit: int = 25) -> List[Dict[str, Any]]:
        """Get posts from a subreddit."""
        url = f"{self.BASE_URL}/r/{subreddit}/{sort}.json"
        params = {"limit": limit}
        
        resp = await self.client.get(url, params=params)
        data = resp.json()
        
        results = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            if post:
                results.append(self._normalize_post(post))
        return results
    
    async def get_user_posts(self, username: str, limit: int = 25) -> List[Dict[str, Any]]:
        """Get user's posts."""
        url = f"{self.BASE_URL}/user/{username}/submitted.json"
        params = {"limit": limit}
        
        resp = await self.client.get(url, params=params)
        data = resp.json()
        
        results = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            if post:
                results.append(self._normalize_post(post))
        return results
    
    def _parse_search_results(self, data: Dict[str, Any], search_type: str) -> List[Dict[str, Any]]:
        """Parse search results from Reddit API."""
        results = []
        
        for child in data.get("data", {}).get("children", []):
            kind = child.get("kind", "")
            data = child.get("data", {})
            
            if search_type == "post" and kind != "t3":
                continue
            elif search_type == "comment" and kind != "t1":
                continue
            elif search_type == "subreddit" and kind != "t5":
                continue
            elif search_type == "user" and kind != "t2":
                continue
            
            if kind == "t3":  # Post
                results.append(self._normalize_post(data))
            elif kind == "t1":  # Comment
                results.append(self._normalize_comment(data))
            elif kind == "t5":  # Subreddit
                results.append(self._normalize_subreddit(data))
            elif kind == "t2":  # User
                results.append(self._normalize_user(data))
        
        return results
    
    def _parse_post_data(self, data: List[Dict[str, Any]], url: str) -> Dict[str, Any]:
        """Parse post from .json endpoint (returns [post, comments])."""
        if not data or len(data) < 1:
            return {"error": "Post not found"}
        
        post_data = data[0].get("data", {}).get("children", [{}])[0].get("data", {})
        return self._normalize_post(post_data)
    
    def _normalize_post(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": data.get("title", ""),
            "link": f"https://reddit.com{data.get('permalink', '')}",
            "snippet": data.get("selftext", "")[:300] if data.get("selftext") else data.get("title", ""),
            "author": data.get("author", "[deleted]"),
            "subreddit": data.get("subreddit", ""),
            "score": data.get("score", 0),
            "upvote_ratio": data.get("upvote_ratio", 0),
            "num_comments": data.get("num_comments", 0),
            "created_utc": data.get("created_utc", 0),
            "url": data.get("url", ""),
            "is_self": data.get("is_self", False),
            "flair": data.get("link_flair_text", ""),
            "source": "reddit",
            "type": "post",
        }
    
    def _normalize_comment(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": f"Comment by {data.get('author', '[deleted]')}",
            "link": f"https://reddit.com{data.get('permalink', '')}",
            "snippet": data.get("body", "")[:300],
            "author": data.get("author", "[deleted]"),
            "subreddit": data.get("subreddit", ""),
            "score": data.get("score", 0),
            "created_utc": data.get("created_utc", 0),
            "parent_id": data.get("parent_id", ""),
            "is_submitter": data.get("is_submitter", False),
            "source": "reddit",
            "type": "comment",
        }
    
    def _normalize_subreddit(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": f"r/{data.get('display_name', '')}",
            "link": f"https://reddit.com{data.get('url', '')}",
            "snippet": data.get("public_description", "")[:300] if data.get("public_description") else data.get("description", "")[:300],
            "subscribers": data.get("subscribers", 0),
            "created_utc": data.get("created_utc", 0),
            "over18": data.get("over18", False),
            "source": "reddit",
            "type": "subreddit",
        }
    
    def _normalize_user(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": f"u/{data.get('name', '')}",
            "link": f"https://reddit.com/user/{data.get('name', '')}",
            "snippet": f"Karma: {data.get('comment_karma', 0) + data.get('link_karma', 0)}",
            "comment_karma": data.get("comment_karma", 0),
            "link_karma": data.get("link_karma", 0),
            "created_utc": data.get("created_utc", 0),
            "is_mod": data.get("is_mod", False),
            "is_gold": data.get("is_gold", False),
            "source": "reddit",
            "type": "user",
        }


# Register
from jiro.plugins import engine_registry
engine_registry.register(RedditEnginePlugin)