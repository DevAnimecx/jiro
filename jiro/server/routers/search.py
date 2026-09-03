"""Search endpoints: GET/POST /search, GET /search.json (SerpAPI-compatible),
POST /search/batch, GET /search/stream (SSE).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, List

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse

from jiro.audit import AuditEventType, ComplianceLogger
from jiro.auth import AuthContext
from jiro.cache import CacheManager
from jiro.errors import EngineError
from jiro.models import SearchRequest, SearchResponse, MultiQuerySearchRequest
from jiro.server.deps import (
    get_auth_context,
    get_audit_logger_dep,
    get_cache,
    get_orchestrator,
    record_usage,
)

router = APIRouter(tags=["search"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/search", response_model=SearchResponse, summary="Search the web")
@router.get("/search.json", response_model=SearchResponse,
            summary="Search (SerpAPI-compatible endpoint)")
async def search_get(
    request: Request,
    q: str = Query(..., description="Search query"),
    engine: str = Query("google", description="google | bing | duckduckgo | brave | youtube | amazon | ebay | yandex | baidu | auto"),
    type: str = Query("web", description="web | images | news | videos | shopping | places"),
    location: str = Query("us"),
    language: str = Query("en"),
    num: int = Query(10, ge=1, le=100),
    start: int = Query(0, ge=0),
    safe: str = Query("off"),
    time_range: str = Query("any"),
    device: str = Query("desktop"),
    gl: str = Query("us"),
    hl: str = Query("en"),
    fresh: bool = Query(False, description="Bypass cache"),
    format: str = Query("json", description="json | csv | xml | rss", alias="format"),
    # v0.2 parameters
    mode: str = Query("auto", description="auto | keyword | hybrid"),
    depth: str = Query("basic", description="instant | fast | basic | advanced | deep"),
    include_domains: str = Query("", description="Comma-separated domains to include"),
    exclude_domains: str = Query("", description="Comma-separated domains to exclude"),
    start_date: str = Query("", description="Start date YYYY-MM-DD"),
    end_date: str = Query("", description="End date YYYY-MM-DD"),
    category: str = Query("", description="publication | financial_report | people | shopping | github | news"),
    highlights: bool = Query(False, description="Extract token-efficient highlights"),
    include_answer: str = Query("", description="none | extractive | advanced"),
    ctx: AuthContext = Depends(get_auth_context),
    orchestrator: Any = Depends(get_orchestrator),
    cache: CacheManager = Depends(get_cache),
    audit: ComplianceLogger = Depends(get_audit_logger_dep),
) -> Response:
    request.state.auth = ctx
    started = time.perf_counter()
    req = SearchRequest(q=q, engine=engine, type=type, location=location,
                        language=language, num=num, start=start, safe=safe,
                        time_range=time_range, device=device, gl=gl, hl=hl,
                        fresh=fresh, mode=mode, depth=depth,
                        include_domains=include_domains.split(",") if include_domains else None,
                        exclude_domains=exclude_domains.split(",") if exclude_domains else None,
                        start_date=start_date or None,
                        end_date=end_date or None,
                        category=category or None,
                        highlights=highlights,
                        include_answer=include_answer or None)
    ip = request.client.host if request.client else None
    key_id = ctx.key_id

    try:
        result = await orchestrator.search(req, fresh=fresh)
    except EngineError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        await record_usage(request, endpoint="/search", status=exc.status_code,
                           latency_ms=latency_ms, engine=engine, query=q)
        audit.log_search(
            event_type=AuditEventType.SEARCH_FAILED,
            engine=engine, query=q, search_type=type,
            status_code=exc.status_code, latency_ms=latency_ms,
            cached=False, results_count=0, key_id=key_id, ip=ip,
            error=exc.message,
        )
        raise

    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/search", status=200, latency_ms=latency_ms,
                       engine=result.search_metadata.get("engine"), query=q,
                       cached=result.search_metadata.get("cached", False))

    audit.log_search(
        event_type=AuditEventType.SEARCH_RESPONSE,
        engine=result.search_metadata.get("engine", engine),
        query=q, search_type=type,
        status_code=200, latency_ms=latency_ms,
        cached=result.search_metadata.get("cached", False),
        results_count=len(result.organic_results),
        key_id=key_id, ip=ip,
        robots_checked=orchestrator.robots is not None,
        robots_allowed=True,  # If we got here, it was allowed
    )

    result.search_metadata["latency_ms"] = round(latency_ms, 1)
    data = result.model_dump()

    # Export format routing
    fmt = format.strip().lower()
    if fmt == "json":
        return Response(content=json.dumps(data, default=str),
                        media_type="application/json")
    if fmt in ("csv", "xml", "rss"):
        from jiro.export import export
        content = export(data, fmt)
        media_types = {
            "csv": "text/csv",
            "xml": "application/xml",
            "rss": "application/rss+xml",
        }
        return Response(content=content, media_type=media_types[fmt])
    return Response(content=json.dumps(data, default=str),
                    media_type="application/json")


@router.post("/search", response_model=SearchResponse, summary="Search (JSON body)")
async def search_post(
    request: Request,
    body: SearchRequest,
    ctx: AuthContext = Depends(get_auth_context),
    orchestrator: Any = Depends(get_orchestrator),
    cache: CacheManager = Depends(get_cache),
    audit: ComplianceLogger = Depends(get_audit_logger_dep),
) -> Dict[str, Any]:
    request.state.auth = ctx
    started = time.perf_counter()
    ip = request.client.host if request.client else None
    key_id = ctx.key_id

    try:
        result = await orchestrator.search(body, fresh=body.fresh)
    except EngineError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        await record_usage(request, endpoint="/search", status=exc.status_code,
                           latency_ms=latency_ms, engine=body.engine, query=body.q)
        audit.log_search(
            event_type=AuditEventType.SEARCH_FAILED,
            engine=body.engine, query=body.q, search_type=body.type,
            status_code=exc.status_code, latency_ms=latency_ms,
            cached=False, results_count=0, key_id=key_id, ip=ip,
            error=exc.message,
        )
        raise

    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/search", status=200, latency_ms=latency_ms,
                       engine=result.search_metadata.get("engine"), query=body.q,
                       cached=result.search_metadata.get("cached", False))

    audit.log_search(
        event_type=AuditEventType.SEARCH_RESPONSE,
        engine=result.search_metadata.get("engine", body.engine),
        query=body.q, search_type=body.type,
        status_code=200, latency_ms=latency_ms,
        cached=result.search_metadata.get("cached", False),
        results_count=len(result.organic_results),
        key_id=key_id, ip=ip,
        robots_checked=orchestrator.robots is not None,
        robots_allowed=True,
    )

    result.search_metadata["latency_ms"] = round(latency_ms, 1)
    return result.model_dump()


# ── Batch Search ─────────────────────────────────────────────────────────

class BatchSearchItem:
    def __init__(self, q: str, engine: str = "google", num: int = 10,
                 type: str = "web", **kwargs: Any) -> None:
        self.q = q
        self.engine = engine
        self.num = num
        self.type = type
        self.extra = kwargs


@router.post("/search/batch", summary="Batch search multiple queries")
async def search_batch(
    request: Request,
    items: List[Dict[str, Any]],
    ctx: AuthContext = Depends(get_auth_context),
    orchestrator: Any = Depends(get_orchestrator),
    cache: CacheManager = Depends(get_cache),
) -> Dict[str, Any]:
    """Execute multiple search queries in parallel.

    Request body: list of objects with `q` (required) and optional `engine`, `num`, `type`.
    Limited to 10 queries per batch.
    """
    request.state.auth = ctx
    if len(items) > 10:
        return {"error": "batch limited to 10 queries", "results": []}
    if not items:
        return {"error": "empty batch", "results": []}

    started = time.perf_counter()
    results = []

    async def _search_one(item: Dict[str, Any]) -> Dict[str, Any]:
        q = item.get("q", "")
        if not q:
            return {"error": "missing 'q' field"}
        engine = item.get("engine", "google")
        num = min(item.get("num", 10), 100)
        stype = item.get("type", "web")
        req = SearchRequest(q=q, engine=engine, type=stype, num=num)
        try:
            result = await orchestrator.search(req)
            data = result.model_dump()
            data["query"] = q
            return data
        except Exception as exc:
            # SECURITY: Log full exception server-side only, return generic message to client
            log.exception("Batch search failed", extra={"query": q, "engine": engine})
            return {"query": q, "error": "Search failed"}

    tasks = [_search_one(item) for item in items]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/search/batch", status=200,
                       latency_ms=latency_ms)
    return {"count": len(results), "results": results}


# ── SSE Search Stream ────────────────────────────────────────────────────

@router.get("/search/stream", summary="Search with SSE streaming results")
async def search_stream(
    request: Request,
    q: str = Query(..., description="Search query"),
    engine: str = Query("google"),
    num: int = Query(10, ge=1, le=100),
    engines: str = Query("", description="Comma-separated engines for multi-engine search"),
    ctx: AuthContext = Depends(get_auth_context),
    orchestrator: Any = Depends(get_orchestrator),
    cache: CacheManager = Depends(get_cache),
):
    """Stream search results as SSE events.

    Events:
    - search_start: emitted when search begins
    - result: emitted for each organic result as it's parsed
    - search_complete: emitted when all results are ready
    - error: emitted on failure
    """
    request.state.auth = ctx
    engine_list = [e.strip() for e in engines.split(",") if e.strip()] or [engine]

    async def gen() -> AsyncIterator[str]:
        yield _sse("search_start", {"query": q, "engines": engine_list})

        try:
            if len(engine_list) == 1:
                # Single engine — search and emit all results at once
                req = SearchRequest(q=q, engine=engine_list[0], num=num)
                result = await orchestrator.search(req)
                data = result.model_dump()
                for r in data.get("organic_results", []):
                    yield _sse("result", r)
                yield _sse("search_complete", {
                    "total": len(data.get("organic_results", [])),
                    "engine": engine_list[0],
                })
            else:
                # Multi-engine — search in parallel, emit as they complete
                total = 0
                async def _search_engine(eng: str) -> Dict[str, Any]:
                    req = SearchRequest(q=q, engine=eng, num=num)
                    return await orchestrator.search(req)

                tasks = {eng: asyncio.create_task(_search_engine(eng))
                         for eng in engine_list}
                for eng, task in tasks.items():
                    try:
                        task_result: Any = await task
                        data = task_result.model_dump()
                        org = data.get("organic_results", [])
                        for r in org:
                            r["engine"] = eng
                            yield _sse("result", r)
                        total += len(org)
                    except Exception as exc:
                        yield _sse("error", {"engine": eng, "error": str(exc)})

                yield _sse("search_complete", {
                    "total": total,
                    "engines": engine_list,
                })
        except Exception as exc:
            yield _sse("error", {"error": str(exc)})

        yield _sse("done", {})

    await record_usage(request, endpoint="/search/stream", status=200, query=q)
    return StreamingResponse(gen(), headers=SSE_HEADERS,
                             media_type="text/event-stream")


# ── Multi-Query Search ─────────────────────────────────────────────────────

@router.post("/search/multi", summary="Multi-query search: break, parallelize, merge, deduplicate, rerank")
async def search_multi(
    request: Request,
    body: MultiQuerySearchRequest,
    ctx: AuthContext = Depends(get_auth_context),
    orchestrator: Any = Depends(get_orchestrator),
    cache: CacheManager = Depends(get_cache),
) -> Dict[str, Any]:
    """Execute multiple search queries in parallel with merging and deduplication.
    
    Request body:
    - queries: list of query strings (max 10)
    - merge: whether to merge results (default true)
    - deduplicate: whether to deduplicate by URL (default true)
    - rerank: whether to rerank merged results (default true)
    - max_results: maximum results to return (default 20)
    - depth: search depth for each query (default basic)
    - engine: engine to use for each query (default auto)
    """
    request.state.auth = ctx
    
    if len(body.queries) > 10:
        return {"error": "multi-query limited to 10 queries", "results": []}
    if not body.queries:
        return {"error": "empty query list", "results": []}
    
    from jiro.search.multiquery import MultiQuerySearcher, MultiQueryRequest
    
    multi_searcher = MultiQuerySearcher(
        orchestrator.settings, orchestrator, cache,
        getattr(orchestrator, 'hybrid_searcher', None)
    )
    
    mq_req = MultiQueryRequest(
        queries=body.queries,
        merge=body.merge,
        deduplicate=body.deduplicate,
        rerank=body.rerank,
        max_results=body.max_results,
        depth=body.depth,
        engine=body.engine,
    )
    
    started = time.perf_counter()
    result = await multi_searcher.search(mq_req)
    latency_ms = (time.perf_counter() - started) * 1000
    
    result.search_metadata["latency_ms"] = round(latency_ms, 1)
    
    await record_usage(request, endpoint="/search/multi", status=200, latency_ms=latency_ms)
    
    return result.model_dump()
