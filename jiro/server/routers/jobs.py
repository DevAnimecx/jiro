"""Async jobs + webhooks endpoints (PRD Phase 3)."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request

from jiro.auth import AuthContext
from jiro.jobs import JobManager
from jiro.models import JobCreate, JobOut
from jiro.server.deps import get_auth_context, record_usage

router = APIRouter(tags=["jobs"])


def _build_runner(request: Request):
    """Async runner that dispatches jobs to the right executor."""
    agent = request.app.state.agent
    client = request.app.state.scraper_client

    async def runner(job_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if job_type == "ai_search":
            from jiro.models import AISearchRequest
            body: Any = AISearchRequest(**payload)
            return await agent.research(body.query, max_sources=body.max_sources,
                                        provider=body.llm_provider,
                                        model=body.llm_model)
        if job_type == "ai_agent":
            from jiro.models import AgentRequest
            body = AgentRequest(**payload)
            return await agent.run_agent(
                body.goal, max_steps=body.max_steps, max_sources=body.max_sources,
                max_sources_per_step=body.max_sources_per_step, refine=body.refine,
                provider=body.llm_provider, model=body.llm_model,
            )
        if job_type == "batch_scrape":
            from jiro.extract import scrape_url
            urls = payload.get("urls") or []
            fmt = payload.get("format", "markdown")
            results = []
            for url in urls[:50]:
                try:
                    page = await scrape_url(url, client, fmt=fmt,
                                            include_metadata=True)
                    results.append(page)
                except Exception as exc:
                    results.append({"url": url, "error": str(exc)})
            return {"count": len(results), "results": results}
        raise ValueError(f"unknown job type '{job_type}'")

    return runner


@router.post("/jobs", response_model=JobOut, summary="Submit an async job")
async def create_job(
    request: Request,
    body: JobCreate,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    jobs: JobManager = request.app.state.jobs
    record = await jobs.submit(
        body.type, body.payload, webhook_url=body.webhook_url,
        webhook_secret=body.webhook_secret, runner=_build_runner(request),
    )
    await record_usage(request, endpoint=f"/jobs/{body.type}", status=202)
    return jobs._to_out(record)  # noqa: SLF001


@router.get("/jobs", response_model=List[JobOut], summary="List recent jobs")
async def list_jobs(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> List[Dict[str, Any]]:
    request.state.auth = ctx
    jobs: JobManager = request.app.state.jobs
    return await jobs.list_jobs(limit=50)


@router.get("/jobs/{job_id}", response_model=JobOut, summary="Get job status/result")
async def get_job(
    job_id: str,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    request.state.auth = ctx
    jobs: JobManager = request.app.state.jobs
    record = await jobs.get(job_id)
    if record is None:
        from jiro.errors import NotFoundError
        raise NotFoundError(f"job {job_id} not found")
    return record
