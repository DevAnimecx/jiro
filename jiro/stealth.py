"""Stealth engine for v0.2.13 -- undetectable web scraping.

Implements:
1. TLS/JA3/JA4 fingerprint rotation with curl-cffi browser impersonation
2. Behavioral simulation (mouse movement, scroll patterns, referer chains)
3. Browser fingerprint randomization (canvas, WebGL, screen dimensions, plugins)

The goal is a 98%+ success rate across Cloudflare, DataDome, Akamai, Imperva,
and Kasada anti-bot systems.
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Tuple

from jiro.log import get_logger

log = get_logger("jiro.stealth")

# curl-cffi impersonation profiles (browser fingerprints for TLS handshake)
# Each profile corresponds to a real browser + version combination
# curl-cffi will use these to impersonate Chrome/Firefox/Safari TLS signatures
BROWSER_PROFILES: List[str] = [
    "chrome119",
    "chrome120",
    "chrome123",
    "chrome124",
    "chrome131",
    "chrome133",
    "chrome136",
    "chrome142",
    "chrome145",
    "chrome146",
    "firefox133",
    "firefox135",
    "firefox144",
    "firefox147",
    "safari170",
    "safari180",
    "safari184",
    "edge101",
    "edge99",
    "tor145",
]

# Weight distribution -- Chrome is most common in the wild
PROFILE_WEIGHTS: List[float] = [
    25, 5, 2, 2, 5, 5, 5, 5, 5, 5,  # Chrome
    5, 5, 5, 5,    # Firefox
    5, 5, 5,       # Safari
    3, 3, 3,       # Edge
    1,             # Tor
]


class StealthClient:
    """Manages stealthy HTTP client configuration for anti-bot bypassing.

    Features:
    - Rotates curl-cffi impersonation profiles per request
    - Simulates realistic browser headers with proper Sec-Ch-Ua values
    - Browser fingerprint randomization (canvas, WebGL, timezone, locale)
    - Behavioral simulation (referer chains, viewport sizes)
    """

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings
        self._profile_index = 0
        self._request_count = 0
        self._geo_profiles: List[Dict[str, str]] = self._init_geo_profiles()

    def _init_geo_profiles(self) -> List[Dict[str, str]]:
        """Initialize geographic header profiles for realistic browser fingerprints."""
        profiles = []
        locales = [
            ("en-US", "en", "US"),
            ("en-GB", "en", "GB"),
            ("en-CA", "en", "CA"),
            ("en-AU", "en", "AU"),
            ("de-DE", "de", "DE"),
            ("fr-FR", "fr", "FR"),
            ("es-ES", "es", "ES"),
            ("ja-JP", "ja", "JP"),
            ("zh-CN", "zh", "CN"),
            ("pt-BR", "pt", "BR"),
        ]
        timezones = [
            "America/New_York",
            "America/Los_Angeles",
            "America/Chicago",
            "America/Denver",
            "Europe/London",
            "Europe/Paris",
            "Europe/Berlin",
            "Asia/Tokyo",
            "Asia/Shanghai",
            "Australia/Sydney",
        ]
        for locale, lang, country in locales:
            for tz in random.sample(timezones, min(3, len(timezones))):
                profiles.append({
                    "Accept-Language": f"{lang}-{country},{lang};q=0.9",
                    "Sec-Ch-Ua-Platform": f'"{self._locale_to_platform(locale)}"',
                    "Sec-Ch-Ua-Mobile": str(random.choice(["?0", "?1"])),
                    "Sec-Ch-Ua-Bitness": random.choice(["64", "32"]),
                    "Sec-Ch-Ua-Wow64": "yes" if random.random() > 0.3 else "no",
                })
        return profiles

    def _locale_to_platform(self, locale: str) -> str:
        if locale.startswith(("en", "fr", "de", "es")):
            return "Windows"
        if locale.startswith("ja"):
            return "macOS"
        if locale.startswith("zh"):
            return "Windows"
        return "Windows"

    def next_profile(self) -> str:
        """Get the next curl-cffi impersonation profile (rotates by request)."""
        self._profile_index = (self._profile_index + 1) % len(BROWSER_PROFILES)
        return BROWSER_PROFILES[self._profile_index]

    def next_geo_headers(self) -> Dict[str, str]:
        """Get the next geographic/browser header profile."""
        self._request_count += 1
        return dict(self._geo_profiles[
            self._request_count % len(self._geo_profiles)
        ])

    def random_delay(self, min_ms: int = 100, max_ms: int = 500) -> float:
        """Generate a realistic human-like delay in seconds."""
        return random.uniform(min_ms / 1000.0, max_ms / 1000.0)

    def get_referer_chain(self, target_url: str, engine: str = "google") -> List[str]:
        """Generate a realistic referer chain for the target URL.

        This simulates a natural browsing path: search engine → intermediate
        site → target. This helps defeat behavioral analysis by anti-bot
        systems that check if the user came directly to the target or via
        a natural browsing path.
        """
        chain = []
        if engine == "google":
            chain.append(f"https://www.google.com/search?q={self._random_query()}")
            chain.append(f"https://www.google.com/url?sa=t&url={target_url}")
        elif engine == "bing":
            chain.append(f"https://www.bing.com/search?q={self._random_query()}")
        else:
            chain.append("https://en.wikipedia.org/wiki/Main_Page")

        return chain

    def _random_query(self) -> str:
        """Generate a pseudo-realistic query string for referer simulation."""
        words = ["python", "javascript", "data", "api", "web", "scraping",
                 "search", "automation", "tutorial", "guide", "2024"]
        return "+".join(random.sample(words, k=3))

    def browser_fingerprint(self) -> Dict[str, Any]:
        """Generate a randomized browser fingerprint for browser-based scraping.

        Used by Playwright/Puppeteer instances to avoid detection.
        """
        width = random.choice([1366, 1920, 1440, 1536, 1280, 1600])
        height = random.choice([768, 1080, 900, 864, 720, 1024])

        # Canvas fingerprint noise
        canvas_noise = random.uniform(0.1, 0.9)

        # WebGL parameters
        webgl_vendor = random.choice([
            "Intel Inc.",
            "NVIDIA Corporation",
            "AMD",
            "Apple Computer, Inc.",
            "Google Inc.",
        ])
        webgl_renderer = random.choice([
            "Intel Iris OpenGL Engine",
            "NVIDIA GeForce RTX 4080",
            "AMD Radeon RX 7900",
            "Apple GPU",
            "ANGLE (Intel, OpenGL 4.5, Direct3D11 vs_5_0, D3D11)",
        ])

        # Screen properties
        color_depth = random.choice([24, 30, 32])
        pixel_depth = color_depth

        return {
            "viewport": {"width": width, "height": height},
            "screen": {
                "width": width,
                "height": height,
                "availWidth": width,
                "availHeight": height - 80,  # account for taskbar
                "colorDepth": color_depth,
                "pixelDepth": pixel_depth,
            },
            "canvas": {
                "noise": canvas_noise,
                "dataURL": f"data:image/png;base64,{self._canvas_hash()}",
            },
            "webgl": {
                "unmaskedVendor": webgl_vendor,
                "unmaskedRenderer": webgl_renderer,
            },
            "timezone": random.choice([
                "America/New_York", "America/Los_Angeles", "America/Chicago",
                "America/Denver", "Europe/London", "Europe/Paris",
                "Europe/Berlin", "Asia/Tokyo", "Asia/Shanghai",
            ]),
            "locale": random.choice([
                "en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "ja-JP",
                "zh-CN", "pt-BR",
            ]),
        }

    def _canvas_hash(self) -> str:
        """Generate a pseudo-random canvas fingerprint hash."""
        chars = "abcdefghijklmnopqrstuvwxyz0123456789"
        return "".join(random.choices(chars, k=64))


class BehavioralSimulator:
    """Simulates realistic human-like browsing behavior.

    Implements timing patterns, scroll behavior, and interaction sequences
    that mimic real user behavior to defeat behavioral analysis.
    """

    def __init__(self) -> None:
        self._session_start = time.time()

    def human_delay(self, min_ms: int = 200, max_ms: int = 1500) -> float:
        """Realistic human delay between actions (typing, clicking, scrolling)."""
        return random.uniform(min_ms / 1000.0, max_ms / 1000.0)

    def scroll_pattern(self, page_height: float) -> List[Tuple[float, float]]:
        """Generate a realistic scroll pattern (position, delay_seconds).

        Humans don't scroll linearly - they scroll with variable speed,
        pause to read sections, then continue.
        """
        if page_height <= 0:
            page_height = 5000

        scrolls = []
        position = 0.0
        target = page_height

        while position < target:
            # Variable scroll amount (simulating mouse wheel or scrollbar drag)
            scroll_amount = random.uniform(
                page_height * 0.15,
                page_height * 0.35,
            )
            position += scroll_amount

            if position >= target:
                position = target

            # Pause to "read" (longer pauses at key positions)
            if random.random() < 0.3:
                delay = random.uniform(1.0, 4.0)
            else:
                delay = random.uniform(0.1, 0.8)

            scrolls.append((position / page_height, delay))

        return scrolls

    def mouse_trajectory(self, start: Tuple[int, int], end: Tuple[int, int],
                         steps: int = 10) -> List[Tuple[int, int]]:
        """Generate a realistic mouse trajectory between two points.

        Humans don't move mice in straight lines - there are micro-adjustments
        and non-linear paths.
        """
        trajectory = []
        for i in range(steps):
            t = i / steps
            # Add Bezier curve variation
            curve = random.uniform(-0.05, 0.05) * t * (1 - t)
            x = int(start[0] + (end[0] - start[0]) * t + curve * 200)
            y = int(start[1] + (end[1] - start[1]) * t + curve * 100)
            trajectory.append((x, y))
        return trajectory

    def session_timing(self) -> float:
        """Get elapsed time in the current simulated session."""
        return time.time() - self._session_start


# Singleton instances
_stealth = StealthClient()
_behavior = BehavioralSimulator()


def get_stealth() -> StealthClient:
    """Get the singleton StealthClient instance."""
    return _stealth


def get_behavioral_simulator() -> BehavioralSimulator:
    """Get the singleton BehavioralSimulator instance."""
    return _behavior


def build_stealth_headers(engine: str = "google",
                          referer_chain: Optional[List[str]] = None) -> Dict[str, str]:
    """Build a complete set of stealth headers for a request.

    Combines TLS impersonation profile, geo headers, browser fingerprint,
    and referer chain into a single header dict.
    """
    headers: Dict[str, str] = {}

    # Browser fingerprint headers
    geo = _stealth.next_geo_headers()
    headers.update(geo)

    # Browser fingerprint canvas
    fp = _stealth.browser_fingerprint()

    # Core browser headers
    profile = _stealth.next_profile()
    if profile.startswith("chrome") or profile.startswith("edge"):
        chrome_ver = profile.replace("chrome", "").replace("edge", "")
        headers["Sec-Ch-Ua"] = (
            f'"Chromium";v="{chrome_ver}", '
            f'"Not(A:Brand";v="24", '
            f'"Google Chrome";v="{chrome_ver}"'
        )
        headers["Sec-Ch-Ua-Mobile"] = geo.get("Sec-Ch-Ua-Mobile", "?0")
        headers["Sec-Ch-Ua-Platform"] = geo.get("Sec-Ch-Ua-Platform", '"Windows"')
    elif profile.startswith("firefox"):
        fox_ver = profile.replace("firefox", "")
        headers["User-Agent"] = (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{fox_ver}.0) "
            f"Gecko/20100101 Firefox/{fox_ver}.0"
        )
    elif profile.startswith("safari"):
        headers["User-Agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        )

    # Referer chain
    if referer_chain:
        chain_str = " <- ".join(referer_chain)
        headers["Referer"] = referer_chain[-1] if referer_chain else ""
        # Simulate referrer policy
        headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Standard browser headers
    headers["Accept"] = (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    )
    headers["Accept-Encoding"] = "gzip, deflate, br, zstd"
    headers["Accept-Language"] = geo.get("Accept-Language", "en-US,en;q=0.9")
    headers["Connection"] = "keep-alive"
    headers["Upgrade-Insecure-Requests"] = "1"
    headers["Sec-Fetch-Dest"] = "document"
    headers["Sec-Fetch-Mode"] = "navigate"
    headers["Sec-Fetch-Site"] = "same-origin" if referer_chain else "none"
    headers["Sec-Fetch-User"] = "?1"
    headers["Cache-Control"] = "max-age=0"
    headers["Sec-CH-UA-Bitness"] = geo.get("Sec-Ch-Ua-Bitness", "64")
    headers["Sec-CH-UA-Wow64"] = geo.get("Sec-CH-UA-Wow64", "yes")

    return headers


def get_impersonation_profile() -> str:
    """Get a curl-cffi impersonation profile string."""
    return _stealth.next_profile()


def simulate_human_delay() -> float:
    """Get a realistic human-like delay in seconds."""
    return _behavior.human_delay()
