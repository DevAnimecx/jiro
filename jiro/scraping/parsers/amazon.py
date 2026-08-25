"""Amazon search parser — web / shopping.

Amazon product results are in ``div[data-component-type='s-search-result']``
blocks with ASIN, title, price, rating, and Prime badge.
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

AMAZON_SEARCH_URL = "https://www.amazon.com/s"


class AmazonEngine(BaseEngine):
    name = "amazon"
    types = ["web", "shopping"]
    parser_version = "1.0"

    async def search(self, req: SearchRequest) -> SearchResponse:
        params: Dict[str, Any] = {
            "k": req.q,
            "ref": "sr_sb_noss",
        }
        if req.num:
            params["rh"] = f"n:1850115011"  # default department
        if req.time_range == "month":
            params["rh"] = "p_n_date_band_type-browse:1260830011"
        elif req.time_range == "week":
            params["rh"] = "p_n_date_band_type-browse:1260831011"

        html, _ = await self.client.get(
            AMAZON_SEARCH_URL, engine=self.name, params=params,
            extra_headers={
                "Referer": "https://www.amazon.com/",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        tree = parse_html(html)
        blocks = tree.css("div[data-component-type='s-search-result']")
        results: List[OrganicResult] = []
        for idx, block in enumerate(blocks[: req.num]):
            parsed = self._parse_product(block, req.start + idx + 1)
            if parsed:
                results.append(parsed)

        if not results:
            body_text = tree.body.text() if tree.body else ""
            if "captcha" in body_text.lower() or "robot" in body_text.lower():
                raise EngineParseError(
                    "amazon returned a CAPTCHA page",
                    details={"engine": self.name, "page_bytes": len(html)},
                )
            # Empty results page
            return SearchResponse(
                search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
                search_information={"query_displayed": req.q, "organic_results_count": 0},
                organic_results=[],
            )

        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(results)},
            organic_results=results,
        )

    def _parse_product(self, block: Node, position: int) -> Optional[OrganicResult]:
        asin = block.attributes.get("data-asin", "").strip()
        if not asin:
            return None

        title_el = block.css_first("h2 a span, h2 span")
        title = title_el.text(strip=True) if title_el else ""
        if not title:
            return None

        link_el = block.css_first("h2 a[href]")
        raw_link = (link_el.attributes.get("href") or "") if link_el else ""
        link = raw_link
        if link and not link.startswith("http"):
            link = "https://www.amazon.com" + link

        price_whole = block.css_first("span.a-price-whole")
        price_frac = block.css_first("span.a-price-fraction")
        price_symbol = block.css_first("span.a-price-symbol")
        price = ""
        if price_whole:
            price = price_symbol.text(strip=True) if price_symbol else "$"
            price += price_whole.text(strip=True).rstrip(".")
            if price_frac:
                price += "." + price_frac.text(strip=True)

        rating_el = block.css_first("span.a-icon-alt")
        rating = rating_el.text(strip=True) if rating_el else None

        reviews_el = block.css_first("span.a-size-base.s-underline-text")
        reviews = reviews_el.text(strip=True) if reviews_el else None

        img_el = block.css_first("img.s-image")
        thumbnail = (img_el.attributes.get("src") or "") if img_el else None

        prime_el = block.css_first("span.a-icon-prime")
        is_prime = prime_el is not None

        snippet_parts = []
        features_el = block.css_first("div.a-section.a-spacing-small.a-spacing-top-micro")
        if features_el:
            snippet_parts.append(features_el.text(strip=True)[:300])

        return OrganicResult(
            position=position,
            title=title,
            link=link,
            snippet=" ".join(snippet_parts),
            price=price or None,
            rating=rating,
            reviews=reviews,
            thumbnail=thumbnail,
            asin=asin,
            source="amazon.com",
            prime=is_prime if is_prime else None,
        )


# Register with the global registry on import.
from jiro.scraping.engines import registry  # noqa: E402

registry.register(AmazonEngine)
