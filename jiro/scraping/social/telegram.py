"""Telegram scraper using web preview (t.me/s/ channel preview)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.telegram")


class TelegramScraper(BaseSocialScraper):
    """Telegram scraper using web preview (t.me/s/channel for public channels)."""
    
    platform = "telegram"
    url_patterns = [
        "t.me",
        "telegram.me",
        "telegram.org",
    ]
    supported_actions = ["channel", "post", "message", "search", "timeline"]
    rate_limit_rpm = 100
    requires_auth = False
    
    PREVIEW_URL = "https://t.me/s/"
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a Telegram message."""
        # URL formats:
        # https://t.me/channel/123 (message in channel)
        # https://t.me/c/123456789/123 (message in private channel - won't work)
        
        match = re.search(r"t\.me/([^/]+)/(\d+)", url)
        if not match:
            raise ValueError("Invalid Telegram message URL format")
        
        channel = match.group(1)
        message_id = match.group(2)
        
        return await self._get_message(channel, message_id, url)
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a Telegram channel/profile."""
        # Remove @ if present
        username = username.lstrip("@")
        
        # Handle different formats
        if username.startswith("c/"):
            # Private channel - can't scrape
            raise ValueError("Private channels cannot be scraped")
        
        return await self._get_channel(username)
    
    async def get_channel_messages(self, channel: str, limit: int = 50) -> List[SocialPost]:
        """Get messages from a public channel."""
        url = f"{self.PREVIEW_URL}{channel}"
        html = await self._fetch_html(url)
        
        return self._parse_channel_page(html, channel, limit)
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search Telegram public channels/messages."""
        # Use Google search to find Telegram content
        search_url = f"https://www.google.com/search?q=site:t.me {query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        
        try:
            text, resp = await self.client.get(search_url, engine=self.platform, extra_headers=headers)
            return self._parse_search_results(text, limit)
        except Exception:
            return []

    def _parse_search_results(self, html: str, limit: int) -> List[SocialPost]:
        """Parse Google search results for Telegram content."""
        results = []
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html)
        
        for item in tree.css("div.g")[:limit]:
            try:
                title_elem = item.css_first("h3")
                link_elem = item.css_first("a")
                snippet_elem = item.css_first("div.VwiC3b")
                
                if title_elem and link_elem:
                    title = title_elem.text(strip=True)
                    link = link_elem.attributes.get("href", "")
                    snippet = snippet_elem.text(strip=True) if snippet_elem else ""
                    
                    if "t.me" in link:
                        channel = re.search(r"t\.me/([^/?]+)", link)
                        channel_name = channel.group(1) if channel else ""
                        
                        results.append(build_post(
                            platform="telegram",
                            post_type="search_result",
                            url=link,
                            id=channel_name,
                            text=f"{title}\n{snippet}",
                            timestamp=None,
                            author={"username": channel_name},
                            engagement={},
                            media=[],
                        ))
            except Exception:
                continue
        
        return results
    
    async def _get_message(self, channel: str, message_id: str, url: str) -> SocialPost:
        """Get a single message from channel preview."""
        url = f"{self.PREVIEW_URL}{channel}/{message_id}"
        html = await self._fetch_html(url)
        
        messages = self._parse_channel_page(html, channel, 1)
        for msg in messages:
            if msg.data.get("id") == message_id:
                return msg
        
        raise ValueError("Message not found in channel preview")
    
    async def _get_channel(self, username: str) -> SocialProfile:
        """Get channel info from preview page."""
        url = f"{self.PREVIEW_URL}{username}"
        html = await self._fetch_html(url)
        
        return self._parse_channel_info(html, username, url)
    
    async def scrape_timeline(self, username: str, limit: int = 20) -> List[SocialPost]:
        """Get messages from a public channel (alias for get_channel_messages)."""
        return await self.get_channel_messages(username, limit)
    
    def _parse_channel_page(self, html: str, channel: str, limit: int) -> List[SocialPost]:
        """Parse channel preview page for messages using selectolax."""
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html)
        messages = []
        
        for msg_elem in tree.css("div.tgme_widget_message")[:limit]:
            try:
                # Extract post ID from data-post attribute
                post_data = msg_elem.attributes.get("data-post", "")
                parts = post_data.split("/")
                if len(parts) != 2:
                    continue
                message_id = parts[1]
                
                # Extract text
                text_elem = msg_elem.css_first("div.tgme_widget_message_text")
                text = text_elem.text(strip=True) if text_elem else ""
                
                # Extract timestamp
                time_elem = msg_elem.css_first("time")
                timestamp = time_elem.attributes.get("datetime") if time_elem else None
                
                # Extract media
                media = []
                for img_elem in msg_elem.css("a.tgme_widget_message_photo_wrap"):
                    href = img_elem.attributes.get("href", "")
                    if href:
                        media.append({"type": "image", "url": href})
                for video_elem in msg_elem.css("video"):
                    src = video_elem.attributes.get("src", "")
                    if src:
                        media.append({"type": "video", "url": src})
                
                # Extract views/forwards
                views_elem = msg_elem.css_first("span.tgme_widget_message_views")
                views = views_elem.text(strip=True) if views_elem else None
                
                forwards_elem = msg_elem.css_first("span.tgme_widget_message_forwards")
                forwards = forwards_elem.text(strip=True) if forwards_elem else None
                
                # Channel name
                channel_name = self._extract_channel_name(html)
                
                messages.append(build_post(
                    platform="telegram",
                    post_type="message",
                    url=f"https://t.me/{channel}/{message_id}",
                    id=message_id,
                    text=text,
                    timestamp=timestamp,
                    author={
                        "username": channel,
                        "display_name": channel_name or channel,
                    },
                    engagement={
                        "views": normalize_number(views),
                        "shares": normalize_number(forwards),
                    },
                    media=media,
                ))
            except Exception as e:
                log.debug("Failed to parse message widget: %s", e)
                continue
        
        return messages
    
    def _parse_channel_info(self, html: str, username: str, url: str) -> SocialProfile:
        """Parse channel info from preview page using selectolax."""
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html)
        
        # Channel title
        title_elem = tree.css_first("div.tgme_page_title")
        title = title_elem.text(strip=True) if title_elem else username
        
        # Channel description
        desc_elem = tree.css_first("div.tgme_page_description")
        description = desc_elem.text(strip=True) if desc_elem else ""
        
        # Channel photo
        photo = ""
        photo_elem = tree.css_first("div.tgme_page_photo img, div.tgme_page_photo")
        if photo_elem:
            photo = photo_elem.attributes.get("src", "") or photo_elem.attributes.get("style", "")
            if "url(" in photo:
                photo = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", photo)
                photo = photo.group(1) if photo else ""
        
        # Subscriber count
        members = ""
        extra_elem = tree.css_first("div.tgme_page_extra")
        members = extra_elem.text(strip=True) if extra_elem else ""
        
        # Verified badge
        verified = bool(tree.css_first("div.tgme_page_verified, .tgme_page_verified"))
        
        return build_profile(
            platform="telegram",
            username=username,
            url=url,
            profile_data={"author": {
                "username": username,
                "display_name": title,
                "avatar": photo,
                "verified": verified,
                "followers": normalize_number(members),
                "bio": description,
            }, "engagement": {}, "id": username},
        )
    
    def _extract_channel_name(self, html: str) -> str:
        """Extract channel name from page using selectolax."""
        try:
            from selectolax.parser import HTMLParser
            tree = HTMLParser(html)
            title_elem = tree.css_first("div.tgme_page_title")
            if title_elem:
                return title_elem.text(strip=True)
        except Exception:
            pass
        return ""
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract channel or message identifier."""
        # Message: t.me/channel/123
        match = re.search(r"t\.me/([^/]+)/(\d+)", url)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
        
        # Channel: t.me/channel
        match = re.search(r"t\.me/([^/?]+)", url)
        if match:
            return match.group(1)
        
        return None


# Register the scraper
registry.register(TelegramScraper)