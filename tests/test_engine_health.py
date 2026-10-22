"""Tests for the orchestrator's engine-fallback, pagination and health logic.

These cover the design-level problems rather than individual MCP tools:

* the requested engine is always recorded so a substituted engine can never
  be mistaken for the one that was asked for;
* ``num`` above one SERP page is either honoured or explicitly disclosed;
* ``health_check`` performs a real request instead of reporting every engine
  healthy because its module imported.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from jiro.errors import EngineBlockedError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine, SearchOrchestrator


class _StubEngine(BaseEngine):
    """Engine stub that returns a configurable number of results per page."""

    name = "stub"
    types = ["web", "images", "news", "videos"]
    supports_pagination = True
    pages = 1
    per_page = 3
    blocked = False

    def __init__(self, client, settings) -> None:
        super().__init__(client, settings)
        self.calls: List[int] = []

    async def search(self, req: SearchRequest) -> SearchResponse:
        self.calls.append(req.start)
        if self.blocked:
            raise EngineBlockedError(f"{self.name} served a bot-wall")
        # Titles/snippets echo the query so the orchestrator's relevance gate
        # (which guards against bot-wall pages) does not discard the results.
        # Each page yields distinct links so dedup does not hide the pages.
        results = [
            OrganicResult(
                position=req.start + i,
                title=f"{req.q} result {i}",
                link=f"https://e.com/{req.start}-{i}",
                snippet=f"snippet about {req.q} on page {req.start}",
            )
            for i in range(self.per_page)
        ]
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={"organic_results_count": len(results)},
            organic_results=results,
        )


@pytest.fixture
def stub_registry(monkeypatch):
    """Install the stub engine as the only registered engine."""
    from jiro.scraping.engines import EngineRegistry

    registry = EngineRegistry()
    monkeypatch.setattr(_StubEngine, "name", "stub")
    registry.register(_StubEngine)

    def _fake_build() -> EngineRegistry:
        return registry

    monkeypatch.setattr("jiro.scraping.engines._build_registry", _fake_build)
    return registry


def _configure(settings, engines, fallback):
    """Point the orchestrator at the stub engines only (keys live under scraping)."""
    scraping = settings.raw["scraping"]
    scraping["engines"] = list(engines)
    scraping["fallback_order"] = list(fallback)
    scraping["default_engine"] = engines[0]
    scraping.setdefault("robots_txt", {})["enabled"] = False
    return settings


@pytest.fixture
def orchestrator(settings, stub_registry):
    from jiro.cache import CacheManager

    _configure(settings, ["stub"], ["stub"])
    return SearchOrchestrator(settings, None, CacheManager(None, memory=True, ttl=60))


# --------------------------------------------------------------------------
# engine substitution is recorded
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_records_requested_engine(orchestrator):
    _StubEngine.blocked = False
    result = await orchestrator.search(SearchRequest(q="hello", engine="stub", num=3))
    meta = result.search_metadata
    assert meta["engine"] == "stub"
    assert meta["engine_requested"] == "stub"
    assert meta["engine_substituted"] is False
    # Served by the requested engine: full success, not partial.
    assert meta["status"] == "success"
    # Served by the requested engine: full success, not partial.
    assert meta["status"] == "success"
    # Served by the requested engine: full success, not partial.
    assert meta["status"] == "success"


@pytest.mark.asyncio
async def test_search_records_substitution_when_engine_blocked(settings, stub_registry):
    """A blocked engine must be visible as substituted, not silently swapped."""
    _configure(settings, ["stub", "stub2"], ["stub", "stub2"])

    class _GoodEngine(_StubEngine):
        name = "stub2"
        blocked = False

    _StubEngine.blocked = True
    stub_registry.register(_GoodEngine)

    from jiro.cache import CacheManager

    orch = SearchOrchestrator(settings, None, CacheManager(None, memory=True, ttl=60))
    result = await orch.search(SearchRequest(q="hello", engine="stub", num=3))
    meta = result.search_metadata
    _StubEngine.blocked = False

    assert meta["engine"] == "stub2"
    assert meta["engine_requested"] == "stub"
    assert meta["engine_substituted"] is True
    # A substituted result must not claim full success, or a caller cannot tell
    # they are looking at a different index than they asked for.
    assert meta["status"] == "partial"
    # the reason must be preserved
    assert any(e.get("error_code") == "engine_blocked" for e in meta.get("skipped_engines", []))


# --------------------------------------------------------------------------
# num / pagination
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pagination_fills_requested_num(orchestrator):
    """num beyond one page is topped up from later pages of the same engine."""
    _StubEngine.blocked = False
    _StubEngine.per_page = 3
    result = await orchestrator.search(SearchRequest(q="hello", engine="stub", num=9))
    _StubEngine.per_page = 3
    assert len(result.organic_results) == 9
    assert result.search_metadata.get("truncated") is not True
    assert result.search_information["organic_results_count"] == 9
    # positions must stay unique/deduped
    links = [r.link for r in result.organic_results]
    assert len(set(links)) == len(links)


@pytest.mark.asyncio
async def test_short_result_is_disclosed_not_silent(orchestrator):
    """If the engine cannot fill num, say so instead of returning a short page."""
    _StubEngine.blocked = False
    _StubEngine.per_page = 3
    _StubEngine.supports_pagination = False
    try:
        result = await orchestrator.search(SearchRequest(q="hello", engine="stub", num=50))
    finally:
        _StubEngine.supports_pagination = True
        _StubEngine.per_page = 3
    assert len(result.organic_results) < 50
    assert result.search_metadata["truncated"] is True
    assert result.search_metadata["requested_num"] == 50
    assert result.search_metadata["pagination_supported"] is False


@pytest.mark.asyncio
async def test_no_pagination_when_num_already_satisfied(orchestrator):
    _StubEngine.blocked = False
    _StubEngine.per_page = 5
    try:
        result = await orchestrator.search(SearchRequest(q="hello", engine="stub", num=5))
    finally:
        _StubEngine.per_page = 3
    assert len(result.organic_results) == 5
    assert "truncated" not in result.search_metadata


# --------------------------------------------------------------------------
# health check reachability
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_health_check_live_detects_blocked_engine(orchestrator):
    _StubEngine.blocked = True
    try:
        health = await orchestrator.health_check(live=True, timeout=5)
    finally:
        _StubEngine.blocked = False
    assert health["stub"]["ok"] is False
    assert health["stub"]["mode"] == "live"
    assert health["stub"]["error_code"] == "engine_blocked"


@pytest.mark.asyncio
async def test_health_check_live_reports_ok_when_reachable(orchestrator):
    _StubEngine.blocked = False
    health = await orchestrator.health_check(live=True, timeout=5)
    assert health["stub"]["ok"] is True
    assert health["stub"]["mode"] == "live"
    assert health["stub"]["results"] >= 1
    assert "latency_ms" in health["stub"]


@pytest.mark.asyncio
async def test_health_check_instantiation_mode(orchestrator):
    _StubEngine.blocked = True
    try:
        health = await orchestrator.health_check(live=False)
    finally:
        _StubEngine.blocked = False
    # Instantiation succeeds even when the engine would be blocked.
    assert health["stub"]["ok"] is True
    assert health["stub"]["mode"] == "instantiation"


@pytest.mark.asyncio
async def test_probe_engine_reports_timeout(orchestrator, monkeypatch):
    async def _hang(self, req):
        await asyncio.sleep(10)
        raise AssertionError("should have been cancelled")

    monkeypatch.setattr(_StubEngine, "search", _hang)
    probe = await orchestrator.probe_engine("stub", timeout=0.1)
    assert probe["ok"] is False
    assert probe["error_code"] == "engine_timeout"


@pytest.mark.asyncio
async def test_health_check_signature_defaults_to_live(orchestrator):
    """Default must be a real probe, not an import check."""
    import inspect

    sig = inspect.signature(SearchOrchestrator.health_check)
    assert sig.parameters["live"].default is True


# --------------------------------------------------------------------------
# engine racing: parallel latency, sequential preference
# --------------------------------------------------------------------------
class _TimedEngine(BaseEngine):
    """Engine stub whose latency and name are set per instance."""

    types = ["web"]
    delay = 0.0

    def __init__(self, client, settings) -> None:
        super().__init__(client, settings)

    async def search(self, req: SearchRequest) -> SearchResponse:
        await asyncio.sleep(self.delay)
        results = [
            OrganicResult(
                position=i + 1,
                title=f"{req.q} from {self.name}",
                link=f"https://{self.name}.example/{i}",
                snippet=f"about {req.q}",
            )
            for i in range(3)
        ]
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False,
                                          total_time=self.delay),
            search_information={"organic_results_count": len(results)},
            organic_results=results,
        )


@pytest.mark.asyncio
async def test_race_engines_runs_concurrently_and_keeps_preference(orchestrator, monkeypatch):
    """Engines are queried in parallel, but the requested one still wins.

    Racing exists to stop a slow or rate-limited engine serialising the whole
    request. It must not change *which* result is chosen: a later engine that
    finishes first does not displace the caller's preferred one.
    """
    import time

    # Timestamps prove the concurrency directly, which a wall-clock threshold
    # cannot do reliably at these durations.
    events = {}

    async def _search(self, req):
        events[f"start:{self.name}"] = time.perf_counter()
        await asyncio.sleep(0.3 if self.name == "first" else 0.02)
        events[f"end:{self.name}"] = time.perf_counter()
        results = [
            OrganicResult(position=i + 1, title=f"{req.q} from {self.name}",
                          link=f"https://{self.name}.example/{i}",
                          snippet=f"about {req.q}")
            for i in range(3)
        ]
        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False,
                                          total_time=0.0),
            search_information={"organic_results_count": len(results)},
            organic_results=results,
        )

    monkeypatch.setattr(_TimedEngine, "search", _search)

    def _get(name):
        return type(f"E_{name}", (_TimedEngine,), {"name": name, "delay": 0.0})

    monkeypatch.setattr(orchestrator.registry, "get", _get)
    errors: List[dict] = []
    winner = await orchestrator._race_engines(
        ["first", "second"], SearchRequest(q="hello", engine="first"),
        ua="test", errors=errors,
    )

    assert winner is not None
    # Concurrency: 'second' must have STARTED before 'first' FINISHED. Under a
    # sequential walk it could only start after.
    assert events["start:second"] < events["end:first"], "engines were serialised"
    # Preference: 'first' was asked for, so it wins even though 'second' was 15x
    # faster and definitely completed first.
    assert winner["engine"] == "first"
    assert events["end:second"] < events["end:first"]
    assert errors == []


@pytest.mark.asyncio
async def test_race_engines_falls_back_when_preferred_engine_fails(orchestrator, monkeypatch):
    """A failing preferred engine yields the next one, recorded as a substitution."""
    def _get(name):
        if name == "broken":
            class _Broken(BaseEngine):
                name = "broken"
                types = ["web"]

                async def search(self, req):
                    raise EngineBlockedError("broken served a bot-wall")
            return _Broken
        made = type("E_ok", (_TimedEngine,), {"name": "ok", "delay": 0.0})
        return made

    monkeypatch.setattr(orchestrator.registry, "get", _get)
    errors: List[dict] = []
    winner = await orchestrator._race_engines(
        ["broken", "ok"], SearchRequest(q="hello", engine="broken"),
        ua="test", errors=errors,
    )
    assert winner is not None and winner["engine"] == "ok"
    assert any(e.get("engine") == "broken" for e in errors)


@pytest.mark.asyncio
async def test_racing_is_off_by_default(orchestrator, monkeypatch):
    """Sequential fallback stays the default: racing costs extra upstream hits."""
    assert orchestrator._should_race(
        SearchRequest(q="x", engine="auto"), ["a", "b"]) is False


@pytest.mark.asyncio
async def test_racing_skipped_for_single_engine(orchestrator, monkeypatch):
    orchestrator.settings.raw.setdefault("search", {})["race_engines"] = True
    assert orchestrator._should_race(
        SearchRequest(q="x", engine="auto"), ["only"]) is False
