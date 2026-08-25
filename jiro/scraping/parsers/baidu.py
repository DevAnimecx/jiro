"""Baidu search parser — web.

Baidu search results are in ``div.result`` or ``div.c-container`` blocks
with title, URL, snippet, and source info. Supports Chinese market queries.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from selectolax.parser import HTMLParser, Node

from jiro.errors import EngineParseError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine
from jiro.scraping.client import parse_html

BAIDU_SEARCH_URL = "https://www.baidu.com/s"


class BaiduEngine(BaseEngine):
    name = "baidu"
    types = ["web"]
    parser_version = "1.0"

    async def search(self, req: SearchRequest) -> SearchResponse:
        params: Dict[str, Any] = {
            "wd": req.q,
            "rn": str(min(req.num, 50)),
            "pn": str(req.start),
        }
        if req.time_range != "any":
            params["gpc"] = self._time_filter(req.time_range)

        html, _ = await self.client.get(
            BAIDU_SEARCH_URL, engine=self.name, params=params,
            extra_headers={
                "Referer": "https://www.baidu.com/",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        tree = parse_html(html)
        results = self._parse_organic(tree, req)
        if not results:
            results = self._parse_json_data(html, req)

        if not results:
            body_text = tree.body.text() if tree.body else ""
            if "安全验证" in body_text or "captcha" in body_text.lower():
                raise EngineParseError(
                    "baidu returned a CAPTCHA/verification page",
                    details={"engine": self.name, "page_bytes": len(html)},
                )
            return SearchResponse(
                search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
                search_information={"query_displayed": req.q, "organic_results_count": 0},
                organic_results=[],
            )

        related = [
            {"text": a.text(strip=True)}
            for a in tree.css("a.result-op, div.c-recommend a")
            if a.text(strip=True)
        ]

        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(results)},
            organic_results=results,
            related_searches=related[:10],
        )

    def _parse_organic(self, tree: HTMLParser, req: SearchRequest) -> List[OrganicResult]:
        results = []
        for idx, block in enumerate(tree.css("div.result, div.c-container")):
            if len(results) >= req.num:
                break
            parsed = self._parse_result(block, req.start + len(results) + 1)
            if parsed:
                results.append(parsed)
        return results

    def _parse_json_data(self, html: str, req: SearchRequest) -> List[OrganicResult]:
        """Fallback: extract results from embedded JSON data."""
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", html, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except Exception:
            return []
        results = []
        for item in data.get("data", []):
            if len(results) >= req.num:
                break
            title = item.get("title", "")
            url = item.get("url", "")
            desc = item.get("desc", "")
            if not title or not url:
                continue
            results.append(OrganicResult(
                position=req.start + len(results) + 1,
                title=title,
                link=url,
                snippet=desc[:500],
                source=self._source(url),
            ))
        return results

    def _parse_result(self, block: Node, position: int) -> Optional[OrganicResult]:
        title_el = block.css_first("h3, a.c-title-text, span.c-title-text")
        title = title_el.text(strip=True) if title_el else ""
        if not title:
            return None

        link_el = block.css_first("a[href]")
        raw_link = (link_el.attributes.get("href") or "") if link_el else ""
        link = raw_link
        # Baidu wraps URLs in redirects
        if "baidu.com/link" in link or link.startswith("//"):
            link = "https:" + link if link.startswith("//") else link

        snippet_el = block.css_first("span.content-right_8Zs40, div.c-abstract, span.c-font-normal")
        snippet = snippet_el.text(strip=True) if snippet_el else ""

        source_el = block.css_first("span.c-color-gray, span.c-gap-right-small")
        source = source_el.text(strip=True) if source_el else None

        return OrganicResult(
            position=position,
            title=title,
            link=link,
            snippet=snippet[:500],
            displayed_link=source or "",
            source=source,
        )

    @staticmethod
    def _source(link: str) -> Optional[str]:
        try:
            from urllib.parse import urlparse
            return urlparse(link).netloc or None
        except Exception:
            return None

    @staticmethod
    def _time_filter(time_range: str) -> str:
        mapping = {
            "day": "stf=1700000000,1700086400|stftype=1",
            "week": "stf=1699481600,1700086400|stftype=1",
            "month": "stf=1697248000,1700086400|stftype=1",
            "year": "stf=1668422400,1700086400|stftype=1",
        }
        return mapping.get(time_range, "")


# Register with the global registry on import.
from jiro.scraping.engines import registry  # noqa: E402

registry.register(BaiduEngine)
