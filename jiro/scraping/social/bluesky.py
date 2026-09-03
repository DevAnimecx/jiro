"""Bluesky scraper using xRPC (AT Protocol) API."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.bluesky")


class BlueskyScraper(BaseSocialScraper):
    """Bluesky scraper using xRPC/AT Protocol API (no auth required for public data)."""
    
    platform = "bluesky"
    url_patterns = [
        "bsky.app",
        "bluesky.app",
    ]
    supported_actions = ["post", "profile", "feed", "search", "thread"]
    rate_limit_rpm = 100
    requires_auth = False
    
    BASE_URL = "https://public.api.bsky.app"
    PDS_URL = "https://bsky.social"  # Default PDS
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a Bluesky post (record)."""
        # Extract DID and rkey from URL
        # Format: https://bsky.app/profile/did:plc:xyz/post/3abc123
        match = re.search(r"/profile/([^/]+)/post/([^/]+)", url)
        if not match:
            raise ValueError("Invalid Bluesky post URL format")
        
        did = match.group(1)
        rkey = match.group(2)
        
        return await self._get_post(did, rkey, url)
    
    async def scrape_profile(self, handle: str) -> SocialProfile:
        """Scrape a Bluesky profile by handle (e.g., user.bsky.social)."""
        # Resolve handle to DID
        did = await self._resolve_handle(handle)
        if not did:
            raise ValueError(f"Could not resolve handle: {handle}")
        
        return await self._get_profile(did, handle)
    
    async def get_author_feed(self, handle: str, limit: int = 25) -> List[SocialPost]:
        """Get posts from a user's feed."""
        did = await self._resolve_handle(handle)
        if not did:
            raise ValueError(f"Could not resolve handle: {handle}")
        
        url = f"{self.BASE_URL}/xrpc/app.bsky.feed.getAuthorFeed"
        params = {"actor": did, "limit": limit}
        data = await self._fetch_json(url, params=params)
        
        feed = data.get("feed", [])
        results = []
        for item in feed:
            post = item.get("post", {})
            if post:
                post_url = f"https://bsky.app/profile/{did}/post/{post.get('uri', '').split('/')[-1]}"
                results.append(self._normalize_post(post, post_url))
        
        return results
    
    async def get_timeline(self, handle: str, limit: int = 25) -> List[SocialPost]:
        """Get user's timeline (requires auth - not implemented for public)."""
        # This would require authentication
        raise NotImplementedError("Timeline requires authentication")
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search Bluesky posts."""
        url = f"{self.BASE_URL}/xrpc/app.bsky.feed.searchPosts"
        params = {"q": query, "limit": limit}
        data = await self._fetch_json(url, params=params)
        
        posts = data.get("posts", [])
        results = []
        for post in posts:
            post_url = f"https://bsky.app/profile/{post.get('author', {}).get('did', '')}/post/{post.get('uri', '').split('/')[-1]}"
            results.append(self._normalize_post(post, post_url))
        
        return results
    
    async def get_post_thread(self, url: str) -> Dict[str, Any]:
        """Get full thread for a post."""
        match = re.search(r"/profile/([^/]+)/post/([^/]+)", url)
        if not match:
            raise ValueError("Invalid Bluesky post URL")
        
        did = match.group(1)
        rkey = match.group(2)
        uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        
        thread_url = f"{self.BASE_URL}/xrpc/app.bsky.feed.getPostThread"
        params = {"uri": uri}
        data = await self._fetch_json(thread_url, params=params)
        
        return data.get("thread", {})
    
    async def _resolve_handle(self, handle: str) -> Optional[str]:
        """Resolve handle to DID."""
        # Handle can be a DID (did:plc:...) or a handle (user.bsky.social)
        if handle.startswith("did:"):
            return handle
        
        url = f"{self.BASE_URL}/xrpc/com.atproto.identity.resolveHandle"
        params = {"handle": handle}
        try:
            data = await self._fetch_json(url, params=params)
            return data.get("did")
        except Exception:
            return None
    
    async def _get_post(self, did: str, rkey: str, url: str) -> SocialPost:
        """Get a single post by DID and rkey."""
        uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        post_url = f"{self.BASE_URL}/xrpc/app.bsky.feed.getPosts"
        params = {"uris": uri}
        data = await self._fetch_json(post_url, params=params)
        
        posts = data.get("posts", [])
        if not posts:
            raise ValueError("Post not found")
        
        return self._normalize_post(posts[0], url)
    
    async def _get_profile(self, did: str, handle: str) -> SocialProfile:
        """Get profile by DID."""
        url = f"{self.BASE_URL}/xrpc/app.bsky.actor.getProfile"
        params = {"actor": did}
        data = await self._fetch_json(url, params=params)
        
        return self._normalize_profile(data, handle)
    
    def _normalize_post(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize Bluesky post."""
        author_data = data.get("author", {})
        record = data.get("record", {})
        
        author = {
            "username": author_data.get("handle", ""),
            "display_name": author_data.get("displayName", ""),
            "avatar": author_data.get("avatar", ""),
            "verified": bool(author_data.get("verification", {}).get("verified", False)),
            "followers": author_data.get("followersCount"),
            "profile_url": f"https://bsky.app/profile/{author_data.get('did', '')}",
            "bio": author_data.get("description", ""),
        }
        
        engagement = {
            "likes": data.get("likeCount"),
            "reposts": data.get("repostCount"),
            "replies": data.get("replyCount"),
            "quotes": data.get("quoteCount"),
        }
        
        media = []
        embed = record.get("embed", {})
        if embed:
            if embed.get("$type") == "app.bsky.embed.images":
                for img in embed.get("images", []):
                    media.append({
                        "type": "image",
                        "url": img.get("fullsize", ""),
                        "thumbnail": img.get("thumb", ""),
                        "alt_text": img.get("alt", ""),
                    })
            elif embed.get("$type") == "app.bsky.embed.video":
                media.append({
                    "type": "video",
                    "url": embed.get("video", {}).get("ref", {}).get("link", ""),
                    "thumbnail": embed.get("thumbnail", ""),
                })
        
        # Get text from record
        text = record.get("text", "")
        
        # Handle reply/ref
        if record.get("reply"):
            text = f"Replying to {record['reply']['parent']['uri']}: {text}"
        
        return build_post(
            platform="bluesky",
            post_type="post",
            url=url,
            id=data.get("uri", "").split("/")[-1],
            text=text,
            timestamp=record.get("createdAt"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile(self, data: Dict[str, Any], handle: str) -> SocialProfile:
        """Normalize Bluesky profile."""
        author = {
            "username": data.get("handle", handle),
            "display_name": data.get("displayName", ""),
            "avatar": data.get("avatar", ""),
            "verified": bool(data.get("verification", {}).get("verified", False)),
            "followers": data.get("followersCount"),
            "following": data.get("followsCount"),
            "posts_count": data.get("postsCount"),
            "bio": data.get("description", ""),
            "location": "",
            "joined_date": data.get("createdAt", ""),
        }
        
        return build_profile(
            platform="bluesky",
            username=handle,
            url=f"https://bsky.app/profile/{handle}",
            profile_data={"author": author, "engagement": {}, "id": data.get("did")},
        )
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract handle or post identifier from URL."""
        # Profile: bsky.app/profile/handle
        match = re.search(r"/profile/([^/]+)(?:/|$)", url)
        if match and "/post/" not in url:
            return match.group(1)
        
        # Post: bsky.app/profile/did/post/rkey
        match = re.search(r"/profile/([^/]+)/post/([^/]+)", url)
        if match:
            return f"{match.group(1)}/post/{match.group(2)}"
        
        return None
    
    @classmethod
    def extract_identifier_class(cls, url: str) -> Optional[str]:
        """Class method to extract identifier without instantiation."""
        # Profile: bsky.app/profile/handle
        match = re.search(r"/profile/([^/]+)(?:/|$)", url)
        if match and "/post/" not in url:
            return match.group(1)
        
        # Post: bsky.app/profile/did/post/rkey
        match = re.search(r"/profile/([^/]+)/post/([^/]+)", url)
        if match:
            return f"{match.group(1)}/post/{match.group(2)}"
        
        return None


# Register the scraper
registry.register(BlueskyScraper)