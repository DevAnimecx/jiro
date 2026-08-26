"""Yandex search parser — web.

Yandex search results are in ``li.serp-item`` blocks with title, URL,
snippet, and domain info. Supports Russian/CIS market queries.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from selectolax.parser import HTMLParser, Node

from jiro.errors import EngineParseError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine
from jiro.scraping.client import parse_html

YANDEX_SEARCH_URL = "https://yandex.com/search/"


class YandexEngine(BaseEngine):
    name = "yandex"
    types = ["web"]
    parser_version = "1.0"

    async def search(self, req: SearchRequest) -> SearchResponse:
        params: Dict[str, Any] = {
            "text": req.q,
            "lr": self._region(req.location),
        }
        if req.time_range != "any":
            params["within"] = self._time_range(req.time_range)

        html, _ = await self.client.get(
            YANDEX_SEARCH_URL, engine=self.name, params=params,
            extra_headers={
                "Referer": "https://yandex.com/",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        tree = parse_html(html)
        results = self._parse_organic(tree, req)
        if not results:
            results = self._parse_json_data(html, req)

        if not results:
            body_text = tree.body.text() if tree.body else ""
            if "captcha" in body_text.lower() or "smartcaptcha" in body_text.lower():
                raise EngineParseError(
                    "yandex returned a CAPTCHA page",
                    details={"engine": self.name, "page_bytes": len(html)},
                )
            return SearchResponse(
                search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
                search_information={"query_displayed": req.q, "organic_results_count": 0},
                organic_results=[],
            )

        related = [
            {"text": a.text(strip=True)}
            for a in tree.css("a.serp-adv__link, a.organic__related-link")
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
        results: list[OrganicResult] = []
        for idx, block in enumerate(tree.css("li.serp-item, div.organic")):
            if len(results) >= req.num:
                break
            parsed = self._parse_result(block, req.start + len(results) + 1)
            if parsed:
                results.append(parsed)
        return results

    def _parse_json_data(self, html: str, req: SearchRequest) -> List[OrganicResult]:
        """Fallback: extract results from embedded JSON data."""
        m = re.search(r"Yandex\.SERP\s*=\s*(\{.*?\});", html, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except Exception:
            return []
        results: list[OrganicResult] = []
        for item in data.get("organic", []):
            if len(results) >= req.num:
                break
            title = item.get("title", "")
            url = item.get("url", "")
            snippet = item.get("snippet", "")
            domain = item.get("domain", "")
            if not title or not url:
                continue
            results.append(OrganicResult(
                position=req.start + len(results) + 1,
                title=title,
                link=url,
                snippet=snippet[:500],
                displayed_link=domain,
                source=domain or None,
            ))
        return results

    def _parse_result(self, block: Node, position: int) -> Optional[OrganicResult]:
        title_el = block.css_first("h2, a.organic__url-text")
        title = title_el.text(strip=True) if title_el else ""
        if not title:
            return None

        link_el = block.css_first("a.organic__url, a[href]")
        raw_link = (link_el.attributes.get("href") or "") if link_el else ""
        link = raw_link
        if link and not link.startswith("http"):
            link = "https:" + link if link.startswith("//") else "https://yandex.com" + link

        snippet_el = block.css_first("div.organic__content-wrapper, div.organic__snippet")
        snippet = snippet_el.text(strip=True) if snippet_el else ""

        source_el = block.css_first("div.organic__url-text, cite.organic__url")
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
    def _region(location: str) -> str:
        """Map location to Yandex region ID."""
        mapping = {
            "us": "84", "ru": "225", "ua": "187", "by": "149",
            "kz": "159", "de": "137", "fr": "223", "gb": "182",
            "cn": "134", "jp": "138", "br": "46",
        }
        return mapping.get(location.lower(), "84")

    @staticmethod
    def _time_range(time_range: str) -> str:
        mapping = {"day": "1d", "week": "1w", "month": "1m", "year": "1y"}
        return mapping.get(time_range, "")


# Register with the global registry on import.
from jiro.scraping.engines import registry  # noqa: E402

registry.register(YandexEngine)
