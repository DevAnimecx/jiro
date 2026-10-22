"""HTTP client for direct scraping.

* ``curl_cffi`` with browser TLS impersonation (Chrome/Firefox/Safari fingerprints).
* ``httpx`` fallback when ``curl_cffi`` is unavailable.
* Rotating user agents, browser-ish headers, and geographic header spoofing.
* Per-engine cookie jar persistence for session continuity.
* Referer chain simulation for natural browsing patterns.
* Retries with exponential backoff + jitter.
* BYOK proxy support (single URL or rotating list).
* Blocked-response detection (CAPTCHA / anomaly / 403/429) → ``EngineBlockedError``
  so the fallback chain can kick in.
* Per-engine circuit breaker to fail fast on repeatedly-blocked engines.
"""

from __future__ import annotations

import asyncio
import random
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from selectolax.parser import HTMLParser

from jiro.browser import BrowserFetcher, playwright_available
from jiro.proxy import ProxyManager

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession

    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _CURL_CFFI_AVAILABLE = False

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

try:  # HTTP/2 support (optional dependency: pip install httpx[http2])
    import h2  # noqa: F401

    _HTTP2_AVAILABLE = True
except ImportError:
    _HTTP2_AVAILABLE = False

from jiro.config import Settings
from jiro.errors import (
    EngineBlockedError,
    EngineError,
    EngineTimeoutError,
    ScrapeError,
    SSRFError,
)
from jiro.security import async_validate_target_url
from jiro.log import get_logger as _get_logger

_ssrf_log = _get_logger("jiro.security")
_cookies_log = _get_logger("jiro.cookies")

