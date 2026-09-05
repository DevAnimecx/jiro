"""Twitter/X scraper with 3-backend fallback: FxTwitter → Nitter → Playwright."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.twitter")


class TwitterScraper(BaseSocialScraper):
    """Twitter/X scraper with multiple backend fallbacks."""
    
    platform = "twitter"
    url_patterns = [
        "twitter.com",
        "x.com",
        "t.co",
        "fxtwitter.com",
        "vxtwitter.com",
        "nitter.net",
        "nitter.it",
    ]
    supported_actions = ["post", "profile", "timeline", "search", "list", "space"]
    rate_limit_rpm = 30
    requires_auth = True  # For full access, but public endpoints work without
    
    # Backend priority order
    BACKENDS = ["fxtwitter", "nitter", "playwright"]
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        self.backend = settings.social.get("twitter", {}).get("backend", "auto")
        self.nitter_url = settings.social.get("twitter", {}).get("nitter_url", "http://127.0.0.1:8788")
        self.auth_token = settings.social.get("twitter", {}).get("auth_token", "")
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a tweet with fallback backends."""
        tweet_id = self._extract_tweet_id(url)
        if not tweet_id:
            raise ValueError("Could not extract tweet ID from URL")
        
        backends = self._get_backend_order()
        
        for backend in backends:
            try:
                if backend == "fxtwitter":
                    return await self._scrape_fxtwitter(tweet_id, url)
                elif backend == "nitter":
                    return await self._scrape_nitter(tweet_id, url)
                elif backend == "playwright":
                    return await self._scrape_playwright(url)
            except Exception as e:
                log.debug(f"Twitter backend {backend} failed", extra={"error": str(e)})
                continue
        
        raise ValueError(f"All Twitter backends failed for tweet: {tweet_id}")
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a Twitter profile."""
        backends = self._get_backend_order()
        
        for backend in backends:
            try:
                if backend == "fxtwitter":
                    return await self._scrape_profile_fxtwitter(username)
                elif backend == "nitter":
                    return await self._scrape_profile_nitter(username)
                elif backend == "playwright":
                    return await self._scrape_profile_playwright(username)
            except Exception as e:
                log.debug(f"Twitter profile backend {backend} failed", extra={"error": str(e)})
                continue
        
        raise ValueError(f"All Twitter backends failed for profile: {username}")
    
    async def scrape_timeline(self, username: str, limit: int = 20) -> List[SocialPost]:
        """Scrape user timeline."""
        backends = self._get_backend_order()
        
        for backend in backends:
            try:
                if backend == "nitter":
                    return await self._scrape_timeline_nitter(username, limit)
                elif backend == "playwright":
                    return await self._scrape_timeline_playwright(username, limit)
            except Exception as e:
                log.debug(f"Twitter timeline backend {backend} failed", extra={"error": str(e)})
                continue
        
        raise ValueError(f"All Twitter backends failed for timeline: {username}")
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search Twitter (uses Nitter or requires auth)."""
        # Nitter search
        try:
            return await self._search_nitter(query, limit)
        except Exception:
            pass
        
        # Playwright search
        try:
            return await self._search_playwright(query, limit)
        except Exception:
            pass
        
        raise ValueError("Twitter search requires authentication or working Nitter instance")
    
    def _get_backend_order(self) -> List[str]:
        """Get ordered list of backends to try."""
        if self.backend == "auto":
            return self.BACKENDS
        elif self.backend in self.BACKENDS:
            return [self.backend] + [b for b in self.BACKENDS if b != self.backend]
        return self.BACKENDS
    
    def _extract_tweet_id(self, url: str) -> Optional[str]:
        """Extract tweet ID from various Twitter URL formats."""
        patterns = [
            r"(?:twitter|x)\.com/\w+/status/(\d+)",
            r"t\.co/(\w+)",  # Short URLs need expansion
            r"(?:fxtwitter|vxtwitter)\.com/\w+/status/(\d+)",
            r"nitter\.(?:net|it)/\w+/status/(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    # --- FxTwitter Backend ---
    
    async def _scrape_fxtwitter(self, tweet_id: str, url: str) -> SocialPost:
        """Scrape via FxTwitter API (fastest, no auth)."""
        api_url = f"https://api.fxtwitter.com/tweet/{tweet_id}"
        data = await self._fetch_json(api_url)
        
        tweet = data.get("tweet", {})
        if not tweet:
            raise ValueError("Tweet not found via FxTwitter")
        
        return self._normalize_fxtwitter(tweet, url)
    
    async def _scrape_profile_fxtwitter(self, username: str) -> SocialProfile:
        """Scrape profile via FxTwitter."""
        api_url = f"https://api.fxtwitter.com/user/{username}"
        data = await self._fetch_json(api_url)
        
        user = data.get("user", {})
        if not user:
            raise ValueError("Profile not found via FxTwitter")
        
        return self._normalize_profile_fxtwitter(user, username)
    
    def _normalize_fxtwitter(self, tweet: Dict[str, Any], url: str) -> SocialPost:
        """Normalize FxTwitter tweet response."""
        author = {
            "username": tweet.get("author", {}).get("screen_name", ""),
            "display_name": tweet.get("author", {}).get("name", ""),
            "avatar": tweet.get("author", {}).get("avatar_url", ""),
            "verified": tweet.get("author", {}).get("verified", False),
            "followers": tweet.get("author", {}).get("followers_count"),
            "profile_url": f"https://twitter.com/{tweet.get('author', {}).get('screen_name', '')}",
        }
        
        engagement = {
            "likes": tweet.get("likes"),
            "retweets": tweet.get("retweets"),
            "replies": tweet.get("replies"),
            "quotes": tweet.get("quotes"),
        }
        
        media = []
        for m in tweet.get("media", {}).get("photos", []):
            media.append({"type": "image", "url": m.get("url", ""), "thumbnail": m.get("url", "")})
        for m in tweet.get("media", {}).get("videos", []):
            media.append({"type": "video", "url": m.get("url", ""), "thumbnail": m.get("thumbnail", ""), "duration": m.get("duration")})
        for m in tweet.get("media", {}).get("gifs", []):
            media.append({"type": "gif", "url": m.get("url", ""), "thumbnail": m.get("thumbnail", "")})
        
        return build_post(
            platform="twitter",
            post_type="post",
            url=url,
            id=str(tweet.get("id", "")),
            text=tweet.get("text", ""),
            timestamp=tweet.get("created_at"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile_fxtwitter(self, user: Dict[str, Any], username: str) -> SocialProfile:
        """Normalize FxTwitter profile response."""
        author = {
            "username": user.get("screen_name", username),
            "display_name": user.get("name", ""),
            "avatar": user.get("avatar_url", ""),
            "verified": user.get("verified", False),
            "followers": user.get("followers_count"),
            "following": user.get("following_count"),
            "posts_count": user.get("statuses_count"),
            "bio": user.get("description", ""),
            "location": user.get("location", ""),
            "joined_date": user.get("created_at"),
        }
        
        return build_profile(
            platform="twitter",
            username=username,
            url=f"https://twitter.com/{username}",
            profile_data={"author": author, "engagement": {}, "id": user.get("id")},
        )
    
    # --- Nitter Backend ---
    
    async def _scrape_nitter(self, tweet_id: str, url: str) -> SocialPost:
        """Scrape via Nitter instance."""
        api_url = f"{self.nitter_url}/api/v1/tweet/{tweet_id}"
        data = await self._fetch_json(api_url)
        
        if not data.get("tweet"):
            raise ValueError("Tweet not found via Nitter")
        
        return self._normalize_nitter(data["tweet"], url)
    
    async def _scrape_profile_nitter(self, username: str) -> SocialProfile:
        """Scrape profile via Nitter."""
        api_url = f"{self.nitter_url}/api/v1/user/{username}"
        data = await self._fetch_json(api_url)
        
        if not data.get("user"):
            raise ValueError("Profile not found via Nitter")
        
        return self._normalize_profile_nitter(data["user"], username)
    
    async def _scrape_timeline_nitter(self, username: str, limit: int) -> List[SocialPost]:
        """Scrape timeline via Nitter."""
        api_url = f"{self.nitter_url}/api/v1/user/{username}/tweets"
        params = {"limit": limit}
        data = await self._fetch_json(api_url, params=params)
        
        tweets = data.get("tweets", [])
        results = []
        for tweet in tweets[:limit]:
            tweet_url = f"https://twitter.com/{username}/status/{tweet.get('id')}"
            results.append(self._normalize_nitter(tweet, tweet_url))
        
        return results
    
    async def _search_nitter(self, query: str, limit: int) -> List[SocialPost]:
        """Search via Nitter."""
        api_url = f"{self.nitter_url}/api/v1/search"
        params = {"q": query, "limit": limit}
        data = await self._fetch_json(api_url, params=params)
        
        tweets = data.get("tweets", [])
        results = []
        for tweet in tweets[:limit]:
            tweet_url = f"https://twitter.com/{tweet.get('user', {}).get('screen_name', '')}/status/{tweet.get('id')}"
            results.append(self._normalize_nitter(tweet, tweet_url))
        
        return results
    
    def _normalize_nitter(self, tweet: Dict[str, Any], url: str) -> SocialPost:
        """Normalize Nitter tweet response."""
        author = {
            "username": tweet.get("user", {}).get("screen_name", ""),
            "display_name": tweet.get("user", {}).get("name", ""),
            "avatar": tweet.get("user", {}).get("avatar", ""),
            "verified": tweet.get("user", {}).get("verified", False),
            "followers": tweet.get("user", {}).get("followers"),
        }
        
        engagement = {
            "likes": tweet.get("likes"),
            "retweets": tweet.get("retweets"),
            "replies": tweet.get("replies"),
            "quotes": tweet.get("quotes"),
        }
        
        media = []
        for m in tweet.get("media", []):
            media.append({
                "type": m.get("type", "image"),
                "url": m.get("url", ""),
                "thumbnail": m.get("thumbnail", ""),
            })
        
        return build_post(
            platform="twitter",
            post_type="post",
            url=url,
            id=str(tweet.get("id", "")),
            text=tweet.get("text", ""),
            timestamp=tweet.get("date"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile_nitter(self, user: Dict[str, Any], username: str) -> SocialProfile:
        """Normalize Nitter profile."""
        author = {
            "username": user.get("screen_name", username),
            "display_name": user.get("name", ""),
            "avatar": user.get("avatar", ""),
            "verified": user.get("verified", False),
            "followers": user.get("followers"),
            "following": user.get("following"),
            "posts_count": user.get("statuses"),
            "bio": user.get("bio", ""),
            "location": user.get("location", ""),
            "joined_date": user.get("joined"),
        }
        
        return build_profile(
            platform="twitter",
            username=username,
            url=f"https://twitter.com/{username}",
            profile_data={"author": author, "engagement": {}, "id": user.get("id")},
        )
    
    # --- Playwright Backend ---
    
    async def _scrape_playwright(self, url: str) -> SocialPost:
        """Scrape via Playwright (requires browser)."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=10000)
            
            tweet_data = await page.evaluate("""() => {
                const tweet = document.querySelector('article[data-testid="tweet"]');
                if (!tweet) return null;
                
                const text = tweet.querySelector('[data-testid="tweetText"]')?.innerText || '';
                const authorName = tweet.querySelector('[data-testid="User-Name"]')?.innerText || '';
                const authorHandle = tweet.querySelector('[data-testid="User-Name"] a[href^="/"]')?.href?.split('/').pop() || '';
                const timestamp = tweet.querySelector('time')?.dateTime || '';
                const likes = tweet.querySelector('[data-testid="like"]')?.innerText || '0';
                const retweets = tweet.querySelector('[data-testid="retweet"]')?.innerText || '0';
                const replies = tweet.querySelector('[data-testid="reply"]')?.innerText || '0';
                
                return {text, authorName, authorHandle, timestamp, likes, retweets, replies};
            }""")
            
            if not tweet_data:
                raise ValueError("Could not extract tweet data")
            
            return self._normalize_playwright(tweet_data, url)
    
    async def _scrape_profile_playwright(self, username: str) -> SocialProfile:
        """Scrape profile via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(f"https://twitter.com/{username}", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('[data-testid="primaryColumn"]', timeout=10000)
            
            profile_data = await page.evaluate("""() => {
                const name = document.querySelector('[data-testid="UserName"]')?.innerText || '';
                const handle = document.querySelector('[data-testid="UserName"] a[href^="/"]')?.href?.split('/').pop() || '';
                const bio = document.querySelector('[data-testid="UserDescription"]')?.innerText || '';
                const followers = document.querySelector('a[href$="/followers"] span')?.innerText || '0';
                const following = document.querySelector('a[href$="/following"] span')?.innerText || '0';
                const avatar = document.querySelector('[data-testid="UserAvatar"] img')?.src || '';
                const verified = !!document.querySelector('[data-testid="UserName"] svg[aria-label="Verified account"]');
                const location = document.querySelector('[data-testid="UserLocation"]')?.innerText || '';
                const joinDate = document.querySelector('[data-testid="UserJoinDate"]')?.innerText || '';
                
                return {name, handle, bio, followers, following, avatar, verified, location, joinDate};
            }""")
            
            return self._normalize_profile_playwright(profile_data, username)
    
    async def _scrape_timeline_playwright(self, username: str, limit: int) -> List[SocialPost]:
        """Scrape timeline via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            await page.goto(f"https://twitter.com/{username}", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('[data-testid="primaryColumn"]', timeout=10000)
            
            tweets = []
            while len(tweets) < limit:
                tweet_elements = await page.query_selector_all('article[data-testid="tweet"]')
                for elem in tweet_elements[len(tweets):]:
                    try:
                        tweet_url = await elem.evaluate("""el => {
                            const link = el.querySelector('a[href*="/status/"]');
                            return link?.href || '';
                        }""")
                        if tweet_url:
                            tweets.append(tweet_url)
                            if len(tweets) >= limit:
                                break
                    except Exception:
                        continue
                
                if len(tweets) >= limit:
                    break
                
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
            
            results = []
            for tweet_url in tweets[:limit]:
                try:
                    results.append(await self._scrape_playwright(tweet_url))
                except Exception:
                    continue
            
            return results
    
    async def _search_playwright(self, query: str, limit: int) -> List[SocialPost]:
        """Search via Playwright."""
        from jiro.browser import get_browser_page

        async with get_browser_page() as page:
            search_url = f"https://twitter.com/search?q={query}&src=typed_query&f=live"
            await page.goto(search_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=10000)
            
            tweets = []
            while len(tweets) < limit:
                tweet_elements = await page.query_selector_all('article[data-testid="tweet"]')
                for elem in tweet_elements[len(tweets):]:
                    try:
                        tweet_url = await elem.evaluate("""el => {
                            const link = el.querySelector('a[href*="/status/"]');
                            return link?.href || '';
                        }""")
                        if tweet_url and tweet_url not in tweets:
                            tweets.append(tweet_url)
                            if len(tweets) >= limit:
                                break
                    except Exception:
                        continue
                
                if len(tweets) >= limit:
                    break
                
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
            
            results = []
            for tweet_url in tweets[:limit]:
                try:
                    results.append(await self._scrape_playwright(tweet_url))
                except Exception:
                    continue
            
            return results
    
    def _normalize_playwright(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize Playwright tweet data."""
        author = {
            "username": data.get("authorHandle", ""),
            "display_name": data.get("authorName", "").split("\n")[0] if data.get("authorName") else "",
        }
        
        engagement = {
            "likes": normalize_number(data.get("likes")),
            "retweets": normalize_number(data.get("retweets")),
            "replies": normalize_number(data.get("replies")),
        }
        
        return build_post(
            platform="twitter",
            post_type="post",
            url=url,
            id=url.split("/")[-1],
            text=data.get("text", ""),
            timestamp=data.get("timestamp"),
            author=author,
            engagement=engagement,
            media=[],
        )
    
    def _normalize_profile_playwright(self, data: Dict[str, Any], username: str) -> SocialProfile:
        """Normalize Playwright profile data."""
        author = {
            "username": data.get("handle", username),
            "display_name": data.get("name", ""),
            "avatar": data.get("avatar", ""),
            "verified": data.get("verified", False),
            "followers": normalize_number(data.get("followers")),
            "following": normalize_number(data.get("following")),
            "bio": data.get("bio", ""),
            "location": data.get("location", ""),
            "joined_date": data.get("joinDate"),
        }
        
        return build_profile(
            platform="twitter",
            username=username,
            url=f"https://twitter.com/{username}",
            profile_data={"author": author, "engagement": {}},
        )
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract tweet ID or username from URL."""
        tweet_id = self._extract_tweet_id(url)
        if tweet_id:
            return tweet_id
        
        # Profile
        match = re.search(r"(?:twitter|x)\.com/([^/?]+)", url)
        if match:
            return match.group(1)
        return None
    
    @classmethod
    def extract_identifier_class(cls, url: str) -> Optional[str]:
        """Class method to extract identifier without instantiation."""
        tweet_id = cls._extract_tweet_id_class(url)
        if tweet_id:
            return tweet_id
        
        # Profile
        match = re.search(r"(?:twitter|x)\.com/([^/?]+)", url)
        if match:
            return match.group(1)
        return None
    
    @classmethod
    def _extract_tweet_id_class(cls, url: str) -> Optional[str]:
        """Class method to extract tweet ID without instantiation."""
        patterns = [
            r"(?:twitter|x)\.com/\w+/status/(\d+)",
            r"t\.co/(\w+)",
            r"(?:fxtwitter|vxtwitter)\.com/\w+/status/(\d+)",
            r"nitter\.(?:net|it)/\w+/status/(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None


# Register the scraper
registry.register(TwitterScraper)