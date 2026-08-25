"""Tests for Phase 2/3 features: Brave, recipes, jobs, SSE, semantic, proxy, captcha."""

from __future__ import annotations

import base64
import json

import pytest
from starlette.testclient import TestClient

from jiro.config import Settings
from jiro.server import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


# --------------------------------------------------------------------------
# Brave parser
# --------------------------------------------------------------------------
BRAVE_FIXTURE = """<!DOCTYPE html><html><body>
<div id="results">
  <div class="snippet svelte-jmfu5f noscript-hide" id="llm-snippet"><div class="title">AI answer</div></div>
  <div class="snippet svelte-jmfu5f" data-pos="0" data-type="web">
    <div class="result-content">
      <a href="https://example.com/guide"><div class="site-name-content"><div class="desktop-small-semibold">Example</div></div>
        <div class="title search-snippet-title">Example Guide Title</div></a>
      <div class="generic-snippet"><div class="content">A useful snippet about guides.</div></div>
      <cite class="snippet-url">example.com › guide</cite>
    </div>
  </div>
  <div class="snippet svelte-jmfu5f" data-pos="1" data-type="web">
    <div class="result-content">
      <a href="https://blog.example.org/post"><div class="title search-snippet-title">Blog Post</div></a>
      <div class="generic-snippet"><div class="content">Second result snippet text.</div></div>
    </div>
  </div>
  <div class="snippet svelte-jmfu5f">
    <div class="result-content">
      <a href="/a/redirect?click_url=https%3A%2F%2Fad.example.com%2Flanding&placement_id=x"><div class="title search-snippet-title">Sponsored Ad</div></a>
    </div>
  </div>
  <div class="related-searches svelte-x"><a href="/s?q=related+one">related one</a><a href="/s?q=related+two">related two</a></div>
</div>
</body></html>"""


@pytest.mark.asyncio
async def test_brave_parser(settings):
    from jiro.models import SearchRequest
    from jiro.scraping.parsers.brave import BraveEngine

    from tests.helpers import FakeClient

    client = FakeClient({"search.brave.com": BRAVE_FIXTURE})
    engine = BraveEngine(client, settings)
    result = await engine.search(SearchRequest(q="guides", engine="brave", num=5))
    assert len(result.organic_results) == 2
    assert result.organic_results[0].title == "Example Guide Title"
    assert result.organic_results[0].link == "https://example.com/guide"
    assert "useful snippet" in result.organic_results[0].snippet
    # ad decoded
    assert len(result.ads) == 1
    assert result.ads[0]["link"] == "https://ad.example.com/landing"
    assert result.related_searches[0]["text"] == "related one"


def test_brave_in_registry(settings):
    from jiro.scraping.engines import _build_registry

    reg = _build_registry()
    assert "brave" in reg.names()


# --------------------------------------------------------------------------
# Recipes
# --------------------------------------------------------------------------
RECIPE_HTML = """
<html><head><script type="application/ld+json">
{"@type":"Article","headline":"Recipe Headline","author":{"name":"Jane"}}
</script></head>
<body>
<article>
  <h1>My Page Title</h1>
  <p class="lead">The lead paragraph text.</p>
  <ul><li data-price="10">item a</li><li data-price="25">item b</li></ul>
  <a href="https://site.example/x">external link</a>
  <meta name="author" content="Jane Doe">
</article>
</body></html>
"""


def test_css_recipe():
    from jiro.recipes import apply_recipe

    out = apply_recipe(RECIPE_HTML, {
        "css": {"title": "h1", "lead": ".lead", "price": "li@data-price"},
    })
    assert out["title"] == "My Page Title"
    assert out["lead"] == "The lead paragraph text."
    assert out["price"] == ["10", "25"]


def test_xpath_recipe():
    from jiro.recipes import apply_recipe

    out = apply_recipe(RECIPE_HTML, {
        "xpath": {"author": "//meta[@name='author']/@content", "h1": "//h1/text()"},
    })
    assert out["author"] == "Jane Doe"
    assert out["h1"] == "My Page Title"


def test_jsonpath_recipe():
    from jiro.recipes import apply_recipe

    out = apply_recipe(RECIPE_HTML, {
        "jsonpath": {"headline": "$.headline", "author": "$.author.name"},
    })
    assert out["headline"] == "Recipe Headline"
    assert out["author"] == "Jane"


def test_recipe_api(client):
    r = client.post("/scrape", json={
        "url": "https://example.com",
        "format": "text",
        "recipe": {"css": {"title": "h1"}},
    })
    assert r.status_code == 200
    assert "recipe_result" in r.json()
    assert "Example Domain" in (r.json()["recipe_result"].get("title") or "")


