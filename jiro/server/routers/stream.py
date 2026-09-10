"""Streaming endpoints: SSE + WebSocket for real-time search and monitoring.

Provides both Server-Sent Events (SSE) and WebSocket transports for:
- Agentic search with real-time progress
- Multi-step autonomous research
- Real-time monitoring (price tracking, news alerts, social feeds)
- Search result streaming
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Dict, Set

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from jiro.ai.agent import Agent
from jiro.auth import AuthContext
from jiro.models import AgentRequest, AISearchRequest
from jiro.server.deps import get_agent, get_auth_context, get_orchestrator, record_usage
from jiro.scraping.client import ScrapingClient

router = APIRouter(tags=["ai"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ConnectionManager:
    """Manage WebSocket connections for real-time streaming."""

    def __init__(self) -> None:
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket) -> str:
        """Accept a new WebSocket connection and return its session ID."""
        await websocket.accept()
        session_id = str(uuid.uuid4())
        self.active_connections[session_id] = websocket
        return session_id

    def disconnect(self, session_id: str) -> None:
        """Remove a WebSocket connection."""
        self.active_connections.pop(session_id, None)

    async def send(self, session_id: str, event: str, data: Any) -> bool:
        """Send a JSON event to a specific connection. Returns False if disconnected."""
        websocket = self.active_connections.get(session_id)
        if websocket is None:
            return False
        try:
            await websocket.send_json({"event": event, "data": data,
                                       "timestamp": uuid.uuid1().time})
            return True
        except Exception:
            self.disconnect(session_id)
            return False

    async def broadcast(self, event: str, data: Any) -> None:
        """Broadcast an event to all connected sessions."""
        msg = {"event": event, "data": data, "timestamp": uuid.uuid1().time}
        for session_id in list(self.active_connections.keys()):
            websocket = self.active_connections.get(session_id)
            if websocket is None:
                continue
            try:
                await websocket.send_json(msg)
            except Exception:
                self.disconnect(session_id)


# Global connection manager for monitoring endpoints
ws_manager = ConnectionManager()


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


# ---------------------------------------------------------------------------
# WebSocket endpoints for real-time streaming (v0.2.13)
# ---------------------------------------------------------------------------

WS_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}


@router.websocket("/ws/search")
async def ws_search(
    websocket: WebSocket,
    query: str,
    engine: str = "google",
    num: int = 10,
    location: str = "us",
    language: str = "en",
):
    """WebSocket endpoint for real-time search results streaming.

    Sends results as they arrive from the search engine, not all at once.
    """
    session_id = await ws_manager.connect(websocket)
    try:
        await ws_manager.send(session_id, "start", {
            "query": query, "engine": engine, "num": num,
        })

        from jiro.scraping.engines import SearchOrchestrator
        orchestrator = request_or_orchestrator(websocket)
        if orchestrator is None:
            async for result in _stream_search_results(
                query, engine, num, location, language
            ):
                await ws_manager.send(session_id, "result", result)
        else:
            async for result in orchestrator.stream_search(
                query, engine, num, location, language
            ):
                await ws_manager.send(session_id, "result", result)

        await ws_manager.send(session_id, "done", {})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        await ws_manager.send(session_id, "error", {"error": str(exc)})
    finally:
        ws_manager.disconnect(session_id)


@router.websocket("/ws/monitor")
async def ws_monitor(
    websocket: WebSocket,
    url: str,
    interval: int = 60,
):
    """WebSocket endpoint for real-time page monitoring.

    Polls a URL at the specified interval and sends changes via WebSocket.
    """
    session_id = await ws_manager.connect(websocket)
    try:
        await ws_manager.send(session_id, "start", {
            "url": url, "interval_seconds": interval,
        })

        import asyncio
        last_content = None
        while True:
            # Fetch current content
            from jiro.scraping.client import ScrapingClient
            # Use a lightweight fetch
            content = await _fetch_url_content(url)
            if content != last_content:
                await ws_manager.send(session_id, "change", {
                    "url": url,
                    "content_length": len(content),
                    "preview": content[:500] if content else "",
                })
                last_content = content
            await asyncio.sleep(interval)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        await ws_manager.send(session_id, "error", {"error": str(exc)})
    finally:
        ws_manager.disconnect(session_id)


@router.websocket("/ws/search/stream")
async def ws_ai_search_stream(
    websocket: WebSocket,
    query: str,
    max_sources: int = 5,
    provider: str = "",
    model: str = "",
):
    """WebSocket endpoint for real-time AI agentic search.

    Streams agent reasoning steps, search results, and final answer.
    """
    session_id = await ws_manager.connect(websocket)
    try:
        await ws_manager.send(session_id, "start", {
            "query": query, "max_sources": max_sources,
        })

        # Get orchestrator from app state
        orchestrator = getattr(websocket.app.state, "orchestrator", None)
        if orchestrator is None:
            await ws_manager.send(session_id, "error", {
                "error": "search orchestrator not available"
            })
            return

        from jiro.ai.llm import LLM
        llm = LLM(websocket.app.state.settings)

        from jiro.ai.agent import Agent
        agent = Agent(
            websocket.app.state.settings,
            orchestrator,
            _scrape_ws,
            llm,
        )

        async for event in agent.research_stream(
            query, max_sources=max_sources,
            provider=provider or None, model=model or None,
        ):
            await ws_manager.send(session_id, event["type"], event)

        await ws_manager.send(session_id, "done", {})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        await ws_manager.send(session_id, "error", {"error": str(exc)})
    finally:
        ws_manager.disconnect(session_id)


async def _stream_search_results(query: str, engine: str, num: int,
                                  location: str, language: str):
    """Stream search results as they arrive."""
    import httpx

    with httpx.Client(timeout=30) as client:
        resp = client.get("http://localhost:8000/search.json", params={
            "q": query, "engine": engine, "num": num,
            "location": location, "language": language,
        })
        data = resp.json()

    for i, result in enumerate(data.get("organic_results", [])):
        yield {
            "position": i + 1,
            "title": result.get("title", ""),
            "url": result.get("link", ""),
            "snippet": result.get("snippet", ""),
        }
        import asyncio
        await asyncio.sleep(0.01)  # Small delay for real-time feel


async def _fetch_url_content(url: str) -> str:
    """Fetch URL content for monitoring (lightweight)."""
    import httpx
    from jiro.scraping.frontend import ScrapingEngine
    # This is a simplified fetch - in production would use the full client
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36"
        })
        return resp.text[:2000]


def _scrape_ws() -> Any:
    """Get a scrape function for AI agent in WebSocket context."""
    async def _scrape(url: str) -> dict:
        from jiro.extract import scrape_url
        from jiro.scraping.client import ScrapingClient
        from jiro.config import Settings
        client = ScrapingClient(Settings.load())
        await client.init()
        result = await scrape_url(url, client, fmt="markdown", include_metadata=True)
        await client.close()
        return result
    return _scrape


def request_or_orchestrator(websocket: WebSocket) -> Any:
    """Get orchestrator from the WebSocket's app state."""
    return getattr(websocket.app.state, "orchestrator", None)
