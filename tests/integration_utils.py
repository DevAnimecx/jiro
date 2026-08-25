"""Shared utilities for integration / chaos / contract tests."""

from __future__ import annotations

from typing import Any, Dict

# Test configuration
TEST_CONFIG: Dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 18000},
    "scraping": {
        "default_engine": "duckduckgo",
        "engines": ["duckduckgo", "bing"],
        "fallback_order": ["duckduckgo", "bing"],
        "timeout": 5,
        "retries": 1,
        "robots_txt": {"enabled": False},  # Disable for tests
    },
    "cache": {"type": "memory", "ttl_seconds": 60},
    "db": {"path": ":memory:"},
    "auth": {"enabled": True, "jwt_secret": "test-secret", "rate_limit_rpm": 1000},
    "llm": {"provider": "openai", "api_key": "", "model": "gpt-4o-mini"},
    "logging": {"level": "debug"},
    "privacy": {"log_queries": True},
    "audit": {"enabled": False},
}


# Mock responses for engine testing
class MockEngineResponses:
    """Pre-recorded HTML responses for engine testing."""

    GOOGLE_WEB = """
    <html><body>
    <div id="search">
        <div class="g">
            <h3><a href="https://example.com/1">Result 1</a></h3>
            <div class="VwiC3b">Snippet for result 1</div>
            <cite>example.com</cite>
        </div>
        <div class="g">
            <h3><a href="https://example.com/2">Result 2</a></h3>
            <div class="VwiC3b">Snippet for result 2</div>
            <cite>example.com</cite>
        </div>
    </div>
    <div id="result-stats">About 1,000,000 results</div>
    </body></html>
    """

    BING_WEB = """
    <html><body>
    <ol id="b_results">
        <li class="b_algo">
            <h2><a href="https://example.com/1">Result 1</a></h2>
            <div class="b_caption"><p>Snippet for result 1</p></div>
            <cite>example.com</cite>
        </li>
        <li class="b_algo">
            <h2><a href="https://example.com/2">Result 2</a></h2>
            <div class="b_caption"><p>Snippet for result 2</p></div>
            <cite>example.com</cite>
        </li>
    </ol>
    </body></html>
    """

    DUCKDUCKGO_WEB = """
    <html><body>
    <div class="results">
        <div class="result">
            <h2 class="result__title"><a class="result__a" href="https://example.com/1">Result 1</a></h2>
            <a class="result__snippet" href="https://example.com/1">Snippet for result 1</a>
            <div class="result__url">example.com</div>
        </div>
        <div class="result">
            <h2 class="result__title"><a class="result__a" href="https://example.com/2">Result 2</a></h2>
            <a class="result__snippet" href="https://example.com/2">Snippet for result 2</a>
            <div class="result__url">example.com</div>
        </div>
    </div>
    </body></html>
    """


# Contract test helpers
class ContractTestMixin:
    """Mixin for contract testing search responses."""

    @staticmethod
    def assert_search_response_structure(response: Dict[str, Any]) -> None:
        """Validate SerpAPI-compatible response structure."""
        required_fields = [
            "search_metadata",
            "search_information",
            "organic_results",
        ]
        for field_name in required_fields:
            assert field_name in response, f"Missing required field: {field_name}"

        meta = response["search_metadata"]
        assert "id" in meta
        assert "engine" in meta
        assert "query" in meta
        assert "status" in meta
        assert meta["status"] in ("success", "error")

        info = response["search_information"]
        assert "total_results" in info
        assert "query_displayed" in info

        assert isinstance(response["organic_results"], list)

        for result in response["organic_results"][:3]:
            assert "position" in result
            assert "title" in result
            assert "link" in result
            assert "snippet" in result


# Chaos engineering helpers
class ChaosScrapingClient:
    """Wraps a scraping client with chaos injection for resilience testing."""

    def __init__(self, inner: Any, *, chaos_config: Dict[str, Any] = None):
        self._inner = inner
        self.chaos_config = chaos_config or {}
        self._call_count = 0
        # proxy attributes commonly used by orchestrator code
        for attr in ("settings", "breaker", "rate_limiter", "proxies",
                     "fingerprint", "cookie_jar", "browser_fallback"):
            setattr(self, attr, getattr(inner, attr, None))

    @property
    def retries(self) -> int:
        return getattr(self._inner, "retries", 3)

    @retries.setter
    def retries(self, value: int) -> None:
        self._inner.retries = value

    async def get(self, url: str, *, engine: str, **kwargs):
        import asyncio
        import random as _random

        self._call_count += 1

        if "latency_ms" in self.chaos_config:
            await asyncio.sleep(self.chaos_config["latency_ms"] / 1000)

        if "fail_rate" in self.chaos_config:
            if _random.random() < self.chaos_config["fail_rate"]:
                from jiro.errors import EngineError
                raise EngineError("Chaos-induced failure", status_code=500)

        if self.chaos_config.get("blocked", False):
            from jiro.errors import EngineBlockedError
            raise EngineBlockedError("Chaos-induced block", details={"engine": engine})

        return await self._inner.get(url, engine=engine, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def generate_search_queries() -> list:
    """Generate diverse search queries for property-based testing."""
    return [
        "python web scraping",
        "machine learning tutorial",
        "best practices 2024",
        "API documentation",
        "async await patterns",
        "docker compose example",
        "kubernetes deployment",
        "react hooks guide",
        "sql optimization tips",
        "graphql vs rest",
    ]