# --------------------------------------------------------------------------
# Jobs + webhooks
# --------------------------------------------------------------------------
def test_job_submit_and_wait(client):
    r = client.post("/jobs", json={
        "type": "ai_search",
        "payload": {"query": "best python web scraping library", "max_sources": 2},
    })
    assert r.status_code == 200
    job = r.json()
    assert job["status"] in ("queued", "running", "completed")

    # poll until completed
    import time
    for _ in range(60):
        j = client.get(f"/jobs/{job['id']}").json()
        if j["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert j["status"] == "completed", j.get("error")
    assert j["result"]["citations"]


def test_batch_scrape_job(client):
    r = client.post("/jobs", json={
        "type": "batch_scrape",
        "payload": {"urls": ["https://example.com", "https://example.org"],
                    "format": "markdown"},
    })
    job = r.json()
    import time
    for _ in range(60):
        j = client.get(f"/jobs/{job['id']}").json()
        if j["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert j["status"] == "completed"
    assert j["result"]["count"] == 2


def test_webhook_delivery(client):
    """Webhook receives the result with HMAC signature."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received["body"] = json.loads(self.rfile.read(length))
            received["sig"] = self.headers.get("X-Jiro-Webhook-Sig")
            received["job"] = self.headers.get("X-Jiro-Job-ID")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        r = client.post("/jobs", json={
            "type": "ai_search",
            "payload": {"query": "hello world", "max_sources": 1},
            "webhook_url": f"http://127.0.0.1:{port}/hook",
            "webhook_secret": "s3cret",
        })
        job = r.json()
        for _ in range(60):
            j = client.get(f"/jobs/{job['id']}").json()
            if j.get("webhook_delivered"):
                break
            time.sleep(0.5)
        assert received.get("body", {}).get("job_id") == job["id"]
        assert received.get("sig")
    finally:
        server.shutdown()


# --------------------------------------------------------------------------
# SSE streaming
# --------------------------------------------------------------------------
def test_ai_search_stream(client):
    with client.stream("GET", "/ai/search/stream",
                       params={"query": "best python web scraping library",
                               "max_sources": 1}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    assert "event: start" in body
    assert "event: search" in body
    assert "event: answer" in body
    assert "event: done" in body


def test_ai_agent_endpoint(client):
    r = client.post("/ai/agent", json={
        "goal": "what are the best python web scraping libraries",
        "max_steps": 2, "max_sources": 2, "max_sources_per_step": 2,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["reasoning_steps"]
    assert data["citations"]
    steps = [s["step"] for s in data["reasoning_steps"]]
    assert "plan" in steps and "synthesize" in steps


# --------------------------------------------------------------------------
# Semantic cache
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_semantic_cache_disabled_without_embeddings(settings):
    from jiro.db import Database
    from jiro.semantic import SemanticCache

    # Force the "no embeddings configured" condition regardless of env.
    settings.raw.setdefault("llm", {})
    settings.raw["llm"]["provider"] = "openai"
    settings.raw["llm"]["api_key"] = ""

    db = Database(settings.db_path)
    await db.connect()
    try:
        settings.raw["cache"]["semantic"] = True  # but no LLM key -> disabled
        sem = SemanticCache(settings, db)
        assert sem.enabled is False
        assert await sem.find("hello world") is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cosine_similarity():
    from jiro.semantic import cosine

    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    assert cosine(a, b) == pytest.approx(1.0)
    assert cosine(a, c) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Proxy manager
# --------------------------------------------------------------------------
def test_proxy_manager_presets(settings):
    from jiro.proxy import ProxyManager

    settings.raw["scraping"]["proxy"] = {
        "enabled": True, "provider": "brightdata", "api_key": "abc123", "url": "",
    }
    pm = ProxyManager(settings)
    urls = pm.configured_urls()
    assert len(urls) == 1
    assert "brd.superproxy.io" in urls[0]
    assert "abc123" in urls[0]
    assert pm.next() == urls[0]
    info = pm.info()
    assert "brd.superproxy.io" not in json.dumps(info) or "abc123" not in json.dumps(info)


def test_proxy_manager_rotation_and_cooldown(settings):
    from jiro.proxy import ProxyManager

    settings.raw["scraping"]["proxy"] = {
        "enabled": True, "url": "http://a:1,http://b:2",
    }
    pm = ProxyManager(settings)
    first = pm.next()
    second = pm.next()
    assert first != second
    pm.record_failure(second)
    pm.record_failure(second)
    pm.record_failure(second)
    assert second not in pm.healthy_urls()


# --------------------------------------------------------------------------
# CAPTCHA module
# --------------------------------------------------------------------------
def test_captcha_not_ready_by_default(settings):
    from jiro.captcha import CaptchaSolver

    solver = CaptchaSolver(settings)
    assert solver.ready is False
    assert solver.enabled is False


def test_captcha_ready_when_configured(settings):
    from jiro.captcha import CaptchaSolver

    settings.raw["scraping"]["captcha"] = {
        "enabled": True, "provider": "capsolver", "api_key": "k",
    }
    solver = CaptchaSolver(settings)
    assert solver.ready is True


# --------------------------------------------------------------------------
# Redis cache backend (fakeredis)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_redis_cache_backend():
    fakeredis = pytest.importorskip("fakeredis.aioredis")

    from jiro.redis_cache import RedisCache

    rc = RedisCache(ttl=60)
    rc._client = fakeredis.FakeRedis(decode_responses=True)
    await rc.put("k1", {"a": 1}, 60)
    assert await rc.get("k1", 60) == {"a": 1}
    stats = await rc.stats()
    assert stats["backend"] == "redis"


def test_ops_proxy_status(client):
    r = client.get("/proxy/status")
    assert r.status_code == 200
    assert "endpoints" in r.json()["proxy"]


def test_ops_captcha_status(client):
    r = client.get("/captcha/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
