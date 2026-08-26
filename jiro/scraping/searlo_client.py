"""Searlo API client — async HTTP client for Searlo REST API.

* Native async httpx client with retry/backoff
* Credit tracking via response headers
* TOON/JSON/enhanced format support
* MCP-compatible authentication
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, Optional, Tuple

import httpx
from pydantic import BaseModel

from jiro.config import Settings
from jiro.errors import EngineBlockedError, EngineError, EngineTimeoutError


class SearloCredits(BaseModel):
    """Credit tracking from Searlo response headers."""
    deducted: int = 0
    remaining: int = 0
    total: int = 0


class SearloSearchParams(BaseModel):
    """Parameters for Searlo search API."""
    q: str
    limit: int = 10
    page: int = 1
    gl: Optional[str] = None
    hl: Optional[str] = None
    lr: Optional[str] = None
    safe: Optional[str] = None
    format: str = "toon"  # toon | json | enhanced
    site: Optional[str] = None
    date_range: Optional[str] = None
    time_range: Optional[str] = None
    file_type: Optional[str] = None
    exact_terms: Optional[str] = None
    exclude_terms: Optional[str] = None


class SearloClient:
    """Async client for Searlo REST API."""

    BASE_URL = "https://api.searlo.tech/api/v1"
    MCP_URL = "https://api.searlo.tech/api/v1/mcp"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = settings.get("searlo.api_key", "")
        self.base_url = settings.get("searlo.base_url", self.BASE_URL)
        self.default_format = settings.get("searlo.default_format", "toon")
        self.timeout = settings.get("searlo.timeout", 30)
        self.retries = settings.get("searlo.retries", 3)
        self._client: Optional[httpx.AsyncClient] = None
        self._credits: Optional[SearloCredits] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or (hasattr(self._client, 'is_closed') and self._client.is_closed):
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=limits,
                follow_redirects=True,
                headers={"User-Agent": "Jiro-Search/1.0 (+https://github.com/DevAnimecx/jiro)"},
                http2=True,
                trust_env=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                if hasattr(self._client, 'is_closed') and not self._client.is_closed:
                    await self._client.aclose()
            except Exception:
                pass
            self._client = None

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json",
        }
        headers.update(extra or {})
        return headers

    @property
    def credits(self) -> Optional[SearloCredits]:
        return self._credits

    def _parse_credits(self, headers: httpx.Headers) -> None:
        """Parse credit headers from Searlo response."""
        try:
            deducted = int(headers.get("X-Credits-Deducted", "0"))
            remaining = int(headers.get("X-Credits-Remaining", "0"))
            total = int(headers.get("X-Credits-Total", "0"))
            self._credits = SearloCredits(
                deducted=deducted,
                remaining=remaining,
                total=total,
            )
        except (ValueError, TypeError):
            pass

    async def search_web(
        self,
        query: str,
        *,
        limit: int = 10,
        page: int = 1,
        gl: Optional[str] = None,
        hl: Optional[str] = None,
        lr: Optional[str] = None,
        safe: Optional[str] = None,
        format: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], SearloCredits]:
        """Search Google web results via Searlo."""
        params = SearloSearchParams(
            q=query,
            limit=min(limit, 10),
            page=page,
            gl=gl,
            hl=hl,
            lr=lr,
            safe=safe,
            format=format or self.default_format,
        )
        return await self._request("GET", "/search/web", params=params.model_dump(exclude_none=True))

    async def search_advanced(
        self,
        query: str,
        *,
        limit: int = 10,
        page: int = 1,
        gl: Optional[str] = None,
        hl: Optional[str] = None,
        lr: Optional[str] = None,
        safe: Optional[str] = None,
        format: Optional[str] = None,
        site: Optional[str] = None,
        date_range: Optional[str] = None,
        time_range: Optional[str] = None,
        file_type: Optional[str] = None,
        exact_terms: Optional[str] = None,
        exclude_terms: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], SearloCredits]:
        """Advanced search with additional filters."""
        params = SearloSearchParams(
            q=query,
            limit=min(limit, 10),
            page=page,
            gl=gl,
            hl=hl,
            lr=lr,
            safe=safe,
            format=format or self.default_format,
            site=site,
            date_range=date_range,
            time_range=time_range,
            file_type=file_type,
            exact_terms=exact_terms,
            exclude_terms=exclude_terms,
        )
        return await self._request("GET", "/search/advanced", params=params.model_dump(exclude_none=True))

    async def search_images(
        self,
        query: str,
        *,
        limit: int = 10,
        page: int = 1,
        gl: Optional[str] = None,
        hl: Optional[str] = None,
        safe: Optional[str] = None,
        format: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], SearloCredits]:
        """Search Google images via Searlo."""
        params = SearloSearchParams(
            q=query,
            limit=min(limit, 10),
            page=page,
            gl=gl,
            hl=hl,
            safe=safe,
            format=format or self.default_format,
        )
        return await self._request("GET", "/search/images", params=params.model_dump(exclude_none=True))

    async def search_news(
        self,
        query: str,
        *,
        limit: int = 10,
        page: int = 1,
        gl: Optional[str] = None,
        hl: Optional[str] = None,
        format: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], SearloCredits]:
        """Search Google news via Searlo."""
        params = SearloSearchParams(
            q=query,
            limit=min(limit, 10),
            page=page,
            gl=gl,
            hl=hl,
            format=format or self.default_format,
        )
        return await self._request("GET", "/search/news", params=params.model_dump(exclude_none=True))

    async def search_shopping(
        self,
        query: str,
        *,
        limit: int = 55,
        page: int = 1,
        gl: Optional[str] = None,
        hl: Optional[str] = None,
        format: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], SearloCredits]:
        """Search Google shopping via Searlo."""
        params = SearloSearchParams(
            q=query,
            limit=min(limit, 55),
            page=page,
            gl=gl,
            hl=hl,
            format=format or self.default_format,
        )
        return await self._request("GET", "/search/shopping", params=params.model_dump(exclude_none=True))

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], SearloCredits]:
        """Execute request with retry/backoff."""
        if not self.api_key:
            raise EngineError(
                "Searlo API key not configured",
                details={"engine": "searlo", "config_key": "searlo.api_key"},
            )

        client = await self._get_client()
        url = f"{self.base_url}{endpoint}"

        last_error: Optional[Exception] = None

        for attempt in range(self.retries + 1):
            try:
                if method == "GET":
                    response = await client.get(
                        url,
                        params=params,
                        headers=self._headers(),
                    )
                else:
                    response = await client.post(
                        url,
                        params=params,
                        json=json,
                        headers=self._headers({"Content-Type": "application/json"}),
                    )

                self._parse_credits(response.headers)

                if response.status_code == 401:
                    raise EngineError(
                        "Invalid Searlo API key",
                        status_code=401,
                        details={"engine": "searlo"},
                    )
                elif response.status_code == 429:
                    raise EngineBlockedError(
                        "Searlo rate limit exceeded",
                        details={"engine": "searlo", "retry_after": response.headers.get("Retry-After")},
                    )
                elif response.status_code >= 500:
                    raise EngineError(
                        f"Searlo server error: {response.status_code}",
                        details={"engine": "searlo", "status": response.status_code},
                    )
                elif response.status_code >= 400:
                    raise EngineError(
                        f"Searlo API error: {response.status_code} - {response.text[:200]}",
                        details={"engine": "searlo", "status": response.status_code},
                    )

                data = response.json()

                if not data.get("success", True):
                    raise EngineError(
                        f"Searlo API returned error: {data.get('message', 'Unknown error')}",
                        details={"engine": "searlo", "response": data},
                    )

                return data, self._credits or SearloCredits()

            except (EngineBlockedError, EngineError):
                raise
            except httpx.TimeoutException as exc:
                last_error = EngineTimeoutError(
                    f"timeout calling Searlo API: {exc}",
                    details={"engine": "searlo", "attempt": attempt},
                )
            except Exception as exc:
                last_error = EngineError(
                    f"Searlo request failed: {exc}",
                    details={"engine": "searlo", "attempt": attempt},
                )

            if attempt < self.retries:
                delay = (2 ** attempt) * random.uniform(0.5, 1.5)
                await asyncio.sleep(delay)

        assert last_error is not None
        raise last_error

    async def get_mcp_config(self) -> Dict[str, Any]:
        """Generate MCP server configuration for Searlo."""
        return {
            "mcpServers": {
                "searlo": {
                    "url": self.MCP_URL,
                    "headers": {
                        "X-API-Key": self.api_key
                    }
                }
            }
        }


async def create_searlo_client(settings: Settings) -> SearloClient:
    """Factory to create and initialize Searlo client."""
    client = SearloClient(settings)
    return client