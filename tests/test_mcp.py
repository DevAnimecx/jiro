"""MCP server protocol tests."""

from __future__ import annotations

import json

import pytest

from jiro.mcp import JiroMCPServer


@pytest.mark.asyncio
async def test_initialize(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {}})
    assert resp["result"]["serverInfo"]["name"] == "jiro"
    assert "tools" in resp["result"]["capabilities"]
    assert "resources" in resp["result"]["capabilities"]
    assert "prompts" in resp["result"]["capabilities"]
    assert resp["result"]["protocolVersion"] == "2025-03-26"


@pytest.mark.asyncio
async def test_initialize_with_client_info(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"clientInfo": {"name": "claude-desktop", "version": "1.0.0"}},
    })
    assert resp["result"]["serverInfo"]["name"] == "jiro"


@pytest.mark.asyncio
async def test_ping(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp["result"] == {}


@pytest.mark.asyncio
async def test_tools_list(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    expected = [
        "search", "scrape", "ai_search", "search_hybrid", "search_structured",
        "social_scrape", "social_search", "social_batch", "smart_search", "smart_classify",
        "compare_engines", "monitor_status", "health_check", "cache_stats", "list_engines",
        "list_social_platforms"
    ]
    assert names == expected


@pytest.mark.asyncio
async def test_tools_list_schemas(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    for tool in resp["result"]["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"
        # Most tools have required fields, but some like list_engines don't
        # Just verify inputSchema is a valid object
        assert isinstance(tool["inputSchema"], dict)


@pytest.mark.asyncio
async def test_tools_list_search_engines(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    search_tool = next(t for t in resp["result"]["tools"] if t["name"] == "search")
    engines = search_tool["inputSchema"]["properties"]["engine"]["enum"]
    assert "google" in engines
    assert "bing" in engines
    assert "brave" in engines
    assert "youtube" in engines
    assert "amazon" in engines
    assert len(engines) == 9


@pytest.mark.asyncio
async def test_tools_call_search(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "search",
                       "arguments": {"q": "hello", "engine": "google", "num": 2}},
        })
        assert "error" not in resp
        assert resp["result"]["content"][0]["type"] == "text"
        text = resp["result"]["content"][0]["text"]
        # Response contains summary + JSON separated by ---
        assert "Query:" in text
        assert "Engine:" in text
        # Extract JSON part after the --- separator
        json_part = text.split("---\n\n", 1)[1] if "---\n\n" in text else text
        data = json.loads(json_part)
        assert "organic_results" in data
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tools_call_scrape(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "scrape",
                       "arguments": {"url": "https://httpbin.org/html",
                                      "format": "markdown"}},
        })
        assert "error" not in resp
        assert resp["result"]["content"][0]["type"] == "text"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tools_call_scrape_missing_url(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "scrape", "arguments": {}},
        })
        assert resp["error"]["code"] == -32602
        assert "url is required" in resp["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tools_call_unknown(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "unknown tool" in resp["error"]["message"]


@pytest.mark.asyncio
async def test_unknown_method(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 5, "method": "bogus"})
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_notification_returns_none(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp is None


@pytest.mark.asyncio
async def test_notification_cancelled_returns_none(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "method": "notifications/cancelled",
        "params": {"requestId": "abc"},
    })
    assert resp is None


@pytest.mark.asyncio
async def test_resources_list(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    uris = [r["uri"] for r in resp["result"]["resources"]]
    assert "jiro://engines" in uris


@pytest.mark.asyncio
async def test_resources_read_not_found(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "resources/read",
        "params": {"uri": "jiro://nonexistent"},
    })
    assert resp["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_resources_read_engines(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "resources/read",
            "params": {"uri": "jiro://engines"},
        })
        contents = resp["result"]["contents"]
        assert contents[0]["uri"] == "jiro://engines"
        data = json.loads(contents[0]["text"])
        assert isinstance(data, list)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_prompts_list(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
    names = [p["name"] for p in resp["result"]["prompts"]]
    assert "search_and_summarize" in names
    assert "compare_engines" in names


@pytest.mark.asyncio
async def test_prompts_get_search_and_summarize(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "prompts/get",
        "params": {"name": "search_and_summarize",
                   "arguments": {"topic": "python web scraping"}},
    })
    assert "messages" in resp["result"]
    assert resp["result"]["messages"][0]["role"] == "user"
    assert "python web scraping" in resp["result"]["messages"][0]["content"]["text"]


