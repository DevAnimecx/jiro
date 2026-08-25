"""robots.txt compliance for scraping.

Provides async robots.txt fetching, caching, and checking per engine.
Respects crawl-delay and disallow rules to stay compliant with each engine's ToS.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.robots")


@dataclass
class RobotsRule:
    """A single robots.txt rule."""
    user_agent: str
    disallow: List[str] = field(default_factory=list)
    allow: List[str] = field(default_factory=list)
    crawl_delay: Optional[float] = None
    request_rate: Optional[str] = None  # e.g., "1/10" = 1 request per 10 seconds


@dataclass
class RobotsTxt:
    """Parsed robots.txt for a host."""
    host: str
    rules: List[RobotsRule] = field(default_factory=list)
    sitemaps: List[str] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)
    raw: str = ""

    def can_fetch(self, user_agent: str, path: str) -> bool:
        """Check if a user agent can fetch a path."""
        # Find matching rules (most specific user-agent first)
        matching = [r for r in self.rules
                    if r.user_agent == "*" or r.user_agent.lower() in user_agent.lower()]
        if not matching:
            return True  # No rules = allowed

        # Sort by specificity: exact match > wildcard
        matching.sort(key=lambda r: 0 if r.user_agent == "*" else 1)

        path = path or "/"
        for rule in matching:
            for pattern in rule.allow:
                if self._match_path(pattern, path):
                    return True
            for pattern in rule.disallow:
                if self._match_path(pattern, path):
                    return False
        return True

    def get_crawl_delay(self, user_agent: str) -> Optional[float]:
        """Get crawl-delay for a user agent."""
        matching = [r for r in self.rules
                    if r.user_agent == "*" or r.user_agent.lower() in user_agent.lower()
                    and r.crawl_delay is not None]
        if not matching:
            return None
        matching.sort(key=lambda r: 0 if r.user_agent == "*" else 1)
        return matching[0].crawl_delay

    def get_request_rate(self, user_agent: str) -> Optional[float]:
        """Get request rate (requests per second) for a user agent."""
        matching = [r for r in self.rules
                    if r.user_agent == "*" or r.user_agent.lower() in user_agent.lower()
                    and r.request_rate is not None]
        if not matching:
            return None
        matching.sort(key=lambda r: 0 if r.user_agent == "*" else 1)
        try:
            num, denom = matching[0].request_rate.split("/")
            return int(num) / int(denom)
        except (ValueError, ZeroDivisionError):
            return None

    @staticmethod
    def _match_path(pattern: str, path: str) -> bool:
        """Match a robots.txt path pattern (supports * and $)."""
        if not pattern:
            return False
        # Convert robots.txt pattern to regex
        regex = pattern.replace(".", r"\.").replace("*", ".*")
        if regex.endswith("$"):
            regex = regex[:-1] + "$"
        else:
            regex = regex + ".*"
        return bool(re.match(regex, path))


class RobotsManager:
    """Manages robots.txt fetching and caching per engine."""

    # Engine -> base URL for robots.txt
    ENGINE_ROBOTS_URLS: Dict[str, str] = {
        "google": "https://www.google.com/robots.txt",
        "bing": "https://www.bing.com/robots.txt",
        "duckduckgo": "https://duckduckgo.com/robots.txt",
        "brave": "https://search.brave.com/robots.txt",
        "youtube": "https://www.youtube.com/robots.txt",
        "amazon": "https://www.amazon.com/robots.txt",
        "ebay": "https://www.ebay.com/robots.txt",
        "yandex": "https://yandex.com/robots.txt",
        "baidu": "https://www.baidu.com/robots.txt",
    }

    def __init__(self, settings: Settings, cache: Any = None) -> None:
        self.settings = settings
        self.cache = cache
        self._cache: Dict[str, RobotsTxt] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._default_ua = "JiroBot/1.0 (+https://github.com/DevAnimecx/jiro)"

    def _get_lock(self, host: str) -> asyncio.Lock:
        if host not in self._locks:
            self._locks[host] = asyncio.Lock()
        return self._locks[host]

    async def get_robots(self, engine: str, *, force_refresh: bool = False) -> Optional[RobotsTxt]:
        """Get parsed robots.txt for an engine."""
        url = self.ENGINE_ROBOTS_URLS.get(engine)
        if not url:
            return None

        host = urlparse(url).netloc

        # Check memory cache
        if not force_refresh and host in self._cache:
            robots = self._cache[host]
            if time.time() - robots.fetched_at < 3600:  # 1 hour TTL
                return robots

        # Check persistent cache
        if not force_refresh and self.cache is not None:
            try:
                cached = await self.cache.get(f"robots:{host}")
                if cached:
                    robots = RobotsTxt(**cached)
                    if time.time() - robots.fetched_at < 3600:
                        self._cache[host] = robots
                        return robots
            except Exception:
                pass

        # Fetch with lock to avoid duplicate requests
        async with self._get_lock(host):
            # Double-check after acquiring lock
            if not force_refresh and host in self._cache:
                robots = self._cache[host]
                if time.time() - robots.fetched_at < 3600:
                    return robots

            robots = await self._fetch_robots(url, host)
            if robots:
                self._cache[host] = robots
                if self.cache is not None:
                    try:
                        await self.cache.put(f"robots:{host}", {
                            "host": robots.host,
                            "rules": [{"user_agent": r.user_agent,
                                       "disallow": r.disallow,
                                       "allow": r.allow,
                                       "crawl_delay": r.crawl_delay,
                                       "request_rate": r.request_rate}
                                      for r in robots.rules],
                            "sitemaps": robots.sitemaps,
                            "fetched_at": robots.fetched_at,
                            "raw": robots.raw,
                        })
                    except Exception:
                        pass
            return robots

    async def _fetch_robots(self, url: str, host: str) -> Optional[RobotsTxt]:
        """Fetch and parse robots.txt from a host."""
        try:
            timeout = httpx.Timeout(10.0, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": self._default_ua})
                if resp.status_code == 404:
                    log.info("robots.txt not found", extra={"host": host})
                    return RobotsTxt(host=host)
                resp.raise_for_status()

            return self._parse_robots(host, resp.text)
        except Exception as exc:
            log.warning("failed to fetch robots.txt", extra={"host": host, "error": str(exc)})
            return None

    def _parse_robots(self, host: str, content: str) -> RobotsTxt:
        """Parse robots.txt content."""
        robots = RobotsTxt(host=host, raw=content)
        current_rule: Optional[RobotsRule] = None

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()

            if key == "user-agent":
                if current_rule:
                    robots.rules.append(current_rule)
                current_rule = RobotsRule(user_agent=value)
            elif key == "disallow" and current_rule:
                if value:
                    current_rule.disallow.append(value)
            elif key == "allow" and current_rule:
                if value:
                    current_rule.allow.append(value)
            elif key == "crawl-delay" and current_rule:
                try:
                    current_rule.crawl_delay = float(value)
                except ValueError:
                    pass
            elif key == "request-rate" and current_rule:
                current_rule.request_rate = value
            elif key == "sitemap":
                robots.sitemaps.append(value)

        if current_rule:
            robots.rules.append(current_rule)

        return robots

    def can_fetch(self, engine: str, url: str, user_agent: Optional[str] = None) -> bool:
        """Synchronous check (uses cached robots.txt only)."""
        host = urlparse(url).netloc
        if host not in self._cache:
            return True  # Unknown = allow (fail open for availability)
        ua = user_agent or self._default_ua
        path = urlparse(url).path
        return self._cache[host].can_fetch(ua, path)

    async def check_fetch(self, engine: str, url: str, user_agent: Optional[str] = None) -> bool:
        """Async check (fetches robots.txt if not cached)."""
        robots = await self.get_robots(engine)
        if robots is None:
            return True
        ua = user_agent or self._default_ua
        path = urlparse(url).path
        return robots.can_fetch(ua, path)

    def get_crawl_delay(self, engine: str, user_agent: Optional[str] = None) -> Optional[float]:
        """Get crawl-delay for an engine."""
        host = urlparse(self.ENGINE_ROBOTS_URLS.get(engine, "")).netloc
        if host not in self._cache:
            return None
        ua = user_agent or self._default_ua
        return self._cache[host].get_crawl_delay(ua)

    async def wait_for_crawl_delay(self, engine: str, user_agent: Optional[str] = None) -> None:
        """Wait if required by crawl-delay."""
        delay = self.get_crawl_delay(engine, user_agent)
        if delay:
            await asyncio.sleep(delay)


# Engine-specific path patterns to check against robots.txt
ENGINE_SEARCH_PATHS: Dict[str, str] = {
    "google": "/search",
    "bing": "/search",
    "duckduckgo": "/",
    "brave": "/search",
    "youtube": "/results",
    "amazon": "/s",
    "ebay": "/sch",
    "yandex": "/search",
    "baidu": "/s",
}


async def check_engine_compliance(engine: str, robots: RobotsManager) -> Dict[str, Any]:
    """Check if an engine's search path is allowed by robots.txt."""
    path = ENGINE_SEARCH_PATHS.get(engine, "/")
    base_url = robots.ENGINE_ROBOTS_URLS.get(engine, "")
    if not base_url:
        return {"engine": engine, "compliant": True, "reason": "no robots.txt URL configured"}

    url = urljoin(base_url, path)
    allowed = await robots.check_fetch(engine, url)
    delay = robots.get_crawl_delay(engine)

    return {
        "engine": engine,
        "compliant": allowed,
        "path_checked": path,
        "crawl_delay_seconds": delay,
        "user_agent": robots._default_ua,
    }