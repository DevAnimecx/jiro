"""LinkedIn scraper using JSON-LD extraction (no auth, limited public data)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from jiro.scraping.social.base import BaseSocialScraper, SocialPost, SocialProfile, registry
from jiro.scraping.social.normalizer import build_post, build_profile, normalize_timestamp, normalize_number
from jiro.log import get_logger

log = get_logger("jiro.scraping.social.linkedin")


class LinkedInScraper(BaseSocialScraper):
    """LinkedIn scraper using JSON-LD structured data extraction."""
    
    platform = "linkedin"
    url_patterns = [
        "linkedin.com",
    ]
    supported_actions = ["profile", "post", "company", "job", "search", "timeline"]
    rate_limit_rpm = 20
    requires_auth = False
    
    async def scrape_post(self, url: str) -> SocialPost:
        """Scrape a LinkedIn post (limited public access)."""
        # LinkedIn posts require authentication for full access
        # We'll extract what we can from JSON-LD
        html = await self._fetch_html(url)
        return self._extract_from_html(html, url)
    
    async def scrape_profile(self, username: str) -> SocialProfile:
        """Scrape a LinkedIn profile."""
        # Handle different URL formats
        if username.startswith("http"):
            profile_url = username
        elif username.startswith("/"):
            profile_url = f"https://linkedin.com{username}"
        else:
            profile_url = f"https://linkedin.com/in/{username}"
        
        html = await self._fetch_html(profile_url)
        return self._extract_profile_from_html(html, profile_url)
    
    async def scrape_company(self, company_id: str) -> SocialProfile:
        """Scrape a LinkedIn company page."""
        url = f"https://linkedin.com/company/{company_id}"
        html = await self._fetch_html(url)
        return self._extract_company_from_html(html, url)
    
    async def search(self, query: str, limit: int = 25) -> List[SocialPost]:
        """Search LinkedIn (public web search fallback)."""
        # LinkedIn requires login for full search
        # Use public Google search to find LinkedIn content
        search_url = f"https://www.google.com/search?q=site:linkedin.com {query}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        
        try:
            text, resp = await self.client.get(search_url, engine=self.platform, extra_headers=headers)
            results = self._parse_search_results(text, limit)
            if results:
                return results
        except Exception:
            log.debug("silenced fallback", exc_info=True)
        
        # Fallback: search LinkedIn's own search page (may redirect to login)
        try:
            search_url = f"https://www.linkedin.com/search/results/people/?keywords={query}"
            html = await self._fetch_html(search_url)
            results = self._parse_linkedin_search(html, limit)
            if results:
                return results
        except Exception:
            log.debug("silenced fallback", exc_info=True)
        
        return []

    def _parse_search_results(self, html: str, limit: int) -> List[SocialPost]:
        """Parse Google search results for LinkedIn content."""
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
                    
                    if "linkedin.com" in link and title:
                        results.append(build_post(
                            platform="linkedin",
                            post_type="search_result",
                            url=link,
                            id=link,
                            text=f"{title}\n{snippet}",
                            timestamp=None,
                            author={},
                            engagement={},
                            media=[],
                        ))
            except Exception:
                log.debug("silenced fallback", exc_info=True)
                continue
        
        return results

    def _parse_linkedin_search(self, html: str, limit: int) -> List[SocialPost]:
        """Parse LinkedIn search results page."""
        results = []
        
        # Try JSON-LD extraction
        json_ld_data = self._extract_json_ld(html)
        for data in json_ld_data:
            if isinstance(data, dict):
                if data.get("@type") in ["Person", "Organization"]:
                    results.append(build_post(
                        platform="linkedin",
                        post_type="search_result",
                        url=data.get("url", ""),
                        id=data.get("url", ""),
                        text=data.get("name", ""),
                        timestamp=None,
                        author={
                            "display_name": data.get("name", ""),
                            "username": data.get("identifier", ""),
                        },
                        engagement={},
                        media=[],
                    ))
        
        return results[:limit]
    
    def _extract_from_html(self, html: str, url: str) -> SocialPost:
        """Extract post data from HTML using JSON-LD."""
        # Find JSON-LD scripts
        json_ld_data = self._extract_json_ld(html)
        
        # Look for SocialMediaPosting or Article
        post_data = None
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if any(t in ["SocialMediaPosting", "Article", "BlogPosting"] for t in types):
                    post_data = data
                    break
        
        if not post_data:
            # Try to extract from meta tags
            post_data = self._extract_meta_tags(html)
        
        if not post_data:
            raise ValueError("Could not extract post data from LinkedIn page")
        
        return self._normalize_post(post_data, url)
    
    def _extract_profile_from_html(self, html: str, url: str) -> SocialProfile:
        """Extract profile data from HTML."""
        json_ld_data = self._extract_json_ld(html)
        
        # Look for Person or ProfilePage
        profile_data = None
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if "Person" in types or "ProfilePage" in types:
                    profile_data = data
                    break
        
        if not profile_data:
            profile_data = self._extract_meta_tags(html)
        
        if not profile_data:
            raise ValueError("Could not extract profile data from LinkedIn page")
        
        return self._normalize_profile(profile_data, url)
    
    def _extract_company_from_html(self, html: str, url: str) -> SocialProfile:
        """Extract company page data from HTML."""
        json_ld_data = self._extract_json_ld(html)
        
        # Look for Organization
        company_data = None
        for data in json_ld_data:
            if isinstance(data, dict):
                types = data.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if "Organization" in types or "Corporation" in types:
                    company_data = data
                    break
        
        if not company_data:
            company_data = self._extract_meta_tags(html)
        
        if not company_data:
            raise ValueError("Could not extract company data from LinkedIn page")
        
        return self._normalize_company(company_data, url)
    
    def _extract_json_ld(self, html: str) -> List[Dict[str, Any]]:
        """Extract JSON-LD structured data from HTML."""
        results = []
        
        # Find all script tags with type="application/ld+json"
        pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
        
        for match in matches:
            try:
                data = json.loads(match.strip())
                if isinstance(data, list):
                    results.extend(data)
                else:
                    results.append(data)
            except json.JSONDecodeError:
                continue
        
        return results
    
    def _extract_meta_tags(self, html: str) -> Dict[str, Any]:
        """Extract Open Graph and other meta tags."""
        data = {}
        
        # Open Graph tags
        og_pattern = r'<meta[^>]*property=["\']og:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(og_pattern, html, re.IGNORECASE):
            data[f"og:{match.group(1)}"] = match.group(2)
        
        # Twitter Card tags
        twitter_pattern = r'<meta[^>]*name=["\']twitter:([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(twitter_pattern, html, re.IGNORECASE):
            data[f"twitter:{match.group(1)}"] = match.group(2)
        
        # Standard meta tags
        meta_pattern = r'<meta[^>]*name=["\']([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']'
        for match in re.finditer(meta_pattern, html, re.IGNORECASE):
            data[match.group(1)] = match.group(2)
        
        return data
    
    def _normalize_post(self, data: Dict[str, Any], url: str) -> SocialPost:
        """Normalize LinkedIn post."""
        author = {
            "username": data.get("author", {}).get("name", "") if isinstance(data.get("author"), dict) else data.get("author", ""),
            "display_name": data.get("author", {}).get("name", "") if isinstance(data.get("author"), dict) else data.get("author", ""),
            "avatar": data.get("author", {}).get("image", "") if isinstance(data.get("author"), dict) else "",
        }
        
        engagement = {
            "likes": normalize_number(data.get("interactionStatistic", {}).get("userInteractionCount")) if isinstance(data.get("interactionStatistic"), dict) else None,
            "comments": None,
            "shares": None,
        }
        
        media = []
        if data.get("image"):
            images = data["image"] if isinstance(data["image"], list) else [data["image"]]
            for img in images:
                img_url = img if isinstance(img, str) else img.get("url", "")
                if img_url:
                    media.append({"type": "image", "url": img_url})
        
        return build_post(
            platform="linkedin",
            post_type="post",
            url=url,
            id=data.get("@id", "") or url.split("/")[-1],
            text=data.get("text", "") or data.get("description", "") or data.get("og:description", ""),
            timestamp=data.get("datePublished") or data.get("dateCreated"),
            author=author,
            engagement=engagement,
            media=media,
        )
    
    def _normalize_profile(self, data: Dict[str, Any], url: str) -> SocialProfile:
        """Normalize LinkedIn profile."""
        # Extract username from URL
        username = url.split("/in/")[-1].rstrip("/")
        
        author = {
            "username": username,
            "display_name": data.get("name", "") or data.get("og:title", ""),
            "avatar": data.get("image", "") or data.get("og:image", ""),
            "verified": False,
            "followers": None,
            "bio": data.get("description", "") or data.get("og:description", ""),
            "location": data.get("location", {}).get("name", "") if isinstance(data.get("location"), dict) else data.get("location", ""),
        }
        
        # Extract headline/position
        headline = data.get("jobTitle", "") or data.get("description", "")
        
        return build_profile(
            platform="linkedin",
            username=username,
            url=url,
            profile_data={"author": author, "engagement": {"headline": headline}, "id": data.get("@id", "")},
        )
    
    def _normalize_company(self, data: Dict[str, Any], url: str) -> SocialProfile:
        """Normalize LinkedIn company page."""
        company_name = data.get("name", "") or data.get("og:title", "").replace(" | LinkedIn", "")
        
        author = {
            "username": company_name.lower().replace(" ", "-"),
            "display_name": company_name,
            "avatar": data.get("logo", "") or data.get("image", "") or data.get("og:image", ""),
            "verified": True,
            "followers": None,
            "bio": data.get("description", "") or data.get("og:description", ""),
            "location": data.get("location", {}).get("address", {}).get("addressLocality", "") if isinstance(data.get("location"), dict) else "",
        }
        
        return build_profile(
            platform="linkedin",
            username=company_name.lower().replace(" ", "-"),
            url=url,
            profile_data={"author": author, "engagement": {}, "id": data.get("@id", ""), "type": "company"},
        )
    
    async def scrape_timeline(self, username: str, limit: int = 20) -> List[SocialPost]:
        """Get recent activity from a LinkedIn profile."""
        profile_url = f"https://linkedin.com/in/{username}"
        html = await self._fetch_html(profile_url, engine=self.platform)
        return self._parse_timeline(html, profile_url, limit)

    def _parse_timeline(self, html: str, base_url: str, limit: int) -> List[SocialPost]:
        """Parse timeline posts from LinkedIn profile HTML."""
        results = []
        
        # Look for activity/feed posts in the HTML
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html)
        
        for item in tree.css("div.feed-shared-update-v2, div.occludable-update, article")[:limit]:
            try:
                # Extract post content
                text_elem = item.css_first(".feed-shared-text, .update-text")
                text = text_elem.text(strip=True) if text_elem else ""
                
                # Extract link
                link_elem = item.css_first("a[href*='/activity/'], a[href*='/posts/']")
                link = link_elem.attributes.get("href", "") if link_elem else base_url
                if link and not link.startswith("http"):
                    link = f"https://linkedin.com{link}"
                
                # Extract timestamp
                time_elem = item.css_first("time, .feed-shared-relative-date")
                timestamp = time_elem.attributes.get("datetime", "") if time_elem else None
                
                if text:
                    results.append(build_post(
                        platform="linkedin",
                        post_type="post",
                        url=link,
                        id=link.split("/")[-1],
                        text=text[:500],
                        timestamp=timestamp,
                        author={},
                        engagement={},
                        media=[],
                    ))
            except Exception:
                log.debug("silenced fallback", exc_info=True)
                continue
        
        # Fallback: use JSON-LD
        if not results:
            json_ld_data = self._extract_json_ld(html)
            for data in json_ld_data:
                if isinstance(data, dict) and data.get("@type") in ["SocialMediaPosting", "Article"]:
                    results.append(self._normalize_post(data, data.get("url", base_url)))
        
        return results[:limit]
    
    def extract_identifier(self, url: str) -> Optional[str]:
        """Extract username or post ID from LinkedIn URL."""
        # Profile: linkedin.com/in/username
        match = re.search(r"linkedin\.com/in/([^/?]+)", url)
        if match:
            return match.group(1)
        
        # Company: linkedin.com/company/name
        match = re.search(r"linkedin\.com/company/([^/?]+)", url)
        if match:
            return match.group(1)
        
        # Post: linkedin.com/feed/update/urn:li:activity:id
        match = re.search(r"feed/update/([^/?]+)", url)
        if match:
            return match.group(1)
        
        return None


# Register the scraper
registry.register(LinkedInScraper)