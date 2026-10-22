"""Query graph endpoints (v0.4.0 Phase 1).

``/v1/graph/plan`` is a dry run: it returns the validated graph and a cost
estimate without touching the network. ``/v1/graph/search`` executes it.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from jiro.auth import AuthContext
from jiro.graph.service import GraphService
from jiro.server.deps import get_settings, record_usage, require_feature

router = APIRouter(prefix="/v1/graph", tags=["graph"])


class GraphPlanRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    engine: str = Field("auto", max_length=32)
    num: int = Field(5, ge=1, le=10)
    max_tokens: Optional[int] = Field(None, ge=64, le=32000)
    deadline_ms: Optional[int] = Field(None, ge=100, le=60000)
    max_nodes: int = Field(12, ge=1, le=32)
    fetch_top: Optional[int] = Field(None, ge=0, le=10)
    intent: Optional[str] = Field(None, max_length=64)


class GraphSearchRequest(GraphPlanRequest):
    include_plan: bool = False


def get_graph_service(request: Request) -> GraphService:
    service = getattr(request.app.state, "graph_service", None)
    if service is None:
        service = GraphService(
            get_settings(request),
            request.app.state.orchestrator,
            request.app.state.scraper_client,
            getattr(request.app.state, "cache", None),
        )
        request.app.state.graph_service = service
    return service


@router.post("/plan")
async def plan_graph(
    body: GraphPlanRequest,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    service: GraphService = Depends(get_graph_service),
) -> dict[str, Any]:
    try:
        result = service.plan(
            body.query,
            engine=body.engine,
            num=body.num,
            max_tokens=body.max_tokens,
            deadline_ms=body.deadline_ms,
            max_nodes=body.max_nodes,
            fetch_top=body.fetch_top,
            intent=body.intent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "graph": result.graph.model_dump(mode="json", by_alias=True),
        "estimate": result.estimate.model_dump(),
        "rationale": result.rationale,
        "alternatives": [a.model_dump() for a in result.alternatives],
    }


@router.post("/search")
async def search_graph(
    body: GraphSearchRequest,
    request: Request,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    service: GraphService = Depends(get_graph_service),
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        plan = service.plan(
            body.query,
            engine=body.engine,
            num=body.num,
            max_tokens=body.max_tokens,
            deadline_ms=body.deadline_ms,
            max_nodes=body.max_nodes,
            fetch_top=body.fetch_top,
            intent=body.intent,
        )
        from jiro.graph.executor import GraphContext

        context = GraphContext(
            settings=service.settings,
            orchestrator=service.orchestrator,
            scraper=service.scraper,
            cache=service.cache,
            extra={
                "query": body.query,
                "policy": plan.graph.policy,
                "max_tokens": plan.graph.budget.max_tokens,
                "fetch_top": plan.graph.policy.get("fetch_top", 0),
            },
        )
        run = await service.executor.run(plan.graph, context)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = run.to_dict()
    if body.include_plan:
        payload["plan"] = {
            "graph": plan.graph.model_dump(mode="json", by_alias=True),
            "estimate": plan.estimate.model_dump(),
            "rationale": plan.rationale,
        }
    await record_usage(
        request,
        endpoint="graph_search",
        status=200,
        latency_ms=(time.perf_counter() - started) * 1000,
        engine=body.engine,
        query=body.query,
        tokens={
            "in": int((run.packet or {}).get("used_tokens") or 0),
            "out": 0,
        },
    )
    return payload
