"""YouTube search parser — web / videos.

YouTube search results live in a ``<script>`` tag containing JSON data.
We extract video metadata from ``ytInitialData`` or from the HTML structure.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from jiro.errors import EngineParseError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine
from jiro.scraping.client import parse_html

YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"


class YouTubeEngine(BaseEngine):
    name = "youtube"
    types = ["videos"]
    parser_version = "1.0"

    async def search(self, req: SearchRequest) -> SearchResponse:
        params: Dict[str, Any] = {
            "search_query": req.q,
            "sp": self._sp_param(req),
        }
        html, _ = await self.client.get(
            YOUTUBE_SEARCH_URL, engine=self.name, params=params,
            extra_headers={
                "Referer": "https://www.youtube.com/",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        results = self._parse_yt_initial_data(html, req)
        if not results:
            results = self._parse_html_fallback(html, req)
        if not results:
            raise EngineParseError(
                "youtube returned a page with no parseable video results",
                details={"engine": self.name, "page_bytes": len(html)},
            )
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(results)},
            organic_results=results,
        )

    def _parse_yt_initial_data(self, html: str, req: SearchRequest) -> List[OrganicResult]:
        """Extract results from ytInitialData JSON embedded in the page."""
        # Find the script tag containing ytInitialData
        script_match = re.search(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
        if not script_match:
            return []
        script = script_match.group(1)
        # Find the JSON assignment
        json_match = re.search(r"var\s+ytInitialData\s*=\s*(\{.+\})\s*;", script, re.DOTALL)
        if not json_match:
            return []
        try:
            data = json.loads(json_match.group(1))
        except Exception:
            return []
        videos: list[OrganicResult] = []
        contents = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )
        for section in contents:
            items = (
                section.get("itemSectionRenderer", {})
                .get("contents", [])
            )
            for item in items:
                vr = item.get("videoRenderer")
                if not vr:
                    continue
                video_id = vr.get("videoId", "")
                title_runs = vr.get("title", {}).get("runs", [])
                title = "".join(r.get("text", "") for r in title_runs)
                if not title or not video_id:
                    continue
                link = f"https://www.youtube.com/watch?v={video_id}"
                channel_runs = vr.get("ownerText", {}).get("runs", [])
                channel = "".join(r.get("text", "") for r in channel_runs)
                view_text = ""
                for run in vr.get("viewCountText", {}).get("simpleText", "").split():
                    view_text += run + " "
                view_text = vr.get("viewCountText", {}).get("simpleText", "")
                published = vr.get("publishedTimeText", {}).get("simpleText", "")
                duration_text = vr.get("lengthText", {}).get("simpleText", "")
                snippet_parts = []
                if published:
                    snippet_parts.append(published)
                if view_text:
                    snippet_parts.append(view_text)
                desc_runs = vr.get("detailedMetadataSnippets", [{}])
                if desc_runs:
                    snippet_text = desc_runs[0].get("snippetText", {}).get("runs", [])
                    snippet_parts.append("".join(r.get("text", "") for r in snippet_text))
                thumb = ""
                thumbs = vr.get("thumbnail", {}).get("thumbnails", [])
                if thumbs:
                    thumb = thumbs[-1].get("url", "")
                if thumb and thumb.startswith("//"):
                    thumb = "https:" + thumb
                videos.append(OrganicResult(
                    position=req.start + len(videos) + 1,
                    title=title,
                    link=link,
                    snippet=" ".join(snippet_parts)[:500],
                    thumbnail=thumb,
                    duration=duration_text or None,
                    channel=channel or None,
                    views=view_text or None,
                    date=published or None,
                    source="youtube.com",
                    source_type="videos",
                ))
                if len(videos) >= req.num:
                    break
        return videos

    def _parse_html_fallback(self, html: str, req: SearchRequest) -> List[OrganicResult]:
        """Fallback: parse video links directly from HTML."""
        tree = parse_html(html)
        videos: list[OrganicResult] = []
        seen = set()
        for a in tree.css("a#video-title, a.yt-simple-endpoint[href*='/watch']"):
            href = (a.attributes.get("href") or "").strip()
            if not href or "/watch" not in href or href in seen:
                continue
            seen.add(href)
            title = a.text(strip=True)
            if not title:
                continue
            if href.startswith("/"):
                href = "https://www.youtube.com" + href
            videos.append(OrganicResult(
                position=req.start + len(videos) + 1,
                title=title,
                link=href,
                source="youtube.com",
                source_type="videos",
            ))
            if len(videos) >= req.num:
                break
        return videos

    @staticmethod
    def _sp_param(req: SearchRequest) -> str:
        """Build the sp parameter for filtering/sorting."""
        # sp is a protobuf-encoded filter; we use common values.
        if req.time_range == "day":
            return "EgIIAQ%3D%3D"  # Today
        if req.time_range == "week":
            return "EgIIAw%3D%3D"  # This week
        if req.time_range == "month":
            return "EgIIBA%3D%3D"  # This month
        if req.time_range == "year":
            return "EgIIBQ%3D%3D"  # This year
        return ""  # No filter


# Register with the global registry on import.
from jiro.scraping.engines import registry  # noqa: E402

registry.register(YouTubeEngine)
