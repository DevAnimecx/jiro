"""YouTube scraper with 4-backend fallback: InnerTube → oEmbed → Invidious → Playwright.

InnerTube API provides full metadata, comments, subtitles, and chapters without browser.
Works with client spoofing (WEB, MWEB, IOS, ANDROID clients).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from jiro.scraping.social.base import BaseSocialScraper, RateLimitError, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.youtube")


class YouTubeScraper(BaseSocialScraper):
    """YouTube scraper with InnerTube API, oEmbed, Invidious, and Playwright."""
    
    platform = "youtube"
    url_patterns = ["youtube.com", "youtu.be", "youtube-nocookie.com", "ytimg.com"]
    supported_actions = ["video", "channel", "playlist", "shorts", "search", "comments", "subtitles"]
    rate_limit_rpm = 60
    requires_auth = False
    
    BACKENDS = ["innertube", "oembed", "invidious", "playwright"]
    
    OEMBED_URL = "https://www.youtube.com/oembed"
    INNERTUBE_URL = "https://www.youtube.com/youtubei/v1"
    INVIDIOUS_INSTANCES = [
        "https://yewtu.be",
        "https://vid.puffyan.us",
        "https://invidious.snopyta.org",
    ]
    
    # InnerTube client configurations
    INNERTUBE_CLIENTS = {
        "WEB": {
            "clientName": "WEB",
            "clientVersion": "2.20241126.01.00",
            "hl": "en",
            "gl": "US",
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        },
        "MWEB": {
            "clientName": "MWEB",
            "clientVersion": "2.20241126.01.00",
            "hl": "en",
            "gl": "US",
            "userAgent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
        },
        "IOS": {
            "clientName": "IOS",
            "clientVersion": "19.45.4",
            "deviceMake": "Apple",
            "deviceModel": "iPhone16,2",
            "hl": "en",
            "gl": "US",
            "osName": "iOS",
            "osVersion": "18.1.0",
            "userAgent": "com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 18_1_0 like Mac OS X;)",
        },
        "ANDROID": {
            "clientName": "ANDROID",
            "clientVersion": "19.44.38",
            "androidSdkVersion": 34,
            "hl": "en",
            "gl": "US",
            "osName": "Android",
            "osVersion": "14",
            "userAgent": "com.google.android.youtube/19.44.38 (Linux; U; Android 14) gzip",
        },
        "TV_EMBEDDED": {
            "clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
            "clientVersion": "2.0",
            "hl": "en",
            "gl": "US",
        },
    }
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        cfg = settings.social.get("youtube", {}) if settings else {}
        self.client_type = cfg.get("client", "WEB")
        self.innertube_api_key = cfg.get("api_key", "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8")
        self.visitor_data = cfg.get("visitor_data", "")
        self.cookie = cfg.get("cookie", "")
    
    def _get_innertube_headers(self, client_name: Optional[str] = None) -> Dict[str, str]:
        client = self.INNERTUBE_CLIENTS.get(client_name or self.client_type, self.INNERTUBE_CLIENTS["WEB"])
        headers = {
            "User-Agent": client.get("userAgent", "Mozilla/5.0"),
            "Content-Type": "application/json",
            "X-Youtube-Client-Name": self._get_client_id(client.get("clientName", "WEB")),
            "X-Youtube-Client-Version": client.get("clientVersion", "2.0"),
            "Origin": "https://www.youtube.com",
            "Referer": "https://www.youtube.com/",
        }
        if self.visitor_data:
            headers["X-Goog-Visitor-Id"] = self.visitor_data
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers
    
    def _get_client_id(self, name: str) -> str:
        ids = {"WEB": 1, "MWEB": 2, "IOS": 5, "ANDROID": 3, "TVHTML5_SIMPLY_EMBEDDED_PLAYER": 85}
        return str(ids.get(name, 1))
    
    def _get_client_context(self, client_name: Optional[str] = None) -> Dict[str, Any]:
        name = client_name or self.client_type
        client = self.INNERTUBE_CLIENTS.get(name, self.INNERTUBE_CLIENTS["WEB"])
        context = {
            "client": {
                "clientName": client.get("clientName", "WEB"),
                "clientVersion": client.get("clientVersion", "2.0"),
                "hl": client.get("hl", "en"),
                "gl": client.get("gl", "US"),
            }
        }
        if client.get("deviceModel"):
            context["client"]["deviceModel"] = client["deviceModel"]
        if client.get("osName"):
            context["client"]["osName"] = client["osName"]
        if client.get("osVersion"):
            context["client"]["osVersion"] = client["osVersion"]
        return context
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    async def scrape_post(self, url: str) -> SocialPost:
        video_id = self._extract_video_id(url)
        if not video_id:
            raise ValueError("Could not extract video ID from URL")
        
        backends = self._get_backend_order()
        errors = []
        
        for backend in backends:
            try:
                if backend == "innertube":
                    return await self._scrape_innertube(video_id, url)
                elif backend == "oembed":
                    return await self._scrape_oembed(video_id, url)
                elif backend == "invidious":
                    return await self._scrape_invidious(video_id, url)
                elif backend == "playwright":
                    return await self._scrape_playwright(url)
            except Exception as e:
                log.debug("YouTube backend %s failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
        
        raise ValueError(f"All YouTube backends failed for {video_id}: {errors}")
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        # Try InnerTube
        try:
            return await self._scrape_profile_innertube(username)
        except Exception as e:
            log.debug("InnerTube profile failed: %s", e)
        
        # Try Invidious
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                data = await self._fetch_json(f"{instance}/api/v1/channels/{username}")
                return self._normalize_channel(data, username)
            except Exception:
                continue
        
        # Fallback
        return build_profile(
            platform="youtube",
            username=username,
            url=self._build_channel_url(username),
            profile_data={"author": {"username": username, "display_name": username}},
        )
    
    async def scrape_channel_videos(self, channel_id: str, limit: int = 25) -> List[SocialPost]:
        """Scrape videos from a channel with pagination."""
        try:
            return await self._get_channel_videos_innertube(channel_id, limit)
        except Exception:
            log.debug("InnerTube channel videos failed", exc_info=True)
        
        # Invidious fallback
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                url = f"{instance}/api/v1/channels/{channel_id}/videos?limit={limit}"
                data = await self._fetch_json(url)
                videos = data.get("videos", []) or data.get("items", [])
                results = []
                for v in videos[:limit]:
                    vid = v.get("videoId") or v.get("id")
                    if vid:
                        video_url = f"https://youtube.com/watch?v={vid}"
                        try:
                            post = await self.scrape_post(video_url)
                            results.append(post)
                        except Exception:
                            continue
                return results
            except Exception:
                continue
        
        return []
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search YouTube videos."""
        # InnerTube search
        try:
            return await self._search_innertube(query, limit)
        except Exception:
            log.debug("InnerTube search failed", exc_info=True)
        
        # Invidious search
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                url = f"{instance}/api/v1/search?q={query}&type=video&limit={limit}"
                data = await self._fetch_json(url)
                items = data.get("items", [])
                results = []
                for item in items[:limit]:
                    vid = item.get("videoId") or item.get("id")
                    if vid:
                        video_url = f"https://youtube.com/watch?v={vid}"
                        try:
                            post = await self.scrape_post(video_url)
                            results.append(post)
                        except Exception:
                            continue
                return results
            except Exception:
                continue
        
        return []
    
    async def scrape_comments(self, url: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Scrape comments for a video."""
        video_id = self._extract_video_id(url)
        if not video_id:
            raise ValueError("Could not extract video ID")
        
        # InnerTube comments
        try:
            return await self._get_comments_innertube(video_id, limit)
        except Exception:
            log.debug("InnerTube comments failed", exc_info=True)
        
        # Invidious comments
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                data = await self._fetch_json(f"{instance}/api/v1/comments/{video_id}?sort_by=top&limit={limit}")
                comments = data.get("comments", [])
                return [
                    {
                        "author": c.get("author", ""),
                        "text": c.get("content", ""),
                        "likes": c.get("likeCount"),
                        "timestamp": c.get("published"),
                        "author_avatar": c.get("authorThumbnails", [{}])[-1].get("url", "") if c.get("authorThumbnails") else "",
                    }
                    for c in comments[:limit]
                ]
            except Exception:
                continue
        
        return []
    
    async def scrape_subtitles(self, url: str) -> List[Dict[str, str]]:
        """Get available subtitles/captions for a video."""
        video_id = self._extract_video_id(url)
        if not video_id:
            raise ValueError("Could not extract video ID")
        
        # InnerTube player
        try:
            data = await self._innertube_player(video_id)
            captions = data.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
            return [
                {
                    "language": c.get("languageCode", ""),
                    "name": c.get("name", {}).get("simpleText", ""),
                    "url": c.get("baseUrl", ""),
                    "is_auto": c.get("kind") == "asr",
                }
                for c in captions
            ]
        except Exception:
            log.debug("InnerTube subtitles failed", exc_info=True)
        
        # Invidious
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                data = await self._fetch_json(f"{instance}/api/v1/captions/{video_id}")
                captions = data.get("captions", [])
                return [
                    {
                        "language": c.get("language_code", ""),
                        "name": c.get("label", ""),
                        "url": f"{instance}{c.get('url', '')}",
                        "is_auto": c.get("autoGenerated", False),
                    }
                    for c in captions
                ]
            except Exception:
                continue
        
        return []
    
    def _get_backend_order(self) -> List[str]:
        if self.backend if hasattr(self, 'backend') and self.backend != "auto" else False:
            return [self.backend] + [b for b in self.BACKENDS if b != self.backend]
        return self.BACKENDS
    
    @property
    def backend(self):
        if hasattr(self, '_backend'):
            return self._backend
        return "auto"
    
    # =========================================================================
    # InnerTube Backend (primary)
    # =========================================================================
    
    async def _innertube_request(self, endpoint: str, body: Dict[str, Any], client_name: Optional[str] = None) -> Dict[str, Any]:
        """Make an InnerTube API request."""
        url = f"{self.INNERTUBE_URL}/{endpoint}"
        headers = self._get_innertube_headers(client_name)
        
        full_body = {
            "context": self._get_client_context(client_name),
            **body,
        }
        
        text, resp = await self.client.post(url, engine=self.platform, extra_headers=headers, content=json.dumps(full_body))
        
        if resp.status_code == 429:
            raise RateLimitError("youtube")
        
        resp.raise_for_status()
        return resp.json()
    
    async def _innertube_player(self, video_id: str, client_name: Optional[str] = None) -> Dict[str, Any]:
        """Get player data via InnerTube."""
        return await self._innertube_request("player", {"videoId": video_id}, client_name)
    
    async def _innertube_next(self, video_id: str, client_name: Optional[str] = None) -> Dict[str, Any]:
        """Get next/engagement panel data via InnerTube."""
        return await self._innertube_request("next", {"videoId": video_id}, client_name)
    
    async def _scrape_innertube(self, video_id: str, url: str) -> SocialPost:
        """Scrape video via InnerTube player + next endpoints."""
        player_data = await self._innertube_player(video_id)
        
        # Check playability
        status = player_data.get("playabilityStatus", {})
        if status.get("status") == "LOGIN_REQUIRED":
            # Try with different client
            for alt_client in ["ANDROID", "IOS", "TV_EMBEDDED"]:
                try:
                    player_data = await self._innertube_player(video_id, alt_client)
                    status = player_data.get("playabilityStatus", {})
                    if status.get("status") != "LOGIN_REQUIRED":
                        break
                except Exception:
                    continue
        
        # Get engagement data
        next_data = await self._innertube_next(video_id)
        
        return self._normalize_innertube(player_data, next_data, url, video_id)
    
    async def _scrape_profile_innertube(self, username: str) -> SocialProfile:
        """Scrape channel via InnerTube browse."""
        browse_id = username if username.startswith("UC") and len(username) == 24 else None
        
        if not browse_id:
            # Resolve handle to channel ID via search
            data = await self._innertube_request("search", {
                "query": username,
                "params": "EgIQAg%3D%3D",  # Channels filter
            })
            results = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
            for section in results:
                items = section.get("itemSectionRenderer", {}).get("contents", [])
                for item in items:
                    chan = item.get("channelRenderer", {})
                    if chan.get("channelId", "").startswith("UC"):
                        browse_id = chan["channelId"]
                        break
                if browse_id:
                    break
        
        if not browse_id:
            raise ValueError(f"Could not find channel for {username}")
        
        data = await self._innertube_request("browse", {"browseId": browse_id})
        return self._normalize_channel_innertube(data, username)
    
    async def _get_channel_videos_innertube(self, channel_id: str, limit: int) -> List[SocialPost]:
        """Get channel videos via InnerTube browse with pagination."""
        if not (channel_id.startswith("UC") and len(channel_id) == 24):
            profile = await self._scrape_profile_innertube(channel_id)
            channel_id = profile.data.get("id", "")
            if not channel_id:
                raise ValueError("Could not resolve channel ID")
        
        all_videos = []
        continuation = None
        
        while len(all_videos) < limit:
            body = {"browseId": channel_id}
            if continuation:
                body["continuation"] = continuation
            else:
                body["params"] = "CAISAhAB"  # Videos tab
            
            data = await self._innertube_request("browse", body)
            
            tab_content = (
                data.get("contents", {})
                .get("twoColumnBrowseResultsRenderer", {})
                .get("tabs", [])
            )
            
            videos_tab = None
            for tab in tab_content:
                tab_renderer = tab.get("tabRenderer", {})
                if tab_renderer.get("selected"):
                    videos_tab = tab_renderer
                    break
            
            if not videos_tab:
                videos_tab = tab_content[0].get("tabRenderer", {}) if tab_content else {}
            
            content = videos_tab.get("content", {})
            
            # Grid items
            grid_items = (
                content.get("richGridRenderer", {}).get("contents", [])
            )
            
            for item in grid_items:
                if len(all_videos) >= limit:
                    break
                
                rich_item = item.get("richItemRenderer", {}).get("content", {})
                video_renderer = rich_item.get("videoRenderer", {})
                
                vid = video_renderer.get("videoId", "")
                if vid:
                    video_url = f"https://youtube.com/watch?v={vid}"
                    all_videos.append(self._normalize_video_renderer(video_renderer, video_url))
            
            # Continuation
            continuation_items = (
                content.get("richGridRenderer", {}).get("contents", [])[-1:]
                if grid_items else []
            )
            continuation = None
            for ci in continuation_items:
                cont_renderer = ci.get("continuationItemRenderer", {})
                if cont_renderer:
                    continuation = cont_renderer.get("continuationEndpoint", {}).get("continuationCommand", {}).get("token")
            
            if not continuation:
                break
        
        return all_videos
    
    async def _search_innertube(self, query: str, limit: int) -> List[SocialPost]:
        """Search via InnerTube."""
        data = await self._innertube_request("search", {
            "query": query,
            "params": "CAISAhAB",  # Video results
        })
        
        results_data = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )
        
        results = []
        for section in results_data:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                if len(results) >= limit:
                    break
                video_renderer = item.get("videoRenderer", {})
                vid = video_renderer.get("videoId", "")
                if vid:
                    video_url = f"https://youtube.com/watch?v={vid}"
                    results.append(self._normalize_video_renderer(video_renderer, video_url))
        
        return results
    
    async def _get_comments_innertube(self, video_id: str, limit: int) -> List[Dict[str, Any]]:
        """Get comments via InnerTube continuation."""
        next_data = await self._innertube_next(video_id)
        
        # Find comments continuation token
        contents = (
            next_data.get("contents", {})
            .get("twoColumnWatchNextResults", {})
            .get("results", {})
            .get("results", {})
            .get("contents", [])
        )
        
        continuation_token = None
        for content in contents:
            section = content.get("itemSectionRenderer", {})
            items = section.get("contents", [])
            for item in items:
                cont_item = item.get("continuationItemRenderer", {})
                if cont_item:
                    continuation_token = (
                        cont_item.get("continuationEndpoint", {})
                        .get("continuationCommand", {})
                        .get("token")
                    )
        
        if not continuation_token:
            return []
        
        # Fetch comments
        data = await self._innertube_request("continuation", {"continuation": continuation_token})
        
        all_comments = []
        continuation = None
        
        while len(all_comments) < limit:
            actions = data.get("onResponseReceivedEndpoints", [])
            for action in actions:
                items = (
                    action.get("reloadContinuationItemsCommand", {}).get("continuationItems", [])
                    or action.get("appendContinuationItemsAction", {}).get("continuationItems", [])
                )
                
                for item in items:
                    if len(all_comments) >= limit:
                        break
                    
                    comment_renderer = item.get("commentThreadRenderer", {}).get("comment", {}).get("commentRenderer", {})
                    if not comment_renderer:
                        continue
                    
                    author_text = comment_renderer.get("authorText", {})
                    content_text = comment_renderer.get("contentText", {})
                    
                    all_comments.append({
                        "author": author_text.get("simpleText", ""),
                        "text": "".join([run.get("text", "") for run in content_text.get("runs", [])]),
                        "likes": comment_renderer.get("voteCount", {}).get("simpleText", "0"),
                        "timestamp": comment_renderer.get("publishedTimeText", {}).get("runs", [{}])[0].get("text", ""),
                        "author_avatar": comment_renderer.get("authorThumbnail", {}).get("thumbnails", [{}])[-1].get("url", ""),
                        "is_hearted": bool(commentRenderer.get("actionButtons", {}).get("commentActionButtonsRenderer", {}).get("creatorHeartRenderer")),
                    })
                
                # Get next continuation
                for item in items:
                    if item.get("continuationItemRenderer"):
                        continuation = (
                            item.get("continuationItemRenderer", {})
                            .get("continuationEndpoint", {})
                            .get("continuationCommand", {})
                            .get("token")
                        )
            
            if not continuation:
                break
            
            data = await self._innertube_request("continuation", {"continuation": continuation})
        
        return all_comments
    
    def _normalize_innertube(self, player_data: Dict, next_data: Dict, url: str, video_id: str) -> SocialPost:
        """Normalize InnerTube player + next data."""
        video_details = player_data.get("videoDetails", {})
        microformat = player_data.get("microformat", {}).get("playerMicroformatRenderer", {})
        
        # Author from video details
        author = {
            "username": video_details.get("author", ""),
            "display_name": video_details.get("author", ""),
            "avatar": "",
            "verified": False,
            "profile_url": f"https://youtube.com/@{video_details.get('author', '')}",
        }
        
        # Try to get author from next data
        owner = (
            next_data.get("contents", {})
            .get("twoColumnWatchNextResults", {})
            .get("results", {})
            .get("results", {})
            .get("contents", [{}])[1]
            .get("videoSecondaryInfoRenderer", {})
            .get("owner", {})
            .get("videoOwnerRenderer", {})
        )
        if owner:
            author["display_name"] = owner.get("title", {}).get("runs", [{}])[0].get("text", "")
            avatars = owner.get("thumbnail", {}).get("thumbnails", [])
            if avatars:
                author["avatar"] = avatars[-1].get("url", "")
        
        engagement = {
            "views": int(video_details.get("viewCount", 0)) or None,
            "likes": None,
            "comments": None,
        }
        
        # Get likes from next data
        sentiment = (
            next_data.get("contents", {})
            .get("twoColumnWatchNextResults", {})
            .get("results", {})
            .get("results", {})
            .get("contents", [])
        )
        for content in sentiment:
            info = content.get("videoPrimaryInfoRenderer", {})
            buttons = info.get("videoActions", {}).get("menuRenderer", {}).get("topLevelButtons", [])
            for button in buttons:
                toggle = button.get("segmentedLikeDislikeButtonViewModel", {})
                like_btn = toggle.get("likeButtonViewModel", {}).get("likeButtonViewModel", {}).get("toggleButtonViewModel", {}).get("toggleButtonViewModel", {})
                like_text = like_btn.get("defaultButtonViewModel", {}).get("buttonViewModel", {}).get("title", "0")
                if like_text and like_text != "0":
                    engagement["likes"] = normalize_number(like_text)
            
            comment_count = info.get("dateText", {}).get("runs", [])
        
        # Description
        desc_runs = video_details.get("shortDescription", "")
        
        # Chapters
        chapters = []
        for marker in player_data.get("chapters", []):
            for chapter in marker.get("chapters", []):
                chapters.append({
                    "title": chapter.get("chapterRenderer", {}).get("title", {}).get("simpleText", ""),
                    "start": chapter.get("chapterRenderer", {}).get("timeRangeStartMillis", 0) / 1000,
                })
        
        media = [{
            "type": "video",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", ""),
            "duration": int(video_details.get("lengthSeconds", 0)) or None,
            "width": video_details.get("width"),
            "height": video_details.get("height"),
        }]
        
        return build_post(
            platform="youtube",
            post_type="video",
            url=url,
            id=video_id,
            text=desc_runs or video_details.get("title", ""),
            timestamp=microformat.get("publishDate"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_video_renderer(self, renderer: Dict, url: str) -> SocialPost:
        """Normalize a videoRenderer from search/channel results."""
        vid = renderer.get("videoId", "")
        title_runs = renderer.get("title", {}).get("runs", [])
        title = "".join([r.get("text", "") for r in title_runs])
        
        owner_text = renderer.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
        author = {
            "username": owner_text,
            "display_name": owner_text,
            "avatar": "",
            "verified": False,
        }
        
        engagement = {
            "views": normalize_number(renderer.get("viewCountText", {}).get("simpleText", "0")),
            "likes": None,
        }
        
        thumbnails = renderer.get("thumbnail", {}).get("thumbnails", [])
        thumbnail = thumbnails[-1].get("url", "") if thumbnails else ""
        
        length_text = renderer.get("lengthText", {}).get("simpleText", "")
        duration = None
        if length_text:
            parts = length_text.split(":")
            try:
                if len(parts) == 3:
                    duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    duration = int(parts[0]) * 60 + int(parts[1])
            except ValueError:
                pass
        
        media = [{"type": "video", "url": url, "thumbnail": thumbnail, "duration": duration}]
        
        return build_post(
            platform="youtube",
            post_type="video",
            url=url,
            id=vid,
            text=title,
            timestamp=None,
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_channel_innertube(self, data: Dict, username: str) -> SocialProfile:
        """Normalize channel from InnerTube browse."""
        header = data.get("header", {}).get("c4TabbedHeaderRenderer", {})
        metadata = data.get("metadata", {}).get("channelMetadataRenderer", {})
        
        author = {
            "username": metadata.get("externalId", username).lstrip("UC"),
            "display_name": metadata.get("title", username),
            "avatar": metadata.get("avatar", {}).get("thumbnails", [{}])[-1].get("url", ""),
            "verified": metadata.get("vanityChannelUrl", "").endswith("/verified"),
            "followers": None,
            "bio": metadata.get("description", ""),
            "location": metadata.get("location", ""),
            "profile_url": metadata.get("channelUrl", f"https://youtube.com/@{username}"),
        }
        
        sub_count = header.get("subscriberCountText", {}).get("simpleText", "")
        if sub_count:
            author["followers"] = normalize_number(sub_count)
        
        return build_profile(
            platform="youtube",
            username=username,
            url=metadata.get("channelUrl", f"https://youtube.com/@{username}"),
            profile_data={"author": author, "engagement": {}, "id": metadata.get("externalId")},
        )
    
    # =========================================================================
    # oEmbed Backend
    # =========================================================================
    
    async def _scrape_oembed(self, video_id: str, url: str) -> SocialPost:
        oembed_url = f"{self.OEMBED_URL}?url=https://www.youtube.com/watch?v={video_id}&format=json"
        data = await self._fetch_json(oembed_url)
        
        author = {
            "username": data.get("author_name", ""),
            "display_name": data.get("author_name", ""),
            "avatar": data.get("author_url", "").replace("www.youtube.com", "yt3.ggpht.com"),
            "verified": False,
            "profile_url": data.get("author_url", ""),
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
            engagement={"views": None, "likes": None},
            media=media,
        )
    
    # =========================================================================
    # Invidious Backend
    # =========================================================================
    
    async def _scrape_invidious(self, video_id: str, url: str) -> SocialPost:
        for instance in self.INVIDIOUS_INSTANCES:
            try:
                data = await self._fetch_json(f"{instance}/api/v1/videos/{video_id}")
                return self._normalize_invidious(data, url, video_id)
            except Exception:
                continue
        raise ValueError("All Invidious instances failed")
    
    def _normalize_invidious(self, data: Dict, url: str, video_id: str) -> SocialPost:
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
    
    def _normalize_channel(self, data: Dict, identifier: str) -> SocialProfile:
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
            url=f"https://youtube.com/channel/{data.get('authorId', '')}" if data.get("authorId") else self._build_channel_url(identifier),
            profile_data={"author": author, "engagement": {}},
        )
    
    # =========================================================================
    # Playwright Backend
    # =========================================================================
    
    async def _scrape_playwright(self, url: str) -> SocialPost:
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector("#above-the-fold", timeout=10000)
            
            data = await page.evaluate("""() => {
                const title = document.querySelector('h1 yt-formatted-string')?.innerText || '';
                const channel = document.querySelector('#channel-name a')?.innerText || '';
                const views = document.querySelector('#info span:first-child')?.innerText || '0';
                const likes = document.querySelector('#top-level-buttons-computed button:first-child')?.innerText || '0';
                const date = document.querySelector('#info-strings span')?.innerText || '';
                const desc = document.querySelector('#description-inline-expander')?.innerText || '';
                const avatar = document.querySelector('#channel-header img')?.src || '';
                
                return {title, channel, views, likes, date, desc, avatar};
            }""")
            
            video_id = self._extract_video_id(url) or url.split("v=")[-1]
            
            return build_post(
                platform="youtube",
                post_type="video",
                url=url,
                id=video_id,
                text=data.get("desc", "") or data.get("title", ""),
                timestamp=data.get("date"),
                author={"username": data.get("channel", ""), "avatar": data.get("avatar", "")},
                engagement={
                    "views": normalize_number(data.get("views")),
                    "likes": normalize_number(data.get("likes")),
                },
                media=[{"type": "video", "url": url, "thumbnail": ""}],
            )
    
    # =========================================================================
    # URL parsing
    # =========================================================================
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        patterns = [
            r"(?:v=|/)([0-9A-Za-z_-]{11})(?:&|$|/)",
            r"youtu\.be/([0-9A-Za-z_-]{11})",
            r"embed/([0-9A-Za-z_-]{11})",
            r"shorts/([0-9A-Za-z_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    def _build_channel_url(self, identifier: str) -> str:
        if identifier.startswith("@"):
            return f"https://youtube.com/{identifier}"
        elif identifier.startswith("UC") and len(identifier) == 24:
            return f"https://youtube.com/channel/{identifier}"
        return f"https://youtube.com/@{identifier}"
    
    def extract_identifier(self, url: str) -> Optional[str]:
        video_id = self._extract_video_id(url)
        if video_id:
            return video_id
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
        patterns = [
            r"(?:v=|/)([0-9A-Za-z_-]{11})(?:&|$|/)",
            r"youtu\.be/([0-9A-Za-z_-]{11})",
            r"embed/([0-9A-Za-z_-]{11})",
            r"shorts/([0-9A-Za-z_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
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


registry.register(YouTubeScraper)
