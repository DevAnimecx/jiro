"""BYOK proxy manager.

Supports:
* Custom HTTP/SOCKS5 proxies — a single URL or a comma-separated rotating list.
* Provider presets (BrightData, Oxylabs, ScraperAPI, ZenRows) — construct the
  proxy endpoint from ``provider`` + ``api_key`` automatically.

The manager rotates endpoints per request and tracks per-endpoint failures so
the scraper can skip unhealthy proxies (circuit-style).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from jiro.config import Settings

PROVIDER_TEMPLATES: Dict[str, str] = {
    # {api_key} is substituted; {user}/{pass} forms use auth placeholders.
    "brightdata": "http://brd-customer-{api_key}:{api_key}@brd.superproxy.io:33335",
    "oxylabs": "http://customer-{api_key}:{api_key}@pr.oxylabs.io:7777",
    "scraperapi": "http://scraperapi:{api_key}@proxy-server.scraperapi.com:8001",
    "zenrows": "http://{api_key}@proxy.zenrows.com:8001",
    "smartproxy": "http://user-{api_key}:{api_key}@gate.smartproxy.com:7000",
}

PROVIDER_NAMES = sorted(PROVIDER_TEMPLATES)


class ProxyManager:
    """Resolves and rotates proxies from config."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._failures: Dict[str, int] = {}
        self._cooldown_until: Dict[str, float] = {}
        self._index = 0
        cfg = self.settings.proxy
        self._strategy = cfg.get("rotation_strategy", "round_robin")
        # Cost tracking
        self._costs: Dict[str, List[float]] = {}
        self._latencies: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------ config
    @property
    def enabled(self) -> bool:
        cfg = self.settings.proxy
        return bool(cfg.get("enabled", False))

    def configured_urls(self) -> List[str]:
        """Raw proxy URLs from config (provider preset or explicit list)."""
        cfg = self.settings.proxy
        provider = (cfg.get("provider") or "").strip().lower()
        api_key = (cfg.get("api_key") or "").strip()
        url = (cfg.get("url") or "").strip()
        urls: List[str] = []
        if provider in PROVIDER_TEMPLATES and api_key:
            urls.append(PROVIDER_TEMPLATES[provider].format(api_key=api_key))
        if url:
            urls.extend(u.strip() for u in url.split(",") if u.strip())
        return urls

    def healthy_urls(self) -> List[str]:
        now = time.time()
        return [
            u for u in self.configured_urls()
            if self._cooldown_until.get(u, 0) < now
        ]

    def next(self) -> Optional[str]:
        """Next healthy proxy URL using configured strategy, or None if none configured."""
        if not self.enabled:
            return None
        urls = self.healthy_urls()
        if not urls:
            return None
        if self._strategy == "random":
            return urls[hash(str(time.time())) % len(urls)]
        if self._strategy == "least_failures":
            return min(urls, key=lambda u: self._failures.get(u, 0))
        # round_robin (default)
        url = urls[self._index % len(urls)]
        self._index += 1
        return url

    def record_failure(self, url: str) -> None:
        self._failures[url] = self._failures.get(url, 0) + 1
        if self._failures[url] >= 3:
            self._cooldown_until[url] = time.time() + 120

    def record_success(self, url: str) -> None:
        self._failures.pop(url, None)
        self._cooldown_until.pop(url, None)

    def record_cost(self, url: str, cost: float, latency_ms: float) -> None:
        """Track proxy cost and latency."""
        if url not in self._costs:
            self._costs[url] = []
            self._latencies[url] = []
        self._costs[url].append(cost)
        self._latencies[url].append(latency_ms)
        # Keep only last 100 entries per proxy
        if len(self._costs[url]) > 100:
            self._costs[url] = self._costs[url][-100:]
            self._latencies[url] = self._latencies[url][-100:]

    def cost_info(self, url: Optional[str] = None) -> Dict[str, Any]:
        """Return cost statistics for a proxy or all proxies."""
        if url:
            costs = self._costs.get(url, [])
            latencies = self._latencies.get(url, [])
            return {
                "url": self._redact(url),
                "total_cost": round(sum(costs), 6),
                "total_requests": len(costs),
                "avg_cost": round(sum(costs) / len(costs), 6) if costs else 0,
                "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
                "failures": self._failures.get(url, 0),
            }
        # Aggregate all proxies
        all_costs = sum(self._costs.values(), [])
        all_latencies = sum(self._latencies.values(), [])
        return {
            "total_cost": round(sum(all_costs), 6),
            "total_requests": len(all_costs),
            "avg_cost": round(sum(all_costs) / len(all_costs), 6) if all_costs else 0,
            "avg_latency_ms": round(sum(all_latencies) / len(all_latencies), 1) if all_latencies else 0,
            "total_failures": sum(self._failures.values()),
        }

    def info(self) -> Dict[str, Any]:
        urls = self.configured_urls()
        return {
            "enabled": self.enabled,
            "providers": PROVIDER_NAMES,
            "endpoints": [self._redact(u) for u in urls],
            "healthy": len(self.healthy_urls()),
            "rotation_strategy": self._strategy,
            "health_check": self.settings.proxy.get("health_check", False),
            "cost": self.cost_info(),
        }

    @staticmethod
    def _redact(url: str) -> str:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url)
        if parts.username or parts.password:
            return urlunsplit((parts.scheme, f"{parts.hostname}:{parts.port or ''}",
                               parts.path, parts.query, parts.fragment))
        return url
