"""Google parser — web / images (best effort for other types).

Google frequently serves a JS-required or consent page to datacenter IPs;
the orchestrator's fallback chain handles that. Selectors are written
defensively (multiple candidates) per the PRD's self-healing parser design.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from selectolax.parser import HTMLParser, Node

from jiro.errors import EngineParseError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine
from jiro.scraping.client import parse_html

GOOGLE_SEARCH_URL = "https://www.google.com/search"

TBM = {"web": "", "images": "isch", "news": "nws", "videos": "vid",
       "shopping": "shop", "places": "lmap"}

# Self-healing selector lists: try each in order until one yields.
RESULT_BLOCKS = [
    "div#search div.MjjYud",
    "div#search div.g",
    "div#rso div.g",
    "div#main div.g",
    "div.g",
]
TITLE_SELECTORS = ["h3", "h3 a", "div[role='heading']"]
SNIPPET_SELECTORS = [
    "div.VwiC3b",
    "div[data-sncf] div[data-sncf]",
    "div.IsZvec",
    ".VwiC3b",
    "div.yDYNvb",
]
DISPLAYED_LINK_SELECTORS = ["cite", ".VdgySc", ".yuRUbf cite", "span.URL"]

DATE_RE = re.compile(
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, \d{4}"
    r"|\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}"
    r"|\d{4}-\d{1,2}-\d{1,2})",
    re.I,
)


class GoogleEngine(BaseEngine):
    name = "google"
    types = ["web", "images", "news", "videos", "shopping", "places"]
    parser_version = "1.1"

    async def search(self, req: SearchRequest) -> SearchResponse:
        if req.type == "images":
            return await self._images(req)
        if req.type in ("news", "videos", "shopping", "places"):
            return await self._tbm(req)
        return await self._web(req)

    def _params(self, req: SearchRequest) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "q": req.q,
            "hl": req.hl or req.language,
            "gl": req.gl or req.location,
            "num": str(min(req.num, 100)),
            "start": str(req.start),
            "safe": {"off": "off", "medium": "active", "high": "active"}.get(req.safe, "off"),
        }
        if req.time_range != "any":
            params["tbs"] = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m",
                             "year": "qdr:y"}.get(req.time_range)
        return params

    async def _web(self, req: SearchRequest) -> SearchResponse:
        params = self._params(req)
        html, _ = await self.client.get(
            GOOGLE_SEARCH_URL, engine=self.name, params=params,
            extra_headers={"Referer": "https://www.google.com/"},
        )
        tree = parse_html(html)

        blocks = self._first_many(tree, RESULT_BLOCKS)
        organic: List[OrganicResult] = []
        position = req.start
        for block in blocks:
            parsed = self._parse_result(block, position + 1)
            if parsed is not None and parsed.link and parsed.title:
                organic.append(parsed)
                position += 1
            if len(organic) >= req.num:
                break

        if not organic:
            body_text = tree.body.text() if tree.body else ""
            if "did not match any" in body_text.lower():
                return SearchResponse(
                    search_metadata=self.metadata(req, engine=self.name, cached=False,
                                                  total_time=0.0),
                    search_information={"query_displayed": req.q,
                                        "organic_results_count": 0},
                    organic_results=[],
                )
            raise EngineParseError(
                "google returned a page with no parseable organic results "
                "(possible JS/consent wall)",
                details={"engine": self.name, "page_bytes": len(html)},
            )

        knowledge_graph = self._knowledge_graph(tree)
        related_questions = self._related_questions(tree)
        related_searches = self._related_searches(tree)
        pagination = self._pagination(tree, req)

        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False,
                                          total_time=0.0),
            search_information={
                "total_results": self._total_results(tree),
                "query_displayed": req.q,
                "organic_results_count": len(organic),
            },
            organic_results=organic,
            knowledge_graph=knowledge_graph,
            related_questions=related_questions,
            related_searches=related_searches,
            pagination=pagination,
        )

    def _parse_result(self, block: Node, position: int) -> Optional[OrganicResult]:
        link_el = None
        for sel in TITLE_SELECTORS:
            el = block.css_first(sel)
            if el is not None:
                link_el = el if el.tag == "a" else el.css_first("a")
                if link_el is not None:
                    break
        if link_el is None:
            return None

        raw_link = link_el.attributes.get("href") or ""
        link = self._clean_link(raw_link)
        if not link:
            return None

        title = ""
        for sel in TITLE_SELECTORS:
            el = block.css_first(sel)
            if el is not None:
                title = el.text(strip=True)
                if title:
                    break

        snippet = ""
        for sel in SNIPPET_SELECTORS:
            el = block.css_first(sel)
            if el is not None and el.text(strip=True):
                snippet = el.text(strip=True)
                break

        displayed = ""
        for sel in DISPLAYED_LINK_SELECTORS:
            el = block.css_first(sel)
            if el is not None and el.text(strip=True):
                displayed = el.text(strip=True)
                break

        date = None
        dm = DATE_RE.search(snippet)
        if dm:
            date = dm.group(1)

        sitelinks: list[dict[str, Any]] = []
        for a in block.css("div.d5lMud li a, div[jsname] a"):
            text = a.text(strip=True)
            if text and len(sitelinks) < 6:
                sitelinks.append({"title": text,
                                  "link": self._clean_link(a.attributes.get("href") or "")})

        source = self._source(link)
        return OrganicResult(
            position=position,
            title=title,
            link=link,
            displayed_link=displayed or source or "",
            snippet=snippet,
            date=date,
            source=source,
            sitelinks=sitelinks,
        )

    # ---------------------------------------------------------------- images
    async def _images(self, req: SearchRequest) -> SearchResponse:
        params = {**self._params(req), "tbm": "isch"}
        html, _ = await self.client.get(GOOGLE_SEARCH_URL, engine=self.name, params=params)
        tree = parse_html(html)
        organic: List[OrganicResult] = []
        seen = set()
        for idx, a in enumerate(tree.css("a.wXeWr, a[href*='imgres']"), start=1):
            href = a.attributes.get("href") or ""
            m = re.search(r"[?&]imgurl=([^&]+)", href)
            link = ""
            if m:
                from urllib.parse import unquote
                link = unquote(m.group(1))
            if not link or link in seen:
                continue
            seen.add(link)
            organic.append(OrganicResult(
                position=req.start + idx,
                title=(a.attributes.get("aria-label") or ""),
                link=link,
                source=self._source(link),
            ))
            if len(organic) >= req.num:
                break
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(organic)},
            organic_results=organic,
        )

    # --------------------------------------------- tbm (news/videos/shopping)
    async def _tbm(self, req: SearchRequest) -> SearchResponse:
        params = {**self._params(req), "tbm": TBM.get(req.type, "")}
        html, _ = await self.client.get(GOOGLE_SEARCH_URL, engine=self.name, params=params)
        tree = parse_html(html)
        organic: List[OrganicResult] = []
        blocks = self._first_many(tree, RESULT_BLOCKS)
        position = req.start
        for block in blocks:
            parsed = self._parse_result(block, position + 1)
            if parsed is not None:
                parsed.source_type = req.type
                organic.append(parsed)
                position += 1
            if len(organic) >= req.num:
                break
        if not organic:
            raise EngineParseError(
                f"google returned no parseable {req.type} results (possible JS/consent wall)",
                details={"engine": self.name, "page_bytes": len(html)},
            )
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(organic)},
            organic_results=organic,
        )

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _first_many(tree: HTMLParser, selectors: List[str]) -> List[Node]:
        for sel in selectors:
            nodes = tree.css(sel)
            if nodes:
                return nodes
        return []

    @staticmethod
    def _clean_link(href: str) -> str:
        from urllib.parse import unquote, urlparse
        href = href.strip()
        if href.startswith("/url?"):
            m = re.search(r"[?&]q=([^&]+)", href)
            if m:
                return unquote(m.group(1))
            return ""
        if href.startswith("/search?"):
            return ""
        parsed = urlparse(href)
        if parsed.netloc == "google.com" or parsed.netloc.endswith(".google.com"):
            return ""
        if href.startswith("http"):
            return href
        return ""

    @staticmethod
    def _source(link: str) -> Optional[str]:
        from urllib.parse import urlparse
        try:
            return urlparse(link).netloc or None
        except Exception:
            return None

    @staticmethod
    def _total_results(tree: HTMLParser) -> Optional[int]:
        el = tree.css_first("#result-stats")
        if not el:
            return None
        text = el.text().replace(",", "")
        m = re.search(r"([\d.]+)\s*(?:million|billion|thousand)?\s*results", text, re.I)
        if m:
            num = float(m.group(1))
            if "million" in text:
                num *= 1_000_000
            elif "billion" in text:
                num *= 1_000_000_000
            elif "thousand" in text:
                num *= 1000
            return int(num)
        return None

    @staticmethod
    def _knowledge_graph(tree: HTMLParser) -> Dict[str, Any]:
        kg = tree.css_first("div.kp-wholepage")
        if not kg:
            return {}
        title_el = kg.css_first("h2, span[data-attrid='title'], div.kp-header h2")
        desc_el = kg.css_first("div.kno-rdesc span, div[data-attrid='description'] span")
        result: Dict[str, Any] = {}
        if title_el:
            result["title"] = title_el.text(strip=True)
        if desc_el:
            result["description"] = desc_el.text(strip=True)
        for tr in kg.css("table tr, div.rhscol tr"):
            cells = [c.text(strip=True) for c in tr.css("th, td, div") if c.text(strip=True)]
            if len(cells) >= 2:
                result.setdefault("attributes", []).append(
                    {"key": cells[0], "value": cells[1]})
        return result

    @staticmethod
    def _related_questions(tree: HTMLParser) -> List[Dict[str, Any]]:
        questions = []
        for el in tree.css("div.XpertTb, div[jsname='yZaxVb'] h3, div.related-question-pair"):
            text = el.text(strip=True)
            if text and text.endswith("?"):
                questions.append({"question": text})
        return questions

    @staticmethod
    def _related_searches(tree: HTMLParser) -> List[Dict[str, Any]]:
        results = []
        for a in tree.css("div.k8XOCe a, div.s75CSd a, a.k8XOCe"):
            text = a.text(strip=True)
            if text:
                results.append({"text": text,
                                "link": GoogleEngine._clean_link(a.attributes.get("href") or "")})
        return results

    @staticmethod
    def _pagination(tree: HTMLParser, req: SearchRequest) -> Dict[str, Any]:
        page_nums = []
        for a in tree.css("td a.fl, a[aria-label*='Page']"):
            try:
                n = int(a.text().strip())
                page_nums.append(n)
            except ValueError:
                continue
        current = (req.start // max(req.num, 1)) + 1
        return {
            "current": current,
            "pages": sorted(set(page_nums)),
            "next_start": req.start + req.num if page_nums else None,
            "has_next": bool(page_nums),
        }

# Register this engine with the global registry on import.
from jiro.scraping.engines import registry  # noqa: E402

registry.register(GoogleEngine)
