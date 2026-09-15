"""Instagram scraper with 3-backend fallback: Mobile API → GraphQL → Playwright.

Mobile API (i.instagram.com) provides richer data without browser. GraphQL works
with public query hashes. Playwright is the last-resort fallback.
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

log = get_logger("jiro.scraping.social.instagram")


class InstagramScraper(BaseSocialScraper):
    """Instagram scraper with mobile API, GraphQL, and Playwright backends."""
    
    platform = "instagram"
    url_patterns = ["instagram.com", "instagr.am"]
    supported_actions = ["post", "profile", "reel", "story", "tag", "highlights", "comments", "search"]
    rate_limit_rpm = 30
    requires_auth = True
    
    BACKENDS = ["mobile_api", "graphql", "playwright"]
    
    GRAPHQL_URL = "https://www.instagram.com/graphql/query/"
    MOBILE_API_BASE = "https://i.instagram.com/api/v1"
    
    # GraphQL query hashes (auto-refreshed)
    DEFAULT_QUERY_HASHES = {
        "post": "b3055c01b4b222b8a47db1c2817f37ba",
        "profile": "c9100bf9110dd6361671f113dd02e7d6",
        "user_posts": "69cba40317214236af40e7efa697781d",
        "reels": "5a3f3b5c3e4c3b5a3f3b5c3e4c3b5a3f",
        "comments": "bc3296d44b082fc75d3d3d5e3f9a1a78",
    }
    
    _cached_hashes: Dict[str, str] = {}
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        cfg = settings.social.get("instagram", {}) if settings else {}
        self.sessionid = cfg.get("sessionid", "")
        self.ds_user_id = cfg.get("ds_user_id", "")
        self.csrf_token = cfg.get("csrf_token", "")
        self.backend = cfg.get("backend", "auto")
    
    def _get_headers(self, mobile: bool = False) -> Dict[str, str]:
        """Get headers for Instagram requests."""
        if mobile:
            headers = {
                "User-Agent": "Instagram 301.0.0.31.109 Android (33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 527869258)",
                "X-IG-App-ID": "567067343352127",
                "X-IG-Connection-Type": "WIFI",
                "X-IG-Capabilities": "AQ==",
                "Accept-Language": "en-US",
                "Accept-Encoding": "gzip, deflate",
            }
        else:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "X-IG-App-ID": "936619743392459",
                "X-Requested-With": "XMLHttpRequest",
            }
        
        if self.sessionid:
            headers["Cookie"] = f"sessionid={self.sessionid}"
            if self.csrf_token:
                headers["X-CSRFToken"] = self.csrf_token
        
        return headers
    
    async def _ensure_query_hashes(self) -> None:
        """Fetch latest query hashes from Instagram if cache is empty."""
        if self._cached_hashes:
            return
        try:
            html = await self._fetch_html("https://www.instagram.com/", engine=self.platform)
            found = self._parse_query_hashes_from_page(html)
            if found:
                self._cached_hashes.update(found)
                log.info("Extracted %d dynamic query hashes", len(found))
        except Exception as exc:
            log.debug("Could not fetch dynamic query hashes: %s", exc)
    
    @staticmethod
    def _parse_query_hashes_from_page(html: str) -> Dict[str, str]:
        hashes = {}
        patterns = re.findall(r'queryId["\s:=]+([a-f0-9]{32})', html, re.IGNORECASE)
        seen = set()
        for h in patterns:
            h = h.lower()
            if h not in seen and len(h) == 32:
                seen.add(h)
                hashes[f"dynamic_{len(hashes)}"] = h
        return hashes
    
    @property
    def QUERY_HASHES(self) -> Dict[str, str]:
        if not self._cached_hashes:
            self._cached_hashes = dict(self.DEFAULT_QUERY_HASHES)
        return self._cached_hashes
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    async def scrape_post(self, url: str) -> SocialPost:
        shortcode = self._extract_shortcode(url)
        if not shortcode:
            raise ValueError("Could not extract shortcode from URL")
        
        backends = self._get_backend_order()
        errors = []
        
        for backend in backends:
            try:
                if backend == "mobile_api":
                    return await self._scrape_mobile(shortcode, url)
                elif backend == "graphql":
                    return await self._scrape_graphql(shortcode, url)
                elif backend == "playwright":
                    return await self._scrape_playwright(url)
            except Exception as e:
                log.debug("Instagram backend %s failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
        
        raise ValueError(f"All Instagram backends failed for {shortcode}: {errors}")
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        username = username.lstrip("@")
        backends = self._get_backend_order()
        errors = []
        
        for backend in backends:
            try:
                if backend == "mobile_api":
                    return await self._scrape_profile_mobile(username)
                elif backend == "graphql":
                    return await self._scrape_profile_graphql(username)
                elif backend == "playwright":
                    return await self._scrape_profile_playwright(username)
            except Exception as e:
                log.debug("Instagram profile backend %s failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
        
        raise ValueError(f"All Instagram backends failed for profile {username}: {errors}")
    
    async def get_user_posts(self, username: str, limit: int = 25) -> List[SocialPost]:
        """Get posts from a user with pagination."""
        username = username.lstrip("@")
        
        # Try mobile API first (best pagination)
        try:
            return await self._get_feed_mobile(username, limit)
        except Exception:
            log.debug("Mobile feed failed", exc_info=True)
        
        # GraphQL pagination
        try:
            return await self._get_feed_graphql(username, limit)
        except Exception:
            log.debug("GraphQL feed failed", exc_info=True)
        
        # Playwright
        try:
            return await self._get_feed_playwright(username, limit)
        except Exception:
            log.debug("Playwright feed failed", exc_info=True)
        
        return []
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search Instagram posts."""
        # Mobile API search
        try:
            return await self._search_mobile(query, limit)
        except Exception:
            log.debug("Mobile search failed", exc_info=True)
        
        # Web search
        try:
            return await self._search_web(query, limit)
        except Exception:
            log.debug("Web search failed", exc_info=True)
        
        return []
    
    async def scrape_comments(self, url: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Scrape comments for a post."""
        shortcode = self._extract_shortcode(url)
        if not shortcode:
            raise ValueError("Could not extract shortcode from URL")
        
        # Mobile API comments
        try:
            return await self._get_comments_mobile(shortcode, limit)
        except Exception:
            log.debug("Mobile comments failed", exc_info=True)
        
        # GraphQL comments
        try:
            return await self._get_comments_graphql(shortcode, limit)
        except Exception:
            log.debug("GraphQL comments failed", exc_info=True)
        
        return []
    
    def _get_backend_order(self) -> List[str]:
        if self.backend == "auto":
            return self.BACKENDS
        elif self.backend in self.BACKENDS:
            return [self.backend] + [b for b in self.BACKENDS if b != self.backend]
        return self.BACKENDS
    
    # =========================================================================
    # Mobile API Backend (primary - richest data, no browser)
    # =========================================================================
    
    async def _scrape_mobile(self, shortcode: str, url: str) -> SocialPost:
        """Scrape post via mobile API."""
        endpoint = f"{self.MOBILE_API_BASE}/media/{shortcode}/info/"
        headers = self._get_headers(mobile=True)
        
        text, resp = await self.client.get(endpoint, engine=self.platform, extra_headers=headers)
        if resp.status_code == 429:
            raise RateLimitError("instagram")
        resp.raise_for_status()
        
        data = resp.json()
        items = data.get("items", [])
        if not items:
            raise ValueError("Post not found via mobile API")
        
        return self._normalize_mobile_item(items[0], url)
    
    async def _scrape_profile_mobile(self, username: str) -> SocialProfile:
        """Scrape profile via mobile API."""
        endpoint = f"{self.MOBILE_API_BASE}/users/web_profile_info/?username={username}"
        headers = self._get_headers(mobile=True)
        
        text, resp = await self.client.get(endpoint, engine=self.platform, extra_headers=headers)
        if resp.status_code == 404:
            raise ValueError("Profile not found")
        resp.raise_for_status()
        
        data = resp.json()
        user = data.get("data", {}).get("user", {})
        if not user:
            raise ValueError("Profile not found")
        
        return self._normalize_profile(user, username)
    
    async def _get_feed_mobile(self, username: str, limit: int) -> List[SocialPost]:
        """Get user feed via mobile API with pagination."""
        # First get user ID
        profile = await self._scrape_profile_mobile(username)
        user_id = profile.data.get("id")
        if not user_id:
            raise ValueError("Could not get user ID")
        
        all_posts = []
        max_id = None
        
        while len(all_posts) < limit:
            endpoint = f"{self.MOBILE_API_BASE}/feed/user/{user_id}/"
            params = {"count": min(12, limit - len(all_posts))}
            if max_id:
                params["max_id"] = max_id
            
            headers = self._get_headers(mobile=True)
            text, resp = await self.client.get(endpoint, engine=self.platform, params=params, extra_headers=headers)
            
            if resp.status_code == 429:
                raise RateLimitError("instagram")
            resp.raise_for_status()
            
            data = resp.json()
            items = data.get("items", [])
            
            for item in items:
                if len(all_posts) >= limit:
                    break
                post_url = f"https://instagram.com/p/{item.get('code', '')}"
                all_posts.append(self._normalize_mobile_item(item, post_url))
            
            max_id = data.get("next_max_id")
            if not max_id or not items:
                break
        
        return all_posts
    
    async def _get_comments_mobile(self, shortcode: str, limit: int) -> List[Dict[str, Any]]:
        """Get comments via mobile API with pagination."""
        endpoint = f"{self.MOBILE_API_BASE}/media/{shortcode}/comments/"
        headers = self._get_headers(mobile=True)
        
        all_comments = []
        max_id = None
        
        while len(all_comments) < limit:
            params = {"count": min(20, limit - len(all_comments))}
            if max_id:
                params["max_id"] = max_id
            
            text, resp = await self.client.get(endpoint, engine=self.platform, params=params, extra_headers=headers)
            
            if resp.status_code == 429:
                raise RateLimitError("instagram")
            resp.raise_for_status()
            
            data = resp.json()
            comments = data.get("comments", [])
            
            for c in comments:
                if len(all_comments) >= limit:
                    break
                all_comments.append({
                    "id": c.get("pk"),
                    "author": c.get("user", {}).get("username", ""),
                    "text": c.get("text", ""),
                    "likes": c.get("comment_like_count"),
                    "timestamp": c.get("created_at"),
                    "has_privately_liked": c.get("has_privately_liked", False),
                    "replies": c.get("child_comment_count", 0),
                })
            
            max_id = data.get("next_max_id")
            if not max_id or not comments:
                break
        
        return all_comments
    
    async def _search_mobile(self, query: str, limit: int) -> List[SocialPost]:
        """Search via mobile API."""
        endpoint = f"{self.MOBILE_API_BASE}/fbsearch/web/"
        params = {"query": query, "context": "blended", "count": limit}
        headers = self._get_headers(mobile=True)
        
        text, resp = await self.client.get(endpoint, engine=self.platform, params=params, extra_headers=headers)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        results = []
        
        for user in data.get("users", [])[:limit]:
            user_info = user.get("user", {})
            if user_info.get("username"):
                try:
                    posts = await self._get_feed_mobile(user_info["username"], 1)
                    results.extend(posts)
                except Exception:
                    continue
        
        return results
    
    def _normalize_mobile_item(self, item: Dict[str, Any], url: str) -> SocialPost:
        """Normalize mobile API item."""
        user = item.get("user", {})
        
        author = {
            "username": user.get("username", ""),
            "display_name": user.get("full_name", ""),
            "avatar": user.get("profile_pic_url", ""),
            "verified": user.get("is_verified", False),
            "followers": None,
        }
        
        engagement = {
            "likes": item.get("like_count"),
            "comments": item.get("comment_count"),
            "views": item.get("play_count"),
            "saves": item.get("save_count"),
        }
        
        media = []
        media_type = item.get("media_type", 1)
        
        if media_type == 2:  # Video
            video_url = item.get("video_url", "")
            media.append({
                "type": "video",
                "url": video_url,
                "thumbnail": item.get("image_versions2", {}).get("candidates", [{}])[0].get("url", ""),
                "duration": item.get("video_duration"),
                "width": item.get("original_width"),
                "height": item.get("original_height"),
            })
        elif media_type == 8:  # Carousel
            for sub_media in item.get("carousel_media", []):
                sub_type = sub_media.get("media_type", 1)
                if sub_type == 2:
                    media.append({
                        "type": "video",
                        "url": sub_media.get("video_url", ""),
                        "thumbnail": sub_media.get("image_versions2", {}).get("candidates", [{}])[0].get("url", ""),
                        "duration": sub_media.get("video_duration"),
                    })
                else:
                    candidates = sub_media.get("image_versions2", {}).get("candidates", [])
                    if candidates:
                        media.append({"type": "image", "url": candidates[0].get("url", "")})
        else:  # Photo
            candidates = item.get("image_versions2", {}).get("candidates", [])
            if candidates:
                media.append({
                    "type": "image",
                    "url": candidates[0].get("url", ""),
                    "width": item.get("original_width"),
                    "height": item.get("original_height"),
                })
        
        caption = item.get("caption", {})
        caption_text = caption.get("text", "") if isinstance(caption, dict) else ""
        
        hashtags = re.findall(r"#(\w+)", caption_text)
        mentions = re.findall(r"@(\w+)", caption_text)
        
        location = item.get("location", {})
        location_name = location.get("name", "") if isinstance(location, dict) else ""
        
        return build_post(
            platform="instagram",
            post_type="reel" if media_type == 2 else ("carousel" if media_type == 8 else "post"),
            url=url,
            id=str(item.get("pk", "")),
            shortcode=item.get("code", ""),
            text=caption_text,
            timestamp=item.get("taken_at"),
            author=author,
            engagement=engagement,
            media=media,
            tags=hashtags,
            mentions=mentions,
            location=location_name,
        )
    
    # =========================================================================
    # GraphQL Backend
    # =========================================================================
    
    async def _graphql_request(self, query_hash: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        params = {
            "query_hash": query_hash,
            "variables": json.dumps(variables),
        }
        headers = self._get_headers()
        text, resp = await self.client.get(self.GRAPHQL_URL, engine=self.platform, params=params, extra_headers=headers)
        if resp.status_code == 429:
            raise RateLimitError("instagram")
        resp.raise_for_status()
        return resp.json()
    
    async def _scrape_graphql(self, shortcode: str, url: str) -> SocialPost:
        await self._ensure_query_hashes()
        variables = {"shortcode": shortcode}
        data = await self._graphql_request(self.QUERY_HASHES["post"], variables)
        
        post = data.get("data", {}).get("xdt_shortcode_media", {})
        if not post:
            raise ValueError("Post not found")
        
        return self._normalize_graphql_post(post, url)
    
    async def _scrape_profile_graphql(self, username: str) -> SocialProfile:
        await self._ensure_query_hashes()
        variables = {"username": username}
        data = await self._graphql_request(self.QUERY_HASHES["profile"], variables)
        
        user = data.get("data", {}).get("user", {})
        if not user:
            raise ValueError("Profile not found")
        
        return self._normalize_profile(user, username)
    
    async def _get_feed_graphql(self, username: str, limit: int) -> List[SocialPost]:
        """Get feed via GraphQL with pagination."""
        profile = await self._scrape_profile_graphql(username)
        user_id = profile.data.get("id")
        if not user_id:
            raise ValueError("Could not get user ID")
        
        await self._ensure_query_hashes()
        
        all_posts = []
        cursor = None
        
        while len(all_posts) < limit:
            variables = {
                "id": user_id,
                "first": min(12, limit - len(all_posts)),
            }
            if cursor:
                variables["after"] = cursor
            
            data = await self._graphql_request(self.QUERY_HASHES["user_posts"], variables)
            
            edges = data.get("data", {}).get("user", {}).get("edge_owner_to_timeline_media", {}).get("edges", [])
            
            for edge in edges:
                if len(all_posts) >= limit:
                    break
                node = edge.get("node", {})
                shortcode = node.get("shortcode", "")
                post_url = f"https://instagram.com/p/{shortcode}"
                all_posts.append(self._normalize_graphql_post(node, post_url))
            
            page_info = data.get("data", {}).get("user", {}).get("edge_owner_to_timeline_media", {}).get("page_info", {})
            cursor = page_info.get("end_cursor") if page_info.get("has_next_page") else None
            if not cursor:
                break
        
        return all_posts
    
    async def _get_comments_graphql(self, shortcode: str, limit: int) -> List[Dict[str, Any]]:
        """Get comments via GraphQL."""
        await self._ensure_query_hashes()
        variables = {"shortcode": shortcode, "first": min(50, limit)}
        
        data = await self._graphql_request(self.QUERY_HASHES["comments"], variables)
        
        edges = data.get("data", {}).get("shortcode_media", {}).get("edge_media_to_comment", {}).get("edges", [])
        
        comments = []
        for edge in edges[:limit]:
            node = edge.get("node", {})
            comments.append({
                "id": node.get("id"),
                "author": node.get("owner", {}).get("username", ""),
                "text": node.get("text", ""),
                "likes": node.get("edge_liked_by", {}).get("count"),
                "timestamp": node.get("created_at"),
            })
        
        return comments
    
    def _normalize_graphql_post(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize GraphQL post data."""
        owner = data.get("owner", {})
        
        author = {
            "username": owner.get("username", ""),
            "display_name": owner.get("full_name", ""),
            "avatar": owner.get("profile_pic_url", ""),
            "verified": owner.get("is_verified", False),
            "followers": owner.get("edge_followed_by", {}).get("count"),
        }
        
        engagement = {
            "likes": data.get("edge_media_preview_like", {}).get("count"),
            "comments": data.get("edge_media_to_comment", {}).get("count"),
        }
        
        media = []
        if data.get("is_video"):
            media.append({
                "type": "video",
                "url": data.get("video_url", ""),
                "thumbnail": data.get("display_url", ""),
                "duration": data.get("video_duration"),
            })
        else:
            media.append({
                "type": "image",
                "url": data.get("display_url", ""),
                "thumbnail": data.get("thumbnail_url", ""),
            })
        
        for edge in data.get("edge_sidecar_to_children", {}).get("edges", []):
            node = edge.get("node", {})
            if node.get("display_url"):
                media.append({
                    "type": "video" if node.get("is_video") else "image",
                    "url": node.get("video_url", "") or node.get("display_url", ""),
                    "thumbnail": node.get("display_url", ""),
                })
        
        caption_edges = data.get("edge_media_to_caption", {}).get("edges", [])
        caption = caption_edges[0].get("node", {}).get("text", "") if caption_edges else ""
        
        return build_post(
            platform="instagram",
            post_type="reel" if data.get("is_video") else "post",
            url=url,
            id=data.get("shortcode", ""),
            text=caption,
            timestamp=data.get("taken_at_timestamp"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    # =========================================================================
    # Profile normalization
    # =========================================================================
    
    def _normalize_profile(self, data: Dict[str, Any], username: str) -> SocialProfile:
        author = {
            "username": data.get("username", username),
            "display_name": data.get("full_name", ""),
            "avatar": data.get("profile_pic_url_hd", "") or data.get("profile_pic_url", ""),
            "verified": data.get("is_verified", False),
            "followers": data.get("edge_followed_by", {}).get("count"),
            "following": data.get("edge_follow", {}).get("count"),
            "posts_count": data.get("edge_owner_to_timeline_media", {}).get("count"),
            "bio": data.get("biography", ""),
            "location": "",
            "joined_date": None,
            "profile_url": f"https://instagram.com/{username}",
        }
        
        return build_profile(
            platform="instagram",
            username=username,
            url=f"https://instagram.com/{username}",
            profile_data={"author": author, "engagement": {}, "id": data.get("id")},
        )
    
    # =========================================================================
    # Playwright Backend
    # =========================================================================
    
    async def _scrape_playwright(self, url: str) -> SocialPost:
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('article', timeout=10000)
            
            data = await page.evaluate("""() => {
                const desc = document.querySelector('h1')?.innerText || '';
                const author = document.querySelector('header a')?.innerText || '';
                const authorHandle = document.querySelector('header a[href^="/"]')?.href?.split('/').pop() || '';
                const likes = document.querySelector('article section span')?.innerText || '0';
                const img = document.querySelector('article img[src*="instagram"]')?.src || '';
                
                return {desc, author, authorHandle, likes, img};
            }""")
            
            return build_post(
                platform="instagram",
                post_type="post",
                url=url,
                id=url.split("/")[-1],
                text=data.get("desc", ""),
                timestamp=None,
                author={"username": data.get("authorHandle", ""), "display_name": data.get("author", "")},
                engagement={"likes": normalize_number(data.get("likes"))},
                media=[{"type": "image", "url": data.get("img", "")}] if data.get("img") else [],
            )
    
    async def _scrape_profile_playwright(self, username: str) -> SocialProfile:
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(f"https://instagram.com/{username}", wait_until="networkidle", timeout=30000)
            
            data = await page.evaluate("""() => {
                const name = document.querySelector('header section h2')?.innerText || '';
                const bio = document.querySelector('header section div span')?.innerText || '';
                const followers = document.querySelector('header section ul li:nth-child(1) span span')?.innerText || '0';
                const following = document.querySelector('header section ul li:nth-child(2) span span')?.innerText || '0';
                const posts = document.querySelector('header section ul li:nth-child(3) span span')?.innerText || '0';
                const avatar = document.querySelector('header img[alt*="profile"]')?.src || '';
                const verified = !!document.querySelector('header svg[aria-label="Verified"]');
                
                return {name, bio, followers, following, posts, avatar, verified};
            }""")
            
            return build_profile(
                platform="instagram",
                username=username,
                url=f"https://instagram.com/{username}",
                profile_data={"author": {
                    "username": username,
                    "display_name": data.get("name", ""),
                    "avatar": data.get("avatar", ""),
                    "verified": data.get("verified", False),
                    "followers": normalize_number(data.get("followers")),
                    "following": normalize_number(data.get("following")),
                    "posts_count": normalize_number(data.get("posts")),
                    "bio": data.get("bio", ""),
                }, "engagement": {}},
            )
    
    async def _get_feed_playwright(self, username: str, limit: int) -> List[SocialPost]:
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(f"https://instagram.com/{username}", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('article a[href*="/p/"]', timeout=10000)
            
            posts = []
            while len(posts) < limit:
                links = await page.query_selector_all('article a[href*="/p/"]')
                for link in links[len(posts):]:
                    try:
                        href = await link.get_attribute("href")
                        if href and href not in posts:
                            posts.append(href)
                            if len(posts) >= limit:
                                break
                    except Exception:
                        continue
                
                if len(posts) >= limit:
                    break
                
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
            
            results = []
            for post_path in posts[:limit]:
                try:
                    post_url = f"https://instagram.com{post_path}"
                    results.append(await self._scrape_playwright(post_url))
                except Exception:
                    continue
            
            return results
    
    async def _search_web(self, query: str, limit: int) -> List[SocialPost]:
        """Search via web endpoint."""
        url = f"https://www.instagram.com/web/search/topsearch/"
        params = {"query": query, "context": "blended"}
        headers = self._get_headers()
        
        text, resp = await self.client.get(url, engine=self.platform, params=params, extra_headers=headers)
        data = resp.json()
        
        results = []
        for user in data.get("users", [])[:limit]:
            user_info = user.get("user", {})
            if user_info.get("username"):
                try:
                    posts = await self._get_feed_mobile(user_info["username"], 1)
                    results.extend(posts)
                except Exception:
                    continue
        
        return results
    
    # =========================================================================
    # URL parsing
    # =========================================================================
    
    def extract_identifier(self, url: str) -> Optional[str]:
        shortcode = self._extract_shortcode(url)
        if shortcode:
            return shortcode
        match = re.search(r"instagram\.com/([^/?]+)", url)
        if match:
            return match.group(1)
        return None
    
    def _extract_shortcode(self, url: str) -> Optional[str]:
        patterns = [
            r"instagram\.com/p/([^/?]+)",
            r"instagram\.com/reel/([^/?]+)",
            r"instagram\.com/tv/([^/?]+)",
            r"instagr\.am/p/([^/?]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None


registry.register(InstagramScraper)
