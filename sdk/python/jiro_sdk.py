"""Jiro Python SDK - Official client library for Jiro Search API.

Provides a simple, Pythonic interface to all Jiro features:
- Web search with multiple engines
- Web scraping with content extraction
- AI-powered research with citations
- Real-time WebSocket streaming
- Batch operations
- Plugin management
- System monitoring

Usage:
    from jiro_sdk import JiroClient, AsyncJiroClient

    # Synchronous
    client = JiroClient(api_key="your-api-key")
    results = client.search("python web scraping")
    content = client.scrape("https://example.com")
    answer = client.ai_ask("What is Python?")

    # Async
    async_client = AsyncJiroClient(api_key="your-api-key")
    results = await async_client.search("python web scraping")
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Union

import httpx


__version__ = "0.2.15"
__all__ = [
    "JiroClient",
    "AsyncJiroClient",
    "JiroError",
    "AuthenticationError",
    "RateLimitError",
    "NotFoundError",
    "SearchResult",
    "ScrapeResult",
    "AIResponse",
    "BatchJob",
    "PluginInfo",
]


# ── Exceptions ──────────────────────────────────────────────────────────────


class JiroError(Exception):
    """Base exception for Jiro SDK errors."""
    
    def __init__(self, message: str, status: int = 0, data: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.data = data


class AuthenticationError(JiroError):
    """Authentication failed."""
    
    def __init__(self, message: str = "Invalid API key") -> None:
        super().__init__(message, status=401)


class RateLimitError(JiroError):
    """Rate limit exceeded."""
    
    def __init__(self, message: str = "Rate limit exceeded") -> None:
        super().__init__(message, status=429)


class NotFoundError(JiroError):
    """Resource not found."""
    
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, status=404)


class ServerError(JiroError):
    """Internal server error."""
    
    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(message, status=500)


# ── Data Classes ────────────────────────────────────────────────────────────


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    snippet: str
    link: str
    source: str = ""
    displayed_link: str = ""
    position: int = 0
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SearchResult:
        return cls(
            title=data.get("title", ""),
            snippet=data.get("snippet", ""),
            link=data.get("link", ""),
            source=data.get("source", ""),
            displayed_link=data.get("displayed_link", ""),
            position=data.get("position", 0),
        )


@dataclass
class ScrapeResult:
    """Scraped content."""
    title: str
    url: str
    content: str
    html: str = ""
    markdown: str = ""
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScrapeResult:
        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
            content=data.get("content", ""),
            html=data.get("html", ""),
            markdown=data.get("markdown", ""),
        )


@dataclass
class Citation:
    """AI research citation."""
    title: str
    url: str
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Citation:
        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
        )


@dataclass
class AIResponse:
    """AI research response with citations."""
    answer: str
    citations: List[Citation]
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AIResponse:
        return cls(
            answer=data.get("answer", ""),
            citations=[Citation.from_dict(c) for c in data.get("citations", [])],
        )


@dataclass
class BatchJob:
    """Batch operation job."""
    job_id: str
    operation: str
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    items: List[Dict[str, Any]] = field(default_factory=list)
    
    @property
    def progress(self) -> float:
        if self.total_items == 0:
            return 0
        return (self.completed_items / self.total_items) * 100
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BatchJob:
        return cls(
            job_id=data.get("job_id", ""),
            operation=data.get("operation", ""),
            status=data.get("status", ""),
            total_items=data.get("total_items", 0),
            completed_items=data.get("completed_items", 0),
            failed_items=data.get("failed_items", 0),
            items=data.get("items", []),
        )


@dataclass
class PluginInfo:
    """Plugin information."""
    name: str
    version: str
    author: str
    description: str
    types: List[str]
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PluginInfo:
        return cls(
            name=data.get("name", ""),
            version=data.get("version", ""),
            author=data.get("author", ""),
            description=data.get("description", ""),
            types=data.get("types", []),
        )


# ── Synchronous Client ──────────────────────────────────────────────────────


class JiroClient:
    """Synchronous Jiro client.
    
    Args:
        api_key: API key for authentication
        base_url: Base URL of Jiro server
        timeout: Request timeout in seconds
        
    Example:
        client = JiroClient(api_key="your-key", base_url="http://localhost:8000")
        results = client.search("python web scraping")
    """
    
    __version__ = __version__
    
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
    
    def __enter__(self) -> JiroClient:
        return self
    
    def __exit__(self, *args: Any) -> None:
        self.close()

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": f"jiro-sdk-python/{__version__}"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _handle_response(self, resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            raise AuthenticationError()
        if resp.status_code == 429:
            raise RateLimitError()
        if resp.status_code == 404:
            raise NotFoundError()
        if resp.status_code >= 500:
            raise ServerError()
        resp.raise_for_status()
        return resp.json()

    # ── Search ──────────────────────────────────────────────────────────────

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
        """Search the web.
        
        Args:
            query: Search query
            engine: Search engine (google, bing, brave, duckduckgo)
            num_results: Number of results to return
            search_type: Search type (web, images, news, videos)
            location: Location code
            language: Language code
            
        Returns:
            Search results dictionary
        """
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
        """Search multiple engines in parallel.
        
        Args:
            query: Search query
            num_results: Number of results per engine
            num_engines: Number of engines to query (max 5)
            
        Returns:
            Combined search results
        """
        resp = self._client.get(
            "/search.json",
            params={
                "q": query,
                "num": num_results,
                "parallel": True,
                "num_engines": min(num_engines, 5),
                **kwargs,
            },
        )
        return self._handle_response(resp)

    def search_images(
        self,
        query: str,
        engine: str = "google",
        num_results: int = 10,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Search for images."""
        return self.search(query, engine=engine, num_results=num_results, search_type="images", **kwargs)

    def search_news(
        self,
        query: str,
        engine: str = "google",
        num_results: int = 10,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Search for news."""
        return self.search(query, engine=engine, num_results=num_results, search_type="news", **kwargs)

    def search_videos(
        self,
        query: str,
        engine: str = "google",
        num_results: int = 10,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Search for videos."""
        return self.search(query, engine=engine, num_results=num_results, search_type="videos", **kwargs)

    # ── Scrape ──────────────────────────────────────────────────────────────

    def scrape(
        self,
        url: str,
        format: str = "markdown",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Scrape a URL and extract content.
        
        Args:
            url: URL to scrape
            format: Output format (markdown, text, html, json)
            
        Returns:
            Scraped content dictionary
        """
        resp = self._client.post(
            "/scrape",
            json={"url": url, "format": format, **kwargs},
        )
        return self._handle_response(resp)

    def scrape_batch(
        self,
        urls: List[str],
        format: str = "markdown",
        max_concurrent: int = 5,
    ) -> BatchJob:
        """Scrape multiple URLs in batch.
        
        Args:
            urls: List of URLs to scrape
            format: Output format
            max_concurrent: Maximum concurrent requests
            
        Returns:
            Batch job with results
        """
        resp = self._client.post(
            "/batch/scrape",
            json={"urls": urls, "format": format, "max_concurrent": max_concurrent},
        )
        return BatchJob.from_dict(self._handle_response(resp))

    # ── AI ──────────────────────────────────────────────────────────────────

    def ai_ask(
        self,
        query: str,
        max_sources: int = 5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Ask an AI research question with citations.
        
        Args:
            query: Research question
            max_sources: Maximum number of sources to cite
            
        Returns:
            AI response with answer and citations
        """
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
        """Configure AI provider.
        
        Args:
            provider: Provider name (openai, anthropic, groq, etc.)
            api_key: API key for the provider
            model: Model name
            base_url: Custom base URL
        """
        body: Dict[str, Any] = {"provider": provider, "api_key": api_key}
        if model:
            body["model"] = model
        if base_url:
            body["base_url"] = base_url
        resp = self._client.post("/ai/config", json=body)
        return self._handle_response(resp)

    # ── Social ──────────────────────────────────────────────────────────────

    def social_scrape(
        self,
        url: str,
        platform: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Scrape social media content."""
        resp = self._client.post(
            "/social/scrape",
            json={"url": url, "platform": platform, **kwargs},
        )
        return self._handle_response(resp)

    def social_search(
        self,
        query: str,
        platform: str = "reddit",
        num_results: int = 10,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Search on social platform."""
        resp = self._client.get(
            f"/social/{platform}/search",
            params={"q": query, "num": num_results, **kwargs},
        )
        return self._handle_response(resp)

    # ── System ──────────────────────────────────────────────────────────────

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

    def health(self) -> Dict[str, Any]:
        """Health check endpoint."""
        resp = self._client.get("/health")
        return self._handle_response(resp)

    def metrics(self) -> str:
        """Get Prometheus metrics."""
        resp = self._client.get("/metrics")
        return resp.text

    # ── Batch ───────────────────────────────────────────────────────────────

    def batch_search(
        self,
        queries: List[str],
        engine: str = "google",
        num_results: int = 10,
    ) -> BatchJob:
        """Execute multiple searches in batch."""
        resp = self._client.post(
            "/batch/search",
            json={"queries": queries, "engine": engine, "num_results": num_results},
        )
        return BatchJob.from_dict(self._handle_response(resp))

    def batch_job(self, job_id: str) -> BatchJob:
        """Get batch job status."""
        resp = self._client.get(f"/batch/jobs/{job_id}")
        return BatchJob.from_dict(self._handle_response(resp))

    # ── WebSocket ───────────────────────────────────────────────────────────

    def search_stream(
        self,
        query: str,
        engine: str = "google",
        num: int = 10,
    ) -> Any:
        """Create WebSocket connection for real-time search streaming.
        
        Returns:
            WebSocket connection object
        """
        try:
            import websockets
            import websocket
            
            ws_url = self.base_url.replace("http", "ws")
            url = f"{ws_url}/ws/search?query={query}&engine={engine}&num={num}"
            
            ws = websocket.create_connection(url)
            return ws
        except ImportError:
            raise JiroError("websocket-client package required for streaming")

    # ── Context Manager ─────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the client."""
        self._client.close()

    def __repr__(self) -> str:
        return f"JiroClient(base_url='{self.base_url}')"


# ── Async Client ────────────────────────────────────────────────────────────


class AsyncJiroClient:
    """Async Jiro client.
    
    Args:
        api_key: API key for authentication
        base_url: Base URL of Jiro server
        timeout: Request timeout in seconds
        
    Example:
        async with AsyncJiroClient(api_key="your-key") as client:
            results = await client.search("python web scraping")
    """
    
    __version__ = __version__
    
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
    
    async def __aenter__(self) -> AsyncJiroClient:
        return self
    
    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": f"jiro-sdk-python/{__version__}"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def _handle_response(self, resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            raise AuthenticationError()
        if resp.status_code == 429:
            raise RateLimitError()
        if resp.status_code == 404:
            raise NotFoundError()
        if resp.status_code >= 500:
            raise ServerError()
        resp.raise_for_status()
        return resp.json()

    async def search(
        self,
        query: str,
        engine: str = "google",
        num_results: int = 10,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Search the web (async)."""
        resp = await self._client.get(
            "/search.json",
            params={"q": query, "engine": engine, "num": num_results, **kwargs},
        )
        return await self._handle_response(resp)

    async def search_parallel(
        self,
        query: str,
        num_results: int = 10,
        num_engines: int = 3,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Search multiple engines in parallel (async)."""
        resp = await self._client.get(
            "/search.json",
            params={
                "q": query,
                "num": num_results,
                "parallel": True,
                "num_engines": min(num_engines, 5),
                **kwargs,
            },
        )
        return await self._handle_response(resp)

    async def scrape(self, url: str, format: str = "markdown", **kwargs: Any) -> Dict[str, Any]:
        """Scrape a URL (async)."""
        resp = await self._client.post("/scrape", json={"url": url, "format": format, **kwargs})
        return await self._handle_response(resp)

    async def ai_ask(self, query: str, max_sources: int = 5, **kwargs: Any) -> Dict[str, Any]:
        """Ask AI research question (async)."""
        resp = await self._client.post("/ai/search", json={"query": query, "max_sources": max_sources, **kwargs})
        return await self._handle_response(resp)

    async def ai_config(self) -> Dict[str, Any]:
        """Get AI config (async)."""
        resp = await self._client.get("/ai/config")
        return await self._handle_response(resp)

    async def ai_setup(
        self,
        provider: str,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Configure AI provider (async)."""
        body: Dict[str, Any] = {"provider": provider, "api_key": api_key}
        if model:
            body["model"] = model
        if base_url:
            body["base_url"] = base_url
        resp = await self._client.post("/ai/config", json=body)
        return await self._handle_response(resp)

    async def social_scrape(self, url: str, platform: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """Scrape social media (async)."""
        resp = await self._client.post("/social/scrape", json={"url": url, "platform": platform, **kwargs})
        return await self._handle_response(resp)

    async def status(self) -> Dict[str, Any]:
        """Get system status (async)."""
        resp = await self._client.get("/status")
        return await self._handle_response(resp)

    async def usage(self, days: int = 7) -> Dict[str, Any]:
        """Get usage stats (async)."""
        resp = await self._client.get("/usage", params={"days": days})
        return await self._handle_response(resp)

    async def plugins(self) -> Dict[str, Any]:
        """List plugins (async)."""
        resp = await self._client.get("/plugins")
        return await self._handle_response(resp)

    async def health(self) -> Dict[str, Any]:
        """Health check (async)."""
        resp = await self._client.get("/health")
        return await self._handle_response(resp)

    async def close(self) -> None:
        """Close the client."""
        await self._client.aclose()

    def __repr__(self) -> str:
        return f"AsyncJiroClient(base_url='{self.base_url}')"
