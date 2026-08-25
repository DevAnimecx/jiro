"""End-to-end API tests via TestClient (real engines, network required)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from jiro.config import Settings
from jiro.server import create_app

network = pytest.mark.network


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_engines(client):
    r = client.get("/engines")
    names = [e["name"] for e in r.json()["engines"]]
    assert "google" in names and "bing" in names


@network
def test_search_web(client):
    r = client.get("/search.json", params={"q": "python web scraping", "engine": "google",
                                           "num": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["search_metadata"]["engine"] in ("google", "bing", "brave", "duckduckgo")
    assert len(data["organic_results"]) > 0


@network
def test_search_cached(client):
    params = {"q": "caching test query", "engine": "google", "num": 2}
    first = client.get("/search.json", params=params)
    assert first.json()["search_metadata"]["cached"] is False
    second = client.get("/search.json", params=params)
    assert second.json()["search_metadata"]["cached"] is True


@network
def test_search_fresh_bypass(client):
    params = {"q": "fresh bypass test", "engine": "google", "num": 2}
    client.get("/search.json", params=params)
    r = client.get("/search.json", params={**params, "fresh": True})
    assert r.json()["search_metadata"]["cached"] is False


@network
def test_search_post_json(client):
    r = client.post("/search", json={"q": "hello world", "engine": "google", "num": 1})
    assert r.status_code == 200
    assert len(r.json()["organic_results"]) > 0


def test_search_unknown_engine(client):
    r = client.get("/search.json", params={"q": "x", "engine": "nope"})
    assert r.status_code == 422
    assert r.json()["error_code"] == "engine_error"


def test_scrape_validation(client):
    r = client.post("/scrape", json={"url": "not-a-url", "format": "markdown"})
    assert r.status_code == 422


def test_ai_search_no_llm(client):
    """Without an LLM key, /ai/search still returns a heuristic answer."""
    r = client.post("/ai/search", json={"query": "best python web scraping library",
                                        "max_sources": 2})
    assert r.status_code == 200
    data = r.json()
    assert data["citations"]
    assert data["answer"]
    # Either extractive fallback (no LLM key) or real LLM provider
    assert data["provider"] in ("extractive-fallback", "openai", "anthropic",
                                "gemini", "openrouter", "ollama")


def test_ai_search_empty_query(client):
    r = client.post("/ai/search", json={"query": "", "max_sources": 2})
    assert r.status_code == 422


@network
def test_usage_and_metrics(client):
    client.get("/search.json", params={"q": "metrics test", "engine": "google"})
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "jiro_requests_total" in r.text


def test_openapi_schema(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for p in ("/search", "/search.json", "/scrape", "/ai/search", "/api-keys",
              "/health", "/engines"):
        assert p in paths


def test_request_id_header(client):
    r = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"


@network
def test_auth_disabled_by_default_allows_access(client):
    assert client.get("/search.json", params={"q": "x", "engine": "google"}).status_code == 200


def test_auth_enabled_requires_key(settings):
    settings.raw["auth"]["enabled"] = True
    with TestClient(create_app(settings)) as client:
        r = client.get("/search.json", params={"q": "x", "engine": "google"})
        assert r.status_code == 401
        assert r.json()["error_code"] == "auth_error"
