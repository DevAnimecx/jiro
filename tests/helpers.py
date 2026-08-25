"""Shared test utilities (non-fixture helpers)."""

from __future__ import annotations

from typing import Any, Dict, Optional


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class FakeClient:
    """ScrapingClient stand-in: serves fixture HTML instead of the network."""

    def __init__(self, pages: Optional[Dict[str, str]] = None) -> None:
        self.pages = pages or {}
        self.requests: list = []

    async def get(self, url: str, *, engine: str,
                  params: Optional[Dict[str, Any]] = None,
                  extra_headers: Optional[Dict[str, str]] = None,
                  raw: bool = False):
        self.requests.append({"url": url, "engine": engine, "params": params})
        for key, html in self.pages.items():
            if key in url:
                return html, FakeResponse(html)
        return "<html><body>empty</body></html>", FakeResponse("<html></html>")
