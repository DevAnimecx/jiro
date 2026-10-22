"""Regression tests for the MCP tool layer.

Each test here corresponds to a tool that previously crashed with a generic
JSON-RPC ``-32603 internal server error`` because ``jiro/mcp.py`` called
functions with signatures that no longer existed, or because bad input was not
translated into a proper ``-32602`` invalid-params error.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pytest

from jiro.mcp import INTERNAL_ERROR, INVALID_PARAMS, JiroMCPServer


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _payload(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the JSON body of a successful tool result."""
    text = resp["result"]["content"][0]["text"]
    return json.loads(text.split("---\n\n", 1)[1] if "---\n\n" in text else text)


def _summary(resp: Dict[str, Any]) -> str:
    return resp["result"]["content"][0]["text"].split("---\n\n", 1)[0]


async def _call(
    server: JiroMCPServer, name: str, arguments: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return await server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
    )


def _install_fake_orchestrator(server: JiroMCPServer, response: Any) -> None:
    """Swap in a stub orchestrator so tests never touch the network."""

    class _FakeOrch:
        def __init__(self) -> None:
            self.calls: list = []

        def available_engines(self, requested: str) -> list:
            return [requested]

        async def search(self, req, *, fresh: bool = False):
            self.calls.append(req)
            if isinstance(response, Exception):
                raise response
            return response

        async def health_check(self, live: bool = True, timeout: float = 15.0):
            return {"mock": {"ok": True, "mode": "live"}}

        async def engines_info(self):
            return [{"name": "mock", "types": ["web"], "description": "", "default": True}]

        def __getattr__(self, item: str) -> Any:
            raise AttributeError(item)

    server.orchestrator = _FakeOrch()


def _make_response(engine: str = "mock", query: str = "test", count: int = 2):
    from jiro.models import OrganicResult, SearchResponse

    return SearchResponse(
        search_metadata={
            "engine": engine,
            "query": query,
            "status": "success",
            "total_time_taken": 0.1,
        },
        search_information={"organic_results_count": count},
        organic_results=[
            OrganicResult(
                position=i, title=f"R{i}", link=f"https://e.com/{i}", snippet=f"snippet {i}"
            )
            for i in range(1, count + 1)
        ],
    )


