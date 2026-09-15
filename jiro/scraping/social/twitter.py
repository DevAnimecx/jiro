"""Twitter/X scraper with 4-backend fallback: GraphQL API → FxTwitter → Nitter → Playwright.

GraphQL backend uses guest token auth for public data and cookie-based auth for
search/timeline. Auto-extracts query IDs from X's frontend JS to adapt to changes.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, quote

from jiro.scraping.social.base import BaseSocialScraper, RateLimitError, SocialPost, SocialProfile, registry
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
    supported_actions = ["post", "profile", "timeline", "search", "list", "space", "comments"]
    rate_limit_rpm = 30
    requires_auth = True
    
    BACKENDS = ["graphql", "fxtwitter", "nitter", "playwright"]
    
    # GraphQL endpoints (auto-refreshed from X's JS)
    _GRAPHQL_ENDPOINTS = {
        "TweetDetail": "https://x.com/i/api/graphql/PoZFFkzHhXmALmGbdkLxbA/TweetDetail",
        "UserByScreenName": "https://x.com/i/api/graphql/xmU6X_CKVnQ5lSrCbAmJsg/UserByScreenName",
        "UserTweets": "https://x.com/i/api/graphql/E3opETHurmVJflFsUBVuUw/UserTweets",
        "SearchTimeline": "https://x.com/i/api/graphql/lZ0GCEojmtQfiUQa5oJSEw/SearchTimeline",
        "TweetResultsById": "https://x.com/i/api/graphql/lZ0GCEojmtQfiUQa5oJSEw/TweetResultsById",
    }
    
    # Feature tokens for GraphQL requests
    _GRAPHQL_FEATURES = {
        "hidden_profile_subscriptions_enabled": True,
        "rweb_tipjar_consumption_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "subscriptions_verification_info_is_identity_verified_enabled": True,
        "subscriptions_verification_info_verified_since_enabled": True,
        "highlights_tweets_tab_ui_enabled": True,
        "responsive_web_twitter_article_notes_tab_enabled": True,
        "subscriptions_feature_can_gift_premium": True,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
    }
    
    def __init__(self, client, settings):
        super().__init__(client, settings)
        cfg = settings.social.get("twitter", {}) if settings else {}
        self.backend = cfg.get("backend", "auto")
        self.nitter_url = cfg.get("nitter_url", "http://127.0.0.1:8788")
        self.auth_token = cfg.get("auth_token", "")
        self.ct0 = cfg.get("ct0", "")
        self.guest_token: Optional[str] = None
        self.guest_token_expires: float = 0
        self._query_ids: Dict[str, str] = {}
        self._query_ids_fetched: float = 0
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a tweet with fallback backends."""
        tweet_id = self._extract_tweet_id(url)
        if not tweet_id:
            raise ValueError("Could not extract tweet ID from URL")
        
        backends = self._get_backend_order()
        errors = []
        
        for backend in backends:
            try:
                if backend == "graphql":
                    return await self._scrape_graphql(tweet_id, url)
                elif backend == "fxtwitter":
                    return await self._scrape_fxtwitter(tweet_id, url)
                elif backend == "nitter":
                    return await self._scrape_nitter(tweet_id, url)
                elif backend == "playwright":
                    return await self._scrape_playwright(url)
            except Exception as e:
                log.debug("Twitter backend %s failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
        
        raise ValueError(f"All Twitter backends failed for tweet {tweet_id}: {errors}")
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a Twitter profile."""
        backends = self._get_backend_order()
        errors = []
        
        for backend in backends:
            try:
                if backend == "graphql":
                    return await self._scrape_profile_graphql(username)
                elif backend == "fxtwitter":
                    return await self._scrape_profile_fxtwitter(username)
                elif backend == "nitter":
                    return await self._scrape_profile_nitter(username)
                elif backend == "playwright":
                    return await self._scrape_profile_playwright(username)
            except Exception as e:
                log.debug("Twitter profile backend %s failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
        
        raise ValueError(f"All Twitter backends failed for profile {username}: {errors}")
    
    async def scrape_timeline(self, username: str, limit: int = 40) -> List[SocialPost]:
        """Scrape user timeline with pagination."""
        backends = self._get_backend_order()
        errors = []
        
        for backend in backends:
            try:
                if backend == "graphql":
                    return await self._scrape_timeline_graphql(username, limit)
                elif backend == "nitter":
                    return await self._scrape_timeline_nitter(username, limit)
                elif backend == "playwright":
                    return await self._scrape_timeline_playwright(username, limit)
            except Exception as e:
                log.debug("Twitter timeline backend %s failed: %s", backend, e)
                errors.append((backend, str(e)))
                continue
        
        raise ValueError(f"All Twitter backends failed for timeline {username}: {errors}")
    
    async def search(self, query: str, limit: int = 40) -> List[SocialPost]:
        """Search Twitter with pagination."""
        # GraphQL search (best with auth)
        if self.auth_token:
            try:
                return await self._search_graphql(query, limit)
            except Exception as e:
                log.debug("GraphQL search failed: %s", e)
        
        # Nitter search
        try:
            return await self._search_nitter(query, limit)
        except Exception as e:
            log.debug("Nitter search failed: %s", e)
        
        # Playwright search
        try:
            return await self._search_playwright(query, limit)
        except Exception as e:
            log.debug("Playwright search failed: %s", e)
        
        raise ValueError("Twitter search requires authentication or working Nitter instance")
    
    async def scrape_comments(self, url: str, limit: int = 50) -> List[SocialPost]:
        """Scrape replies/comments for a tweet."""
        tweet_id = self._extract_tweet_id(url)
        if not tweet_id:
            raise ValueError("Could not extract tweet ID from URL")
        
        try:
            return await self._scrape_replies_graphql(tweet_id, limit)
        except Exception as e:
            log.debug("GraphQL replies failed: %s", e)
        
        # Fallback: Nitter replies
        try:
            return await self._scrape_replies_nitter(tweet_id, limit)
        except Exception:
            pass
        
        return []
    
    # =========================================================================
    # Backend routing
    # =========================================================================
    
    def _get_backend_order(self) -> List[str]:
        if self.backend == "auto":
            return self.BACKENDS
        elif self.backend in self.BACKENDS:
            return [self.backend] + [b for b in self.BACKENDS if b != self.backend]
        return self.BACKENDS
    
    # =========================================================================
    # GraphQL Backend (primary - no browser needed)
    # =========================================================================
    
    async def _ensure_guest_token(self) -> str:
        """Get or refresh guest token for unauthenticated GraphQL access."""
        if self.guest_token and time.time() < self.guest_token_expires:
            return self.guest_token
        
        url = "https://api.x.com/1.1/guest/activate.json"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
        }
        
        try:
            text, resp = await self.client.post(url, extra_headers=headers, engine=self.platform)
            data = resp.json()
            self.guest_token = data.get("guest_token", "")
            self.guest_token_expires = time.time() + 1800  # 30 min
            return self.guest_token
        except Exception as e:
            log.debug("Failed to get guest token: %s", e)
            return ""
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get headers for GraphQL requests (guest or authenticated)."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
            "X-Twitter-Active-User": "yes",
            "X-Twitter-Auth-Type": "OAuth2Session",
            "X-Csrf-Token": self.ct0,
            "Content-Type": "application/json",
        }
        
        if self.auth_token and self.ct0:
            headers["Cookie"] = f"auth_token={self.auth_token}; ct0={self.ct0}"
        else:
            guest = asyncio.get_event_loop().run_until_complete(self._ensure_guest_token()) if self._ensure_guest_token else ""
            if guest:
                headers["X-Guest-Token"] = guest
        
        return headers
    
    async def _graphql_request(self, endpoint: str, variables: Dict[str, Any], features: Optional[Dict] = None) -> Dict[str, Any]:
        """Make a GraphQL request to Twitter/X API."""
        headers = self._get_auth_headers()
        
        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(features or self._GRAPHQL_FEATURES, separators=(",", ":")),
        }
        
        text, resp = await self.client.get(endpoint, engine=self.platform, params=params, extra_headers=headers)
        
        if resp.status_code == 429:
            raise RateLimitError("twitter")
        
        if resp.status_code == 401:
            # Token expired, try guest token
            self.guest_token = None
            self.guest_token_expires = 0
            headers = self._get_auth_headers()
            text, resp = await self.client.get(endpoint, engine=self.platform, params=params, extra_headers=headers)
        
        resp.raise_for_status()
        return resp.json()
    
    async def _scrape_graphql(self, tweet_id: str, url: str) -> SocialPost:
        """Scrape tweet via GraphQL TweetDetail endpoint."""
        variables = {
            "focalTweetId": tweet_id,
            "with_rux_injects": False,
            "includePromotedContent": False,
            "withCommunity": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withBirdwatchNotes": True,
            "withVoice": True,
            "withV2Timeline": True,
        }
        
        endpoint = self._GRAPHQL_ENDPOINTS.get("TweetDetail")
        data = await self._graphql_request(endpoint, variables)
        
        # Extract tweet from timeline instructions
        tweet = self._extract_tweet_from_graphql(data, tweet_id)
        if not tweet:
            raise ValueError("Tweet not found in GraphQL response")
        
        return self._normalize_graphql_tweet(tweet, url)
    
    async def _scrape_profile_graphql(self, username: str) -> SocialProfile:
        """Scrape profile via GraphQL UserByScreenName."""
        variables = {
            "screen_name": username,
            "withSafetyModeUserFields": True,
        }
        
        endpoint = self._GRAPHQL_ENDPOINTS.get("UserByScreenName")
        data = await self._graphql_request(endpoint, variables)
        
        user = data.get("data", {}).get("user", {}).get("result", {})
        if not user or user.get("__typename") == "UserUnavailable":
            raise ValueError(f"Profile not found: {username}")
        
        return self._normalize_graphql_profile(user, username)
    
    async def _scrape_timeline_graphql(self, username: str, limit: int = 40) -> List[SocialPost]:
        """Scrape timeline via GraphQL UserTweets with pagination."""
        # First get user ID
        profile = await self._scrape_profile_graphql(username)
        user_id = profile.data.get("id")
        if not user_id:
            raise ValueError("Could not get user ID")
        
        all_tweets = []
        cursor = None
        
        while len(all_tweets) < limit:
            variables = {
                "userId": user_id,
                "count": min(20, limit - len(all_tweets)),
                "includePromotedContent": False,
                "withQuickPromoteEligibilityTweetFields": True,
                "withVoice": True,
                "withV2Timeline": True,
            }
            if cursor:
                variables["cursor"] = cursor
            
            endpoint = self._GRAPHQL_ENDPOINTS.get("UserTweets")
            data = await self._graphql_request(endpoint, variables)
            
            tweets, next_cursor = self._extract_tweets_from_timeline(data)
            
            for tweet in tweets:
                if len(all_tweets) >= limit:
                    break
                tweet_id = tweet.get("rest_id", "")
                tweet_url = f"https://twitter.com/{username}/status/{tweet_id}"
                all_tweets.append(self._normalize_graphql_tweet(tweet, tweet_url))
            
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        
        return all_tweets
    
    async def _search_graphql(self, query: str, limit: int = 40) -> List[SocialPost]:
        """Search via GraphQL SearchTimeline."""
        all_tweets = []
        cursor = None
        
        while len(all_tweets) < limit:
            variables = {
                "rawQuery": query,
                "count": min(20, limit - len(all_tweets)),
                "querySource": "typed_query",
                "product": "Latest",
            }
            if cursor:
                variables["cursor"] = cursor
            
            endpoint = self._GRAPHQL_ENDPOINTS.get("SearchTimeline")
            data = await self._graphql_request(endpoint, variables)
            
            tweets, next_cursor = self._extract_tweets_from_timeline(data)
            
            for tweet in tweets:
                if len(all_tweets) >= limit:
                    break
                tweet_id = tweet.get("rest_id", "")
                user = tweet.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
                username = user.get("screen_name", "")
                tweet_url = f"https://twitter.com/{username}/status/{tweet_id}"
                all_tweets.append(self._normalize_graphql_tweet(tweet, tweet_url))
            
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        
        return all_tweets
    
    async def _scrape_replies_graphql(self, tweet_id: str, limit: int = 50) -> List[SocialPost]:
        """Scrape replies for a tweet."""
        variables = {
            "focalTweetId": tweet_id,
            "withRuxInjects": False,
            "includePromotedContent": True,
            "withCommunity": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withBirdwatchNotes": True,
            "withVoice": True,
            "withV2Timeline": True,
        }
        
        endpoint = self._GRAPHQL_ENDPOINTS.get("TweetDetail")
        data = await self._graphql_request(endpoint, variables)
        
        # Extract all tweets from timeline (replies are in the conversation)
        tweets, _ = self._extract_tweets_from_timeline(data)
        
        results = []
        for tweet in tweets:
            if len(results) >= limit:
                break
            tid = tweet.get("rest_id", "")
            if tid == tweet_id:
                continue  # Skip the parent tweet
            user = tweet.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
            username = user.get("screen_name", "")
            tweet_url = f"https://twitter.com/{username}/status/{tid}"
            results.append(self._normalize_graphql_tweet(tweet, tweet_url))
        
        return results
    
    # =========================================================================
    # GraphQL response parsing
    # =========================================================================
    
    def _extract_tweet_from_graphql(self, data: Dict, tweet_id: str) -> Optional[Dict]:
        """Extract a single tweet from GraphQL TweetDetail response."""
        try:
            instructions = (
                data.get("data", {})
                .get("threaded_conversation_with_injections_v2", {})
                .get("instructions", [])
            )
            
            for instruction in instructions:
                entries = instruction.get("entries", [])
                for entry in entries:
                    entry_id = entry.get("entryId", "")
                    
                    # Main tweet
                    if entry_id == f"tweet-{tweet_id}":
                        content = entry.get("content", {})
                        tweet_results = content.get("itemContent", {}).get("tweet_results", {})
                        tweet = tweet_results.get("result", {})
                        if tweet.get("__typename") == "Tweet":
                            return tweet
                    
                    # Timeline item
                    items = entry.get("content", {}).get("items", [])
                    for item in items:
                        tweet_results = item.get("item", {}).get("itemContent", {}).get("tweet_results", {})
                        tweet = tweet_results.get("result", {})
                        if tweet.get("__typename") == "Tweet" and tweet.get("rest_id") == tweet_id:
                            return tweet
        except Exception as e:
            log.debug("Failed to extract tweet from GraphQL: %s", e)
        
        return None
    
    def _extract_tweets_from_timeline(self, data: Dict) -> tuple[List[Dict], Optional[str]]:
        """Extract tweets and pagination cursor from timeline response."""
        tweets = []
        cursor = None
        
        try:
            instructions = (
                data.get("data", {})
                .get("user", {})
                .get("result", {})
                .get("timeline_v2", {})
                .get("timeline", {})
                .get("instructions", [])
            )
            
            # Also check search timeline
            if not instructions:
                instructions = (
                    data.get("data", {})
                    .get("search_by_raw_query", {})
                    .get("search_timeline", {})
                    .get("timeline", {})
                    .get("instructions", [])
                )
            
            for instruction in instructions:
                entries = instruction.get("entries", [])
                
                for entry in entries:
                    entry_id = entry.get("entryId", "")
                    
                    # Pagination cursor
                    if entry_id.startswith("cursor-bottom"):
                        cursor = (
                            entry.get("content", {})
                            .get("value")
                            or entry.get("content", {})
                            .get("itemContent", {})
                            .get("value")
                        )
                        continue
                    
                    # Tweet entries
                    content = entry.get("content", {})
                    
                    # Single item
                    item_content = content.get("itemContent", {})
                    tweet_results = item_content.get("tweet_results", {})
                    tweet = tweet_results.get("result", {})
                    if tweet.get("__typename") == "Tweet":
                        tweets.append(tweet)
                    
                    # Module items (conversation threads)
                    items = content.get("items", [])
                    for item in items:
                        item_content = item.get("item", {}).get("itemContent", {})
                        tweet_results = item_content.get("tweet_results", {})
                        tweet = tweet_results.get("result", {})
                        if tweet.get("__typename") == "Tweet":
                            tweets.append(tweet)
        except Exception as e:
            log.debug("Failed to extract tweets from timeline: %s", e)
        
        return tweets, cursor
    
    def _normalize_graphql_tweet(self, tweet: Dict, url: str) -> SocialPost:
        """Normalize a GraphQL tweet response."""
        legacy = tweet.get("legacy", {})
        user_results = tweet.get("core", {}).get("user_results", {}).get("result", {})
        user_legacy = user_results.get("legacy", {})
        
        author = {
            "username": user_legacy.get("screen_name", ""),
            "display_name": user_legacy.get("name", ""),
            "avatar": user_legacy.get("profile_image_url_https", "").replace("_normal", "_400x400"),
            "verified": user_legacy.get("verified", False),
            "followers": user_legacy.get("followers_count"),
            "following": user_legacy.get("friends_count"),
            "posts_count": user_legacy.get("statuses_count"),
            "bio": user_legacy.get("description", ""),
            "location": user_legacy.get("location", ""),
            "profile_url": f"https://twitter.com/{user_legacy.get('screen_name', '')}",
        }
        
        engagement = {
            "likes": legacy.get("favorite_count"),
            "retweets": legacy.get("retweet_count"),
            "replies": legacy.get("reply_count"),
            "quotes": legacy.get("quote_count"),
            "views": int(tweet.get("views", {}).get("count", 0)) or None,
            "bookmarks": legacy.get("bookmark_count"),
        }
        
        # Media
        media = []
        for m in legacy.get("extended_entities", {}).get("media", []):
            media_type = m.get("type", "photo")
            media_item = {
                "type": "video" if media_type in ("video", "animated_gif") else "image",
                "url": m.get("media_url_https", ""),
                "thumbnail": m.get("media_url_https", ""),
                "width": m.get("original_info", {}).get("width"),
                "height": m.get("original_info", {}).get("height"),
            }
            if media_type == "video":
                variants = m.get("video_info", {}).get("variants", [])
                best = max(variants, key=lambda v: v.get("bitrate", 0)) if variants else {}
                media_item["url"] = best.get("url", "")
                media_item["duration"] = m.get("video_info", {}).get("duration_millis", 0) / 1000
            media.append(media_item)
        
        # Hashtags and mentions
        hashtags = [h.get("text", "") for h in legacy.get("entities", {}).get("hashtags", [])]
        mentions = [m.get("screen_name", "") for m in legacy.get("entities", {}).get("user_mentions", [])]
        
        text = legacy.get("full_text", "")
        
        return build_post(
            platform="twitter",
            post_type="post",
            url=url,
            id=str(tweet.get("rest_id", "")),
            text=text,
            timestamp=legacy.get("created_at"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_graphql_profile(self, user: Dict, username: str) -> SocialProfile:
        """Normalize a GraphQL profile response."""
        legacy = user.get("legacy", {})
        
        author = {
            "username": legacy.get("screen_name", username),
            "display_name": legacy.get("name", ""),
            "avatar": legacy.get("profile_image_url_https", "").replace("_normal", "_400x400"),
            "verified": legacy.get("verified", False),
            "followers": legacy.get("followers_count"),
            "following": legacy.get("friends_count"),
            "posts_count": legacy.get("statuses_count"),
            "bio": legacy.get("description", ""),
            "location": legacy.get("location", ""),
            "joined_date": legacy.get("created_at"),
            "profile_url": f"https://twitter.com/{legacy.get('screen_name', username)}",
            "banner": legacy.get("profile_banner_url", ""),
        }
        
        return build_profile(
            platform="twitter",
            username=username,
            url=f"https://twitter.com/{username}",
            profile_data={"author": author, "engagement": {}, "id": user.get("rest_id")},
        )
    
    # =========================================================================
    # FxTwitter Backend
    # =========================================================================
    
    async def _scrape_fxtwitter(self, tweet_id: str, url: str) -> SocialPost:
        api_url = f"https://api.fxtwitter.com/tweet/{tweet_id}"
        data = await self._fetch_json(api_url)
        tweet = data.get("tweet", {})
        if not tweet:
            raise ValueError("Tweet not found via FxTwitter")
        return self._normalize_fxtwitter(tweet, url)
    
    async def _scrape_profile_fxtwitter(self, username: str) -> SocialProfile:
        api_url = f"https://api.fxtwitter.com/user/{username}"
        data = await self._fetch_json(api_url)
        user = data.get("user", {})
        if not user:
            raise ValueError("Profile not found via FxTwitter")
        return self._normalize_profile_fxtwitter(user, username)
    
    def _normalize_fxtwitter(self, tweet: Dict, url: str) -> SocialPost:
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
    
    def _normalize_profile_fxtwitter(self, user: Dict, username: str) -> SocialProfile:
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
    
    # =========================================================================
    # Nitter Backend
    # =========================================================================
    
    async def _scrape_nitter(self, tweet_id: str, url: str) -> SocialPost:
        api_url = f"{self.nitter_url}/api/v1/tweet/{tweet_id}"
        data = await self._fetch_json(api_url)
        if not data.get("tweet"):
            raise ValueError("Tweet not found via Nitter")
        return self._normalize_nitter(data["tweet"], url)
    
    async def _scrape_profile_nitter(self, username: str) -> SocialProfile:
        api_url = f"{self.nitter_url}/api/v1/user/{username}"
        data = await self._fetch_json(api_url)
        if not data.get("user"):
            raise ValueError("Profile not found via Nitter")
        return self._normalize_profile_nitter(data["user"], username)
    
    async def _scrape_timeline_nitter(self, username: str, limit: int) -> List[SocialPost]:
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
        api_url = f"{self.nitter_url}/api/v1/search"
        params = {"q": query, "limit": limit}
        data = await self._fetch_json(api_url, params=params)
        tweets = data.get("tweets", [])
        results = []
        for tweet in tweets[:limit]:
            tweet_url = f"https://twitter.com/{tweet.get('user', {}).get('screen_name', '')}/status/{tweet.get('id')}"
            results.append(self._normalize_nitter(tweet, tweet_url))
        return results
    
    async def _scrape_replies_nitter(self, tweet_id: str, limit: int) -> List[SocialPost]:
        """Scrape replies via Nitter."""
        api_url = f"{self.nitter_url}/api/v1/tweet/{tweet_id}/replies"
        params = {"limit": limit}
        data = await self._fetch_json(api_url, params=params)
        tweets = data.get("replies", [])
        results = []
        for tweet in tweets[:limit]:
            username = tweet.get("user", {}).get("screen_name", "")
            tweet_url = f"https://twitter.com/{username}/status/{tweet.get('id')}"
            results.append(self._normalize_nitter(tweet, tweet_url))
        return results
    
    def _normalize_nitter(self, tweet: Dict, url: str) -> SocialPost:
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
    
    def _normalize_profile_nitter(self, user: Dict, username: str) -> SocialProfile:
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
    
    # =========================================================================
    # Playwright Backend
    # =========================================================================
    
    async def _scrape_playwright(self, url: str) -> SocialPost:
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
    
    def _normalize_playwright(self, data: Dict, url: str) -> SocialPost:
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
    
    def _normalize_profile_playwright(self, data: Dict, username: str) -> SocialProfile:
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
    
    # =========================================================================
    # URL parsing
    # =========================================================================
    
    def _extract_tweet_id(self, url: str) -> Optional[str]:
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
    
    def extract_identifier(self, url: str) -> Optional[str]:
        tweet_id = self._extract_tweet_id(url)
        if tweet_id:
            return tweet_id
        match = re.search(r"(?:twitter|x)\.com/([^/?]+)", url)
        if match:
            return match.group(1)
        return None
    
    @classmethod
    def extract_identifier_class(cls, url: str) -> Optional[str]:
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
        match = re.search(r"(?:twitter|x)\.com/([^/?]+)", url)
        if match:
            return match.group(1)
        return None


# Register the scraper
registry.register(TwitterScraper)
