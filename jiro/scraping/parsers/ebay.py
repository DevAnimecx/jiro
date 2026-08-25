"""eBay search parser — web / shopping.

eBay product results are in ``li.s-item`` blocks with title, price, shipping,
condition, and seller info.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from selectolax.parser import HTMLParser, Node

from jiro.errors import EngineParseError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine
from jiro.scraping.client import parse_html

EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"


class EbayEngine(BaseEngine):
    name = "ebay"
    types = ["web", "shopping"]
    parser_version = "1.0"

    async def search(self, req: SearchRequest) -> SearchResponse:
        params: Dict[str, Any] = {
            "_nkw": req.q,
            "_sacat": "0",
            "LH_BIN": "1",  # Buy It Now
            "_ipg": str(min(req.num, 240)),
        }
        if req.time_range == "day":
            params["LH_PrefLoc"] = "1"
            params["_sop"] = "10"  # newly listed
        elif req.time_range == "week":
            params["_sop"] = "10"
        elif req.time_range == "month":
            params["LH_ItemCondition"] = "3"

        html, _ = await self.client.get(
            EBAY_SEARCH_URL, engine=self.name, params=params,
            extra_headers={
                "Referer": "https://www.ebay.com/",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        tree = parse_html(html)
        blocks = tree.css("li.s-item")
        results: List[OrganicResult] = []
        for idx, block in enumerate(blocks):
            if len(results) >= req.num:
                break
            parsed = self._parse_item(block, req.start + len(results) + 1)
            if parsed:
                results.append(parsed)

        if not results:
            raise EngineParseError(
                "ebay returned a page with no parseable product results",
                details={"engine": self.name, "page_bytes": len(html)},
            )

        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"query_displayed": req.q,
                                "organic_results_count": len(results)},
            organic_results=results,
        )

    def _parse_item(self, block: Node, position: int) -> Optional[OrganicResult]:
        title_el = block.css_first("div.s-item__title span, a.s-item__link span")
        title = title_el.text(strip=True) if title_el else ""
        if not title or title.lower() == "shop on ebay":
            return None

        link_el = block.css_first("a.s-item__link")
        link = (link_el.attributes.get("href") or "").strip() if link_el else ""
        if not link:
            return None

        price_el = block.css_first("span.s-item__price")
        price = price_el.text(strip=True) if price_el else None

        shipping_el = block.css_first("span.s-item__shipping, span.s-item__freeXDays")
        shipping = shipping_el.text(strip=True) if shipping_el else None

        condition_el = block.css_first("span[class='SECONDARY_INFO']")
        condition = condition_el.text(strip=True) if condition_el else None

        seller_el = block.css_first("span.s-item__seller-info-text")
        seller = seller_el.text(strip=True) if seller_el else None

        img_el = block.css_first("img.s-item__image-img")
        thumbnail = (img_el.attributes.get("src") or "") if img_el else None

        snippet_parts = []
        if condition:
            snippet_parts.append(condition)
        if shipping:
            snippet_parts.append(shipping)
        if seller:
            snippet_parts.append(f"Seller: {seller}")

        return OrganicResult(
            position=position,
            title=title,
            link=link,
            snippet=" | ".join(snippet_parts)[:500],
            price=price,
            condition=condition,
            seller=seller,
            shipping=shipping,
            thumbnail=thumbnail,
            source="ebay.com",
        )


# Register with the global registry on import.
from jiro.scraping.engines import registry  # noqa: E402

registry.register(EbayEngine)
