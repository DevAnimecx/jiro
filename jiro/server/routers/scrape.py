"""Scrape endpoints: POST /scrape, POST /scrape/batch."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request

from jiro.ai.llm import LLM
from jiro.auth import AuthContext
from jiro.cache import CacheManager
from jiro.extract import scrape_url
from jiro.models import BatchScrapeItem, ScrapeRequest, ScrapeResponse
from jiro.server.deps import (
    get_auth_context,
    get_cache,
    get_client,
    get_llm,
    record_usage,
    require_feature,
)

router = APIRouter(tags=["scrape"])


@router.post("/scrape", response_model=ScrapeResponse, summary="Scrape a URL")
async def scrape(
    request: Request,
    body: ScrapeRequest,
    ctx: AuthContext = Depends(require_feature("basic_scrape")),
    client: Any = Depends(get_client),
    cache: CacheManager = Depends(get_cache),
    llm: LLM = Depends(get_llm),
) -> Dict[str, Any]:
    request.state.auth = ctx
    started = time.perf_counter()

    if not body.url.startswith(("http://", "https://")):
        from jiro.errors import ScrapeError
        raise ScrapeError("url must start with http:// or https://", status_code=422)

    cache_key = cache.make_key("scrape", body.url, body.format, body.include_metadata)
    cached = await cache.get(cache_key)
    payload: Dict[str, Any]
    raw_html: str = ""
    if cached is not None:
        payload = dict(cached)
        payload["cached"] = True
    else:
        # Fetch once; reuse the HTML for the custom recipe if requested.
        html, response = await client.get(body.url, engine="scrape", raw=True)
        raw_html = html
        payload = await scrape_url(body.url, client, fmt=body.format,
                                   include_metadata=body.include_metadata,
                                   include_structured=body.include_structured,
                                   html=html, response=response)
        payload["cached"] = False
        await cache.put(cache_key, payload, engine="scrape", kind="scrape")

    if body.extract_schema:
        payload["extracted"] = await _extract_with_schema(
            llm, body.url, payload, body.extract_schema
        )

    if body.recipe:
        from jiro.recipes import RecipeError, apply_recipe
        try:
            payload["recipe_result"] = apply_recipe(
                raw_html or _raw_html(payload), body.recipe, url=body.url
            )
        except RecipeError as exc:
            payload["recipe_error"] = exc.message

    payload["time_taken"] = round(time.perf_counter() - started, 3)
    await record_usage(request, endpoint="/scrape", status=200,
                       latency_ms=(time.perf_counter() - started) * 1000,
                       query=body.url, cached=payload.get("cached", False))
    return payload


@router.post("/scrape/batch", summary="Batch scrape multiple URLs (limited)")
async def scrape_batch(
    request: Request,
    items: List[BatchScrapeItem],
    ctx: AuthContext = Depends(require_feature("smart_search")),
    client: Any = Depends(get_client),
    cache: CacheManager = Depends(get_cache),
    llm: LLM = Depends(get_llm),
) -> Dict[str, Any]:
    request.state.auth = ctx
    if len(items) > 50:
        return {"error": "batch limited to 50 URLs", "results": []}
    started = time.perf_counter()

    semaphore = asyncio.Semaphore(10)

    async def _scrape_one(item: "BatchScrapeItem") -> Dict[str, Any]:
        async with semaphore:
            try:
                payload = await scrape_url(item.url, client, fmt=item.format,
                                           include_metadata=True)
                if item.extract_schema:
                    payload["extracted"] = await _extract_with_schema(
                        llm, item.url, payload, item.extract_schema
                    )
                payload["time_taken"] = round(time.perf_counter() - started, 3)
                return payload
            except Exception as exc:
                return {"url": item.url, "error": str(exc)}

    results = await asyncio.gather(*[_scrape_one(item) for item in items])
    await record_usage(request, endpoint="/scrape/batch", status=200,
                       latency_ms=(time.perf_counter() - started) * 1000)
    return {"count": len(results), "results": results}


def _raw_html(payload: Dict[str, Any]) -> str:
    """Recover raw HTML from a scrape payload (fetches again if not present)."""
    content = payload.get("content")
    if isinstance(content, dict) and content.get("html"):
        return content["html"]
    if isinstance(content, str) and content.startswith("<"):
        return content
    return ""


async def _extract_with_schema(llm: LLM, url: str, payload: Dict[str, Any],
                               schema: Dict[str, Any]) -> Dict[str, Any]:
    """LLM-assisted structured extraction; falls back to naive mapping."""
    text = (payload.get("content") or "")
    if isinstance(text, dict):
        text = text.get("markdown") or text.get("text") or ""
    text = text[:8000]

    if llm.available:
        try:
            fields = ", ".join(f"{k} ({v})" for k, v in schema.items())
            system = ("Extract the requested fields from the page content. Return ONLY a "
                      "JSON object with exactly those keys. Use null when a field is absent.")
            user = (f"Page URL: {url}\n\nFields to extract: {fields}\n\n"
                    f"Page content:\n{text}")
            answer = await llm.complete([{"role": "user", "content": user}], system=system)
            import json as _json
            answer = answer.strip()
            if answer.startswith("```"):
                answer = answer.split("```")[1]
                if answer.startswith("json"):
                    answer = answer[4:]
            return _json.loads(answer)
        except Exception:
            pass
    return _naive_extract(text, schema)


def _naive_extract(text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """Dependency-free fallback: first line for 'title', first 200 chars for others."""
    import re
    result: Dict[str, Any] = {}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for key, kind in schema.items():
        low = key.lower()
        if kind in ("date",) and (m := re.search(r"\d{4}-\d{2}-\d{2}", text)):
            result[key] = m.group(0)
        elif low in ("title", "heading") and lines:
            result[key] = lines[0][:200]
        elif kind == "list":
            items = [line for line in lines if line.startswith(("-", "*", "1."))][:10]
            result[key] = items
        else:
            result[key] = text[:200] if text else None
    return result
