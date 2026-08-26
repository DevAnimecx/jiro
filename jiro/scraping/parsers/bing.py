"""Bing parser — web / images / news.

Web results live in ``li.b_algo`` blocks; links are wrapped in
``bing.com/ck/a`` redirects that are decoded back to the real URL.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, Dict, List, Optional

from selectolax.parser import HTMLParser, Node

from jiro.errors import EngineParseError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine
from jiro.scraping.client import normalize_url, parse_html

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?"
DATE_RE = re.compile(
    r"("
    r"(?:" + MONTHS + r")\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:" + MONTHS + r"),?\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r")",
    re.I,
)

BING_SEARCH_URL = "https://www.bing.com/search"
BING_IMAGE_URL = "https://www.bing.com/images/search"
BING_NEWS_URL = "https://www.bing.com/news/search"


class BingEngine(BaseEngine):
    name = "bing"
    types = ["web", "images", "news"]
    parser_version = "1.1"

    async def search(self, req: SearchRequest) -> SearchResponse:
        if req.type == "images":
            return await self._images(req)
        if req.type == "news":
            return await self._news(req)
        if req.type == "videos":
            return await self._videos(req)
        return await self._web(req)

    # ------------------------------------------------------------------ web
    async def _web(self, req: SearchRequest) -> SearchResponse:
        params: Dict[str, Any] = {
            "q": req.q,
            "count": str(min(req.num, 50)),
            "first": str(req.start + 1),
            "mkt": self._market(req.language, req.location),
            "setlang": req.language,
            "cc": req.location,
        }
        if req.time_range != "any":
            params["qft"] = self._time_filter(req.time_range)

        html, _ = await self.client.get(
            BING_SEARCH_URL, engine=self.name, params=params,
            extra_headers={"Referer": "https://www.bing.com/"},
        )
        tree = parse_html(html)
        results = tree.css("li.b_algo")

        organic: List[OrganicResult] = []
        for idx, block in enumerate(results[: req.num]):
            organic.append(self._parse_result(block, req.start + idx + 1))

        if not organic:
            body_text = tree.body.text() if tree.body else ""
            if "no results" in body_text.lower() or "did not match any" in body_text.lower():
                # Legitimate empty result page — not a block.
                return SearchResponse(
                    search_metadata=self.metadata(req, engine=self.name, cached=False,
                                                  total_time=0.0),
                    search_information={"query_displayed": req.q,
                                        "organic_results_count": 0},
                    organic_results=[],
                )
            raise EngineParseError(
                "bing returned a page with no parseable organic results "
                "(possible bot-wall)",
                details={"engine": self.name, "page_bytes": len(html)},
            )

        related = [
            {"text": a.text().strip()}
            for a in tree.css("#b_related_searches a, .b_rs a")
            if a.text().strip()
        ]
        # related searches may be in a dedicated tile list
        if not related:
            for a in tree.css("li.b_rs a"):
                text = a.text().strip()
                if text and text.lower() != req.q.lower():
                    related.append({"text": text})

        pagination = self._pagination(tree, req)
        total_est = self._total_est(tree)

        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False,
                                          total_time=0.0),
            search_information={
                "total_results": total_est,
                "query_displayed": req.q,
                "organic_results_count": len(organic),
            },
            organic_results=organic,
            related_searches=related,
            pagination=pagination,
        )

    def _parse_result(self, block: Node, position: int) -> OrganicResult:
        title_el = block.css_first("h2")
        link_el = title_el.css_first("a") if title_el else None
        title = title_el.text(strip=True) if title_el else ""
        raw_link = (link_el.attributes.get("href") or "") if link_el else ""
        link = normalize_url(raw_link)

        cite_el = block.css_first("cite")
        displayed = cite_el.text(strip=True) if cite_el else ""

        snippet_el = block.css_first(".b_caption p, .b_lineclamp, p.b_lineclamp4")
        snippet = snippet_el.text(strip=True) if snippet_el else ""

        date: Optional[str] = None
        date_match = DATE_RE.search(snippet)
        if date_match:
            date = date_match.group(1)

        sitelinks: List[Dict[str, Any]] = []
        for slink in block.css("ul.b_sitelink a, .b_attribution a"):
            text = slink.text(strip=True)
            if text and len(sitelinks) < 6:
                sitelinks.append({"title": text,
                                  "link": normalize_url(slink.attributes.get("href") or "")})

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
        params = {"q": req.q, "form": "HDRSC2", "first": str(req.start)}
        html, _ = await self.client.get(BING_IMAGE_URL, engine=self.name, params=params)
        tree = parse_html(html)
        organic: List[OrganicResult] = []
        seen = set()
        for idx, a in enumerate(tree.css("a.iusc"), start=1):
            m = a.attributes.get("m")
            if not m:
                continue
            try:
                data = json.loads(m)
            except Exception:
                continue
            url = data.get("murl") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            organic.append(OrganicResult(
                position=req.start + idx,
                title=data.get("t", ""),
                link=url,
                thumbnail=data.get("turl", ""),
                dimensions=f"{data.get('w', '')}x{data.get('h', '')}"
                if data.get("w") and data.get("h") else None,
                source=self._source(data.get("purl") or url),
            ))
            if len(organic) >= req.num:
                break
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(organic)},
            organic_results=organic,
        )

    # ------------------------------------------------------------------ news
    async def _news(self, req: SearchRequest) -> SearchResponse:
        params: Dict[str, Any] = {"q": req.q, "format": "rss", "setmkt": self._market(
            req.language, req.location)}
        html, response = await self.client.get(BING_NEWS_URL, engine=self.name, params=params)
        body = response.text
        tree = parse_html(body)
        organic: List[OrganicResult] = []
        for idx, item in enumerate(tree.css("item"), start=1):
            title = item.css_first("title")
            link = item.css_first("link")
            desc = item.css_first("description")
            pub = item.css_first("pubDate")
            snippet = desc.text() if desc else ""
            snippet = re.sub(r"\s+", " ", snippet).strip()
            organic.append(OrganicResult(
                position=idx,
                title=title.text() if title else "",
                link=link.text() if link else "",
                snippet=snippet[:500],
                date=(pub.text()[:25] if pub else None),
                source_type="news",
            ))
            if len(organic) >= req.num:
                break
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(organic)},
            organic_results=organic,
        )

    # --------------------------------------------------------------- videos
    async def _videos(self, req: SearchRequest) -> SearchResponse:
        params: Dict[str, Any] = {
            "q": req.q, "mkt": self._market(req.language, req.location),
        }
        html, _ = await self.client.get(
            "https://www.bing.com/videos/search", engine=self.name, params=params,
            extra_headers={"Referer": "https://www.bing.com/"},
        )
        tree = parse_html(html)
        organic: List[OrganicResult] = []
        seen = set()
        for idx, block in enumerate(tree.css("div.mc_vtvc"), start=1):
            link_el = block.css_first("a.mc_vtvc_link")
            aria = (link_el.attributes.get("aria-label") or "") if link_el else ""
            title = aria.split("· Duration")[0].strip()
            title = re.sub(r"\s+from (YouTube|Vimeo|Dailymotion)\s*$", "", title, flags=re.I)
            if not title:
                continue
            # real URL from ourl attr or mmeta JSON
            ourl = ""
            con = block.css_first("div[ourl]")
            if con:
                ourl = (con.attributes.get("ourl") or "")
            if not ourl:
                mmeta = (block.attributes.get("mmeta") or "")
                m = re.search(r"murl\\?\\?&quot;:&quot;([^&]+)&quot;", mmeta) or \
                    re.search(r"murl\\?\":\\?\"([^\"]+)\\\?", mmeta)
                if m:
                    ourl = html_lib.unescape(m.group(1))
            if not ourl or ourl in seen:
                continue
            seen.add(ourl)
            duration = self._video_duration(aria)
            thumb = self._video_thumb(block)
            organic.append(OrganicResult(
                position=req.start + idx,
                title=title,
                link=ourl,
                duration=duration,
                thumbnail=thumb,
                source=self._source(ourl),
                source_type="videos",
                snippet=aria[-220:],
            ))
            if len(organic) >= req.num:
                break
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(organic)},
            organic_results=organic,
        )

    @staticmethod
    def _video_duration(aria: str) -> Optional[str]:
        m = re.search(r"Duration:\s+([\d\s\w]*(?:minutes?|hours?|seconds?))", aria)
        return m.group(1).strip() if m else None

    @staticmethod
    def _video_thumb(block: Node) -> Optional[str]:
        img = block.css_first("img[data-src-hq], img[src]")
        if img is None:
            return None
        src = img.attributes.get("data-src-hq") or img.attributes.get("src") or ""
        if not src.startswith("http"):
            src = "https:" + src
        return src or None

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _market(language: str, location: str) -> str:
        return f"{language}-{location.upper()}" if language and location else "en-US"

    @staticmethod
    def _time_filter(time_range: str) -> str:
        mapping = {"day": "interval='7'", "week": "interval='30'",
                   "month": "interval='180'", "year": "interval='730'"}
        return mapping.get(time_range, "")

    @staticmethod
    def _source(link: str) -> Optional[str]:
        try:
            from urllib.parse import urlparse
            return urlparse(link).netloc or None
        except Exception:
            return None

    @staticmethod
    def _total_est(tree: HTMLParser) -> Optional[int]:
        el = tree.css_first(".b_count, .b_ans .b_count")
        if not el:
            return None
        text = el.text().replace(",", "")
        m = re.search(r"(\d+)\s*(?:results|RESULT|result)", text, re.I)
        return int(m.group(1)) if m else None

    @staticmethod
    def _pagination(tree: HTMLParser, req: SearchRequest) -> Dict[str, Any]:
        links = tree.css("nav a.sb_pagN, a.sb_pagN")
        pages = []
        for a in links:
            try:
                pages.append(int(a.text().strip()))
            except ValueError:
                continue
        current = (req.start // max(req.num, 1)) + 1
        return {
            "current": current,
            "pages": sorted(set(pages)),
            "next_start": req.start + req.num if links else None,
            "has_next": bool(links),
        }

# Register this engine with the global registry on import.
from jiro.scraping.engines import registry  # noqa: E402

registry.register(BingEngine)
