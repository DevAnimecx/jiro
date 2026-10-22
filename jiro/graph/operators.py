"""Network operators used by the query graph.

Operators are intentionally small adapters. They return plain dictionaries so
node results can be cached, replayed, and compared without Pydantic coupling.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.parse import urlsplit, urlunsplit

from jiro.errors import JiroError
from jiro.extract import scrape_url
from jiro.graph.executor import GraphContext, Operator
from jiro.graph.model import QueryNode
from jiro.models import SearchRequest


class OperatorError(JiroError):
    code = "graph_operator_error"
    status_code = 502


def _clean_query(template: str, context: GraphContext) -> str:
    query = template.strip()
    if "{query}" in query:
        query = query.replace("{query}", str(context.extra.get("query") or ""))
    return " ".join(query.split())[:500]


def _normalize_link(link: str) -> str:
    try:
        parts = urlsplit(link.strip())
    except ValueError:
        return link.strip()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


async def search(node: QueryNode, inputs: Dict[str, Any], context: GraphContext) -> Dict[str, Any]:
    query = _clean_query(node.query_template, context)
    if not query:
        raise OperatorError("SEARCH node produced an empty query")

    policy = context.extra.get("policy") or {}
    engine = node.engine_policy[0] if node.engine_policy else policy.get("engine", "auto")
    request = SearchRequest(
        q=query,
        engine=engine or "auto",
        type=str(policy.get("type") or "web"),
        num=int(policy.get("num") or 5),
        location=str(policy.get("location") or "us"),
        language=str(policy.get("language") or "en"),
        safe=str(policy.get("safe") or "off"),
    )
    response = await context.orchestrator.search(request)
    results = [r.model_dump(exclude_none=True) for r in response.organic_results]
    return {
        "kind": "search",
        "query": query,
        "results": results,
        "metadata": dict(response.search_metadata or {}),
    }


def _collect_urls(inputs: Dict[str, Any], limit: int) -> List[str]:
    urls: List[str] = []
    seen: set[str] = set()
    for value in inputs.values():
        if not isinstance(value, dict):
            continue
        for result in value.get("results") or []:
            link = str(result.get("link") or "")
            if not link.startswith(("http://", "https://")):
                continue
            key = _normalize_link(link)
            if key in seen:
                continue
            seen.add(key)
            urls.append(link)
            if len(urls) >= limit:
                return urls
    return urls


def _page_from_result(url: str, data: Dict[str, Any]) -> Dict[str, Any]:
    content = str(data.get("content") or "")
    metadata = data.get("metadata") or {}
    return {
        "url": url,
        "title": str(data.get("title") or metadata.get("title") or ""),
        "content": content,
        "status_code": data.get("status_code"),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest() if content else "",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "description": metadata.get("description", ""),
            "site_name": metadata.get("site_name", ""),
        },
    }


async def fetch(node: QueryNode, inputs: Dict[str, Any], context: GraphContext) -> Dict[str, Any]:
    urls: List[str] = []
    template = node.query_template.strip()
    if template.startswith(("http://", "https://")):
        urls = [template]
    else:
        limit = int(context.extra.get("fetch_top") or 3)
        urls = _collect_urls(inputs, limit)

    if not urls:
        return {"kind": "pages", "pages": [], "note": "no fetchable URLs in inputs"}

    # Concurrency was hardcoded to 3 regardless of how many pages were asked
    # for, so a fetch_top=10 node ran at most three requests at a time. Bound it
    # by the configured limit and by the actual working set.
    concurrency = int(context.extra.get("fetch_concurrency") or 6)
    semaphore = asyncio.Semaphore(max(1, min(concurrency, len(urls))))

    async def _one(url: str) -> Dict[str, Any]:
        async with semaphore:
            try:
                data = await scrape_url(url, context.scraper, fmt="markdown", include_metadata=True)
                return _page_from_result(url, data)
            except Exception as exc:  # network failures are data, not crashes
                return {
                    "url": url,
                    "title": "",
                    "content": "",
                    "status_code": None,
                    "content_hash": "",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc),
                }

    pages = await asyncio.gather(*(_one(url) for url in urls))
    if pages and all(page.get("error") for page in pages):
        raise OperatorError(
            "all FETCH requests failed",
            details={"errors": [page.get("error") for page in pages]},
        )
    return {"kind": "pages", "pages": pages}


async def extract(node: QueryNode, inputs: Dict[str, Any], context: GraphContext) -> Dict[str, Any]:
    """Phase 1 extraction is the existing scraper output.

    Structured extraction is attached in Phase 2; keeping the operator stable
    now means the graph contract does not change when it lands.
    """
    pages: List[Dict[str, Any]] = []
    for value in inputs.values():
        if isinstance(value, dict):
            pages.extend(value.get("pages") or [])
    return {"kind": "pages", "pages": pages}


async def merge(node: QueryNode, inputs: Dict[str, Any], context: GraphContext) -> Dict[str, Any]:
    scores: Dict[str, float] = {}
    best: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, int] = {}

    for value in inputs.values():
        if not isinstance(value, dict):
            continue
        for rank, result in enumerate(value.get("results") or [], start=1):
            link = str(result.get("link") or "")
            key = _normalize_link(link) if link else str(result.get("title") or rank)
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
            sources[key] = sources.get(key, 0) + 1
            current = best.get(key)
            if current is None or len(str(result.get("snippet") or "")) > len(
                str(current.get("snippet") or "")
            ):
                best[key] = dict(result)

    ranked = []
    for key, result in best.items():
        result["jiro_score"] = round(scores[key], 6)
        result["jiro_sources"] = sources[key]
        ranked.append(result)
    ranked.sort(
        key=lambda r: (
            -float(r.get("jiro_score") or 0),
            -int(r.get("jiro_sources") or 0),
            int(r.get("position") or 999),
        )
    )
    return {"kind": "merged", "results": ranked[:20]}


async def compress(node: QueryNode, inputs: Dict[str, Any], context: GraphContext) -> Dict[str, Any]:
    from jiro.parametric.packet import build_packet

    results: List[Dict[str, Any]] = []
    pages: List[Dict[str, Any]] = []
    for value in inputs.values():
        if not isinstance(value, dict):
            continue
        results.extend(value.get("results") or [])
        pages.extend(value.get("pages") or [])

    query = str(context.extra.get("query") or "")
    max_tokens = int(context.extra.get("max_tokens") or 1200)
    return build_packet(query, results, pages, max_tokens=max_tokens)


async def verify(node: QueryNode, inputs: Dict[str, Any], context: GraphContext) -> Dict[str, Any]:
    """Deterministic placeholder.

    Phase 2 wires the claim ledger here. Until then, the operator is explicit
    that no verification ran instead of implying support.
    """
    value: Dict[str, Any] = {"kind": "verification", "status": "not_run"}
    for item in inputs.values():
        if isinstance(item, dict):
            value.setdefault("results", []).extend(item.get("results") or [])
            value.setdefault("pages", []).extend(item.get("pages") or [])
    return value


async def synthesize(node: QueryNode, inputs: Dict[str, Any], context: GraphContext) -> Dict[str, Any]:
    raise OperatorError(
        "SYNTHESIZE requires an enabled LLM and is not part of the Phase 1 surface"
    )


DEFAULT_OPERATORS: Dict[str, Operator] = {
    "SEARCH": search,
    "FETCH": fetch,
    "EXTRACT": extract,
    "MERGE": merge,
    "VERIFY": verify,
    "COMPRESS": compress,
    "SYNTHESIZE": synthesize,
}
