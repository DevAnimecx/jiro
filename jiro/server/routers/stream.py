"""SSE streaming endpoints (PRD Phase 2/3)."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from jiro.ai.agent import Agent
from jiro.ai.llm import LLM
from jiro.auth import AuthContext
from jiro.models import AgentRequest, AISearchRequest
from jiro.server.deps import get_agent, get_auth_context, record_usage

router = APIRouter(tags=["ai"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/ai/search/stream", summary="Agentic search as SSE stream")
async def ai_search_stream(
    request: Request,
    query: str,
    max_sources: int = 5,
    llm_provider: str = "",
    llm_model: str = "",
    ctx: AuthContext = Depends(get_auth_context),
    agent: Agent = Depends(get_agent),
):
    request.state.auth = ctx
    req = AISearchRequest(query=query, max_sources=max_sources,
                          llm_provider=llm_provider or None,
                          llm_model=llm_model or None)

    async def gen() -> AsyncIterator[str]:
        yield _sse("start", {"query": req.query, "max_sources": req.max_sources})
        try:
            async for event in agent.research_stream(
                req.query, max_sources=req.max_sources,
                provider=req.llm_provider, model=req.llm_model,
            ):
                yield _sse(event["type"], event)
        except Exception as exc:  # pragma: no cover - safety
            yield _sse("error", {"error": str(exc)})
        yield _sse("done", {})

    await record_usage(request, endpoint="/ai/search/stream", status=200, query=query)
    return StreamingResponse(gen(), headers=SSE_HEADERS,
                             media_type="text/event-stream")


@router.get("/ai/agent/stream", summary="Multi-step autonomous research as SSE stream")
async def ai_agent_stream(
    request: Request,
    goal: str,
    max_steps: int = 5,
    max_sources: int = 8,
    ctx: AuthContext = Depends(get_auth_context),
    agent: Agent = Depends(get_agent),
):
    request.state.auth = ctx
    req = AgentRequest(goal=goal, max_steps=max_steps, max_sources=max_sources)

    async def gen() -> AsyncIterator[str]:
        yield _sse("start", {"goal": req.goal, "max_steps": req.max_steps})
        try:
            # run the full agent, then emit steps incrementally
            result = await agent.run_agent(
                req.goal, max_steps=req.max_steps, max_sources=req.max_sources,
                max_sources_per_step=req.max_sources_per_step, refine=req.refine,
            )
            for step in result.get("reasoning_steps", []):
                yield _sse("step", step)
            yield _sse("answer", {"answer": result.get("answer", ""),
                                  "citations": result.get("citations", []),
                                  "provider": result.get("provider")})
        except Exception as exc:  # pragma: no cover
            yield _sse("error", {"error": str(exc)})
        yield _sse("done", {})

    await record_usage(request, endpoint="/ai/agent/stream", status=200, query=goal)
    return StreamingResponse(gen(), headers=SSE_HEADERS,
                             media_type="text/event-stream")
