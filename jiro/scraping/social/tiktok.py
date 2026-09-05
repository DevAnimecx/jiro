"""TikTok scraper using embed API and Playwright fallback."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.tiktok")


class TikTokScraper(BaseSocialScraper):
    """TikTok scraper using oEmbed API and Playwright fallback."""
    
    platform = "tiktok"
    url_patterns = [
        "tiktok.com",
        "vm.tiktok.com",
        "vt.tiktok.com",
    ]
    supported_actions = ["video", "profile", "hashtag", "search", "trending"]
    rate_limit_rpm = 60
    requires_auth = False
    
    OEMBED_URL = "https://www.tiktok.com/oembed"
    API_URL = "https://www.tiktok.com/api/post/item_list/"
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        self.use_browser = settings.social.get("tiktok", {}).get("use_browser", True) if settings else True
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a TikTok video."""
        video_id = self._extract_video_id(url)
        if not video_id:
            raise ValueError("Could not extract video ID from URL")
        
        # Try oEmbed first
        try:
            return await self._scrape_oembed(video_id, url)
        except Exception as e:
            log.debug("TikTok oEmbed failed", extra={"error": str(e)})
        
        # Try API
        try:
            return await self._scrape_api(video_id, url)
        except Exception as e:
            log.debug("TikTok API failed", extra={"error": str(e)})
        
        # Fallback to Playwright
        if self.use_browser:
            try:
                return await self._scrape_playwright(url)
            except Exception as e:
                log.debug("TikTok Playwright failed", extra={"error": str(e)})
        
        raise ValueError(f"All TikTok methods failed for video: {video_id}")
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a TikTok profile."""
        username = username.lstrip("@")
        
        # Try API first
        try:
            return await self._scrape_profile_api(username)
        except Exception:
            pass
        
        # Fallback to Playwright
        if self.use_browser:
            return await self._scrape_profile_playwright(username)
        
        raise ValueError(f"Could not scrape TikTok profile: {username}")
    
    async def get_user_videos(self, username: str, limit: int = 25) -> List[SocialPost]:
        """Get videos from a user."""
        username = username.lstrip("@")
        
        # Try API
        try:
            return await self._get_user_videos_api(username, limit)
        except Exception:
            pass
        
        # Fallback to Playwright
        if self.use_browser:
            return await self._get_user_videos_playwright(username, limit)
        
        return []
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search TikTok videos."""
        # Try HTTP API first (no browser needed)
        try:
            results = await self._search_api(query, limit)
            if results:
                return results
        except Exception:
            pass
        
        # Fallback to Playwright
        if self.use_browser:
            return await self._search_playwright(query, limit)
        
        return []
    
    async def _search_api(self, query: str, limit: int) -> List[SocialPost]:
        """Search TikTok via HTTP API (no browser)."""
        search_url = "https://www.tiktok.com/api/search/general/full/"
        params = {
            "keyword": query,
            "count": limit,
            "cursor": 0,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
        }
        
        text, resp = await self.client.get(search_url, engine=self.platform, params=params, extra_headers=headers)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        items = data.get("data", [])
        
        results = []
        for item in items[:limit]:
            video_data = item.get("item", item)
            if not video_data:
                continue
            video_id = video_data.get("id")
            if video_id:
                video_url = f"https://tiktok.com/@{video_data.get('author', {}).get('uniqueId', 'user')}/video/{video_id}"
                results.append(self._normalize_api_item(video_data, video_url))
        
        return results
    
    async def get_trending(self, limit: int = 25) -> List[SocialPost]:
        """Get trending TikTok videos."""
        # Try HTTP API first
        try:
            results = await self._get_trending_api(limit)
            if results:
                return results
        except Exception:
            pass
        
        # Fallback to Playwright
        if self.use_browser:
            return await self._get_trending_playwright(limit)
        
        return []
    
    async def _get_trending_api(self, limit: int) -> List[SocialPost]:
        """Get trending videos via HTTP API."""
        search_url = "https://www.tiktok.com/api/search/general/full/"
        params = {
            "keyword": "trending",
            "count": limit,
            "cursor": 0,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
        }
        
        text, resp = await self.client.get(search_url, engine=self.platform, params=params, extra_headers=headers)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        items = data.get("data", [])
        
        results = []
        for item in items[:limit]:
            video_data = item.get("item", item)
            if not video_data:
                continue
            video_id = video_data.get("id")
            if video_id:
                video_url = f"https://tiktok.com/@{video_data.get('author', {}).get('uniqueId', 'user')}/video/{video_id}"
                results.append(self._normalize_api_item(video_data, video_url))
        
        return results
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from TikTok URL."""
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
    
    async def _scrape_api(self, video_id: str, url: str) -> SocialPost:
        """Scrape via TikTok internal API."""
        api_url = f"https://www.tiktok.com/api/item/detail/?itemId={video_id}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        text, resp = await self.client.get(api_url, engine=self.platform, extra_headers=headers)
        data = resp.json()
        
        item = data.get("itemInfo", {}).get("itemStruct", {})
        if not item:
            raise ValueError("Video not found")
        
        return self._normalize_api_item(item, url)
    
    def _normalize_api_item(self, item: Dict[str, Any], url: str) -> SocialPost:
        """Normalize TikTok API item."""
        author_data = item.get("author", {})
        stats = item.get("stats", {})
        video = item.get("video", {})
        
        author = {
            "username": author_data.get("uniqueId", ""),
            "display_name": author_data.get("nickname", ""),
            "avatar": author_data.get("avatarLarger", "") or author_data.get("avatarMedium", ""),
            "verified": author_data.get("verified", False),
            "followers": author_data.get("followerCount"),
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
        )
    
    async def _scrape_profile_api(self, username: str) -> SocialProfile:
        """Scrape profile via API."""
        url = f"https://www.tiktok.com/api/user/detail/?uniqueId={username}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        text, resp = await self.client.get(url, engine=self.platform, extra_headers=headers)
        data = resp.json()
        
        user = data.get("userInfo", {}).get("user", {})
        stats = data.get("userInfo", {}).get("stats", {})
        
        if not user:
            raise ValueError("Profile not found")
        
        author = {
            "username": user.get("uniqueId", username),
            "display_name": user.get("nickname", ""),
            "avatar": user.get("avatarLarger", "") or user.get("avatarMedium", ""),
            "verified": user.get("verified", False),
            "followers": stats.get("followerCount"),
            "following": stats.get("followingCount"),
            "posts_count": stats.get("videoCount"),
            "bio": user.get("signature", ""),
        }
        
        return build_profile(
            platform="tiktok",
            username=username,
            url=f"https://tiktok.com/@{username}",
            profile_data={"author": author, "engagement": {}, "id": user.get("id")},
        )
    
    async def _get_user_videos_api(self, username: str, limit: int) -> List[SocialPost]:
        """Get user videos via API."""
        # First get user ID
        profile = await self._scrape_profile_api(username)
        user_id = profile.data.get("id")
        
        if not user_id:
            raise ValueError("Could not get user ID")
        
        url = f"https://www.tiktok.com/api/post/item_list/?id={user_id}&type=1&count={limit}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        text, resp = await self.client.get(url, engine=self.platform, extra_headers=headers)
        data = resp.json()
        
        items = data.get("itemList", [])
        results = []
        for item in items[:limit]:
            video_id = item.get("id")
            if video_id:
                video_url = f"https://tiktok.com/@{username}/video/{video_id}"
                results.append(self._normalize_api_item(item, video_url))
        
        return results
    
    # --- Playwright Fallbacks ---
    
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


# Register the scraper
registry.register(TikTokScraper)