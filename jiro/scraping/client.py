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
from typing import Any, Dict, List, Optional, Tuple

from selectolax.parser import HTMLParser

from jiro.browser import BrowserFetcher, playwright_available
from jiro.proxy import ProxyManager

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    from curl_cffi.requests import BrowserType

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
from jiro.errors import EngineBlockedError, EngineError, EngineTimeoutError

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
            except Exception:
                pass

    async def save(self, engine: str) -> None:
        """Persist current engine cookies to SQLite."""
        if self._db is not None and engine in self._cookies:
            try:
                await self._db.cookie_save(engine, self._cookies[engine])
            except Exception:
                pass

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
    def __init__(self, settings: Settings, db: Optional[Any] = None) -> None:
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
        self._client = None  # httpx fallback
        self._db = db  # for proxy cost tracking
        self._proxy_costs: Dict[str, List[float]] = {}  # per-proxy latencies

    def _next_proxy(self) -> Optional[str]:
        return self.proxies.next()

    async def init(self) -> None:
        """Initialize the client (load persisted cookies)."""
        await self.cookie_jar.load()

    async def _get_curl_session(self, engine: str) -> Any:
        """Get or create a curl_cffi AsyncSession with browser impersonation."""
        if engine not in self._curl_sessions:
            profile = self.fingerprint.next_profile()
            session = CurlAsyncSession(
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
        if self._client is not None:
            try:
                if hasattr(self._client, 'is_closed') and not self._client.is_closed:
                    await self._client.aclose()
            except Exception:
                pass
            self._client = None
        if self.browser_fallback is not None:
            await self.browser_fallback.close()

    def _headers(self, engine: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = dict(HEADER_TEMPLATE)
        if self.settings.user_agent_rotation:
            headers["User-Agent"] = random.choice(USER_AGENTS)
        # Rotate geographic headers for diversity
        geo = self.fingerprint.next_geo_headers()
        headers.update(geo)
        headers.update(ENGINE_HEADERS.get(engine, {}))
        headers.update(extra or {})
        # Add cookies from jar
        cookies = self.cookie_jar.get_dict(engine)
        if cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            headers["Cookie"] = cookie_str
        return headers

    async def get(self, url: str, *, engine: str, params: Optional[Dict[str, Any]] = None,
                  extra_headers: Optional[Dict[str, str]] = None,
                  raw: bool = False) -> Tuple[str, Any]:
        """GET with retries + backoff + proxy rotation + block detection."""
        if self.breaker.is_open(engine):
            raise EngineBlockedError(
                f"engine '{engine}' circuit open (recent failures); cooling down",
                details={"engine": engine},
            )

        # Per-engine rate limiting
        await self.rate_limiter.acquire(engine)

        proxy = self._next_proxy()
        last_error: Optional[Exception] = None

        for attempt in range(self.retries + 1):
            try:
                response = await self._do_request(
                    url, params=params, engine=engine,
                    extra_headers=extra_headers, proxy=proxy,
                )
            except (EngineBlockedError, EngineError, EngineTimeoutError):
                raise
            except httpx.TimeoutException as exc:
                last_error = EngineTimeoutError(
                    f"timeout fetching {url}", details={"engine": engine, "attempt": attempt}
                )
            except Exception as exc:
                last_error = EngineError(
                    f"request failed: {exc}", details={"engine": engine, "attempt": attempt}
                )
            else:
                if proxy:
                    self.proxies.record_success(proxy)
                    # Track proxy cost/latency
                    try:
                        latency = getattr(response, '_latency_ms', 0)
                        self.proxies.record_cost(proxy, 0.0, latency)
                    except Exception:
                        pass
                try:
                    self._check_blocked(engine, url, response)
                except EngineBlockedError:
                    if proxy:
                        self.proxies.record_failure(proxy)
                    self.breaker.record_failure(engine)
                    raise
                self.breaker.record_success(engine)
                return (response.text, response)

            if proxy:
                self.proxies.record_failure(proxy)
            if attempt < self.retries:
                # Adaptive delay based on engine to avoid detection
                min_d, max_d = _ADAPTIVE_DELAYS.get(engine, (0.5, 2.0))
                delay = (2 ** attempt) * random.uniform(min_d, max_d)
                await asyncio.sleep(delay)

        assert last_error is not None
        self.breaker.record_failure(engine)
        raise last_error

    async def _do_request(self, url: str, *, params: Optional[Dict[str, Any]],
                          engine: str, extra_headers: Optional[Dict[str, str]],
                          proxy: Optional[str]) -> Any:
        """Route request through curl_cffi (preferred) or httpx (fallback)."""
        if _CURL_CFFI_AVAILABLE:
            return await self._request_curl(url, params=params, engine=engine,
                                            extra_headers=extra_headers, proxy=proxy)
        return await self._request_httpx(url, params=params, engine=engine,
                                         extra_headers=extra_headers, proxy=proxy)

    async def _request_curl(self, url: str, *, params: Optional[Dict[str, Any]],
                            engine: str, extra_headers: Optional[Dict[str, str]],
                            proxy: Optional[str]) -> Any:
        """Request via curl_cffi with TLS fingerprint impersonation."""
        session = await self._get_curl_session(engine)
        headers = self._headers(engine, extra_headers)
        # Simulate referer chain
        chain = _REFERER_CHAINS.get(engine, [])
        if chain:
            headers["Referer"] = chain[-1]

        import time as _time
        start = _time.monotonic()
        resp = await session.get(url, params=params, headers=headers, proxy=proxy)
        latency_ms = (_time.monotonic() - start) * 1000

        # Update cookie jar from response
        set_cookies = resp.headers.get_list("set-cookie") if hasattr(resp.headers, 'get_list') else []
        if set_cookies:
            self.cookie_jar.update(engine, set_cookies)

        # Track proxy cost
        if proxy and self._db is not None:
            try:
                await self._db.proxy_cost_add(
                    proxy_url=proxy, latency_ms=latency_ms,
                    status=resp.status_code, cost=0.0, engine=engine,
                    success=resp.status_code < 400,
                )
            except Exception:
                pass

        # Build a response-like object for compatibility
        return _CurlResponseAdapter(resp)

    async def _request_httpx(self, url: str, *, params: Optional[Dict[str, Any]],
                             engine: str, extra_headers: Optional[Dict[str, str]],
                             proxy: Optional[str]) -> Any:
        """Request via httpx (fallback)."""
        client = await self.client()
        if proxy:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                follow_redirects=True,
                headers=self._headers(engine, extra_headers),
                http2=_HTTP2_AVAILABLE,
                trust_env=False,
                proxy=proxy,
            ) as proxy_client:
                return await proxy_client.get(url, params=params)
        return await client.get(url, params=params, headers=self._headers(engine, extra_headers))

    async def get_rendered(self, url: str, *, engine: str,
                           params: Optional[Dict[str, Any]] = None) -> str:
        """Browser-render a page (Playwright fallback for JS-heavy/blocked pages)."""
        from urllib.parse import urlencode

        target = url + ("?" + urlencode(params) if params else "")
        if self.browser_fallback is None:
            raise EngineBlockedError(
                "browser fallback disabled (set scraping.browser_fallback: true and "
                "install 'jiro-search[browser]')",
                details={"engine": engine},
            )
        try:
            html = await self.browser_fallback.fetch(target)
        except Exception as exc:
            raise EngineBlockedError(
                f"browser fallback failed for {engine}: {exc}",
                details={"engine": engine},
            ) from exc
        return html

    @staticmethod
    def _check_blocked(engine: str, url: str, response: Any) -> None:
        if response.status_code in (403, 429):
            raise EngineBlockedError(
                f"engine '{engine}' returned HTTP {response.status_code}",
                details={"engine": engine, "status": response.status_code, "url": url},
            )
        if response.status_code >= 400:
            raise EngineError(
                f"engine '{engine}' returned HTTP {response.status_code}",
                details={"engine": engine, "status": response.status_code},
            )
        markers = BLOCK_MARKERS.get(engine, ())
        if markers:
            body = response.text[:200_000].lower()
            if any(m in body for m in markers):
                raise EngineBlockedError(
                    f"engine '{engine}' served a bot-wall (captcha/anomaly) page",
                    details={"engine": engine, "status": response.status_code},
                )


class _CurlResponseAdapter:
    """Adapter to make curl_cffi responses compatible with httpx-like interface."""

    def __init__(self, resp: Any) -> None:
        self._resp = resp

    @property
    def status_code(self) -> int:
        return self._resp.status_code

    @property
    def text(self) -> str:
        if isinstance(self._resp.text, bytes):
            return self._resp.text.decode("utf-8", errors="replace")
        return self._resp.text

    @property
    def headers(self) -> Any:
        return self._resp.headers

    @property
    def content(self) -> bytes:
        return self._resp.content


def parse_html(html: str) -> HTMLParser:
    return HTMLParser(html)


def normalize_url(raw: str) -> str:
    """Clean engine redirect wrappers (Bing /ck/a) and URL entities."""
    import base64
    import re
    raw = raw.strip()
    m = re.match(r"https?://www\.bing\.com/ck/a\?(.*)", raw)
    if m:
        query = dict(pair.split("=", 1) for pair in m.group(1).split("&") if "=" in pair)
        encoded = query.get("u", "")
        if encoded.startswith("a1"):
            data = encoded[2:].rstrip("=")
            pad = (-len(data)) % 4
            try:
                decoded = base64.b64decode(data + "=" * pad, validate=True).decode("utf-8", "ignore")
                if decoded:
                    return decoded
            except Exception:
                try:
                    decoded = base64.b64decode(data).decode("utf-8", "ignore")
                    if decoded:
                        return decoded
                except Exception:
                    pass
        return raw
    return raw
