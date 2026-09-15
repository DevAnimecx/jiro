"""Reddit scraper with 3-backend fallback: Old Reddit JSON → Reddit JSON API → Playwright.

Supports deep pagination, nested comment extraction, time filters, subreddit
browsing, and user post/comment history.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.reddit")


class RedditScraper(BaseSocialScraper):
    """Reddit scraper with multiple backends and deep pagination."""
    
    platform = "reddit"
    url_patterns = ["reddit.com", "old.reddit.com", "www.reddit.com", "redd.it"]
    supported_actions = ["post", "profile", "subreddit", "search", "comments", "user_history"]
    rate_limit_rpm = 60
    requires_auth = False
    
    BACKENDS = ["old_reddit", "new_reddit", "playwright"]
    
    OLD_REDDIT_BASE = "https://old.reddit.com"
    NEW_REDDIT_BASE = "https://www.reddit.com"
    
    # Time filter options
    TIME_FILTERS = ["hour", "day", "week", "month", "year", "all"]
    
    # Sort options
    SORT_OPTIONS = ["hot", "new", "top", "rising", "controversial", "best"]
    
    _DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        cfg = settings.social.get("reddit", {}) if settings else {}
        self.backend = cfg.get("backend", "auto")
        self.client_id = cfg.get("client_id", "")
        self.client_secret = cfg.get("client_secret", "")
        self.access_token = cfg.get("access_token", "")
    
    def _get_headers(self) -> Dict[str, str]:
        headers = dict(self._DEFAULT_HEADERS)
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a Reddit post."""
        # Convert to .json endpoint
        json_url = self._to_json_url(url)
        
        backends = self._get_backend_order()
        errors = []
        
        for backend in backends:
            try:
                if backend == "old_reddit":
                    return await self._scrape_old_reddit(json_url, url)
                elif backend == "new_reddit":
                    return await self._scrape_new_reddit(url)
                elif backend == "playwright":
                    return await self._scrape_playwright(url)
            except Exception as e:
                log.debug("Reddit backend %s failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
        
        raise ValueError(f"All Reddit backends failed for {url}: {errors}")
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a Reddit user profile."""
        backends = self._get_backend_order()
        errors = []
        
        for backend in backends:
            try:
                if backend == "old_reddit":
                    return await self._scrape_profile_old(username)
                elif backend == "new_reddit":
                    return await self._scrape_profile_new(username)
                elif backend == "playwright":
                    return await self._scrape_profile_playwright(username)
            except Exception as e:
                log.debug("Reddit profile backend %s failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
        
        raise ValueError(f"All Reddit backends failed for profile {username}: {errors}")
    
    async def scrape_subreddit(self, subreddit: str, sort: str = "hot", limit: int = 25, time_filter: str = "day") -> List[SocialPost]:
        """Scrape posts from a subreddit with sorting and time filters."""
        subreddit = subreddit.lstrip("r/")
        
        try:
            return await self._scrape_subreddit_old(subreddit, sort, limit, time_filter)
        except Exception:
            log.debug("Old Reddit subreddit failed", exc_info=True)
        
        try:
            return await self._scrape_subreddit_new(subreddit, sort, limit, time_filter)
        except Exception:
            log.debug("New Reddit subreddit failed", exc_info=True)
        
        return []
    
    async def search(self, query: str, limit: int = 25, subreddit: Optional[str] = None, sort: str = "relevance", time_filter: str = "all") -> List[SocialPost]:
        """Search Reddit posts with sorting and time filters."""
        subreddit = subreddit.lstrip("r/") if subreddit else None
        
        try:
            return await self._search_old(query, limit, subreddit, sort, time_filter)
        except Exception:
            log.debug("Old Reddit search failed", exc_info=True)
        
        try:
            return await self._search_new(query, limit, subreddit, sort, time_filter)
        except Exception:
            log.debug("New Reddit search failed", exc_info=True)
        
        return []
    
    async def scrape_comments(self, url: str, limit: int = 50, sort: str = "best") -> List[Dict[str, Any]]:
        """Scrape comments from a Reddit post with nested replies."""
        json_url = self._to_json_url(url)
        
        try:
            return await self._get_comments_old(json_url, limit, sort)
        except Exception:
            log.debug("Old Reddit comments failed", exc_info=True)
        
        try:
            return await self._get_comments_new(url, limit, sort)
        except Exception:
            log.debug("New Reddit comments failed", exc_info=True)
        
        return []
    
    async def get_user_posts(self, username: str, post_type: str = "submitted", limit: int = 25, sort: str = "new") -> List[SocialPost]:
        """Get user's post or comment history."""
        try:
            return await self._get_user_posts_old(username, post_type, limit, sort)
        except Exception:
            log.debug("Old Reddit user posts failed", exc_info=True)
        
        try:
            return await self._get_user_posts_new(username, post_type, limit, sort)
        except Exception:
            log.debug("New Reddit user posts failed", exc_info=True)
        
        return []
    
    def _get_backend_order(self) -> List[str]:
        if self.backend and self.backend != "auto":
            return [self.backend] + [b for b in self.BACKENDS if b != self.backend]
        return self.BACKENDS
    
    # =========================================================================
    # Old Reddit Backend (most reliable)
    # =========================================================================
    
    async def _scrape_old_reddit(self, json_url: str, original_url: str) -> SocialPost:
        """Scrape via old.reddit.com .json endpoint."""
        data = await self._fetch_json(json_url, headers=self._get_headers())
        
        if isinstance(data, list) and len(data) > 0:
            post_data = data[0].get("data", {}).get("children", [{}])[0].get("data", {})
        else:
            post_data = data.get("data", {})
        
        if not post_data:
            raise ValueError("No post data found")
        
        return self._normalize_post(post_data, original_url)
    
    async def _scrape_subreddit_old(self, subreddit: str, sort: str, limit: int, time_filter: str) -> List[SocialPost]:
        """Scrape subreddit via old Reddit."""
        url = f"{self.OLD_REDDIT_BASE}/r/{subreddit}/{sort}.json?limit={limit}"
        if sort in ("top", "controversial"):
            url += f"&t={time_filter}"
        
        data = await self._fetch_json(url, headers=self._get_headers())
        posts = data.get("data", {}).get("children", [])
        
        results = []
        after = data.get("data", {}).get("after")
        
        for post in posts:
            post_data = post.get("data", {})
            if post_data:
                post_url = f"https://reddit.com{post_data.get('permalink', '')}"
                results.append(self._normalize_post(post_data, post_url))
        
        return results[:limit]
    
    async def _search_old(self, query: str, limit: int, subreddit: Optional[str], sort: str, time_filter: str) -> List[SocialPost]:
        """Search via old Reddit."""
        if subreddit:
            url = f"{self.OLD_REDDIT_BASE}/r/{subreddit}/search.json?q={query}&limit={limit}&restrict_sr=1&sort={sort}&t={time_filter}"
        else:
            url = f"{self.OLD_REDDIT_BASE}/search.json?q={query}&limit={limit}&sort={sort}&t={time_filter}"
        
        data = await self._fetch_json(url, headers=self._get_headers())
        posts = data.get("data", {}).get("children", [])
        
        results = []
        for post in posts:
            post_data = post.get("data", {})
            if post_data:
                post_url = f"https://reddit.com{post_data.get('permalink', '')}"
                results.append(self._normalize_post(post_data, post_url))
        
        return results[:limit]
    
    async def _get_comments_old(self, json_url: str, limit: int, sort: str) -> List[Dict[str, Any]]:
        """Get comments via old Reddit with nested reply extraction."""
        url = f"{json_url}?sort={sort}&limit={min(100, limit)}"
        data = await self._fetch_json(url, headers=self._get_headers())
        
        if isinstance(data, list) and len(data) > 1:
            comments_data = data[1].get("data", {}).get("children", [])
        else:
            comments_data = []
        
        return self._extract_comments_recursive(comments_data, limit)
    
    async def _get_user_posts_old(self, username: str, post_type: str, limit: int, sort: str) -> List[SocialPost]:
        """Get user posts via old Reddit."""
        url = f"{self.OLD_REDDIT_BASE}/user/{username}/{post_type}.json?limit={limit}&sort={sort}"
        
        data = await self._fetch_json(url, headers=self._get_headers())
        posts = data.get("data", {}).get("children", [])
        
        results = []
        for post in posts:
            post_data = post.get("data", {})
            if post_data:
                post_url = f"https://reddit.com{post_data.get('permalink', '')}"
                results.append(self._normalize_post(post_data, post_url))
        
        return results[:limit]
    
    def _extract_comments_recursive(self, comments_data: List[Dict], limit: int, depth: int = 0) -> List[Dict[str, Any]]:
        """Recursively extract comments with nesting info."""
        comments = []
        
        for item in comments_data[:limit]:
            if len(comments) >= limit:
                break
            
            kind = item.get("kind", "")
            cdata = item.get("data", {})
            
            if kind != "t1":
                continue
            
            if cdata.get("body") and cdata.get("body") not in ("[deleted]", "[removed]"):
                comment = {
                    "id": cdata.get("id"),
                    "author": cdata.get("author"),
                    "text": cdata.get("body"),
                    "score": cdata.get("score"),
                    "created_utc": cdata.get("created_utc"),
                    "permalink": f"https://reddit.com{cdata.get('permalink', '')}",
                    "is_submitter": cdata.get("is_submitter", False),
                    "depth": depth,
                    "replies": [],
                }
                
                # Extract nested replies
                replies_data = cdata.get("replies", "")
                if isinstance(replies_data, dict):
                    children = replies_data.get("data", {}).get("children", [])
                    remaining = limit - len(comments) - 1
                    if remaining > 0:
                        comment["replies"] = self._extract_comments_recursive(children, remaining, depth + 1)
                
                comments.append(comment)
        
        return comments
    
    # =========================================================================
    # New Reddit Backend
    # =========================================================================
    
    async def _scrape_new_reddit(self, url: str) -> SocialPost:
        """Scrape via new Reddit API."""
        # Extract post ID from URL
        match = re.search(r"/comments/([a-z0-9]+)/", url)
        if not match:
            raise ValueError("Could not extract post ID")
        
        post_id = match.group(1)
        api_url = f"{self.NEW_REDDIT_BASE}/comments/{post_id}.json"
        
        data = await self._fetch_json(api_url, headers=self._get_headers())
        
        if isinstance(data, list) and len(data) > 0:
            post_data = data[0].get("data", {}).get("children", [{}])[0].get("data", {})
        else:
            post_data = data.get("data", {})
        
        if not post_data:
            raise ValueError("No post data found")
        
        return self._normalize_post(post_data, url)
    
    async def _scrape_profile_new(self, username: str) -> SocialProfile:
        """Scrape profile via new Reddit API."""
        url = f"{self.NEW_REDDIT_BASE}/user/{username}/about.json"
        data = await self._fetch_json(url, headers=self._get_headers())
        profile_data = data.get("data", {})
        
        if not profile_data:
            raise ValueError("Profile not found")
        
        return self._normalize_profile(profile_data, username)
    
    async def _scrape_subreddit_new(self, subreddit: str, sort: str, limit: int, time_filter: str) -> List[SocialPost]:
        """Scrape subreddit via new Reddit API."""
        url = f"{self.NEW_REDDIT_BASE}/r/{subreddit}/{sort}.json?limit={limit}"
        if sort in ("top", "controversial"):
            url += f"&t={time_filter}"
        
        data = await self._fetch_json(url, headers=self._get_headers())
        posts = data.get("data", {}).get("children", [])
        
        results = []
        for post in posts:
            post_data = post.get("data", {})
            if post_data:
                post_url = f"https://reddit.com{post_data.get('permalink', '')}"
                results.append(self._normalize_post(post_data, post_url))
        
        return results[:limit]
    
    async def _search_new(self, query: str, limit: int, subreddit: Optional[str], sort: str, time_filter: str) -> List[SocialPost]:
        """Search via new Reddit API."""
        if subreddit:
            url = f"{self.NEW_REDDIT_BASE}/r/{subreddit}/search.json?q={query}&limit={limit}&restrict_sr=1&sort={sort}&t={time_filter}"
        else:
            url = f"{self.NEW_REDDIT_BASE}/search.json?q={query}&limit={limit}&sort={sort}&t={time_filter}"
        
        data = await self._fetch_json(url, headers=self._get_headers())
        posts = data.get("data", {}).get("children", [])
        
        results = []
        for post in posts:
            post_data = post.get("data", {})
            if post_data:
                post_url = f"https://reddit.com{post_data.get('permalink', '')}"
                results.append(self._normalize_post(post_data, post_url))
        
        return results[:limit]
    
    async def _get_comments_new(self, url: str, limit: int, sort: str) -> List[Dict[str, Any]]:
        """Get comments via new Reddit API."""
        match = re.search(r"/comments/([a-z0-9]+)/", url)
        if not match:
            raise ValueError("Could not extract post ID")
        
        post_id = match.group(1)
        api_url = f"{self.NEW_REDDIT_BASE}/comments/{post_id}.json?sort={sort}&limit={min(100, limit)}"
        
        data = await self._fetch_json(api_url, headers=self._get_headers())
        
        if isinstance(data, list) and len(data) > 1:
            comments_data = data[1].get("data", {}).get("children", [])
        else:
            comments_data = []
        
        return self._extract_comments_recursive(comments_data, limit)
    
    async def _get_user_posts_new(self, username: str, post_type: str, limit: int, sort: str) -> List[SocialPost]:
        """Get user posts via new Reddit API."""
        url = f"{self.NEW_REDDIT_BASE}/user/{username}/{post_type}.json?limit={limit}&sort={sort}"
        
        data = await self._fetch_json(url, headers=self._get_headers())
        posts = data.get("data", {}).get("children", [])
        
        results = []
        for post in posts:
            post_data = post.get("data", {})
            if post_data:
                post_url = f"https://reddit.com{post_data.get('permalink', '')}"
                results.append(self._normalize_post(post_data, post_url))
        
        return results[:limit]
    
    # =========================================================================
    # Playwright Backend
    # =========================================================================
    
    async def _scrape_playwright(self, url: str) -> SocialPost:
        """Scrape via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            
            data = await page.evaluate("""() => {
                const title = document.querySelector('h1')?.innerText || '';
                const author = document.querySelector('[data-testid="post_author_link"]')?.innerText || '';
                const body = document.querySelector('[data-testid="post-content"]')?.innerText || '';
                const score = document.querySelector('[data-testid="post_score"]')?.innerText || '0';
                const comments = document.querySelector('[data-testid="post_comment_count"]')?.innerText || '0';
                const subreddit = document.querySelector('[data-testid="subreddit-link"]')?.innerText || '';
                const time = document.querySelector('time')?.getAttribute('datetime') || '';
                const img = document.querySelector('[data-testid="post_image"]')?.src || '';
                
                return {title, author, body, score, comments, subreddit, time, img};
            }""")
            
            return build_post(
                platform="reddit",
                post_type="post",
                url=url,
                id=url.split("/")[-1],
                text=data.get("body", "") or data.get("title", ""),
                timestamp=data.get("time"),
                author={
                    "username": data.get("author", "").lstrip("u/"),
                    "display_name": data.get("author", ""),
                    "subreddit": data.get("subreddit", "").lstrip("r/"),
                },
                engagement={
                    "likes": normalize_number(data.get("score")),
                    "comments": normalize_number(data.get("comments")),
                },
                media=[{"type": "image", "url": data.get("img", "")}] if data.get("img") else [],
            )
    
    async def _scrape_profile_playwright(self, username: str) -> SocialProfile:
        """Scrape profile via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(f"https://www.reddit.com/user/{username}", wait_until="networkidle", timeout=30000)
            
            data = await page.evaluate("""() => {
                const name = document.querySelector('h1')?.innerText || '';
                const karma = document.querySelector('[data-testid="karma-number"]')?.innerText || '0';
                const cakeDay = document.querySelector('[data-testid="cake-day"]')?.innerText || '';
                const bio = document.querySelector('[data-testid="user-profile-description"]')?.innerText || '';
                const avatar = document.querySelector('[data-testid="user-profile-image"]')?.src || '';
                
                return {name, karma, cakeDay, bio, avatar};
            }""")
            
            return build_profile(
                platform="reddit",
                username=username,
                url=f"https://reddit.com/user/{username}",
                profile_data={"author": {
                    "username": username,
                    "display_name": data.get("name", ""),
                    "avatar": data.get("avatar", ""),
                    "bio": data.get("bio", ""),
                    "karma": normalize_number(data.get("karma")),
                    "cake_day": data.get("cakeDay"),
                }, "engagement": {}},
            )
    
    # =========================================================================
    # Normalization
    # =========================================================================
    
    def _normalize_post(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize Reddit post data."""
        author = {
            "username": data.get("author", "[deleted]"),
            "display_name": data.get("author", "[deleted]"),
            "avatar": f"https://www.reddit.com/static/avatars/default.png",
            "subreddit": data.get("subreddit", ""),
        }
        
        engagement = {
            "likes": data.get("score"),
            "comments": data.get("num_comments"),
            "shares": data.get("num_crossposts"),
            "upvote_ratio": data.get("upvote_ratio"),
            "awards": data.get("total_awards_received"),
        }
        
        media = []
        if data.get("post_hint") in ("image", "hosted:image"):
            media.append({"type": "image", "url": data.get("url", "")})
        elif data.get("post_hint") in ("video", "hosted:video"):
            video_data = data.get("secure_media", {}).get("reddit_video", {})
            media.append({
                "type": "video",
                "url": video_data.get("fallback_url", ""),
                "width": video_data.get("width"),
                "height": video_data.get("height"),
                "duration": video_data.get("duration"),
            })
        elif data.get("post_hint") == "link":
            # External link - include thumbnail
            media.append({
                "type": "link",
                "url": data.get("url", ""),
                "thumbnail": data.get("thumbnail", ""),
            })
        elif data.get("preview", {}).get("images"):
            for img in data["preview"]["images"]:
                media.append({"type": "image", "url": img.get("source", {}).get("url", "")})
        
        # Gallery
        if data.get("is_gallery"):
            gallery_items = data.get("media_metadata", {})
            for item_id, item in gallery_items.items():
                if item.get("s", {}).get("u"):
                    media.append({"type": "image", "url": item["s"]["u"]})
        
        # Flair
        flair = data.get("link_flair_text", "")
        
        # Cross-post
        crosspost = None
        if data.get("crosspost_parent"):
            crosspost = data.get("crosspost_parent_list", [{}])[0].get("permalink", "")
        
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
            tags=[flair] if flair else [],
        )
    
    def _normalize_profile(self, data: Dict[str, Any], username: str) -> SocialProfile:
        """Normalize Reddit user profile."""
        author = {
            "username": data.get("name", username),
            "display_name": data.get("name", username),
            "avatar": data.get("icon_img", ""),
            "verified": data.get("has_verified_email", False),
            "followers": None,
            "bio": data.get("description", ""),
            "location": "",
            "joined_date": data.get("created_utc"),
            "profile_url": f"https://reddit.com/user/{username}",
        }
        
        engagement = {
            "link_karma": data.get("link_karma"),
            "comment_karma": data.get("comment_karma"),
            "total_karma": data.get("total_karma"),
            "awards": data.get("awarder_karma"),
        }
        
        return build_profile(
            platform="reddit",
            username=username,
            url=f"https://reddit.com/user/{username}",
            profile_data={"author": author, "engagement": engagement, "id": data.get("id")},
        )
    
    # =========================================================================
    # URL helpers
    # =========================================================================
    
    def _to_json_url(self, url: str) -> str:
        """Convert Reddit URL to .json endpoint."""
        if url.endswith(".json"):
            return url
        
        # Handle short reddit URLs
        url = url.replace("www.reddit.com", "old.reddit.com")
        
        if not url.endswith("/"):
            url += "/"
        
        return url + ".json"
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract post ID, username, or subreddit from URL."""
        match = re.search(r"/comments/([a-z0-9]+)/", url)
        if match:
            return match.group(1)
        
        match = re.search(r"/user/([^/]+)", url)
        if match:
            return match.group(1)
        
        match = re.search(r"/r/([^/]+)", url)
        if match:
            return match.group(1)
        
        return None
    
    @classmethod
    def extract_identifier_class(cls, url: str) -> Optional[str]:
        match = re.search(r"/comments/([a-z0-9]+)/", url)
        if match:
            return match.group(1)
        
        match = re.search(r"/user/([^/]+)", url)
        if match:
            return match.group(1)
        
        match = re.search(r"/r/([^/]+)", url)
        if match:
            return match.group(1)
        
        return None


registry.register(RedditScraper)
