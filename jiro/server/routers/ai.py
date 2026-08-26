"""AI endpoints: POST /ai/search, POST /ai/extract."""

from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from jiro.ai.agent import Agent
from jiro.ai.llm import LLM
from jiro.auth import AuthContext
from jiro.models import AIExtractRequest, AgentRequest, AISearchRequest, AISearchResponse
from jiro.server.deps import (
    get_agent,
    get_auth_context,
    get_client,
    get_llm,
    record_usage,
)

router = APIRouter(tags=["ai"])


@router.post("/ai/agent", summary="Multi-step autonomous research (Phase 3)")
async def ai_agent(
    request: Request,
    body: AgentRequest,
    ctx: AuthContext = Depends(get_auth_context),
    agent: Agent = Depends(get_agent),
) -> Dict[str, Any]:
    request.state.auth = ctx
    started = time.perf_counter()
    result = await agent.run_agent(
        body.goal, max_steps=body.max_steps, max_sources=body.max_sources,
        max_sources_per_step=body.max_sources_per_step, refine=body.refine,
        provider=body.llm_provider, model=body.llm_model,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/ai/agent", status=200, latency_ms=latency_ms,
                       query=body.goal,
                       tokens={"in": 0, "out": len(result.get("answer", "")) // 4})
    result["time_taken"] = round(latency_ms / 1000, 3)
    return result


@router.post("/ai/search", response_model=AISearchResponse,
             summary="Agentic search: plan → search → scrape → synthesize")
async def ai_search(
    request: Request,
    body: AISearchRequest,
    ctx: AuthContext = Depends(get_auth_context),
    agent: Agent = Depends(get_agent),
) -> Dict[str, Any]:
    request.state.auth = ctx
    started = time.perf_counter()
    result = await agent.research(
        body.query, max_sources=body.max_sources,
        provider=body.llm_provider, model=body.llm_model,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    await record_usage(request, endpoint="/ai/search", status=200, latency_ms=latency_ms,
                       query=body.query,
                       tokens={"in": 0, "out": len(result.get("answer", "")) // 4})
    result["time_taken"] = round(latency_ms / 1000, 3)
    return result


@router.post("/ai/extract", summary="LLM-assisted structured extraction from URL/text")
async def ai_extract(
    request: Request,
    body: AIExtractRequest,
    ctx: AuthContext = Depends(get_auth_context),
    llm: LLM = Depends(get_llm),
    client: Any = Depends(get_client),
) -> Dict[str, Any]:
    request.state.auth = ctx
    started = time.perf_counter()
    if not body.url and not body.text:
        return {"error": "provide either url or text"}
    if not body.schema_:
        return {"error": "provide a schema of field names to types"}

    text = body.text or ""
    if body.url and not text:
        from jiro.extract import scrape_url
        payload = await scrape_url(body.url, client, fmt="text",
                                   include_metadata=False)
        text = payload.get("content") or ""

    from jiro.server.routers.scrape import _extract_with_schema
    extracted = await _extract_with_schema(llm, body.url or "", {"content": text},
                                           body.schema_)
    await record_usage(request, endpoint="/ai/extract", status=200,
                       latency_ms=(time.perf_counter() - started) * 1000,
                       query=body.url)
    return {"url": body.url, "extracted": extracted,
            "provider": llm.provider_name if llm.available else "extractive-fallback"}
