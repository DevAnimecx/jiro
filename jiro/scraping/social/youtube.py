"""YouTube scraper using oEmbed and yt-dlp."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.youtube")


class YouTubeScraper(BaseSocialScraper):
    """YouTube scraper using oEmbed API and yt-dlp for metadata."""
    
    platform = "youtube"
    url_patterns = [
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
    ]
    supported_actions = ["video", "channel", "playlist", "shorts", "search"]
    rate_limit_rpm = 60
    requires_auth = False
    
    OEMBED_URL = "https://www.youtube.com/oembed"
    INVIDIOUS_INSTANCES = [
        "https://yewtu.be",
        "https://invidious.snopyta.org",
        "https://invidious.kavin.rocks",
    ]
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        self._invidious_index = 0
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a YouTube video."""
        video_id = self._extract_video_id(url)
        if not video_id:
            raise ValueError("Could not extract video ID from URL")
        
        # Try oEmbed first (fastest)
        try:
            oembed_data = await self._fetch_oembed(video_id)
            return self._normalize_from_oembed(oembed_data, url, video_id)
        except Exception as e:
            log.debug("oEmbed failed, trying Invidious", extra={"error": str(e)})
        
        # Try Invidious instances
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                data = await self._fetch_invidious(instance, video_id)
                return self._normalize_from_invidious(data, url, video_id)
            except Exception as e:
                log.debug(f"Invidious {instance} failed", extra={"error": str(e)})
                continue
        
        # Try yt-dlp as last resort
        try:
            data = await self._fetch_ytdlp(url)
            return self._normalize_from_ytdlp(data, url, video_id)
        except Exception as e:
            log.warning("All YouTube methods failed", extra={"error": str(e)})
            raise ValueError(f"Could not scrape YouTube video: {video_id}")
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a YouTube channel."""
        # Handle @handle, /channel/UC..., /c/..., /user/...
        channel_url = self._build_channel_url(username)
        
        # Try Invidious for channel info
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                data = await self._fetch_invidious_channel(instance, username)
                return self._normalize_channel(data, username)
            except Exception:
                log.debug("silenced fallback", exc_info=True)
                continue
        
        # Fallback: basic profile from URL
        return build_profile(
            platform="youtube",
            username=username,
            url=channel_url,
            profile_data={"author": {"username": username, "display_name": username}},
        )
    
    async def scrape_channel_videos(self, channel_id: str, limit: int = 25) -> List[SocialPost]:
        """Scrape videos from a channel."""
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                url = f"{instance}/api/v1/channels/{channel_id}/videos?limit={limit}"
                data = await self._fetch_json(url)
                videos = data.get("videos", []) or data.get("items", [])
                
                results = []
                for v in videos[:limit]:
                    video_id = v.get("videoId") or v.get("id")
                    if video_id:
                        video_url = f"https://youtube.com/watch?v={video_id}"
                        try:
                            post = await self.scrape_post(video_url)
                            results.append(post)
                        except Exception:
                            log.debug("silenced fallback", exc_info=True)
                return results
            except Exception:
                log.debug("silenced fallback", exc_info=True)
                continue
        
        return []
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search YouTube videos."""
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                url = f"{instance}/api/v1/search?q={query}&type=video&limit={limit}"
                data = await self._fetch_json(url)
                items = data.get("items", [])
                
                results = []
                for item in items[:limit]:
                    video_id = item.get("videoId") or item.get("id")
                    if video_id:
                        video_url = f"https://youtube.com/watch?v={video_id}"
                        try:
                            post = await self.scrape_post(video_url)
                            results.append(post)
                        except Exception:
                            log.debug("silenced fallback", exc_info=True)
                return results
            except Exception:
                log.debug("silenced fallback", exc_info=True)
                continue
        
        return []
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from various YouTube URL formats."""
        patterns = [
            r"(?:v=|/)([0-9A-Za-z_-]{11})(?:&|$|/)",
            r"youtu\.be/([0-9A-Za-z_-]{11})",
            r"embed/([0-9A-Za-z_-]{11})",
            r"shorts/([0-9A-Za-z_-]{11})",
            r"watch\?v=([0-9A-Za-z_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    def _build_channel_url(self, identifier: str) -> str:
        """Build channel URL from identifier."""
        if identifier.startswith("@"):
            return f"https://youtube.com/{identifier}"
        elif identifier.startswith("UC") and len(identifier) == 24:
            return f"https://youtube.com/channel/{identifier}"
        elif identifier.startswith("/"):
            return f"https://youtube.com{identifier}"
        else:
            return f"https://youtube.com/@{identifier}"
    
    async def _fetch_oembed(self, video_id: str) -> Dict[str, Any]:
        """Fetch video info via oEmbed."""
        url = f"{self.OEMBED_URL}?url=https://www.youtube.com/watch?v={video_id}&format=json"
        return await self._fetch_json(url)
    
    async def _fetch_invidious(self, instance: str, video_id: str) -> Dict[str, Any]:
        """Fetch video info from Invidious instance."""
        url = f"{instance}/api/v1/videos/{video_id}"
        return await self._fetch_json(url)
    
    async def _fetch_invidious_channel(self, instance: str, identifier: str) -> Dict[str, Any]:
        """Fetch channel info from Invidious."""
        # Try different endpoint patterns
        urls = [
            f"{instance}/api/v1/channels/{identifier}",
            f"{instance}/api/v1/channels/by-name/{identifier}",
        ]
        for url in urls:
            try:
                return await self._fetch_json(url)
            except Exception:
                log.debug("silenced fallback", exc_info=True)
                continue
        raise ValueError("Channel not found")
    
    async def _fetch_ytdlp(self, url: str) -> Dict[str, Any]:
        """Fetch video info using yt-dlp (subprocess)."""
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-json", "--no-download", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {stderr.decode()}")
        
        return json.loads(stdout.decode())
    
    def _normalize_from_oembed(self, data: Dict[str, Any], url: str, video_id: str) -> SocialPost:
        """Normalize from oEmbed response."""
        author = {
            "username": data.get("author_name", ""),
            "display_name": data.get("author_name", ""),
            "avatar": data.get("author_url", "").replace("www.youtube.com", "yt3.ggpht.com"),
            "verified": False,
            "profile_url": data.get("author_url", ""),
        }
        
        engagement = {
            "views": None,
            "likes": None,
        }
        
        media = [{
            "type": "video",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": data.get("thumbnail_url", ""),
            "width": data.get("width"),
            "height": data.get("height"),
        }]
        
        return build_post(
            platform="youtube",
            post_type="video",
            url=url,
            id=video_id,
            text=data.get("title", ""),
            timestamp=None,
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_from_invidious(self, data: Dict[str, Any], url: str, video_id: str) -> SocialPost:
        """Normalize from Invidious API response."""
        author = {
            "username": data.get("author", ""),
            "display_name": data.get("author", ""),
            "avatar": data.get("authorThumbnails", [{}])[-1].get("url", "") if data.get("authorThumbnails") else "",
            "verified": data.get("authorVerified", False),
            "profile_url": data.get("authorUrl", ""),
        }
        
        engagement = {
            "views": data.get("viewCount"),
            "likes": data.get("likeCount"),
            "comments": data.get("commentCount"),
        }
        
        media = [{
            "type": "video",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": data.get("videoThumbnails", [{}])[-1].get("url", "") if data.get("videoThumbnails") else "",
            "duration": data.get("lengthSeconds"),
        }]
        
        text = data.get("description", "") or data.get("title", "")
        
        return build_post(
            platform="youtube",
            post_type="video",
            url=url,
            id=video_id,
            text=text,
            timestamp=data.get("published"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_from_ytdlp(self, data: Dict[str, Any], url: str, video_id: str) -> SocialPost:
        """Normalize from yt-dlp response."""
        author = {
            "username": data.get("uploader", ""),
            "display_name": data.get("uploader", ""),
            "avatar": data.get("uploader_thumbnail", ""),
            "verified": data.get("uploader_verified", False),
            "profile_url": data.get("uploader_url", ""),
        }
        
        engagement = {
            "views": data.get("view_count"),
            "likes": data.get("like_count"),
            "comments": data.get("comment_count"),
        }
        
        media = [{
            "type": "video",
            "url": url,
            "thumbnail": data.get("thumbnail", ""),
            "width": data.get("width"),
            "height": data.get("height"),
            "duration": data.get("duration"),
        }]
        
        text = data.get("description", "") or data.get("title", "")
        
        return build_post(
            platform="youtube",
            post_type="video",
            url=url,
            id=video_id,
            text=text,
            timestamp=data.get("upload_date"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_channel(self, data: Dict[str, Any], identifier: str) -> SocialProfile:
        """Normalize channel data from Invidious."""
        author = {
            "username": data.get("author", identifier),
            "display_name": data.get("author", identifier),
            "avatar": data.get("authorThumbnails", [{}])[-1].get("url", "") if data.get("authorThumbnails") else "",
            "verified": data.get("authorVerified", False),
            "followers": data.get("subscriberCount"),
            "bio": data.get("description", ""),
        }
        
        return build_profile(
            platform="youtube",
            username=identifier,
            url=f"https://youtube.com/channel/{data.get('authorId', '')}" if data.get('authorId') else self._build_channel_url(identifier),
            profile_data={"author": author, "engagement": {}},
        )
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract video ID or channel identifier."""
        video_id = self._extract_video_id(url)
        if video_id:
            return video_id
        
        # Channel URLs
        patterns = [
            r"youtube\.com/channel/([^/]+)",
            r"youtube\.com/c/([^/]+)",
            r"youtube\.com/user/([^/]+)",
            r"youtube\.com/@([^/]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    @classmethod
    def extract_identifier_class(cls, url: str) -> Optional[str]:
        """Class method to extract identifier without instantiation."""
        # First try video ID
        patterns = [
            r"(?:v=|/)([0-9A-Za-z_-]{11})(?:&|$|/)",
            r"youtu\.be/([0-9A-Za-z_-]{11})",
            r"embed/([0-9A-Za-z_-]{11})",
            r"shorts/([0-9A-Za-z_-]{11})",
            r"watch\?v=([0-9A-Za-z_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # Channel URLs
        channel_patterns = [
            r"youtube\.com/channel/([^/]+)",
            r"youtube\.com/c/([^/]+)",
            r"youtube\.com/user/([^/]+)",
            r"youtube\.com/@([^/]+)",
        ]
        for pattern in channel_patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None


# Register the scraper
registry.register(YouTubeScraper)