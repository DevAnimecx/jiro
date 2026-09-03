"""Reddit scraper using public .json endpoints."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.reddit")


class RedditScraper(BaseSocialScraper):
    """Reddit scraper using public .json endpoints (no auth required)."""
    
    platform = "reddit"
    url_patterns = [
        "reddit.com",
        "old.reddit.com",
        "www.reddit.com",
    ]
    supported_actions = ["post", "profile", "subreddit", "search", "comments"]
    rate_limit_rpm = 60
    requires_auth = False
    
    BASE_URL = "https://www.reddit.com"
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a Reddit post/submission."""
        # Convert to .json endpoint
        json_url = self._to_json_url(url)
        data = await self._fetch_json(json_url)
        
        # Reddit returns [listing, comments], we want the first listing's children[0].data
        if isinstance(data, list) and len(data) > 0:
            post_data = data[0].get("data", {}).get("children", [{}])[0].get("data", {})
        else:
            post_data = data.get("data", {})
        
        if not post_data:
            raise ValueError("No post data found")
        
        return self._normalize_post(post_data, url)
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a Reddit user profile."""
        url = f"{self.BASE_URL}/user/{username}/about.json"
        data = await self._fetch_json(url)
        profile_data = data.get("data", {})
        
        if not profile_data:
            raise ValueError("Profile not found")
        
        return self._normalize_profile(profile_data, username)
    
    async def scrape_subreddit(self, subreddit: str, sort: str = "hot", limit: int = 25) -> List[SocialPost]:
        """Scrape posts from a subreddit."""
        url = f"{self.BASE_URL}/r/{subreddit}/{sort}.json?limit={limit}"
        data = await self._fetch_json(url)
        posts = data.get("data", {}).get("children", [])
        
        results = []
        for post in posts:
            post_data = post.get("data", {})
            if post_data:
                post_url = f"https://reddit.com{post_data.get('permalink', '')}"
                results.append(self._normalize_post(post_data, post_url))
        
        return results
    
    async def search(self, query: str, limit: int = 25, subreddit: Optional[str] = None) -> List[SocialPost]:
        """Search Reddit posts."""
        if subreddit:
            url = f"{self.BASE_URL}/r/{subreddit}/search.json?q={query}&limit={limit}&restrict_sr=1"
        else:
            url = f"{self.BASE_URL}/search.json?q={query}&limit={limit}"
        
        data = await self._fetch_json(url)
        posts = data.get("data", {}).get("children", [])
        
        results = []
        for post in posts:
            post_data = post.get("data", {})
            if post_data:
                post_url = f"https://reddit.com{post_data.get('permalink', '')}"
                results.append(self._normalize_post(post_data, post_url))
        
        return results
    
    async def scrape_comments(self, url: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Scrape comments from a Reddit post."""
        json_url = self._to_json_url(url)
        data = await self._fetch_json(json_url)
        
        if isinstance(data, list) and len(data) > 1:
            comments_data = data[1].get("data", {}).get("children", [])
        else:
            comments_data = []
        
        comments = []
        for comment in comments_data[:limit]:
            cdata = comment.get("data", {})
            if cdata.get("body") and cdata.get("body") != "[deleted]":
                comments.append({
                    "id": cdata.get("id"),
                    "author": cdata.get("author"),
                    "text": cdata.get("body"),
                    "score": cdata.get("score"),
                    "created_utc": cdata.get("created_utc"),
                    "permalink": f"https://reddit.com{cdata.get('permalink', '')}",
                    "is_submitter": cdata.get("is_submitter", False),
                    "replies": self._extract_replies(cdata.get("replies", "")),
                })
        
        return comments
    
    def _extract_replies(self, replies_data: Any) -> List[Dict[str, Any]]:
        """Recursively extract reply comments."""
        if not replies_data or replies_data == "":
            return []
        
        replies = []
        if isinstance(replies_data, dict):
            children = replies_data.get("data", {}).get("children", [])
            for reply in children:
                rdata = reply.get("data", {})
                if rdata.get("body") and rdata.get("body") != "[deleted]":
                    replies.append({
                        "id": rdata.get("id"),
                        "author": rdata.get("author"),
                        "text": rdata.get("body"),
                        "score": rdata.get("score"),
                        "created_utc": rdata.get("created_utc"),
                        "replies": self._extract_replies(rdata.get("replies", "")),
                    })
        return replies
    
    def _to_json_url(self, url: str) -> str:
        """Convert Reddit URL to .json endpoint."""
        # Already a .json URL
        if url.endswith(".json"):
            return url
        
        # Ensure it ends with /
        if not url.endswith("/"):
            url += "/"
        
        return url + ".json"
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract post ID or username from URL."""
        # Post: reddit.com/r/sub/comments/POST_ID/title/
        match = re.search(r"/comments/([a-z0-9]+)/", url)
        if match:
            return match.group(1)
        
        # Profile: reddit.com/user/username
        match = re.search(r"/user/([^/]+)", url)
        if match:
            return match.group(1)
        
        # Subreddit: reddit.com/r/subreddit
        match = re.search(r"/r/([^/]+)", url)
        if match:
            return match.group(1)
        
        return None
    
    @classmethod
    def extract_identifier_class(cls, url: str) -> Optional[str]:
        """Class method to extract identifier without instantiation."""
        # Post: reddit.com/r/sub/comments/POST_ID/title/
        match = re.search(r"/comments/([a-z0-9]+)/", url)
        if match:
            return match.group(1)
        
        # Profile: reddit.com/user/username
        match = re.search(r"/user/([^/]+)", url)
        if match:
            return match.group(1)
        
        # Subreddit: reddit.com/r/subreddit
        match = re.search(r"/r/([^/]+)", url)
        if match:
            return match.group(1)
        
        return None
    
    def _normalize_post(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize Reddit post data."""
        author = {
            "username": data.get("author", "[deleted]"),
            "display_name": data.get("author", "[deleted]"),
            "avatar": f"https://www.reddit.com/static/avatars/default.png",
        }
        
        engagement = {
            "likes": data.get("score"),
            "comments": data.get("num_comments"),
            "shares": data.get("num_crossposts"),
            "views": None,
        }
        
        media = []
        if data.get("post_hint") in ("image", "hosted:image"):
            media.append({"type": "image", "url": data.get("url", "")})
        elif data.get("post_hint") in ("video", "hosted:video"):
            media.append({"type": "video", "url": data.get("secure_media", {}).get("reddit_video", {}).get("fallback_url", "")})
        elif data.get("preview", {}).get("images"):
            for img in data["preview"]["images"]:
                media.append({"type": "image", "url": img.get("source", {}).get("url", "")})
        
        return build_post(
            platform="reddit",
            post_type="post",
            url=url,
            id=data.get("id", ""),
            text=data.get("selftext", "") or data.get("title", ""),
            timestamp=data.get("created_utc"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile(self, data: Dict[str, Any], username: str) -> SocialProfile:
        """Normalize Reddit user profile."""
        author = {
            "username": data.get("name", username),
            "display_name": data.get("name", username),
            "avatar": data.get("icon_img", ""),
            "verified": data.get("has_verified_email", False),
            "followers": None,  # Reddit doesn't expose follower count
            "bio": data.get("description", ""),
            "location": "",
            "joined_date": data.get("created_utc"),
        }
        
        engagement = {
            "posts_count": data.get("link_karma"),
            "comments_count": data.get("comment_karma"),
        }
        
        return build_profile(
            platform="reddit",
            username=username,
            url=f"https://reddit.com/user/{username}",
            profile_data={"author": author, "engagement": engagement, "id": data.get("id")},
        )


# Register the scraper
registry.register(RedditScraper)