# --------------------------------------------------------------------------
# 1. ai_search — ImportError: cannot import name 'has_feature'
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ai_search_license_gate_returns_invalid_params(settings):
    """ai_search must fail with a clear licence message, not -32603."""
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await _call(server, "ai_search", {"query": "what is rust"})
        assert "error" in resp
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "ai_search" in resp["error"]["message"]
        assert "ImportError" not in resp["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_ai_search_gate_reports_licence_not_internal_error(settings):
    """The licence gate is checked first and reports itself clearly."""
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await _call(server, "ai_search", {})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "ai_search" in resp["error"]["message"]
        assert "license" in resp["error"]["message"].lower()
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# 2/3/4. social_* — SocialRouter.get_scraper() takes 2 args but 3 given
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_social_scrape_rejects_unsupported_platform(settings):
    """A non-social URL fails validation instead of raising TypeError."""
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await _call(server, "social_scrape", {"url": "https://example.com/post"})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "unsupported platform" in resp["error"]["message"]
        assert "TypeError" not in resp["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_social_scrape_requires_url(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await _call(server, "social_scrape", {})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "url" in resp["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_social_scrape_resolves_scraper_instance(settings):
    """get_scraper must return an instance with the real scrape_* methods."""
    server = JiroMCPServer(settings)
    await server.start()
    try:
        scraper = server._social_scraper("reddit")
        assert hasattr(scraper, "scrape_post")
        assert hasattr(scraper, "scrape_profile")
        assert hasattr(scraper, "search")
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_social_scrape_unknown_platform_lists_available(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        with pytest.raises(Exception) as exc:
            server._social_scraper("myspace")
        assert "myspace" in str(exc.value)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_social_search_validates_platforms(settings):
    """An unknown platform yields -32602 listing the real platforms."""
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await _call(server, "social_search", {"query": "python", "platforms": ["myspace"]})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "myspace" in resp["error"]["message"]
        assert "reddit" in resp["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_social_search_accepts_comma_string(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        # Must not raise -32603; either a real attempt or a clean error.
        resp = await _call(
            server, "social_search", {"query": "python", "platforms": "reddit,hackernews"}
        )
        assert (
            resp.get("error", {}).get("code") != INTERNAL_ERROR
            or "TypeError" not in resp["error"]["message"]
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_social_batch_validates_urls(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await _call(server, "social_batch", {"urls": []})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "urls" in resp["error"]["message"]

        resp = await _call(server, "social_batch", {})
        assert resp["error"]["code"] == INVALID_PARAMS

        resp = await _call(server, "social_batch", {"urls": [1, 2, 3]})
        assert resp["error"]["code"] == INVALID_PARAMS
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_social_batch_enforces_max_items(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        urls = [f"https://reddit.com/r/x/comments/{i}" for i in range(11)]
        resp = await _call(server, "social_batch", {"urls": urls})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "at most 10" in resp["error"]["message"]
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# 5/6. smart_search / smart_classify — IntentResult has no target/label
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_smart_classify_reports_real_intent_fields(settings):
    """Only fields that exist on IntentResult may be read."""
    server = JiroMCPServer(settings)
    try:
        data = _payload(
            await _call(server, "smart_classify", {"query": "what is quantum computing"})
        )
        assert data["intent"]
        assert "confidence" in data
        assert "target" in data
        # derived, not read from a non-existent attribute
        assert data["target"] in ("web", "social", "scrape", "news", "videos")
        assert "platform" in data
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_smart_classify_social_intent(settings):
    server = JiroMCPServer(settings)
    try:
        data = _payload(
            await _call(server, "smart_classify", {"query": "reddit discussions about rust"})
        )
        assert data["target"] == "social"
        assert data["platform"] == "reddit"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_smart_classify_scrape_intent_extracts_url(settings):
    server = JiroMCPServer(settings)
    try:
        data = _payload(
            await _call(
                server, "smart_classify", {"query": "summarize https://example.com/article"}
            )
        )
        assert data["target"] == "scrape"
        assert data["extracted"]["url"].startswith("https://")
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_smart_search_runs_without_attribute_error(settings):
    """smart_search previously died on intent.target / classifier.social_platform."""
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response())
    try:
        resp = await _call(server, "smart_search", {"query": "python asyncio"})
        assert "error" not in resp, resp.get("error")
        data = _payload(resp)
        assert "intent" in data
        assert data["intent"]["target"] in ("web", "social", "scrape", "news", "videos")
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_smart_search_news_intent_uses_news_type(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response())
    try:
        resp = await _call(server, "smart_search", {"query": "latest news about python release"})
        assert "error" not in resp, resp.get("error")
        req = server.orchestrator.calls[-1]
        assert req.type == "news"
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# 7. search_structured — extract() missing 'sources'
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_structured_requires_schema(settings):
    server = JiroMCPServer(settings)
    try:
        resp = await _call(server, "search_structured", {"query": "python libs"})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "schema" in resp["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_search_structured_rejects_invalid_schema(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response(count=2))
    try:
        resp = await _call(
            server, "search_structured", {"query": "x", "schema": {"type": "nonsense"}}
        )
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "schema" in resp["error"]["message"].lower()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_search_structured_passes_sources_to_extractor(settings, monkeypatch):
    """extract(query, sources, schema) must receive a real source list."""
    import jiro.search.structured as structured_mod

    seen: Dict[str, Any] = {}

    class _FakeExtractor:
        def __init__(self, _settings):
            pass

        async def extract(self, query, sources, schema, mode=None):
            seen["query"] = query
            seen["sources"] = sources
            seen["schema"] = schema
            return structured_mod.ExtractionResult(
                data={"name": "x"}, provider="test", model=None, confidence=0.9, mode="extractive"
            )

    monkeypatch.setattr(structured_mod, "StructuredExtractor", _FakeExtractor)

    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response(count=3))
    try:
        resp = await _call(
            server,
            "search_structured",
            {
                "query": "python libs",
                "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            },
        )
        assert "error" not in resp, resp.get("error")
        assert seen["query"] == "python libs"
        assert len(seen["sources"]) == 3
        assert seen["sources"][0]["url"].startswith("https://")
        data = _payload(resp)
        assert data["data"] == {"name": "x"}
        assert data["sources_used"] == 3
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_search_structured_accepts_schema_as_json_string(settings):
    server = JiroMCPServer(settings)
    try:
        resp = await _call(server, "search_structured", {"query": "x", "schema": "{not json"})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "schema" in resp["error"]["message"]
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# 8. search_hybrid — ValidationError: engine — Input should be a valid string
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_hybrid_without_engine_does_not_crash(settings):
    """Omitting engine used to pass None into SearchRequest -> -32603."""
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response(count=3))
    try:
        resp = await _call(server, "search_hybrid", {"query": "best laptop"})
        assert "error" not in resp, resp.get("error")
        req = server.orchestrator.calls[-1]
        assert req.engine is not None
        assert isinstance(req.engine, str)
        # the tool must actually engage hybrid mode
        assert req.mode == "hybrid"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_search_hybrid_maps_max_results_to_num(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response(count=3))
    try:
        await _call(server, "search_hybrid", {"query": "q", "max_results": 7})
        assert server.orchestrator.calls[-1].num == 7
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_search_hybrid_rejects_bad_engine_and_type(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response())
    try:
        resp = await _call(server, "search_hybrid", {"query": "q", "engine": "altavista"})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "altavista" in resp["error"]["message"]

        resp = await _call(server, "search_hybrid", {"query": "q", "type": "nonsense"})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "nonsense" in resp["error"]["message"]
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# 9. search — input validation and honest engine reporting
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_rejects_bad_inputs(settings):
    """num:0, num:-5, empty q, missing q, bad type -> -32602, not -32603."""
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response())
    try:
        cases = [
            ({"q": "x", "num": 0}, "num"),
            ({"q": "x", "num": -5}, "num"),
            ({"q": "x", "num": 1000}, "num"),
            ({"q": ""}, "q"),
            ({"q": "   "}, "q"),
            ({}, "q"),
            ({"q": "x", "type": "nonsense"}, "nonsense"),
            ({"q": "x", "engine": "altavista"}, "altavista"),
            ({"q": "x" * 501}, "too long"),
        ]
        for arguments, expected in cases:
            resp = await _call(server, "search", arguments)
            assert "error" in resp, f"{arguments} should have failed"
            assert resp["error"]["code"] == INVALID_PARAMS, (
                f"{arguments} -> {resp['error']['code']}"
            )
            assert expected in resp["error"]["message"], (
                f"{arguments} -> {resp['error']['message']}"
            )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_search_rejects_non_string_query(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response())
    try:
        resp = await _call(server, "search", {"q": 12345})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "must be a string" in resp["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_search_reports_engine_substitution(settings):
    """The summary must never claim the requested engine served the results."""
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response(engine="duckduckgo"))
    try:
        resp = await _call(server, "search", {"q": "x", "engine": "google"})
        assert "error" not in resp, resp.get("error")
        summary = _summary(resp)
        assert "duckduckgo" in summary
        assert "requested google" in summary
        # must not present the substituted engine as a plain match
        assert "Engine: google\n" not in summary
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_search_summary_matches_engine_when_not_substituted(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response(engine="bing"))
    try:
        resp = await _call(server, "search", {"q": "x", "engine": "bing"})
        assert "Engine: bing" in _summary(resp)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auto_engine_is_not_labelled_a_fallback(settings):
    """engine=auto delegating routing is not a substitution."""
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response(engine="duckduckgo"))
    try:
        resp = await _call(server, "search", {"q": "x", "engine": "auto"})
        assert "error" not in resp, resp.get("error")
        summary = _summary(resp)
        assert "auto-selected" in summary
        assert "fallback used" not in summary
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_hybrid_engine_is_not_labelled_a_fallback(settings):
    """A hybrid fusion is an aggregation, not a substituted engine."""
    from jiro.models import SearchResponse

    response = SearchResponse(
        search_metadata={
            "engine": "hybrid",
            "query": "q",
            "status": "success",
            "engines_used": ["google", "bing", "brave"],
        },
        organic_results=[],
    )
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, response)
    try:
        resp = await _call(server, "search_hybrid", {"query": "q"})
        assert "error" not in resp, resp.get("error")
        summary = _summary(resp)
        assert "Engine: hybrid" in summary
        assert "fused from google, bing, brave" in summary
        assert "fallback used" not in summary
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_summary_lists_skipped_engines(settings):
    """A fallback must name the engines that were skipped and why."""
    from jiro.models import SearchResponse

    response = SearchResponse(
        search_metadata={
            "engine": "bing",
            "query": "q",
            "status": "success",
            "skipped_engines": [
                {"engine": "google", "error": "bot wall", "error_code": "engine_blocked"},
            ],
        },
        organic_results=[],
    )
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, response)
    try:
        resp = await _call(server, "search", {"q": "x", "engine": "google"})
        summary = _summary(resp)
        assert "fallback used" in summary
        assert "Skipped: google (engine_blocked)" in summary
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_summary_discloses_short_page(settings):
    from jiro.models import SearchResponse

    response = SearchResponse(
        search_metadata={
            "engine": "duckduckgo",
            "query": "q",
            "requested_num": 50,
            "truncated": True,
            "pagination_supported": False,
        },
        organic_results=[],
    )
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, response)
    try:
        resp = await _call(server, "search", {"q": "x", "engine": "duckduckgo", "num": 50})
        summary = _summary(resp)
        assert "requested 50" in summary
        assert "single fixed result page" in summary
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# 10. list_engines — capability matrix must match the engines
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_engines_matrix_matches_registry(settings):
    server = JiroMCPServer(settings)
    try:
        data = _payload(await _call(server, "list_engines"))
        by_name = {e["name"]: e for e in data["engines"]}
        assert len(by_name) == 9
        # every advertised type must be implemented by that engine class
        registry = server._engine_registry()
        for name, entry in by_name.items():
            actual = set(registry.get(name).types)
            assert set(entry["types"]) == actual, name
        # no engine may claim a type it does not implement
        for type_name, capable in data["type_matrix"].items():
            for name in capable:
                assert type_name in by_name[name]["types"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_list_engines_does_not_overclaim(settings):
    """Regression: hardcoded table claimed videos/places for engines without them."""
    server = JiroMCPServer(settings)
    try:
        data = _payload(await _call(server, "list_engines"))
        by_name = {e["name"]: e for e in data["engines"]}
        assert "places" not in by_name["bing"]["types"]
        assert "videos" not in by_name["duckduckgo"]["types"]
        assert "shopping" not in by_name["bing"]["types"]
        assert "notes" in data
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# 11. compare_engines / social listings
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_compare_engines_flags_substitution(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response(engine="bing"))
    try:
        resp = await _call(server, "compare_engines", {"query": "q", "engines": ["google"]})
        assert "error" not in resp, resp.get("error")
        entry = _payload(resp)["results"]["google"]
        assert entry["engine_used"] == "bing"
        assert entry["substituted"] is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_compare_engines_validates_input(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response())
    try:
        assert (await _call(server, "compare_engines", {}))["error"]["code"] == INVALID_PARAMS
        resp = await _call(server, "compare_engines", {"query": "q", "engines": ["altavista"]})
        assert resp["error"]["code"] == INVALID_PARAMS
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_list_social_platforms_from_registry(settings):
    server = JiroMCPServer(settings)
    try:
        data = _payload(await _call(server, "list_social_platforms"))
        assert data["total"] == 12
        assert "reddit" in data["searchable"]
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# generic argument handling
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_object_arguments_rejected(settings):
    server = JiroMCPServer(settings)
    try:
        resp = await server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "search", "arguments": "not-an-object"},
            }
        )
        assert resp["error"]["code"] == INVALID_PARAMS
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_validate_int_accepts_numeric_string(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response())
    try:
        resp = await _call(server, "search", {"q": "x", "num": "4"})
        assert "error" not in resp, resp.get("error")
        assert server.orchestrator.calls[-1].num == 4
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_validate_int_rejects_bool(settings):
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response())
    try:
        resp = await _call(server, "search", {"q": "x", "num": True})
        assert resp["error"]["code"] == INVALID_PARAMS
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_scrape_rejects_bad_format(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await _call(server, "scrape", {"url": "https://example.com", "format": "pdf"})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "pdf" in resp["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_scrape_rejects_overlong_url(settings):
    server = JiroMCPServer(settings)
    await server.start()
    try:
        resp = await _call(server, "scrape", {"url": "https://example.com/" + "a" * 3000})
        assert resp["error"]["code"] == INVALID_PARAMS
        assert "too long" in resp["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_engine_error_surfaces_real_reason(settings):
    """An EngineError must not be flattened into -32603."""
    from jiro.errors import EngineBlockedError

    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, EngineBlockedError("google rate limited (HTTP 429)"))
    try:
        resp = await _call(server, "search", {"q": "x", "engine": "google"})
        assert resp["error"]["code"] != INTERNAL_ERROR
        assert "429" in resp["error"]["message"]
        assert resp["error"]["data"]["error_code"] == "engine_blocked"
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# argument validation must not depend on a started server
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_type_engine_mismatch_reports_invalid_params(settings):
    """An engine that cannot serve the requested type is a client error."""
    server = JiroMCPServer(settings)
    try:  # note: start() is deliberately not called
        for engine_type in [("shopping", "bing"), ("places", "bing"), ("videos", "duckduckgo")]:
            search_type, engine = engine_type
            resp = await _call(server, "search", {"q": "x", "type": search_type, "engine": engine})
            assert resp["error"]["code"] == INVALID_PARAMS, engine_type
            assert search_type in resp["error"]["message"]
            # must name the engines that do support it
            assert resp["error"]["data"]["supported_by"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_validation_precedes_orchestrator_requirement(settings):
    """Bad input is diagnosed as -32602 even on an unstarted server."""
    server = JiroMCPServer(settings)
    try:
        for tool, arguments in [
            ("search", {"q": ""}),
            ("search", {"q": "x", "num": 0}),
            ("search", {"q": "x", "type": "nonsense"}),
            ("search_hybrid", {}),
            ("search_hybrid", {"query": "x", "engine": "altavista"}),
            ("search_structured", {"query": "x"}),
            ("search_structured", {"query": "x", "schema": "nope"}),
            ("compare_engines", {"query": "x", "engines": ["altavista"]}),
            ("smart_search", {}),
            ("smart_classify", {"query": "x" * 501}),
        ]:
            resp = await _call(server, tool, arguments)
            assert "error" in resp, f"{tool} {arguments}"
            assert resp["error"]["code"] == INVALID_PARAMS, (
                f"{tool} {arguments} -> {resp['error']['code']}"
            )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_smart_search_routes_schema_to_structured_extraction(settings):
    """A supplied schema must short-circuit to structured extraction."""
    server = JiroMCPServer(settings)
    _install_fake_orchestrator(server, _make_response(count=1))
    try:
        resp = await _call(
            server,
            "smart_search",
            {
                "query": "python libraries",
                "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
            },
        )
        # Extraction runs (extractor is real here) so a result or a clean
        # error is acceptable — but never an attribute/type error.
        if "error" in resp:
            assert resp["error"]["code"] != INTERNAL_ERROR
        else:
            assert "data" in _payload(resp)
    finally:
        await server.stop()