@pytest.mark.asyncio
async def test_prompts_get_compare_engines(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "prompts/get",
        "params": {"name": "compare_engines",
                   "arguments": {"query": "fastapi tutorial"}},
    })
    assert "messages" in resp["result"]
    assert "fastapi tutorial" in resp["result"]["messages"][0]["content"]["text"]


@pytest.mark.asyncio
async def test_prompts_get_unknown(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "prompts/get",
        "params": {"name": "nonexistent"},
    })
    assert resp["error"]["code"] == -32602
    assert "unknown prompt" in resp["error"]["message"]


@pytest.mark.asyncio
async def test_completion_engine(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "completion/complete",
        "params": {
            "ref": {"type": "ref/tool", "name": "search"},
            "argument": {"name": "engine", "value": "go"},
        },
    })
    assert resp["result"]["completion"]["values"] == ["google"]
    assert resp["result"]["completion"]["hasMore"] is False


@pytest.mark.asyncio
async def test_completion_search_type(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "completion/complete",
        "params": {
            "ref": {"type": "ref/tool", "name": "search"},
            "argument": {"name": "type", "value": "vi"},
        },
    })
    assert resp["result"]["completion"]["values"] == ["videos"]


@pytest.mark.asyncio
async def test_completion_time_range(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "completion/complete",
        "params": {
            "ref": {"type": "ref/tool", "name": "search"},
            "argument": {"name": "time_range", "value": "we"},
        },
    })
    assert resp["result"]["completion"]["values"] == ["week"]


@pytest.mark.asyncio
async def test_completion_scrape_format(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "completion/complete",
        "params": {
            "ref": {"type": "ref/tool", "name": "scrape"},
            "argument": {"name": "format", "value": "mar"},
        },
    })
    assert resp["result"]["completion"]["values"] == ["markdown"]


@pytest.mark.asyncio
async def test_completion_no_match(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "completion/complete",
        "params": {
            "ref": {"type": "ref/tool", "name": "search"},
            "argument": {"name": "engine", "value": "zzz"},
        },
    })
    assert resp["result"]["completion"]["values"] == []


@pytest.mark.asyncio
async def test_error_response_format(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "bogus"})
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert "error" in resp
    assert "code" in resp["error"]
    assert "message" in resp["error"]


@pytest.mark.asyncio
async def test_resources_list_has_new_resources(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    uris = [r["uri"] for r in resp["result"]["resources"]]
    assert "jiro://social_platforms" in uris
    assert "jiro://plans" in uris
    assert "jiro://plugins" in uris


@pytest.mark.asyncio
async def test_resources_read_social_platforms(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "resources/read",
        "params": {"uri": "jiro://social_platforms"},
    })
    contents = resp["result"]["contents"]
    assert contents[0]["uri"] == "jiro://social_platforms"
    data = json.loads(contents[0]["text"])
    assert "platforms" in data
    assert len(data["platforms"]) == 12


@pytest.mark.asyncio
async def test_resources_read_plans(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "resources/read",
        "params": {"uri": "jiro://plans"},
    })
    contents = resp["result"]["contents"]
    data = json.loads(contents[0]["text"])
    assert "plans" in data
    assert len(data["plans"]) == 2


@pytest.mark.asyncio
async def test_resources_read_plugins(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "resources/read",
        "params": {"uri": "jiro://plugins"},
    })
    contents = resp["result"]["contents"]
    data = json.loads(contents[0]["text"])
    assert "plugins" in data