def _sanitize_url_for_error(url: str) -> str:
    """Strip potentially sensitive query parameters from URL for error messages."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.query:
            return urllib.parse.urlunparse((
                parsed.scheme, parsed.netloc, parsed.path,
                parsed.params, "[redacted]", parsed.fragment,
            ))
        return url
    except Exception:
        return "[invalid url]"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"
    " Version/17.4 Safari/605.1.15",
]

# Substrings that indicate a bot-wall page, per engine.
BLOCK_MARKERS: Dict[str, Tuple[str, ...]] = {
    "google": ("enablejs", "unusual traffic", "sorry/index", "captcha", "recaptcha",
               "detected unusual traffic", "consent.google", "before you continue"),
    "bing": ("captcha", "si_captcha", "unusual amount of traffic",
             "<title>challenge", "id=\"challengeform\"", "verify you are human",
             "access denied", "rate limit exceeded"),
    "duckduckgo": ("anomaly", "captcha", "challenge", "bot detection"),
    "brave": ("captcha", "blocked", "unusual", "verify you are human"),
    "youtube": ("captcha", "blocked", "unusual", "sign in",
                "before you continue", "verify you are human"),
    "amazon": ("captcha", "robot", "automated", "unusual traffic",
               "enter the characters", "blocked"),
    "ebay": ("captcha", "blocked", "unusual", "verify you are human",
             "access denied", "robot"),
    "yandex": ("captcha", "blocked", "unusual", "robot",
               "check if you are not a robot"),
    "baidu": ("captcha", "blocked", "unusual", "verify you are human",
              "verify you are human"),
}

# Adaptive delay ranges per engine (min_seconds, max_seconds) — avoid detection.
_ADAPTIVE_DELAYS: Dict[str, Tuple[float, float]] = {
    "google": (1.5, 3.5),
    "bing": (0.5, 1.5),
    "duckduckgo": (0.3, 1.0),
    "brave": (0.5, 1.5),
    "youtube": (1.0, 2.5),
    "amazon": (2.0, 4.0),
    "ebay": (1.5, 3.0),
    "yandex": (1.0, 2.5),
    "baidu": (1.0, 2.5),
}

HEADER_TEMPLATE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
              "*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="125", "Not.A/Brand";v="24", "Google Chrome";v="125"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Cache-Control": "max-age=0",
}

# Per-engine extra headers to look more like a real browser.
ENGINE_HEADERS: Dict[str, Dict[str, str]] = {
    "bing": {
        "Referer": "https://www.bing.com/",
        "Sec-Fetch-Site": "same-origin",
    },
    "google": {
        "Referer": "https://www.google.com/",
        "Sec-Fetch-Site": "same-origin",
    },
    "duckduckgo": {
        "Referer": "https://duckduckgo.com/",
        "Sec-Fetch-Site": "same-origin",
    },
    "brave": {
        "Referer": "https://search.brave.com/",
        "Sec-Fetch-Site": "same-origin",
    },
}

# curl_cffi browser impersonation profiles — rotate these for TLS fingerprint diversity.
_BROWSER_PROFILES: List[str] = [
    "chrome131",
    "chrome124",
    "chrome120",
    "chrome116",
    "chrome110",
    "chrome107",
    "chrome104",
    "chrome101",
    "chrome100",
    "chrome99",
    "firefox147",
    "firefox144",
    "firefox135",
    "firefox133",
    "safari18_0",
    "safari17_0",
    "safari15_5",
    "safari15_3",
    "edge101",
    "edge99",
]

# Geographic header variations for different regions.
_GEO_HEADERS: List[Dict[str, str]] = [
    {"Accept-Language": "en-US,en;q=0.9", "Sec-Ch-Ua-Platform": '"Windows"'},
    {"Accept-Language": "en-GB,en;q=0.9", "Sec-Ch-Ua-Platform": '"Windows"'},
    {"Accept-Language": "en-US,en;q=0.9", "Sec-Ch-Ua-Platform": '"macOS"'},
    {"Accept-Language": "en-CA,en;q=0.9", "Sec-Ch-Ua-Platform": '"Windows"'},
    {"Accept-Language": "en-AU,en;q=0.9", "Sec-Ch-Ua-Platform": '"macOS"'},
    {"Accept-Language": "en-US,en;q=0.9,es;q=0.8", "Sec-Ch-Ua-Platform": '"Windows"'},
]

# Per-engine referer chains (simulate natural navigation to the search engine).
_REFERER_CHAINS: Dict[str, List[str]] = {
    "bing": [
        "https://www.bing.com/",
        "https://www.bing.com/search?q=",
    ],
    "google": [
        "https://www.google.com/",
        "https://www.google.com/search?q=",
    ],
    "duckduckgo": [
        "https://duckduckgo.com/",
        "https://duckduckgo.com/?q=",
    ],
    "brave": [
        "https://search.brave.com/",
        "https://search.brave.com/search?q=",
    ],
}


class CircuitBreaker:
    """Fail fast after N consecutive failures, then cool down."""

    def __init__(self, threshold: int = 3, cooldown: float = 60.0) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._failures: Dict[str, int] = {}
        self._open_until: Dict[str, float] = {}

    def is_open(self, engine: str) -> bool:
        if self._open_until.get(engine, 0) > time.time():
            return True
        return False

    def record_success(self, engine: str) -> None:
        self._failures.pop(engine, None)
        self._open_until.pop(engine, None)

    def record_failure(self, engine: str) -> None:
        self._failures[engine] = self._failures.get(engine, 0) + 1
        if self._failures[engine] >= self.threshold:
            self._open_until[engine] = time.time() + self.cooldown
            self._failures[engine] = 0


class EngineRateLimiter:
    """Per-engine rate limiting with token bucket + burst allowance."""

    def __init__(self, limits: Dict[str, Dict[str, int]]) -> None:
        self.limits = limits
        self._buckets: Dict[str, Dict[str, Any]] = {}

    def _get_bucket(self, engine: str) -> Dict[str, Any]:
        if engine not in self._buckets:
            limit = self.limits.get(engine, {"rpm": 60, "burst": 10})
            self._buckets[engine] = {
                "tokens": float(limit["burst"]),
                "max_tokens": float(limit["burst"]),
                "refill_rate": limit["rpm"] / 60.0,  # tokens per second
                "last_refill": time.time(),
            }
        return self._buckets[engine]

    def _refill(self, bucket: Dict[str, Any]) -> None:
        now = time.time()
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(bucket["max_tokens"], bucket["tokens"] + elapsed * bucket["refill_rate"])
        bucket["last_refill"] = now

    async def acquire(self, engine: str, tokens: int = 1) -> float:
        """Acquire tokens, returning wait time in seconds."""
        bucket = self._get_bucket(engine)
        self._refill(bucket)

        if bucket["tokens"] >= tokens:
            bucket["tokens"] -= tokens
            return 0.0

        # Calculate wait time
        needed = tokens - bucket["tokens"]
        wait_time = needed / bucket["refill_rate"]
        await asyncio.sleep(wait_time)

        # Refill after wait
        self._refill(bucket)
        bucket["tokens"] -= tokens
        return wait_time

    def try_acquire(self, engine: str, tokens: int = 1) -> bool:
        """Try to acquire tokens without blocking. Returns True if successful."""
        bucket = self._get_bucket(engine)
        self._refill(bucket)

        if bucket["tokens"] >= tokens:
            bucket["tokens"] -= tokens
            return True
        return False

    def get_available(self, engine: str) -> float:
        """Get available tokens for an engine."""
        bucket = self._get_bucket(engine)
        self._refill(bucket)
        return bucket["tokens"]

    def reset(self, engine: Optional[str] = None) -> None:
        """Reset rate limiter for an engine or all engines."""
        if engine:
            self._buckets.pop(engine, None)
        else:
            self._buckets.clear()


class BrowserFingerprint:
    """Rotates browser profiles, geographic headers, and viewport sizes."""

    def __init__(self) -> None:
        self._profile_idx = 0
        self._geo_idx = 0

    def next_profile(self) -> str:
        profile = _BROWSER_PROFILES[self._profile_idx % len(_BROWSER_PROFILES)]
        self._profile_idx += 1
        return profile

    def next_geo_headers(self) -> Dict[str, str]:
        geo = _GEO_HEADERS[self._geo_idx % len(_GEO_HEADERS)]
        self._geo_idx += 1
        return dict(geo)


class EngineCookieJar:
    """Per-engine cookie jar for session persistence (memory + optional SQLite)."""

    def __init__(self, db: Optional[Any] = None) -> None:
        self._cookies: Dict[str, Dict[str, str]] = {}
        self._db = db

    async def load(self) -> None:
        """Load persisted cookies from SQLite."""
        if self._db is not None:
            try:
                loaded = await self._db.cookie_load_all()
                self._cookies.update(loaded)
            except Exception as exc:  # signal but never crash the client
                _cookies_log.warning("cookie load failed",
                                     extra={"engine": "*", "error": str(exc)})

    async def save(self, engine: str) -> None:
        """Persist current engine cookies to SQLite."""
        if self._db is not None and engine in self._cookies:
            try:
                await self._db.cookie_save(engine, self._cookies[engine])
            except Exception as exc:  # signal but never crash the client
                _cookies_log.warning("cookie save failed",
                                     extra={"engine": engine, "error": str(exc)})

    def update(self, engine: str, set_cookie_headers: List[str]) -> None:
        """Parse Set-Cookie headers and store cookies per engine."""
        if engine not in self._cookies:
            self._cookies[engine] = {}
        for header in set_cookie_headers:
            parts = header.split(";", 1)[0].strip()
            if "=" in parts:
                k, v = parts.split("=", 1)
                self._cookies[engine][k.strip()] = v.strip()

    def get_dict(self, engine: str) -> Dict[str, str]:
        return dict(self._cookies.get(engine, {}))

    def clear(self, engine: Optional[str] = None) -> None:
        if engine:
            self._cookies.pop(engine, None)
        else:
            self._cookies.clear()


class ScrapingClient:
    def __init__(self, settings: Settings, db: Optional[Any] = None, *,
                 allow_private_targets: bool = False) -> None:
        self.settings = settings
        self.timeout = settings.timeout
        self.retries = settings.retries
        self.breaker = CircuitBreaker()
        self.rate_limiter = EngineRateLimiter(settings.rate_limits_per_engine)
        self.proxies = ProxyManager(settings)
        self.fingerprint = BrowserFingerprint()
        self.cookie_jar = EngineCookieJar(db)
        self.browser_fallback = BrowserFetcher() if (
            settings.get("scraping.browser_fallback", False)
            and playwright_available()
        ) else None
        self._curl_sessions: Dict[str, Any] = {}  # per-engine curl_cffi sessions
        self._client: Any = None  # httpx fallback
        # Pooled httpx clients, keyed by proxy URL, so proxied requests reuse
        # connections instead of handshaking per request.
        self._proxy_clients: Dict[str, Any] = {}
        self._proxy_clients_lock: asyncio.Lock = asyncio.Lock()
        self._db = db  # for proxy cost tracking
        self._proxy_costs: Dict[str, List[float]] = {}  # per-proxy latencies
        # SSRF guard: refuse to scrape the server's own host.
        host = settings.host
        own_hosts = []
        if host and host not in ("0.0.0.0", "::", "*"):
            own_hosts.append(host)
        own_hosts.append("localhost")
        self._own_hosts = own_hosts
        # Permit private/loopback scrape targets. Off by default and never
        # enabled from user input: it exists so an integration test can point
        # the client at a controlled local fixture server.
        self._allow_private_targets = bool(allow_private_targets)
        # robots.txt enforcement for user-supplied scrape targets.
        robots_cfg = settings.robots_txt
        self._respect_robots = bool(robots_cfg.get("enabled", True))
        self._robots_strict = bool(robots_cfg.get("strict_mode", False))
        self._robots_cache: Dict[str, tuple] = {}
        self._robots_cache_ttl = float(robots_cfg.get("cache_ttl_seconds", 3600))

    def _next_proxy(self) -> Optional[str]:
        return self.proxies.next()

    async def init(self) -> None:
        """Initialize the client (load persisted cookies)."""
        await self.cookie_jar.load()

    async def _get_curl_session(self, engine: str) -> Any:
        """Get or create a curl_cffi AsyncSession with browser impersonation.

        Uses the stealth engine for TLS/JA3 fingerprint rotation, cycling
        through Chrome/Firefox/Safari profiles per request to avoid detection.
        """
        if engine not in self._curl_sessions:
            from jiro.stealth import get_impersonation_profile
            profile = get_impersonation_profile()
            session: Any = CurlAsyncSession(
                impersonate=profile,  # type: ignore[arg-type]
                timeout=self.timeout,
            )
            self._curl_sessions[engine] = session
        return self._curl_sessions[engine]

    async def client(self) -> Any:
        """Get httpx client (fallback when curl_cffi unavailable)."""
        if self._client is None or (hasattr(self._client, 'is_closed') and self._client.is_closed):
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=limits,
                follow_redirects=True,
                headers={"User-Agent": random.choice(USER_AGENTS)},
                http2=_HTTP2_AVAILABLE,
                trust_env=False,
            )
        return self._client

    async def close(self) -> None:
        # Persist cookies before closing
        for engine in list(self.cookie_jar._cookies.keys()):
            await self.cookie_jar.save(engine)
        for session in self._curl_sessions.values():
            try:
                await session.close()
            except Exception:
                pass
        self._curl_sessions.clear()
        for proxy_client in list(self._proxy_clients.values()):
            try:
                if not getattr(proxy_client, "is_closed", False):
                    await proxy_client.aclose()
            except Exception:
                pass
        self._proxy_clients.clear()
        if self._client is not None:
            try:
                if hasattr(self._client, 'is_closed') and not self._client.is_closed:
                    await self._client.aclose()
            except Exception:
                pass
            self._client = None
        if self.browser_fallback is not None:
            await self.browser_fallback.close()

    async def _headers(self, engine: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        from jiro.stealth import build_stealth_headers

        # Use stealth engine for realistic browser headers with fingerprint rotation
        headers = build_stealth_headers(engine)

        # Rotate user agent if configured
        if self.settings.user_agent_rotation:
            if "User-Agent" not in headers:
                headers["User-Agent"] = random.choice(USER_AGENTS)

        # Add engine-specific headers
        headers.update(ENGINE_HEADERS.get(engine, {}))
        headers.update(extra or {})

        # Add cookies from jar
        cookies = self.cookie_jar.get_dict(engine)
        if cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            headers["Cookie"] = cookie_str

        # Simulate human-like delay before request (anti-detection)
        if self.settings.get("scraping.stealth.delay_between_requests", True):
            if random.random() < 0.1:  # 10% of requests get a small delay
                import asyncio as _asyncio
                await _asyncio.sleep(0.1 + random.random() * 0.2)

        return headers

    async def _validate_response_url(self, url: str, response: Any) -> None:
        """Validate the final URL after redirects to prevent SSRF via redirect.

        If the response URL differs from the request URL, validate the
        redirect target against blocked IP ranges.
        """
        if not hasattr(response, 'url') or response.url is None:
            return
        final_url = str(response.url)
        if final_url == url:
            return
        try:
            await async_validate_target_url(final_url, own_hosts=self._own_hosts)
        except (ValueError, Exception):
            safe_url = _sanitize_url_for_error(final_url)
            raise SSRFError(
                f"redirect target blocked: {safe_url}",
                details={"initial_url": _sanitize_url_for_error(url),
                         "redirect_url": safe_url},
            )

    async def get(self, url: str, *, engine: str = "social",
                  params: Optional[Dict[str, Any]] = None,
                  extra_headers: Optional[Dict[str, str]] = None,
                  headers: Optional[Dict[str, str]] = None,
                  raw: bool = False) -> Tuple[str, Any]:
        """GET with retries + backoff + proxy rotation + block detection.

        ``engine`` defaults to ``"social"`` for backward-compatibility with
        social scrapers and plugin engines that omit it.
        ``headers`` is accepted as an alias for ``extra_headers``.
        """
        if headers and not extra_headers:
            extra_headers = headers
        # SSRF + robots defenses apply only to user-controlled scrape targets.
        if engine == "scrape":
            try:
                await async_validate_target_url(
                    url, allow_private=self._allow_private_targets,
                    own_hosts=self._own_hosts)
            except ValueError as exc:
                raise SSRFError(str(exc), details={"url": url})
            if self._respect_robots:
                await self._enforce_robots(url)

        if self.breaker.is_open(engine):
            raise EngineBlockedError(
                f"engine '{engine}' circuit open (recent failures); cooling down",
