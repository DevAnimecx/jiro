"""MCP HTTP transports — production-hardened remote access.

Implements two MCP transports so remote AI agents can use Jiro over the
network (stdio remains available via ``jiro mcp``):

1. **Streamable HTTP** (spec 2025-03-26)
   * ``POST /mcp``  — client → server JSON-RPC; responds either as a single
     JSON body or as an SSE stream when the client sends ``Accept:
     text/event-stream`` and the call produces progress notifications.
   * ``GET /mcp``   — server-initiated notification stream (SSE).
   * ``DELETE /mcp``— explicit session termination.

2. **HTTP+SSE (legacy, spec 2024-11-05)** — kept for Claude Desktop and
   other clients that still speak it.
   * ``GET /sse``        — opens the server→client event stream and delivers
     an ``endpoint`` event pointing at the message URL.
   * ``POST /messages``  — client→server JSON-RPC channel.

Production features:
* Sessions with ``Mcp-Session-Id`` (or generated ids), TTL expiry, heartbeat,
  and **resume** via ``Last-Event-ID`` replay of missed events.
* Auth: ``X-API-Key: jsk_…`` or ``Authorization: Bearer <jwt>`` validated
  against the same API-key store as the REST API.
* Per-key sliding-window rate limiting (shared AuthManager).
* Request cancellation via ``notifications/cancelled``.
* Progress notifications streamed to SSE clients during long tool calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from jiro.auth import AuthContext, build_auth_context
from jiro.errors import AuthError, RateLimitError
from jiro.log import get_logger
from jiro.mcp import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    PARSE_ERROR,
    JiroMCPServer,
    SUPPORTED_PROTOCOL_VERSIONS,
)

log = get_logger("jiro.mcp_http")

SESSION_TTL_SECONDS = 1800          # 30 min idle expiry
EVENT_BUFFER_SIZE = 512             # per-session replay buffer for resume
HEARTBEAT_INTERVAL = 10.0           # SSE keepalive comment
POST_SSE_DEADLINE = 120.0           # hard cap on POST /mcp SSE replies
# Max lifetime of long-lived GET streams (clients reconnect & replay via
# Last-Event-ID). Override for tests: JIRO_MCP_SSE_MAX_LIFETIME.
def _max_stream_lifetime() -> float:
    return float(os.environ.get("JIRO_MCP_SSE_MAX_LIFETIME", "1800"))


async def _wait_for_disconnect(request: Request) -> None:
    """Resolve when the HTTP client disconnects (works under uvicorn & tests)."""
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


async def _race_queue_disconnect(queue: asyncio.Queue,
                                 request: Optional[Request] = None):
    """Await the next session event OR client disconnect.

    Returns ("event", payload-or-None-closed-sentinel) or ("disconnect", None).
    """
    get_task: asyncio.Task = asyncio.create_task(queue.get())
    disc_task: Optional[asyncio.Task] = (
        asyncio.create_task(_wait_for_disconnect(request)) if request is not None else None)
    try:
        wait_set = {get_task} | ({disc_task} if disc_task is not None else set())
        done, pending = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)
        if disc_task is not None and disc_task in done:
            return "disconnect", None
        return "event", get_task.result()
    finally:
        for t in (get_task, disc_task):
            if t is not None and not t.done():
                t.cancel()


# ---------------------------------------------------------------------------
# Sessions & event buffers
# ---------------------------------------------------------------------------

@dataclass
class SessionEvent:
    seq: int
    payload: Dict[str, Any]


@dataclass
class MCPSession:
    id: str
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    key_id: Optional[str] = None            # owning API key (None = local open)
    client_info: Dict[str, Any] = field(default_factory=dict)
    initialized: bool = False
    negotiated_version: str = "2025-03-26"
    events: Deque[SessionEvent] = field(default_factory=lambda: deque(maxlen=EVENT_BUFFER_SIZE))
    next_seq: int = 1
    # subscribers waiting on GET /mcp or /sse streams
    out_queues: List[asyncio.Queue] = field(default_factory=list)

    def record(self, payload: Dict[str, Any]) -> SessionEvent:
        evt = SessionEvent(seq=self.next_seq, payload=payload)
        self.next_seq += 1
        self.events.append(evt)
        return evt

    def replay_after(self, last_event_id: int) -> List[SessionEvent]:
        return [e for e in self.events if e.seq > last_event_id]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.out_queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self.out_queues.remove(q)
        except ValueError:
            pass

    def push(self, evt: SessionEvent) -> None:
        for q in list(self.out_queues):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                log.warning("session queue full, dropping notification",
                            extra={"session": self.id})


class SessionStore:
    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self._sessions: Dict[str, MCPSession] = {}
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def create(self, key_id: Optional[str]) -> MCPSession:
        async with self._lock:
            session = MCPSession(id=uuid.uuid4().hex, key_id=key_id)
            self._sessions[session.id] = session
            return session

    async def get(self, session_id: str) -> Optional[MCPSession]:
        async with self._lock:
            s = self._sessions.get(session_id)
            if s is not None:
                if time.time() - s.last_seen > self._ttl:
                    del self._sessions[session_id]
                    return None
                s.last_seen = time.time()
            return s

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            s = self._sessions.pop(session_id, None)
            if s:
                for q in list(s.out_queues):
                    try:
                        q.put_nowait(None)  # sentinel to close streams
                    except Exception:
                        pass
                s.out_queues.clear()
            return s is not None

    async def cleanup(self) -> int:
        """Drop expired sessions. Returns count removed."""
        cutoff = time.time() - self._ttl
        removed = 0
        async with self._lock:
            for sid in [sid for sid, s in self._sessions.items()
                        if s.last_seen < cutoff]:
                await self.delete(sid)
                removed += 1
        return removed


def _sse(event: Optional[str], data: Any, event_id: Optional[int] = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, default=str)}")
    return "\n".join(lines) + "\n\n"


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def _get_mcp_server(request: Request) -> JiroMCPServer:
    server: Optional[JiroMCPServer] = getattr(request.app.state, "mcp_server", None)
    if server is None:
        raise RuntimeError("MCP server not initialised")
    return server


def _get_session_store(request: Request) -> SessionStore:
    store: Optional[SessionStore] = getattr(request.app.state, "mcp_sessions", None)
    if store is None:
        raise RuntimeError("MCP session store not initialised")
    return store


async def _authenticate(request: Request, mcp_server: JiroMCPServer,
                        *, required: Optional[bool] = None) -> Optional[AuthContext]:
    """Resolve API-key/JWT identity for remote MCP access.

    When auth.enabled is true a valid credential is mandatory; when false,
    credentials are still honoured (per-key rate limits apply) but optional.
    """
    settings = request.app.state.settings
    auth_manager = request.app.state.auth
    require = settings.auth_enabled if required is None else required
    try:
        ctx = await build_auth_context(request, auth_manager, require=require)
    except AuthError:
        raise
    if ctx.record is not None:
        # per-key rate limit (sliding window, shared with REST API)
        try:
            await auth_manager.check_rate_limit_async(ctx.bucket)
        except RateLimitError:
            raise
    return ctx if ctx.record is not None else None


def create_mcp_router() -> APIRouter:
    router = APIRouter(tags=["mcp"])

    # ------------------------------------------------------------------
    # Streamable HTTP (2025-03-26): single endpoint, three verbs.
    # ------------------------------------------------------------------

    @router.post("/mcp", summary="MCP Streamable HTTP endpoint")
    async def mcp_post(request: Request):
        server = _get_mcp_server(request)
        store = _get_session_store(request)

        try:
            ctx = await _authenticate(request, server)
        except AuthError as exc:
            return JSONResponse(status_code=401, content={
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32001, "message": f"unauthorized: {exc.message}"}})
        except RateLimitError as exc:
            return JSONResponse(status_code=429, content={
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32002, "message": exc.message}})

        raw = await request.body()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            return JSONResponse(status_code=400, content={
                "jsonrpc": "2.0", "id": None,
                "error": {"code": PARSE_ERROR, "message": f"parse error: {exc}"}})

        # Batch support (spec allows arrays)
        batch = msg if isinstance(msg, list) else [msg]

        # Resolve/create session
        session: Optional[MCPSession] = None
        sid_header = request.headers.get("Mcp-Session-Id")
        wants_sse = "text/event-stream" in request.headers.get("accept", "")
        is_init = any(m.get("method") == "initialize" for m in batch)

        if sid_header:
            session = await store.get(sid_header)
            if session is None:
                return JSONResponse(status_code=404, content={
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32001,
                              "message": "session expired or unknown; re-initialize"}})
            if session.key_id is not None and ctx is not None \
                    and session.key_id != ctx.key_id:
                return JSONResponse(status_code=403, content={
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32001, "message": "session belongs to another key"}})
        elif is_init:
            session = await store.create(ctx.key_id if ctx else None)
        else:
            # Spec: servers may reject non-initialize requests without a session
            # when they are stateful. We stay permissive but anonymous.
            session = await store.create(ctx.key_id if ctx else None)

        assert session is not None
        headers = {"Mcp-Session-Id": session.id}

        # progress sink: forward notifications into the session (and thus to
        # any open GET /mcp stream), and into the POST's own SSE stream below.
        post_queue: Optional[asyncio.Queue] = session.subscribe() if wants_sse else None

        def sink(payload: Dict[str, Any]) -> None:
            evt = session.record(payload)
            session.push(evt)

        results = []
        for m in batch:
            resp = await server.dispatch(m, progress_sink=sink)
            if resp is not None:
                evt = session.record(resp)
                session.push(evt)
                results.append(evt)

        if not wants_sse or not results:
            body = results[0].payload if len(results) == 1 else [e.payload for e in results]
            return JSONResponse(content=body, headers=headers)

        # SSE response: emit notifications already queued during dispatch,
        # then the final response(s), then stop.
        async def stream():
            deadline = time.time() + POST_SSE_DEADLINE
            sent_final = 0
            while time.time() < deadline and post_queue is not None:
                kind, item = await _race_queue_disconnect(post_queue, request)
                if kind == "disconnect":
                    break
                if item is None:  # session closed
                    break
                is_final = any(item.seq == r.seq for r in results)
                if is_final:
                    sent_final += 1
                    yield _sse("message", item.payload, item.seq)
                    if sent_final >= len(results):
                        break
                else:
                    yield _sse("notification", item.payload, item.seq)

        return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)

    @router.get("/mcp", summary="MCP server-initiated notification stream")
    async def mcp_get(request: Request):
        server = _get_mcp_server(request)
        store = _get_session_store(request)

        try:
            ctx = await _authenticate(request, server)
        except AuthError:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

        sid = request.headers.get("Mcp-Session-Id", "")
        session = await store.get(sid) if sid else None
        if session is None:
            return JSONResponse(status_code=404, content={"error": "unknown session"})

        queue = session.subscribe()

        async def stream():
            last_id_hdr = request.headers.get("Last-Event-ID")
            if last_id_hdr and last_id_hdr.isdigit():
                for evt in session.replay_after(int(last_id_hdr)):
                    yield _sse("message", evt.payload, evt.seq)
            deadline = time.time() + _max_stream_lifetime()
            last_beat = time.time()
            try:
                while time.time() < deadline:
                    remaining = min(HEARTBEAT_INTERVAL, max(deadline - time.time(), 0.01))
                    try:
                        kind, item = await asyncio.wait_for(
                            _race_queue_disconnect(queue, request), timeout=remaining)
                    except asyncio.TimeoutError:
                        if time.time() - last_beat >= HEARTBEAT_INTERVAL:
                            yield ": keepalive\n\n"
                            last_beat = time.time()
                        continue
                    last_beat = time.time()
                    if kind == "disconnect" or item is None:
                        break
                    yield _sse("message", item.payload, item.seq)
            finally:
                session.unsubscribe(queue)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                     "X-Accel-Buffering": "no"})

    @router.delete("/mcp", summary="Terminate an MCP session")
    async def mcp_delete(request: Request):
        store = _get_session_store(request)
        sid = request.headers.get("Mcp-Session-Id", "")
        if not sid:
            return JSONResponse(status_code=400, content={"error": "missing Mcp-Session-Id"})
        deleted = await store.delete(sid)
        return Response(status_code=204 if deleted else 404)

    # ------------------------------------------------------------------
    # Legacy HTTP+SSE transport (2024-11-05) — Claude Desktop & friends.
    # ------------------------------------------------------------------

    @router.get("/sse", summary="MCP legacy SSE transport (server→client)")
    async def sse_connect(request: Request):
        server = _get_mcp_server(request)
        store = _get_session_store(request)

        try:
            ctx = await _authenticate(request, server)
        except AuthError:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

        session = await store.create(ctx.key_id if ctx else None)
        queue = session.subscribe()

        async def stream():
            base = str(request.base_url).rstrip("/")
            # 1. announce the message endpoint
            yield _sse("endpoint", {"uri": f"{base}/messages?sessionId={session.id}"})
            deadline = time.time() + _max_stream_lifetime()
            last_beat = time.time()
            try:
                while time.time() < deadline:
                    remaining = min(HEARTBEAT_INTERVAL, max(deadline - time.time(), 0.01))
                    try:
                        kind, item = await asyncio.wait_for(
                            _race_queue_disconnect(queue, request), timeout=remaining)
                    except asyncio.TimeoutError:
                        if time.time() - last_beat >= HEARTBEAT_INTERVAL:
                            yield ": keepalive\n\n"
                            last_beat = time.time()
                        continue
                    last_beat = time.time()
                    if kind == "disconnect" or item is None:
                        break
                    yield _sse("message", item.payload, item.seq)
            finally:
                session.unsubscribe(queue)
                await store.delete(session.id)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                     "X-Accel-Buffering": "no"})

    @router.post("/messages", summary="MCP legacy SSE transport (client→server)")
    async def sse_messages(request: Request, sessionId: str = ""):
        server = _get_mcp_server(request)
        store = _get_session_store(request)

        try:
            ctx = await _authenticate(request, server)
        except AuthError as exc:
            return JSONResponse(status_code=401, content={"error": exc.message})
        except RateLimitError as exc:
            return JSONResponse(status_code=429, content={"error": exc.message})

        session = await store.get(sessionId) if sessionId else None
        if session is None:
            return JSONResponse(status_code=404, content={"error": "unknown session"})
        if session.key_id is not None and ctx is not None and session.key_id != ctx.key_id:
            return JSONResponse(status_code=403, content={"error": "wrong key for session"})

        raw = await request.body()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "invalid JSON"})

        def sink(payload: Dict[str, Any]) -> None:
            evt = session.record(payload)
            session.push(evt)

        resp = await server.dispatch(msg, progress_sink=sink)
        if resp is not None:
            evt = session.record(resp)
            session.push(evt)
        return Response(status_code=202)  # responses flow back over /sse

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @router.get("/mcp/sessions", summary="List active MCP sessions (admin)")
    async def list_sessions(request: Request):
        server = _get_mcp_server(request)
        store = _get_session_store(request)
        try:
            ctx = await _authenticate(request, server)
        except AuthError:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        if ctx is None or ctx.role != "admin":
            return JSONResponse(status_code=403, content={"error": "admin required"})
        items = []
        for sid, s in store._sessions.items():
            items.append({
                "id": sid, "key_id": s.key_id, "created_at": s.created_at,
                "last_seen": s.last_seen, "initialized": s.initialized,
                "client_info": s.client_info, "buffered_events": len(s.events),
                "streams": len(s.out_queues),
            })
        return {"sessions": items, "total": len(items)}

    return router


async def mcp_session_cleanup_loop(store: SessionStore, interval: float = 60.0) -> None:
    """Background task: expire idle sessions."""
    while True:
        try:
            n = await store.cleanup()
            if n:
                log.info("expired mcp sessions", extra={"count": n})
        except Exception:
            pass
        await asyncio.sleep(interval)


def run_mcp_http(settings: Any, host: Optional[str] = None,
                 port: Optional[int] = None) -> None:
    """Standalone entry point: ``jiro mcp --transport http``."""
    import uvicorn

    from jiro.server import create_app

    app = create_app(settings)
    uvicorn.run(app, host=host or settings.host, port=port or settings.port)