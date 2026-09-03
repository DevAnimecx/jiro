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
    supported_actions = ["channel", "post", "message"]
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
        """Search Telegram (limited - no public search)."""
        raise NotImplementedError("Telegram public search not available")
    
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
    
    def _parse_channel_page(self, html: str, channel: str, limit: int) -> List[SocialPost]:
        """Parse channel preview page for messages."""
        # Telegram preview page has message widgets
        # Each message is in a div with class "tgme_widget_message"
        
        messages = []
        
        # Pattern for message widgets
        widget_pattern = r'<div[^>]*class=["\']tgme_widget_message[^"\']*["\'][^>]*data-post=["\']([^"\']+)["\'][^>]*>(.*?)</div>\s*</div>\s*</div>'
        matches = re.findall(widget_pattern, html, re.DOTALL)
        
        for post_data, widget_html in matches[:limit]:
            try:
                msg = self._parse_message_widget(widget_html, post_data, channel)
                if msg:
                    messages.append(msg)
            except Exception as e:
                log.debug("Failed to parse message widget", extra={"error": str(e)})
                continue
        
        return messages
    
    def _parse_message_widget(self, html: str, post_data: str, channel: str) -> Optional[SocialPost]:
        """Parse a single message widget."""
        # Extract message ID from post_data (format: channel/message_id)
        parts = post_data.split("/")
        if len(parts) != 2:
            return None
        
        message_id = parts[1]
        
        # Extract text
        text_pattern = r'<div[^>]*class=["\']tgme_widget_message_text[^"\']*["\'][^>]*>(.*?)</div>'
        text_match = re.search(text_pattern, html, re.DOTALL)
        text = text_match.group(1) if text_match else ""
        text = re.sub(r"<[^>]+>", "", text).strip()
        
        # Extract timestamp
        time_pattern = r'<time[^>]*datetime=["\']([^"\']+)["\']'
        time_match = re.search(time_pattern, html)
        timestamp = time_match.group(1) if time_match else None
        
        # Extract media
        media = []
        photo_pattern = r'<a[^>]*class=["\']tgme_widget_message_photo_wrap[^"\']*["\'][^>]*href=["\']([^"\']+)["\']'
        for match in re.finditer(photo_pattern, html):
            media.append({"type": "image", "url": match.group(1)})
        
        video_pattern = r'<video[^>]*src=["\']([^"\']+)["\']'
        for match in re.finditer(video_pattern, html):
            media.append({"type": "video", "url": match.group(1)})
        
        # Extract views/forwards
        views_pattern = r'<span[^>]*class=["\']tgme_widget_message_views[^"\']*["\'][^>]*>(.*?)</span>'
        views_match = re.search(views_pattern, html)
        views = views_match.group(1) if views_match else None
        
        forwards_pattern = r'<span[^>]*class=["\']tgme_widget_message_forwards[^"\']*["\'][^>]*>(.*?)</span>'
        forwards_match = re.search(forwards_pattern, html)
        forwards = forwards_match.group(1) if forwards_match else None
        
        # Channel info from page
        channel_name = self._extract_channel_name(html)
        
        return build_post(
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
        )
    
    def _parse_channel_info(self, html: str, username: str, url: str) -> SocialProfile:
        """Parse channel info from preview page."""
        # Channel title
        title_pattern = r'<div[^>]*class=["\']tgme_page_title[^"\']*["\'][^>]*>(.*?)</div>'
        title_match = re.search(title_pattern, html, re.DOTALL)
        title = title_match.group(1).strip() if title_match else username
        
        # Channel description
        desc_pattern = r'<div[^>]*class=["\']tgme_page_description[^"\']*["\'][^>]*>(.*?)</div>'
        desc_match = re.search(desc_pattern, html, re.DOTALL)
        description = desc_match.group(1).strip() if desc_match else ""
        description = re.sub(r"<[^>]+>", "", description)
        
        # Channel photo
        photo_pattern = r'<div[^>]*class=["\']tgme_page_photo[^"\']*["\'][^>]*style=["\'][^"\']*url\(([^)]+)\)'
        photo_match = re.search(photo_pattern, html)
        photo = photo_match.group(1).strip("'\"") if photo_match else ""
        
        # Subscriber count
        members_pattern = r'<div[^>]*class=["\']tgme_page_extra[^"\']*["\'][^>]*>(.*?)</div>'
        members_match = re.search(members_pattern, html, re.DOTALL)
        members = members_match.group(1).strip() if members_match else ""
        members = re.sub(r"<[^>]+>", "", members)
        
        # Verified badge
        verified = "tgme_page_verified" in html
        
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
        """Extract channel name from page."""
        title_pattern = r'<div[^>]*class=["\']tgme_page_title[^"\']*["\'][^>]*>(.*?)</div>'
        title_match = re.search(title_pattern, html, re.DOTALL)
        if title_match:
            return title_match.group(1).strip()
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