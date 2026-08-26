"""Tests for the production-hardened MCP server: protocol negotiation,
progress notifications, cancellation, HTTP transports, sessions, resume, auth.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from jiro.mcp import (
    JiroMCPServer,
    ProgressReporter,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from jiro.mcp_http import SessionStore, MCPSession


@pytest.mark.asyncio
async def test_protocol_negotiation_newest(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-03-26"}})
    assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS


@pytest.mark.asyncio
async def test_protocol_negotiation_fallback(settings):
    """Old clients get the old protocol version they asked for."""
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2024-11-05"}})
    assert resp["result"]["protocolVersion"] == "2024-11-05"


@pytest.mark.asyncio
async def test_protocol_negotiation_unknown_version(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "1999-01-01"}})
    # falls back to server's newest supported version
    assert resp["result"]["protocolVersion"] in SUPPORTED_PROTOCOL_VERSIONS


@pytest.mark.asyncio
async def test_initialize_includes_instructions(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {}})
    assert "instructions" in resp["result"]
    assert "search" in resp["result"]["instructions"]


@pytest.mark.asyncio
async def test_progress_reporter_sends_notifications():
    sent = []
    reporter = ProgressReporter(progress_token="tok-123", sink=sent.append)
    reporter.report(0.5, total=1.0, message="halfway")

    assert len(sent) == 1
    msg = sent[0]
    assert msg["method"] == "notifications/progress"
    assert msg["params"]["progressToken"] == "tok-123"
    assert msg["params"]["progress"] == 0.5
    assert msg["params"]["total"] == 1.0
    assert msg["params"]["message"] == "halfway"


@pytest.mark.asyncio
async def test_progress_reporter_disabled_without_token():
    sent = []
    reporter = ProgressReporter(sink=sent.append)
    reporter.report(1.0)
    assert sent == []
    assert reporter.enabled is False


@pytest.mark.asyncio
async def test_tool_call_with_progress_token_emits_notifications(settings):
    server = JiroMCPServer(settings)
    await server.start()
    notifications = []

    def sink(payload):
        notifications.append(payload)

    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {"q": "test", "engine": "duckduckgo", "num": 3},
                "_meta": {"progressToken": "pt-42"},
            },
        }, progress_sink=sink)

        assert "error" not in resp
        progress_msgs = [n for n in notifications
                         if n.get("method") == "notifications/progress"]
        assert len(progress_msgs) >= 1
        tokens = {m["params"]["progressToken"] for m in progress_msgs}
        assert tokens == {"pt-42"}
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_cancellation_of_running_request(settings):
    """notifications/cancelled aborts an in-flight tools/call."""
    server = JiroMCPServer(settings)
    await server.start()

    slow_started = asyncio.Event()

    async def slow_search(args, progress):
        slow_started.set()
        await asyncio.sleep(30)  # would hang forever without cancel
        raise AssertionError("should have been cancelled")

    server._tool_search = slow_search  # type: ignore[method-assign]

    task = asyncio.create_task(server.dispatch({
        "jsonrpc": "2.0", "id": "req-99", "method": "tools/call",
        "params": {"name": "search", "arguments": {"q": "x", "engine": "duckduckgo"}},
    }))

    await asyncio.wait_for(slow_started.wait(), timeout=5)
    # send the cancellation notification while the call runs
    await server.dispatch({"jsonrpc": "2.0", "method": "notifications/cancelled",
                           "params": {"requestId": "req-99"}})

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    # inflight registry must be cleaned up
    assert "req-99" not in server._inflight


@pytest.mark.asyncio
async def test_resources_list_has_builtin_resources(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    uris = [r["uri"] for r in resp["result"]["resources"]]
    assert "jiro://engines" in uris
    assert "jiro://compliance" in uris


@pytest.mark.asyncio
async def test_resources_read_engines(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "resources/read",
            "params": {"uri": "jiro://engines"}})
        contents = resp["result"]["contents"]
        assert contents[0]["uri"] == "jiro://engines"
        data = json.loads(contents[0]["text"])
        assert isinstance(data, list)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_resources_read_compliance(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "resources/read",
            "params": {"uri": "jiro://compliance"}})
        text = resp["result"]["contents"][0]["text"]
        data = json.loads(text)
        assert "engines" in data
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_resources_templates_list(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "resources/templates/list"})
    assert resp["result"]["resourceTemplates"] == []


@pytest.mark.asyncio
async def test_notify_sinks_broadcast(settings):
    server = JiroMCPServer(settings)
    received = []
    unsubscribe = server.add_notify_sink(received.append)

    server._broadcast({"jsonrpc": "2.0", "method": "notifications/test"})
    assert received == [{"jsonrpc": "2.0", "method": "notifications/test"}]

    unsubscribe()
    server._broadcast({"jsonrpc": "2.0", "method": "notifications/after"})
    assert len(received) == 1


# ---------------------------------------------------------------------------
# HTTP transport tests (via ASGI transport)
# ---------------------------------------------------------------------------

@pytest.fixture
def http_client(settings, monkeypatch):
    from starlette.testclient import TestClient
    from jiro.server import create_app

    # bound long-lived SSE streams so TestClient can shut down cleanly
    monkeypatch.setenv("JIRO_MCP_SSE_MAX_LIFETIME", "3")
    settings.raw.setdefault("server", {})["host"] = "127.0.0.1"
    settings.raw["server"]["port"] = 18000
    settings.raw["scraping"]["robots_txt"] = {"enabled": False}

    app = create_app(settings)
    with TestClient(app) as client:
        yield client


class TestStreamableHTTPTransport:
    def test_initialize_returns_session_header(self, http_client):
        resp = http_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"clientInfo": {"name": "test-client", "version": "1.0"}},
        })
        assert resp.status_code == 200
        assert "Mcp-Session-Id" in resp.headers
        body = resp.json()
        assert body["result"]["serverInfo"]["name"] == "jiro"

    def test_ping_with_session(self, http_client):
        init = http_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        sid = init.headers["Mcp-Session-Id"]

        resp = http_client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
            headers={"Mcp-Session-Id": sid})
        assert resp.status_code == 200
        assert resp.json()["result"] == {}

    def test_tools_list_over_http(self, http_client):
        resp = http_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [t["name"] for t in resp.json()["result"]["tools"]]
        assert names == ["search", "scrape", "ai_search"]

    def test_unknown_session_404(self, http_client):
        resp = http_client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Mcp-Session-Id": "nonexistent"})
        assert resp.status_code == 404

    def test_delete_session(self, http_client):
        init = http_client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        sid = init.headers["Mcp-Session-Id"]

        resp = http_client.delete("/mcp", headers={"Mcp-Session-Id": sid})
        assert resp.status_code == 204

        # session gone
        resp2 = http_client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
            headers={"Mcp-Session-Id": sid})
        assert resp2.status_code == 404

    def test_batch_request(self, http_client):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]
        resp = http_client.post("/mcp", json=msgs)
        results = resp.json()
        assert isinstance(results, list)
        ids = [r["id"] for r in results]
        assert ids == [1, 2]

    def test_parse_error_400(self, http_client):
        resp = http_client.post(
            "/mcp", content=b"{invalid",
            headers={"Content-Type": "application/json"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32700

    def test_initialize_sse_response(self, http_client):
        resp = http_client.post(
            "/mcp", json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "text/event-stream"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "event: message" in resp.text
        assert '"id":1' in resp.text.replace(" ", "").replace('"id": 1', '"id":1')


class TestLegacySSETransport:
    def test_sse_endpoint_announces_messages_url(self, http_client):
        with http_client.stream("GET", "/sse") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            chunks = []
            for line in resp.iter_lines():
                chunks.append(line)
                if line.startswith("data:"):
                    break
            endpoint_line = next(line for line in chunks if line.startswith("data:"))
            payload = json.loads(endpoint_line[len("data:"):].strip())
            assert "/messages" in payload["uri"]
            assert "sessionId=" in payload["uri"]

    def test_messages_roundtrip(self, http_client):
        """POST /messages resolves against a live session; responses are buffered."""
        import asyncio
        store: SessionStore = http_client.app.state.mcp_sessions

        loop = asyncio.new_event_loop()
        session = loop.run_until_complete(store.create(None))
        try:
            resp = http_client.post(f"/messages?sessionId={session.id}",
                                    json={"jsonrpc": "2.0", "id": 5, "method": "ping"})
            assert resp.status_code == 202

            # the ping response must have been recorded into the session buffer
            # (a connected /sse client would receive it there)
            seqs = [e.payload.get("id") for e in session.events]
            assert 5 in seqs
        finally:
            loop.run_until_complete(store.delete(session.id))
            loop.close()

    def test_messages_unknown_session_404(self, http_client):
        resp = http_client.post("/messages?sessionId=bogus",
                                json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert resp.status_code == 404


class TestSessionStore:
    @pytest.mark.asyncio
    async def test_create_get_delete(self):
        store = SessionStore(ttl_seconds=60)
        s = await store.create(None)
        got = await store.get(s.id)
        assert got is s
        assert await store.delete(s.id) is True
        assert await store.get(s.id) is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self):
        store = SessionStore(ttl_seconds=0)
        s = await store.create(None)
        import time as _time
        s.last_seen = _time.time() - 10
        assert await store.get(s.id) is None  # expired

    @pytest.mark.asyncio
    async def test_event_replay_for_resume(self):
        s = MCPSession(id="s1")
        e1 = s.record({"n": 1})
        e2 = s.record({"n": 2})
        e3 = s.record({"n": 3})

        replayed = s.replay_after(e1.seq)
        assert [e.seq for e in replayed] == [e2.seq, e3.seq]
        assert replayed[0].payload == {"n": 2}

    @pytest.mark.asyncio
    async def test_push_to_subscribers(self):
        s = MCPSession(id="s1")
        q = s.subscribe()
        evt = s.record({"hello": True})
        s.push(evt)
        got = q.get_nowait()
        assert got.payload == {"hello": True}
        s.unsubscribe(q)


@pytest.mark.asyncio
async def test_mcp_server_stop_cancels_inflight(settings):
    server = JiroMCPServer(settings)
    await server.start()

    started = asyncio.Event()
    finished = asyncio.Event()

    async def blocker(*args, **kwargs):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            finished.set()
            raise

    async def runner():
        task = asyncio.current_task()
        server._inflight["bg"] = task  # type: ignore[arg-type]
        await blocker()

    bg = asyncio.create_task(runner())
    await asyncio.wait_for(started.wait(), timeout=2)
    await server.stop()
    await asyncio.wait_for(finished.wait(), timeout=2)
    bg.cancel()