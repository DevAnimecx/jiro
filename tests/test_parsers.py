"""Parser unit tests against fixture HTML (no network)."""

from __future__ import annotations

import pytest

from jiro.models import SearchRequest
from jiro.scraping.engines import _build_registry
from jiro.scraping.parsers.bing import BingEngine
from jiro.scraping.parsers.google import GoogleEngine

from tests.helpers import FakeClient, FakeResponse


def _request(**kw) -> SearchRequest:
    base = dict(q="python web scraping", engine="bing", num=5)
    base.update(kw)
    return SearchRequest(**base)


def test_registry_registers_all_engines(settings):
    reg = _build_registry()
    expected = {"google", "bing", "brave", "duckduckgo",
                "youtube", "amazon", "ebay", "yandex", "baidu"}
    assert set(reg.names()) == expected


@pytest.mark.asyncio
async def test_bing_parser_real_fixture(settings, bing_html):
    client = FakeClient({"bing.com/search": bing_html})
    engine = BingEngine(client, settings)
    result = await engine.search(_request(num=5))
    assert len(result.organic_results) > 0
    first = result.organic_results[0]
    assert first.title
    assert first.link.startswith("http")
    assert "bing.com/ck/" not in first.link
    assert first.source


@pytest.mark.asyncio
async def test_google_parser_fixture(settings, google_html):
    client = FakeClient({"google.com/search": google_html})
    engine = GoogleEngine(client, settings)
    result = await engine.search(_request(q="test", engine="google", num=5))
    assert len(result.organic_results) == 2
    first = result.organic_results[0]
    assert first.title == "Example Guide"
    assert first.link == "https://example.com/guide"
    assert "complete guide" in first.snippet
    assert result.related_questions
    assert result.related_searches
    assert result.search_information.get("total_results") == 1230000


@pytest.mark.asyncio
async def test_google_parser_raises_on_empty_page(settings):
    client = FakeClient({"google.com/search": "<html><body><div id='search'></div></body></html>"})
    engine = GoogleEngine(client, settings)
    from jiro.errors import EngineParseError
    with pytest.raises(EngineParseError):
        await engine.search(_request(q="test", engine="google"))


@pytest.mark.asyncio
async def test_duckduckgo_html_parser(settings, ddg_html):
    from jiro.scraping.parsers.duckduckgo import DuckDuckGoEngine

    client = FakeClient({"html.duckduckgo.com/html": ddg_html})
    engine = DuckDuckGoEngine(client, settings)
    result = await engine.search(_request(q="ducks", engine="duckduckgo"))
    assert len(result.organic_results) == 1
    assert result.organic_results[0].link == "https://duck.example.com/page"


@pytest.mark.asyncio
async def test_fallback_chain_uses_next_engine(settings, bing_html):
    """google blocked → bing succeeds."""
    from jiro.cache import CacheManager
    from jiro.db import Database
    from jiro.errors import EngineBlockedError
    from jiro.scraping.engines import SearchOrchestrator

    class BlockingGoogleClient(FakeClient):
        async def get(self, url, *, engine, params=None, extra_headers=None, raw=False):
            if "google.com" in url:
                raise EngineBlockedError("blocked", details={})
            return bing_html, FakeResponse(bing_html)

    db = Database(settings.db_path)
    await db.connect()
    try:
        cache = CacheManager(db, ttl=60)
        orch = SearchOrchestrator(settings, BlockingGoogleClient(), cache)
        result = await orch.search(SearchRequest(q="python web scraping", engine="google"))
        assert result.search_metadata.get("engine") == "bing"
        assert result.search_metadata.get("fallback_engine") == "bing"
        assert len(result.organic_results) > 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_all_engines_fail_raises(settings):
    from jiro.cache import CacheManager
    from jiro.db import Database
    from jiro.errors import EngineError
    from jiro.scraping.engines import SearchOrchestrator

    class FailingClient(FakeClient):
        async def get(self, *a, **kw):
            from jiro.errors import EngineBlockedError
            raise EngineBlockedError("blocked")

    settings.raw["scraping"]["engines"] = ["google", "bing", "duckduckgo"]
    settings.raw["scraping"]["fallback_order"] = ["google", "bing", "duckduckgo"]
    db = Database(settings.db_path)
    await db.connect()
    try:
        cache = CacheManager(db, ttl=60)
        orch = SearchOrchestrator(settings, FailingClient(), cache)
        with pytest.raises(EngineError):
            await orch.search(SearchRequest(q="x", engine="auto"))
    finally:
        await db.close()
