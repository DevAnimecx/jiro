"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from tests.helpers import FakeClient, FakeResponse  # noqa: F401
from tests.integration_utils import TEST_CONFIG  # noqa: F401

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def settings(tmp_path) -> Any:
    from jiro.config import Settings

    s = Settings.load()
    s.raw["db"]["path"] = str(tmp_path / "jiro.db")
    s.raw["cache"]["path"] = str(tmp_path / "cache.db")
    s.raw["cache"]["type"] = "sqlite"
    return s


@pytest.fixture
async def db(settings) -> Any:
    from jiro.db import Database

    database = Database(settings.db_path)
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def bing_html() -> str:
    return (FIXTURES / "bing_serp.html").read_text(encoding="utf-8", errors="ignore")


@pytest.fixture
def google_html() -> str:
    return GOOGLE_FIXTURE


@pytest.fixture
def ddg_html() -> str:
    return DDG_HTML_FIXTURE


# ---------------------------------------------------------------------------
# Integration-test component fixtures (chaos / contract / integration suites)
# ---------------------------------------------------------------------------

@pytest.fixture
def test_settings() -> Any:
    from jiro.config import Settings

    return Settings(raw=TEST_CONFIG.copy())


@pytest_asyncio.fixture
async def test_db(test_settings) -> Any:
    from jiro.db import Database

    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def test_cache(test_settings, test_db) -> Any:
    from jiro.cache import CacheManager

    cache = CacheManager(
        test_db if test_settings.cache_type == "sqlite" else None,
        memory=test_settings.cache_type == "memory",
        ttl=test_settings.cache_ttl,
    )
    yield cache


@pytest_asyncio.fixture
async def test_auth(test_settings, test_db) -> Any:
    from jiro.auth import AuthManager

    auth = AuthManager(test_settings, test_db)
    yield auth


@pytest_asyncio.fixture
async def test_client(test_settings) -> Any:
    from jiro.scraping.client import ScrapingClient

    client = ScrapingClient(test_settings)
    await client.init()
    yield client
    await client.close()


@pytest_asyncio.fixture
async def test_orchestrator(test_settings, test_client, test_cache) -> Any:
    from jiro.scraping.engines import SearchOrchestrator

    orchestrator = SearchOrchestrator(test_settings, test_client, test_cache)
    yield orchestrator


@pytest_asyncio.fixture
async def test_llm(test_settings) -> Any:
    from jiro.ai.llm import LLM

    yield LLM(test_settings)


@pytest_asyncio.fixture
async def test_agent(test_settings, test_orchestrator, test_client, test_llm) -> Any:
    from jiro.ai.agent import Agent
    from jiro.extract import scrape_url

    async def _scrape(url: str) -> dict:
        return await scrape_url(url, test_client, fmt="markdown", include_metadata=False)

    agent = Agent(test_settings, test_orchestrator, _scrape, test_llm)
    yield agent


@pytest_asyncio.fixture
async def test_captcha(test_settings) -> Any:
    from jiro.captcha import CaptchaSolver

    solver = CaptchaSolver(test_settings)
    yield solver


@pytest_asyncio.fixture
async def test_jobs(test_db) -> Any:
    from jiro.jobs import JobManager

    jobs = JobManager(test_db)
    yield jobs


GOOGLE_FIXTURE = """<!DOCTYPE html>
<html lang="en"><head><title>test - Google Search</title></head><body>
<div id="result-stats">About 1,230,000 results (0.42 seconds) </div>
<div id="search">
  <div class="MjjYud">
    <div class="g">
      <h3 class="LC20lb MBeuO DKV0Md"><a href="https://example.com/guide">Example Guide</a></h3>
      <div class="VwiC3b">Learn how to do things with our <b>complete guide</b>.</div>
      <div><cite>example.com</cite></div>
    </div>
  </div>
  <div class="MjjYud">
    <div class="g">
      <h3 class="LC20lb MBeuO DKV0Md"><a href="https://blog.example.org/post">Blog Post</a></h3>
      <div class="VwiC3b">Jan 5, 2026 — A deep dive into advanced topics.</div>
      <div><cite>blog.example.org</cite></div>
    </div>
  </div>
</div>
<div class="XpertTb"><h3>What is the best way to start?</h3></div>
<div class="k8XOCe"><a href="/search?q=related">related search</a></div>
</body></html>"""

DDG_HTML_FIXTURE = """<!DOCTYPE html>
<html><body>
<div class="results">
  <div class="result results_links results_links_deep web-result">
    <h2 class="result__title"><a class="result__a"
        href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fduck.example.com%2Fpage">Duck Example</a></h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=..."><a class="result__snippet">
    A snippet about ducks and scraping.</a>
    <div class="result__url">duck.example.com</div>
  </div>
</div>
</body></html>"""
