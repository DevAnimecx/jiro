"""Jiro Python SDK - Official client library for Jiro Search API.

Provides a simple, Pythonic interface to all Jiro features:
- Web search with multiple engines
- Web scraping with content extraction
- AI-powered research with citations
- Real-time WebSocket streaming
- Plugin management

Usage:
    from jiro_sdk import JiroClient

    client = JiroClient(api_key="your-api-key")
    results = client.search("python web scraping")
    content = client.scrape("https://example.com")
    answer = client.ai_ask("What is Python?")
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import httpx


class JiroError(Exception):
    """Base exception for Jiro SDK errors."""
    pass


class AuthenticationError(JiroError):
    """Authentication failed."""
    pass


class RateLimitError(JiroError):
    """Rate limit exceeded."""
    pass


class NotFoundError(JiroError):
    """Resource not found."""
    pass


class JiroClient:
    """Synchronous Jiro client."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._headers(),
        )

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _handle_response(self, resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            raise AuthenticationError("Invalid API key")
        if resp.status_code == 429:
            raise RateLimitError("Rate limit exceeded")
        if resp.status_code == 404:
            raise NotFoundError("Resource not found")
        resp.raise_for_status()
        return resp.json()

    # ---- Search ----

    def search(
        self,
        query: str,
        engine: str = "google",
        num_results: int = 10,
        search_type: str = "web",
        location: str = "us",
        language: str = "en",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Search the web."""
        resp = self._client.get(
            "/search.json",
            params={
                "q": query,
                "engine": engine,
                "num": num_results,
                "type": search_type,
                "location": location,
                "language": language,
                **kwargs,
            },
        )
        return self._handle_response(resp)

    def search_parallel(
        self,
        query: str,
        num_results: int = 10,
        num_engines: int = 3,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Search multiple engines in parallel."""
        resp = self._client.get(
            "/search.json",
            params={
                "q": query,
                "num": num_results,
                "parallel": True,
                "num_engines": num_engines,
                **kwargs,
            },
        )
        return self._handle_response(resp)

    # ---- Scrape ----

    def scrape(
        self,
        url: str,
        format: str = "markdown",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Scrape a URL and extract content."""
        resp = self._client.post(
            "/scrape",
            json={"url": url, "format": format, **kwargs},
        )
        return self._handle_response(resp)

    # ---- AI ----

    def ai_ask(
        self,
        query: str,
        max_sources: int = 5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Ask an AI research question with citations."""
        resp = self._client.post(
            "/ai/search",
            json={"query": query, "max_sources": max_sources, **kwargs},
        )
        return self._handle_response(resp)

    def ai_config(self) -> Dict[str, Any]:
        """Get current AI configuration."""
        resp = self._client.get("/ai/config")
        return self._handle_response(resp)

    def ai_setup(
        self,
        provider: str,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Configure AI provider."""
        body: Dict[str, Any] = {"provider": provider, "api_key": api_key}
        if model:
            body["model"] = model
        if base_url:
            body["base_url"] = base_url
        resp = self._client.post("/ai/config", json=body)
        return self._handle_response(resp)

    # ---- System ----

    def status(self) -> Dict[str, Any]:
        """Get system status."""
        resp = self._client.get("/status")
        return self._handle_response(resp)

    def usage(self, days: int = 7) -> Dict[str, Any]:
        """Get usage statistics."""
        resp = self._client.get("/usage", params={"days": days})
        return self._handle_response(resp)

    def plugins(self) -> Dict[str, Any]:
        """List installed plugins."""
        resp = self._client.get("/plugins")
        return self._handle_response(resp)

    def close(self) -> None:
        """Close the client."""
        self._client.close()


class AsyncJiroClient:
    """Async Jiro client."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._headers(),
        )

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def _handle_response(self, resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            raise AuthenticationError("Invalid API key")
        if resp.status_code == 429:
            raise RateLimitError("Rate limit exceeded")
        if resp.status_code == 404:
            raise NotFoundError("Resource not found")
        resp.raise_for_status()
        return resp.json()

    async def search(
        self,
        query: str,
        engine: str = "google",
        num_results: int = 10,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        resp = await self._client.get(
            "/search.json",
            params={"q": query, "engine": engine, "num": num_results, **kwargs},
        )
        return await self._handle_response(resp)

    async def scrape(self, url: str, format: str = "markdown", **kwargs: Any) -> Dict[str, Any]:
        resp = await self._client.post("/scrape", json={"url": url, "format": format, **kwargs})
        return await self._handle_response(resp)

    async def ai_ask(self, query: str, max_sources: int = 5, **kwargs: Any) -> Dict[str, Any]:
        resp = await self._client.post("/ai/search", json={"query": query, "max_sources": max_sources, **kwargs})
        return await self._handle_response(resp)

    async def close(self) -> None:
        await self._client.aclose()
