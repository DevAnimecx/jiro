"""TikTok scraper with 4-backend fallback: Internal API → oEmbed → Playwright → Embed Page.

Internal API provides full video data, comments, and user profiles. Falls back to
oEmbed for basic data, Playwright for full rendering, and embed page for extraction.
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

log = get_logger("jiro.scraping.social.tiktok")


class TikTokScraper(BaseSocialScraper):
    """TikTok scraper with internal API, oEmbed, Playwright, and embed backends."""
    
    platform = "tiktok"
    url_patterns = ["tiktok.com", "vm.tiktok.com", "vt.tiktok.com"]
    supported_actions = ["video", "profile", "hashtag", "search", "trending", "comments"]
    rate_limit_rpm = 60
    requires_auth = False
    
    BACKENDS = ["internal_api", "oembed", "embed_page", "playwright"]
    
    OEMBED_URL = "https://www.tiktok.com/oembed"
    INTERNAL_API_BASE = "https://www.tiktok.com"
    
    # Browser-like headers for internal API
    _DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.tiktok.com/",
        "Origin": "https://www.tiktok.com",
    }
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        cfg = settings.social.get("tiktok", {}) if settings else {}
        self.use_browser = cfg.get("use_browser", True)
        self.backend = cfg.get("backend", "auto")
        self.ms_token = cfg.get("ms_token", "")
        self.session_id = cfg.get("session_id", "")
    
    def _get_headers(self) -> Dict[str, str]:
        headers = dict(self._DEFAULT_HEADERS)
        if self.ms_token:
            headers["Cookie"] = f"msToken={self.ms_token}"
        if self.session_id:
            headers.setdefault("Cookie", "")
            headers["Cookie"] += f"; sessionid={self.session_id}"
        return headers
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a TikTok video with fallback backends."""
        video_id = await self._resolve_url(url)
        if not video_id:
            raise ValueError("Could not extract video ID from URL")
        
        backends = self._get_backend_order()
        errors = []
        
        for backend in backends:
            try:
                if backend == "internal_api":
                    return await self._scrape_internal(video_id, url)
                elif backend == "oembed":
                    return await self._scrape_oembed(video_id, url)
                elif backend == "embed_page":
                    return await self._scrape_embed(video_id, url)
                elif backend == "playwright":
                    return await self._scrape_playwright(url)
            except Exception as e:
                log.debug("TikTok backend %s failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
        
        raise ValueError(f"All TikTok backends failed for video {video_id}: {errors}")
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a TikTok profile."""
        username = username.lstrip("@")
        
        # Try internal API
        try:
            return await self._scrape_profile_internal(username)
        except Exception:
            log.debug("Internal profile failed", exc_info=True)
        
        # Try Playwright
        if self.use_browser:
            try:
                return await self._scrape_profile_playwright(username)
            except Exception:
                log.debug("Playwright profile failed", exc_info=True)
        
        raise ValueError(f"Could not scrape TikTok profile: {username}")
    
    async def get_user_videos(self, username: str, limit: int = 25) -> List[SocialPost]:
        """Get videos from a user with pagination."""
        username = username.lstrip("@")
        
        try:
            return await self._get_user_videos_internal(username, limit)
        except Exception:
            log.debug("Internal user videos failed", exc_info=True)
        
        if self.use_browser:
            try:
                return await self._get_user_videos_playwright(username, limit)
            except Exception:
                log.debug("Playwright user videos failed", exc_info=True)
        
        return []
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search TikTok videos."""
        try:
            return await self._search_internal(query, limit)
        except Exception:
            log.debug("Internal search failed", exc_info=True)
        
        if self.use_browser:
            try:
                return await self._search_playwright(query, limit)
            except Exception:
                log.debug("Playwright search failed", exc_info=True)
        
        return []
    
    async def get_trending(self, limit: int = 25) -> List[SocialPost]:
        """Get trending TikTok videos."""
        try:
            return await self._get_trending_internal(limit)
        except Exception:
            log.debug("Internal trending failed", exc_info=True)
        
        if self.use_browser:
            try:
                return await self._get_trending_playwright(limit)
            except Exception:
                log.debug("Playwright trending failed", exc_info=True)
        
        return []
    
    async def scrape_comments(self, url: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Scrape comments for a video."""
        video_id = await self._resolve_url(url)
        if not video_id:
            raise ValueError("Could not extract video ID")
        
        try:
            return await self._get_comments_internal(video_id, limit)
        except Exception:
            log.debug("Internal comments failed", exc_info=True)
        
        return []
    
    def _get_backend_order(self) -> List[str]:
        if self.backend and self.backend != "auto":
            return [self.backend] + [b for b in self.BACKENDS if b != self.backend]
        return self.BACKENDS
    
    # =========================================================================
    # URL resolution
    # =========================================================================
    
    async def _resolve_url(self, url: str) -> Optional[str]:
        """Resolve short URLs to video IDs."""
        video_id = self._extract_video_id(url)
        if video_id:
            return video_id
        
        # Follow redirects for short URLs
        try:
            headers = self._get_headers()
            text, resp = await self.client.head(url, engine=self.platform, extra_headers=headers, follow_redirects=True)
            final_url = str(resp.url)
            return self._extract_video_id(final_url)
        except Exception:
            log.debug("URL redirect failed", exc_info=True)
        
        return None
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        patterns = [
            r"tiktok\.com/@[\w.]+/video/(\d+)",
            r"vm\.tiktok\.com/([\w]+)",
            r"vt\.tiktok\.com/([\w]+)",
            r"tiktok\.com/t/([\w]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    # =========================================================================
    # Internal API Backend (primary)
    # =========================================================================
    
    async def _internal_api_get(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Make internal API request."""
        url = f"{self.INTERNAL_API_BASE}{endpoint}"
        headers = self._get_headers()
        
        text, resp = await self.client.get(url, engine=self.platform, params=params, extra_headers=headers)
        
        if resp.status_code == 429:
            raise RateLimitError("tiktok")
        
        resp.raise_for_status()
        
        # Internal API returns HTML with __UNIVERSAL_DATA_FOR_REHYDRATION__
        if resp.headers.get("content-type", "").startswith("text/html"):
            return self._extract_rehydration_data(text)
        
        return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    
    def _extract_rehydration_data(self, html: str) -> Dict[str, Any]:
        """Extract data from __UNIVERSAL_DATA_FOR_REHYDRATION__ script tag."""
        match = re.search(
            r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"\s+[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Also try SIGI_STATE
        match = re.search(r'<script\s+id="SIGI_STATE"\s+[^>]*>(.*?)</script>', html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        
        return {}
    
    async def _scrape_internal(self, video_id: str, url: str) -> SocialPost:
        """Scrape video via internal API (page HTML)."""
        video_url = f"{self.INTERNAL_API_BASE}/video/{video_id}"
        headers = self._get_headers()
        
        text, resp = await self.client.get(video_url, engine=self.platform, extra_headers=headers)
        
        if resp.status_code == 429:
            raise RateLimitError("tiktok")
        resp.raise_for_status()
        
        data = self._extract_rehydration_data(text)
        
        # Try to get video info from rehydration data
        default_scope = data.get("__DEFAULT_SCOPE__", {})
        video_detail = default_scope.get("webapp.video-detail", {}).get("itemInfo", {}).get("itemStruct", {})
        
        if not video_detail:
            # Try alternate path
            video_detail = default_scope.get("videoDetail", {}).get("itemStruct", {})
        
        if not video_detail or not video_detail.get("id"):
            raise ValueError("Video not found in rehydration data")
        
        return self._normalize_internal(video_detail, url)
    
    async def _scrape_profile_internal(self, username: str) -> SocialProfile:
        """Scrape profile via internal API."""
        profile_url = f"{self.INTERNAL_API_BASE}/@{username}"
        headers = self._get_headers()
        
        text, resp = await self.client.get(profile_url, engine=self.platform, extra_headers=headers)
        
        if resp.status_code == 429:
            raise RateLimitError("tiktok")
        resp.raise_for_status()
        
        data = self._extract_rehydration_data(text)
        
        default_scope = data.get("__DEFAULT_SCOPE__", {})
        user_detail = default_scope.get("webapp.user-detail", {}).get("userInfo", {})
        
        if not user_detail or not user_detail.get("user"):
            raise ValueError("Profile not found")
        
        return self._normalize_profile_internal(user_detail, username)
    
    async def _get_user_videos_internal(self, username: str, limit: int) -> List[SocialPost]:
        """Get user videos via internal API with pagination."""
        # First get user profile to get secUid
        profile = await self._scrape_profile_internal(username)
        sec_uid = profile.data.get("secUid")
        
        if not sec_uid:
            raise ValueError("Could not get user secUid")
        
        all_videos = []
        max_cursor = "0"
        
        while len(all_videos) < limit:
            params = {
                "secUid": sec_uid,
                "count": min(20, limit - len(all_videos)),
                "maxCursor": max_cursor,
                "aid": "1988",
            }
            
            try:
                data = await self._internal_api_get("/api/comment/list/", params)
            except Exception:
                # Fallback to user page scraping
                break
            
            items = data.get("itemList", [])
            
            for item in items:
                if len(all_videos) >= limit:
                    break
                video_id = item.get("id", "")
                if video_id:
                    video_url = f"https://tiktok.com/@{username}/video/{video_id}"
                    all_videos.append(self._normalize_internal(item, video_url))
            
            max_cursor = data.get("maxCursor", "0")
            has_more = data.get("hasMore", False)
            
            if not has_more or max_cursor == "0":
                break
        
        return all_videos
    
    async def _search_internal(self, query: str, limit: int) -> List[SocialPost]:
        """Search via internal API."""
        params = {
            "keyword": query,
            "count": min(20, limit),
            "cursor": 0,
            "search_source": "normal_search",
            "query_type": 1,
        }
        
        data = await self._internal_api_get("/api/search/general/full/", params)
        
        items = data.get("data", [])
        results = []
        
        for item in items[:limit]:
            video_data = item.get("item", item)
            if not video_data:
                continue
            video_id = video_data.get("id")
            if video_id:
                author = video_data.get("author", {})
                username = author.get("uniqueId", "user")
                video_url = f"https://tiktok.com/@{username}/video/{video_id}"
                results.append(self._normalize_internal(video_data, video_url))
        
        return results
    
    async def _get_trending_internal(self, limit: int) -> List[SocialPost]:
        """Get trending via internal API."""
        params = {
            "count": min(20, limit),
            "aid": "1988",
        }
        
        data = await self._internal_api_get("/api/discover/", params)
        
        items = data.get("data", [])
        results = []
        
        for item in items[:limit]:
            video_data = item.get("item", item)
            if not video_data:
                continue
            video_id = video_data.get("id")
            if video_id:
                author = video_data.get("author", {})
                username = author.get("uniqueId", "user")
                video_url = f"https://tiktok.com/@{username}/video/{video_id}"
                results.append(self._normalize_internal(video_data, video_url))
        
        return results
    
    async def _get_comments_internal(self, video_id: str, limit: int) -> List[Dict[str, Any]]:
        """Get comments via internal API with pagination."""
        all_comments = []
        cursor = 0
        
        while len(all_comments) < limit:
            params = {
                "aid": "1988",
                "item_id": video_id,
                "cursor": cursor,
                "count": min(20, limit - len(all_comments)),
            }
            
            data = await self._internal_api_get("/api/comment/list/", params)
            
            comments = data.get("comments", [])
            
            for c in comments:
                if len(all_comments) >= limit:
                    break
                all_comments.append({
                    "id": c.get("cid"),
                    "author": c.get("user", {}).get("uniqueId", ""),
                    "author_name": c.get("user", {}).get("nickname", ""),
                    "author_avatar": c.get("user", {}).get("avatarThumb", ""),
                    "text": c.get("text", ""),
                    "likes": c.get("diggCount"),
                    "replies": c.get("replyCommentTotal"),
                    "timestamp": c.get("createTime"),
                    "is_hearted": c.get("aigcLabel") == 0,
                })
            
            has_more = data.get("hasMore", False)
            cursor = data.get("cursor", 0)
            
            if not has_more:
                break
        
        return all_comments
    
    def _normalize_internal(self, item: Dict[str, Any], url: str) -> SocialPost:
        """Normalize internal API item."""
        author_data = item.get("author", {})
        stats = item.get("stats", {})
        video = item.get("video", {})
        music = item.get("music", {})
        
        author = {
            "username": author_data.get("uniqueId", ""),
            "display_name": author_data.get("nickname", ""),
            "avatar": author_data.get("avatarLarger", "") or author_data.get("avatarMedium", ""),
            "verified": author_data.get("verified", False),
            "followers": author_data.get("followerCount"),
            "following": author_data.get("followingCount"),
            "bio": author_data.get("signature", ""),
            "profile_url": f"https://tiktok.com/@{author_data.get('uniqueId', '')}",
        }
        
        engagement = {
            "likes": stats.get("diggCount"),
            "comments": stats.get("commentCount"),
            "shares": stats.get("shareCount"),
            "views": stats.get("playCount"),
            "saves": stats.get("collectCount"),
        }
        
        media = [{
            "type": "video",
            "url": video.get("playAddr", "") or url,
            "thumbnail": video.get("cover", "") or video.get("dynamicCover", ""),
            "duration": video.get("duration"),
            "width": video.get("width"),
            "height": video.get("height"),
        }]
        
        hashtags = [tag.get("title", "") for tag in item.get("textExtra", []) if tag.get("hashtagId")]
        mentions = [tag.get("userId", "") for tag in item.get("textExtra", []) if tag.get("userId")]
        
        return build_post(
            platform="tiktok",
            post_type="video",
            url=url,
            id=str(item.get("id", "")),
            text=item.get("desc", ""),
            timestamp=item.get("createTime"),
            author=author,
            engagement=engagement,
            media=media,
            tags=hashtags,
            mentions=mentions,
        )
    
    def _normalize_profile_internal(self, user_data: Dict[str, Any], username: str) -> SocialProfile:
        """Normalize internal profile data."""
        user = user_data.get("user", {})
        stats = user_data.get("stats", {})
        
        author = {
            "username": user.get("uniqueId", username),
            "display_name": user.get("nickname", ""),
            "avatar": user.get("avatarLarger", "") or user.get("avatarMedium", ""),
            "verified": user.get("verified", False),
            "followers": stats.get("followerCount"),
            "following": stats.get("followingCount"),
            "posts_count": stats.get("videoCount"),
            "bio": user.get("signature", ""),
            "profile_url": f"https://tiktok.com/@{user.get('uniqueId', username)}",
            "secUid": user.get("secUid", ""),
        }
        
        engagement = {
            "total_likes": stats.get("heartCount"),
            "total_videos": stats.get("videoCount"),
        }
        
        return build_profile(
            platform="tiktok",
            username=username,
            url=f"https://tiktok.com/@{username}",
            profile_data={"author": author, "engagement": engagement, "id": user.get("id")},
        )
    
    # =========================================================================
    # oEmbed Backend
    # =========================================================================
    
    async def _scrape_oembed(self, video_id: str, url: str) -> SocialPost:
        """Scrape via oEmbed API."""
        oembed_url = f"{self.OEMBED_URL}?url=https://www.tiktok.com/@user/video/{video_id}"
        data = await self._fetch_json(oembed_url)
        
        return build_post(
            platform="tiktok",
            post_type="video",
            url=url,
            id=video_id,
            text=data.get("title", ""),
            timestamp=None,
            author={
                "username": data.get("author_name", ""),
                "display_name": data.get("author_name", ""),
                "avatar": data.get("author_url", "").replace("www.tiktok.com", "p16-sign-va.tiktokcdn.com"),
            },
            engagement={},
            media=[{
                "type": "video",
                "url": url,
                "thumbnail": data.get("thumbnail_url", ""),
                "width": data.get("width"),
                "height": data.get("height"),
            }],
        )
    
    # =========================================================================
    # Embed Page Backend
    # =========================================================================
    
    async def _scrape_embed(self, video_id: str, url: str) -> SocialPost:
        """Scrape via embed page (simpler, no cookies)."""
        embed_url = f"{self.INTERNAL_API_BASE}/embed/{video_id}"
        headers = self._get_headers()
        
        text, resp = await self.client.get(embed_url, engine=self.platform, extra_headers=headers)
        resp.raise_for_status()
        
        data = self._extract_rehydration_data(text)
        
        # Extract from embed page data
        video_info = data.get("video", {})
        
        return build_post(
            platform="tiktok",
            post_type="video",
            url=url,
            id=video_id,
            text=video_info.get("desc", ""),
            timestamp=None,
            author={
                "username": video_info.get("author", ""),
                "display_name": video_info.get("author", ""),
            },
            engagement={},
            media=[{
                "type": "video",
                "url": url,
                "thumbnail": video_info.get("cover", ""),
            }],
        )
    
    # =========================================================================
    # Playwright Backend
    # =========================================================================
    
    async def _scrape_playwright(self, url: str) -> SocialPost:
        """Scrape via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('[data-e2e="video-player"]', timeout=10000)
            
            data = await page.evaluate("""() => {
                const desc = document.querySelector('[data-e2e="video-desc"]')?.innerText || '';
                const author = document.querySelector('[data-e2e="video-author-uniqueid"]')?.innerText || '';
                const authorName = document.querySelector('[data-e2e="video-author-nickname"]')?.innerText || '';
                const likes = document.querySelector('[data-e2e="video-like-count"]')?.innerText || '0';
                const comments = document.querySelector('[data-e2e="video-comment-count"]')?.innerText || '0';
                const shares = document.querySelector('[data-e2e="video-share-count"]')?.innerText || '0';
                const views = document.querySelector('[data-e2e="video-view-count"]')?.innerText || '0';
                const video = document.querySelector('video')?.src || '';
                const cover = document.querySelector('[data-e2e="video-cover"]')?.src || '';
                
                return {desc, author, authorName, likes, comments, shares, views, video, cover};
            }""")
            
            return build_post(
                platform="tiktok",
                post_type="video",
                url=url,
                id=url.split("/")[-1],
                text=data.get("desc", ""),
                timestamp=None,
                author={
                    "username": data.get("author", "").lstrip("@"),
                    "display_name": data.get("authorName", ""),
                },
                engagement={
                    "likes": normalize_number(data.get("likes")),
                    "comments": normalize_number(data.get("comments")),
                    "shares": normalize_number(data.get("shares")),
                    "views": normalize_number(data.get("views")),
                },
                media=[{
                    "type": "video",
                    "url": data.get("video", "") or url,
                    "thumbnail": data.get("cover", ""),
                }],
            )
    
    async def _scrape_profile_playwright(self, username: str) -> SocialProfile:
        """Scrape profile via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(f"https://tiktok.com/@{username}", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('[data-e2e="user-profile"]', timeout=10000)
            
            data = await page.evaluate("""() => {
                const name = document.querySelector('[data-e2e="user-title"]')?.innerText || '';
                const bio = document.querySelector('[data-e2e="user-bio"]')?.innerText || '';
                const followers = document.querySelector('[data-e2e="followers-count"]')?.innerText || '0';
                const following = document.querySelector('[data-e2e="following-count"]')?.innerText || '0';
                const likes = document.querySelector('[data-e2e="likes-count"]')?.innerText || '0';
                const videos = document.querySelector('[data-e2e="user-post-count"]')?.innerText || '0';
                const avatar = document.querySelector('[data-e2e="user-avatar"]')?.src || '';
                const verified = !!document.querySelector('[data-e2e="user-verified-icon"]');
                
                return {name, bio, followers, following, likes, videos, avatar, verified};
            }""")
            
            return build_profile(
                platform="tiktok",
                username=username,
                url=f"https://tiktok.com/@{username}",
                profile_data={"author": {
                    "username": username,
                    "display_name": data.get("name", ""),
                    "avatar": data.get("avatar", ""),
                    "verified": data.get("verified", False),
                    "followers": normalize_number(data.get("followers")),
                    "following": normalize_number(data.get("following")),
                    "posts_count": normalize_number(data.get("videos")),
                    "bio": data.get("bio", ""),
                }, "engagement": {}},
            )
    
    async def _get_user_videos_playwright(self, username: str, limit: int) -> List[SocialPost]:
        """Get user videos via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(f"https://tiktok.com/@{username}", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('[data-e2e="user-post-item"]', timeout=10000)
            
            videos = []
            while len(videos) < limit:
                items = await page.query_selector_all('[data-e2e="user-post-item"] a')
                for item in items[len(videos):]:
                    try:
                        href = await item.get_attribute("href")
                        if href and "/video/" in href:
                            videos.append(href)
                            if len(videos) >= limit:
                                break
                    except Exception:
                        continue
                
                if len(videos) >= limit:
                    break
                
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
            
            results = []
            for video_url in videos[:limit]:
                try:
                    results.append(await self._scrape_playwright(video_url))
                except Exception:
                    continue
            
            return results
    
    async def _search_playwright(self, query: str, limit: int) -> List[SocialPost]:
        """Search via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            search_url = f"https://tiktok.com/search?q={query}&t=video"
            await page.goto(search_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('[data-e2e="search-video-item"]', timeout=10000)
            
            videos = []
            while len(videos) < limit:
                items = await page.query_selector_all('[data-e2e="search-video-item"] a')
                for item in items[len(videos):]:
                    try:
                        href = await item.get_attribute("href")
                        if href and "/video/" in href:
                            videos.append(href)
                            if len(videos) >= limit:
                                break
                    except Exception:
                        continue
                
                if len(videos) >= limit:
                    break
                
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
            
            results = []
            for video_url in videos[:limit]:
                try:
                    results.append(await self._scrape_playwright(video_url))
                except Exception:
                    continue
            
            return results
    
    async def _get_trending_playwright(self, limit: int) -> List[SocialPost]:
        """Get trending via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto("https://tiktok.com/discover", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('[data-e2e="video-item"]', timeout=10000)
            
            videos = []
            while len(videos) < limit:
                items = await page.query_selector_all('[data-e2e="video-item"] a')
                for item in items[len(videos):]:
                    try:
                        href = await item.get_attribute("href")
                        if href and "/video/" in href:
                            videos.append(href)
                            if len(videos) >= limit:
                                break
                    except Exception:
                        continue
                
                if len(videos) >= limit:
                    break
                
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
            
            results = []
            for video_url in videos[:limit]:
                try:
                    results.append(await self._scrape_playwright(video_url))
                except Exception:
                    continue
            
            return results
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract video ID or username from TikTok URL."""
        video_id = self._extract_video_id(url)
        if video_id:
            return video_id
        
        match = re.search(r"tiktok\.com/@([^/?]+)", url)
        if match:
            return match.group(1)
        
        return None


registry.register(TikTokScraper)
