"""Brave Search parser — web / videos (images via Brave API is not public).

Organic results: ``div.snippet`` blocks (skip the ``#llm-snippet`` AI box and
``data-type="cluster"`` video clusters). Ads use ``/a/redirect`` links whose
``click_url`` param holds the real target.

Requires ``scraping.engines`` to include ``brave``; the host may rate-limit
datacenter IPs, in which case the fallback chain engages.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from selectolax.parser import HTMLParser, Node

from jiro.errors import EngineParseError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine
from jiro.scraping.client import parse_html

BRAVE_SEARCH_URL = "https://search.brave.com/search"


class BraveEngine(BaseEngine):
    name = "brave"
    types = ["web", "videos"]
    parser_version = "1.0"

    async def search(self, req: SearchRequest) -> SearchResponse:
        params: Dict[str, Any] = {
            "q": req.q,
            "source": "web",
            "tf": "all",
        }
        if req.type == "videos":
            params["source"] = "videos"
        html, _ = await self.client.get(
            BRAVE_SEARCH_URL, engine=self.name, params=params,
            extra_headers={"Referer": "https://search.brave.com/"},
        )
        tree = parse_html(html)
        blocks = tree.css("div.snippet")

        organic: List[OrganicResult] = []
        ads: List[Dict[str, Any]] = []
        position = req.start
        for block in blocks:
            if block.attributes.get("id") == "llm-snippet":
                continue
            kind = block.attributes.get("data-type", "")
            a = block.css_first("a")
            if a is None:
                continue
            href = (a.attributes.get("href") or "").strip()

            if href.startswith("/a/redirect"):  # ad
                ads.append(self._parse_ad(block, href))
                continue
            if not href.startswith("http"):
                continue

            parsed = self._parse_result(block, href, position + 1)
            if parsed is None:
                continue
            if kind == "cluster":  # video cluster
                parsed.source_type = "videos"
            position += 1
            organic.append(parsed)
            if len(organic) >= req.num:
                break

        if not organic:
            body_text = tree.body.text() if tree.body else ""
            if "no results" in body_text.lower():
                return SearchResponse(
                    search_metadata=self.metadata(req, engine=self.name, cached=False,
                                                  total_time=0.0),
                    search_information={"query_displayed": req.q,
                                        "organic_results_count": 0},
                    organic_results=[],
                )
            raise EngineParseError(
                "brave returned a page with no parseable organic results",
                details={"engine": self.name, "page_bytes": len(html)},
            )

        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False,
                                          total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(organic)},
            organic_results=organic,
            ads=ads,
            related_searches=self._related_searches(tree),
        )

    # ---------------------------------------------------------------- parsing
    def _parse_result(self, block: Node, href: str, position: int) -> Optional[OrganicResult]:
        title_el = block.css_first("div.title")
        title = title_el.text(strip=True) if title_el else ""
        if not title:
            return None

        cite = block.css_first("cite.snippet-url")
        displayed = cite.text(strip=True) if cite else ""

        snippet_el = block.css_first(
            "div.snippet-description, div.generic-snippet div.content"
        )
        snippet = snippet_el.text(strip=True) if snippet_el else ""
        if not snippet:
            # fallback: pull text after the title from the result content
            content = block.css_first("div.result-content")
            if content is not None:
                raw = content.text(separator=" ", strip=True)
                if title in raw:
                    snippet = raw.split(title, 1)[-1].strip()[:400]

        source = self._source(href)
        return OrganicResult(
            position=position,
            title=title,
            link=href,
            displayed_link=displayed or source or "",
            snippet=snippet,
            source=source,
        )

    @staticmethod
    def _parse_ad(block: Node, href: str) -> Dict[str, Any]:
        query = parse_qs(urlparse(href).query)
        real = unquote(query.get("click_url", [""])[0])
        title_el = block.css_first("div.title")
        return {
            "position": None,
            "title": title_el.text(strip=True) if title_el else "",
            "link": real or href,
            "displayed_link": "",
            "snippet": "",
            "source": BraveEngine._source(real) if real else "",
        }

    @staticmethod
    def _related_searches(tree: HTMLParser) -> List[Dict[str, str]]:
        out: list[dict[str, str]] = []
        for a in tree.css("div[class*='related'] a"):
            text = a.text(strip=True)
            if text and len(out) < 12:
                out.append({"text": text,
                            "link": (a.attributes.get("href") or "").strip()})
        return out

    @staticmethod
    def _source(link: str) -> Optional[str]:
        try:
            return urlparse(link).netloc or None
        except Exception:
            return None


# Register with the global registry on import.
from jiro.scraping.engines import registry  # noqa: E402

registry.register(BraveEngine)