@pytest.mark.asyncio
async def test_prompts_list_has_new_prompts(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
    names = [p["name"] for p in resp["result"]["prompts"]]
    assert "social_research" in names
    assert "deep_research" in names
    assert "extract_structured" in names
    assert "competitor_analysis" in names


@pytest.mark.asyncio
async def test_prompts_get_social_research(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "prompts/get",
        "params": {"name": "social_research",
                   "arguments": {"topic": "AI news", "platforms": "twitter,reddit"}},
    })
    assert "messages" in resp["result"]
    assert "AI news" in resp["result"]["messages"][0]["content"]["text"]


@pytest.mark.asyncio
async def test_prompts_get_deep_research(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "prompts/get",
        "params": {"name": "deep_research",
                   "arguments": {"question": "quantum computing"}},
    })
    assert "messages" in resp["result"]
    assert "quantum computing" in resp["result"]["messages"][0]["content"]["text"]


@pytest.mark.asyncio
async def test_prompts_get_extract_structured(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "prompts/get",
        "params": {"name": "extract_structured",
                   "arguments": {"query": "python web scraping libraries",
                                  "fields": '{"name": {}, "stars": {}}'}},
    })
    assert "messages" in resp["result"]
    assert "python web scraping libraries" in resp["result"]["messages"][0]["content"]["text"]


@pytest.mark.asyncio
async def test_prompts_get_competitor_analysis(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "prompts/get",
        "params": {"name": "competitor_analysis",
                   "arguments": {"competitor": "openai", "focus": "products"}},
    })
    assert "messages" in resp["result"]
    assert "openai" in resp["result"]["messages"][0]["content"]["text"]


@pytest.mark.asyncio
async def test_tools_list_has_new_tools(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert "health_check" in names
    assert "cache_stats" in names
    assert "list_engines" in names
    assert "list_social_platforms" in names
    assert len(names) == 16


@pytest.mark.asyncio
async def test_tools_call_health_check(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "health_check", "arguments": {}},
        })
        assert "error" not in resp
        text = resp["result"]["content"][0]["text"]
        data = json.loads(text.split("---\n\n", 1)[1] if "---\n\n" in text else text)
        assert "status" in data
        assert data["status"] in ("healthy", "degraded")
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tools_call_cache_stats(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "cache_stats", "arguments": {}},
        })
        assert "error" not in resp
        text = resp["result"]["content"][0]["text"]
        data = json.loads(text.split("---\n\n", 1)[1] if "---\n\n" in text else text)
        assert "enabled" in data
        assert "type" in data
        assert "ttl" in data
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tools_call_list_engines(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "list_engines", "arguments": {}},
    })
    assert "error" not in resp
    text = resp["result"]["content"][0]["text"]
    data = json.loads(text.split("---\n\n", 1)[1] if "---\n\n" in text else text)
    assert "engines" in data
    assert len(data["engines"]) == 9
    engine_names = [e["name"] for e in data["engines"]]
    assert "google" in engine_names
    assert "bing" in engine_names


@pytest.mark.asyncio
async def test_tools_call_list_social_platforms(settings):
    server = JiroMCPServer(settings)
    resp = await server.dispatch({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "list_social_platforms", "arguments": {}},
    })
    assert "error" not in resp
    text = resp["result"]["content"][0]["text"]
    data = json.loads(text.split("---\n\n", 1)[1] if "---\n\n" in text else text)
    assert "platforms" in data
    assert len(data["platforms"]) == 12


@pytest.mark.asyncio
async def test_tools_call_scrape_rejects_internal_url(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await server.dispatch({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "scrape",
                       "arguments": {"url": "http://169.254.169.254/metadata"}},
        })
        assert "error" in resp
        assert resp["error"]["code"] == -32602
        assert "validation" in resp["error"]["message"].lower() or "blocked" in resp["error"]["message"].lower()
    finally:
        await server.stop()
