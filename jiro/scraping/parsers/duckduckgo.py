"""DuckDuckGo parser with a multi-strategy fallback.

1. ``html.duckduckgo.com/html`` — classic results page (``div.result``).
2. ``lite.duckduckgo.com/lite`` — minimal table layout.
3. vqd token + ``/d.js`` JSON API (used by many community clients).

Any strategy may raise ``EngineBlockedError`` (anomaly page) which the
orchestrator converts into engine fallback.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, Dict, List, Optional

from selectolax.parser import HTMLParser

from jiro.errors import EngineBlockedError, EngineParseError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine
from jiro.scraping.client import parse_html

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
DDG_HOME = "https://duckduckgo.com/"
DDG_IMAGES_URL = "https://duckduckgo.com/i.js"


class DuckDuckGoEngine(BaseEngine):
    name = "duckduckgo"
    types = ["web", "images"]
    parser_version = "1.2"

    async def search(self, req: SearchRequest) -> SearchResponse:
        if req.type == "images":
            return await self._images(req)
        return await self._web(req)

    # ------------------------------------------------------------------ web
    async def _web(self, req: SearchRequest) -> SearchResponse:
        strategies = [
            ("html", self._via_html),
            ("lite", self._via_lite),
            ("djs", self._via_djs),
        ]
        errors: List[Dict[str, Any]] = []
        for name, fn in strategies:
            try:
                organic, info = await fn(req)
                if organic:
                    return SearchResponse(
                        search_metadata=self.metadata(
                            req, engine=self.name, cached=False, total_time=0.0,
                            extra={"strategy": name},
                        ),
                        search_information={"query_displayed": req.q,
                                            "organic_results_count": len(organic)},
                        organic_results=organic,
                        related_searches=info.get("related", []),
                        pagination=info.get("pagination", {}),
                    )
            except (EngineBlockedError, EngineParseError) as exc:
                errors.append({"strategy": name, "error": exc.message})
                continue
        if errors and len(errors) == len(strategies):
            raise EngineBlockedError(
                "duckduckgo blocked all strategies",
                details={"strategies": errors},
            )
        raise EngineParseError("duckduckgo returned no parseable results")

    # ------------------------------------------------------------ html strategy
    async def _via_html(self, req: SearchRequest) -> tuple:
        params: Dict[str, Any] = {"q": req.q}
        if req.safe != "off":
            params["kp"] = "-2"
        html, _ = await self.client.get(DDG_HTML_URL, engine=self.name, params=params,
                                        extra_headers={"Referer": DDG_HOME})
        tree = parse_html(html)
        blocks = tree.css("div.result")
        if not blocks:
            raise EngineParseError("no result blocks on html page")
        organic = []
        for idx, block in enumerate(blocks[: req.num], start=1):
            a = block.css_first("a.result__a")
            if a is None:
                continue
            title = a.text(strip=True)
            link = a.attributes.get("href") or ""
            link = self._uddg(link)
            if not link:
                continue
            sn = block.css_first(".result__snippet")
            snippet = sn.text(strip=True) if sn else ""
            url_el = block.css_first(".result__url")
            displayed = url_el.text(strip=True) if url_el else ""
            organic.append(OrganicResult(
                position=req.start + idx,
                title=title,
                link=link,
                displayed_link=displayed,
                snippet=snippet,
                source=self._source(link),
            ))
        related = [
            {"text": a.text(strip=True)}
            for a in tree.css("a.result__more")
            if a.text(strip=True)
        ]
        return organic, {"related": related}

    # ------------------------------------------------------------ lite strategy
    async def _via_lite(self, req: SearchRequest) -> tuple:
        params: Dict[str, Any] = {"q": req.q}
        html, _ = await self.client.get(DDG_LITE_URL, engine=self.name, params=params,
                                        extra_headers={"Referer": DDG_HOME})
        tree = parse_html(html)
        # lite layout: <a href='...'> in table rows
        organic = []
        links = tree.css("a[href^='//duckduckgo.com/l/?uddg=']")
        for idx, a in enumerate(links[: req.num], start=1):
            title = a.text(strip=True)
            link = self._uddg(a.attributes.get("href") or "")
            if not title or not link:
                continue
            organic.append(OrganicResult(
                position=req.start + idx,
                title=title,
                link=link,
                snippet="",
                source=self._source(link),
            ))
        if not organic:
            raise EngineParseError("no results on lite page")
        return organic, {}

    # ------------------------------------------------------------ vqd strategy
    async def _via_djs(self, req: SearchRequest) -> tuple:
        home_html, _ = await self.client.get(DDG_HOME, engine=self.name,
                                             params={"q": req.q})
        m = re.search(r'vqd="?([0-9-]+)"?', home_html)
        if not m:
            raise EngineBlockedError("could not obtain vqd token")
        vqd = m.group(1)
        params: Dict[str, Any] = {
            "q": req.q, "vqd": vqd, "api": "/d.js", "o": "json",
            "kl": f"{req.language}-{req.location}",
        }
        text, _ = await self.client.get(DDG_HOME, engine=self.name, params=params,
                                        extra_headers={"Referer": DDG_HOME})
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EngineParseError("d.js returned non-JSON") from exc
        organic = []
        for idx, item in enumerate(data.get("Results", [])[: req.num], start=1):
            title = html_lib.unescape(item.get("t", ""))
            link = item.get("u", "")
            snippet = html_lib.unescape(re.sub(r"<[^>]+>", "", item.get("a", "")))
            if not title or not link:
                continue
            organic.append(OrganicResult(
                position=req.start + idx,
                title=title,
                link=link,
                snippet=snippet,
                source=self._source(link),
            ))
        if not organic:
            raise EngineParseError("d.js returned no organic results")
        return organic, {}

    # ---------------------------------------------------------------- images
    async def _images(self, req: SearchRequest) -> SearchResponse:
        home_html, _ = await self.client.get(DDG_HOME, engine=self.name,
                                             params={"q": req.q})
        m = re.search(r'vqd="?([0-9-]+)"?', home_html)
        if not m:
            raise EngineBlockedError("could not obtain vqd token for images")
        params = {"l": f"{req.language}-{req.location}", "o": "json", "q": req.q,
                  "vqd": m.group(1)}
        text, _ = await self.client.get(DDG_IMAGES_URL, engine=self.name, params=params,
                                        extra_headers={"Referer": DDG_HOME})
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EngineParseError("i.js returned non-JSON") from exc
        organic = []
        for idx, item in enumerate(data.get("results", [])[: req.num], start=1):
            organic.append(OrganicResult(
                position=req.start + idx,
                title=item.get("title", ""),
                link=item.get("image", ""),
                thumbnail=item.get("thumbnail", ""),
                dimensions=f"{item.get('width', '')}x{item.get('height', '')}"
                if item.get("width") and item.get("height") else None,
                source=item.get("source", ""),
            ))
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False,
                                          total_time=0.0, extra={"strategy": "i.js"}),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(organic)},
            organic_results=organic,
        )

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _uddg(href: str) -> str:
        """Decode DDG's /l/?uddg= redirect wrapper."""
        from urllib.parse import unquote
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            return unquote(m.group(1))
        return href

    @staticmethod
    def _source(link: str) -> Optional[str]:
        from urllib.parse import urlparse
        try:
            return urlparse(link).netloc or None
        except Exception:
            return None

# Register this engine with the global registry on import.
from jiro.scraping.engines import registry  # noqa: E402

registry.register(DuckDuckGoEngine)
