"""Playwright browser fallback (lazy-loaded, graceful degradation).

Used when direct HTTP scraping fails or the engine serves a JS-required /
consent page. Chromium is downloaded once with ``playwright install chromium``;
if Playwright or the browser is unavailable, Jiro keeps working with direct
HTTP only (PRD §6.7: graceful degradation).

Stealth hardening: rotating viewport, locale, timezone, screen resolution,
and ``navigator.webdriver`` spoofing via init scripts.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, List, Optional, Tuple

from jiro.log import get_logger

log = get_logger("jiro.browser")

_BROWSER_AVAILABLE: Optional[bool] = None
_lock = asyncio.Lock()

# Viewport size variations (width, height) — common desktop resolutions.
VIEWPORTS: List[Tuple[int, int]] = [
    (1920, 1080),
    (1366, 768),
    (1536, 864),
    (1440, 900),
    (1280, 720),
    (1600, 900),
    (1280, 1024),
    (1680, 1050),
    (2560, 1440),
    (1920, 1200),
]

# Timezone IDs — major regions.
TIMEZONES: List[str] = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Australia/Sydney",
    "America/Sao_Paulo",
    "Asia/Kolkata",
]

# Locale variations.
LOCALES: List[str] = [
    "en-US",
    "en-GB",
    "en-CA",
    "en-AU",
    "en-IN",
    "de-DE",
    "fr-FR",
    "es-ES",
    "pt-BR",
    "ja-JP",
    "zh-CN",
    "it-IT",
]

# Navigator plugins data — realistic Chrome plugin list.
_CHROME_PLUGINS: List[Dict[str, Any]] = [
    {"name": "Chrome PDF Plugin", "filename": "internal-pdf-viewer",
     "description": "Portable Document Format"},
    {"name": "Chrome PDF Viewer", "filename": "mhjfbmdgcfjbbpaeojofohoefgiehjai",
     "description": ""},
    {"name": "Native Client", "filename": "internal-nacl-plugin",
     "description": ""},
]

# Navigator languages variations.
_NAVIGATOR_LANGUAGES: List[List[str]] = [
    ["en-US", "en"],
    ["en-GB", "en"],
    ["en-US", "en", "es"],
    ["en-CA", "en", "fr"],
    ["de-DE", "de", "en"],
    ["fr-FR", "fr", "en"],
    ["ja-JP", "ja", "en"],
]


def playwright_available() -> bool:
    """Cheap, cached availability check — no browser launch."""
    global _BROWSER_AVAILABLE
    if _BROWSER_AVAILABLE is not None:
        return _BROWSER_AVAILABLE
    try:
        import playwright  # noqa: F401
        _BROWSER_AVAILABLE = True
    except ImportError:
        _BROWSER_AVAILABLE = False
    return _BROWSER_AVAILABLE


def _build_stealth_script(viewport: Tuple[int, int], timezone: str,
                          locale: str) -> str:
    """Build JavaScript stealth patches for browser context."""
    width, height = viewport
    plugins = random.choice([_CHROME_PLUGINS, []])
    languages = random.choice(_NAVIGATOR_LANGUAGES)
    return f"""
    // Overwrite navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {{
        get: () => undefined,
    }});
    // Overwrite navigator.plugins
    Object.defineProperty(navigator, 'plugins', {{
        get: () => {plugins!r},
    }});
    // Overwrite navigator.languages
    Object.defineProperty(navigator, 'languages', {{
        get: () => {languages!r},
    }});
    // Overwrite navigator.language
    Object.defineProperty(navigator, 'language', {{
        get: () => '{languages[0] if languages else "en-US"}',
    }});
    // Overwrite screen dimensions
    Object.defineProperty(screen, 'width', {{
        get: () => {width},
    }});
    Object.defineProperty(screen, 'height', {{
        get: () => {height},
    }});
    Object.defineProperty(screen, 'availWidth', {{
        get: () => {width},
    }});
    Object.defineProperty(screen, 'availHeight', {{
        get: () => {height - 40},
    }});
    Object.defineProperty(screen, 'colorDepth', {{
        get: () => 24,
    }});
    Object.defineProperty(screen, 'pixelDepth', {{
        get: () => 24,
    }});
    // Overwrite devicePixelRatio
    Object.defineProperty(window, 'devicePixelRatio', {{
        get: () => {random.choice([1, 1.25, 1.5, 2])},
    }});
    // Overwrite permissions API
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (params) => (
        params.name === 'notifications' ?
        Promise.resolve({{ state: Notification.permission }}) :
        originalQuery(params)
    );
    // Overwrite chrome runtime
    window.chrome = {{
        runtime: {{}},
        loadTimes: function() {{}},
        csi: function() {{}},
        app: {{}},
    }};
    // Overwrite WebGL vendor/renderer
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {{
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, parameter);
    }};
    // Overwrite connection info
    Object.defineProperty(navigator, 'connection', {{
        get: () => ({{
            effectiveType: '4g',
            rtt: {random.randint(50, 150)},
            downlink: {random.uniform(5, 10):.1f},
            saveData: false,
        }}),
    }});
    // Hide automation-related properties
    delete navigator.__proto__.webdriver;
    // Mock window.outerWidth/outerHeight to match viewport
    Object.defineProperty(window, 'outerWidth', {{
        get: () => {width},
    }});
    Object.defineProperty(window, 'outerHeight', {{
        get: () => {height + 85},
    }});
    """


class BrowserFetcher:
    """Render a URL in headless Chromium and return its final HTML.

    Applies stealth patches per-context with randomized viewport,
    timezone, locale, screen resolution, and navigator properties.
    """

    def __init__(self, *, headless: bool = True, timeout: float = 30.0,
                 user_agent: str = "") -> None:
        self.headless = headless
        self.timeout = timeout
        self.user_agent = user_agent
        self._browser: Any = None
        self._viewport_idx = 0

    def _next_fingerprint(self) -> Dict[str, Any]:
        """Return a fresh browser fingerprint dict."""
        vp = VIEWPORTS[self._viewport_idx % len(VIEWPORTS)]
        self._viewport_idx += 1
        return {
            "viewport": {"width": vp[0], "height": vp[1]},
            "timezone": random.choice(TIMEZONES),
            "locale": random.choice(LOCALES),
        }

    async def fetch(self, url: str, *, wait_for: str = "networkidle") -> str:
        if not playwright_available():
            raise RuntimeError(
                "Playwright is not installed. Run `pip install 'jiro-search[browser]'` "
                "and `playwright install chromium` to enable browser fallback."
            )
        from playwright.async_api import async_playwright

        async with _lock:
            if self._browser is None:
                pw = await async_playwright().start()
                self._browser = await pw.chromium.launch(headless=self.headless)
                log.info("chromium launched for browser fallback")

        fp = self._next_fingerprint()
        stealth_js = _build_stealth_script(
            (fp["viewport"]["width"], fp["viewport"]["height"]),
            fp["timezone"],
            fp["locale"],
        )
        log.debug("browser fingerprint: viewport=%s tz=%s locale=%s",
                  fp["viewport"], fp["timezone"], fp["locale"])

        context = await self._browser.new_context(
            viewport=fp["viewport"],
            locale=fp["locale"],
            timezone_id=fp["timezone"],
            user_agent=self.user_agent or None,
        )
        await context.add_init_script(stealth_js)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until=wait_for, timeout=self.timeout * 1000)
            await page.wait_for_timeout(800)  # let late JS settle
            return await page.content()
        finally:
            await context.close()

    async def close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # pragma: no cover
                pass
            self._browser = None


async def get_browser(headless: bool = True, timeout: float = 30.0) -> BrowserFetcher:
    """Return a ready-to-use BrowserFetcher for Playwright-based scraping.

    This is the entry point used by social scrapers (TikTok, Twitter, etc.)
    for browser-based fallback when HTTP scraping is blocked.
    """
    fetcher = BrowserFetcher(headless=headless, timeout=timeout)
    return fetcher


class BrowserPage:
    """Context manager that provides a stealth Playwright page.

    Usage:
        async with get_browser_page() as page:
            await page.goto(url)
            content = await page.content()
    """

    def __init__(self, headless: bool = True, timeout: float = 30.0):
        self.headless = headless
        self.timeout = timeout
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    async def __aenter__(self):
        if not playwright_available():
            raise RuntimeError(
                "Playwright is not installed. Run `pip install 'jiro-search[browser]'` "
                "and `playwright install chromium` to enable browser fallback."
            )
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)

        vp = random.choice(VIEWPORTS)
        fp = {
            "viewport": {"width": vp[0], "height": vp[1]},
            "timezone": random.choice(TIMEZONES),
            "locale": random.choice(LOCALES),
        }
        stealth_js = _build_stealth_script(
            (fp["viewport"]["width"], fp["viewport"]["height"]),
            fp["timezone"], fp["locale"],
        )
        self._context = await self._browser.new_context(
            viewport=fp["viewport"],
            locale=fp["locale"],
            timezone_id=fp["timezone"],
        )
        await self._context.add_init_script(stealth_js)
        self._page = await self._context.new_page()
        return self._page

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        return False


def get_browser_page(headless: bool = True, timeout: float = 30.0) -> BrowserPage:
    """Return a BrowserPage context manager for Playwright-based scraping.

    Used by social scrapers (TikTok, Twitter) for browser fallback.

    Usage::

        async with get_browser_page() as page:
            await page.goto(url)
            content = await page.content()
    """
    return BrowserPage(headless=headless, timeout=timeout)
