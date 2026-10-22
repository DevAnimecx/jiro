"""Query graph API tests with a fake search engine."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from jiro.graph.service import GraphDisabledError, GraphService
from jiro.models import OrganicResult, SearchResponse
from jiro.server.routers.graph import router


class FakeOrchestrator:
    async def search(self, request, *, fresh: bool = False):
        return SearchResponse(
            search_metadata={"engine": "fake", "query": request.q},
            organic_results=[
                OrganicResult(
                    position=1,
                    title="Rust ownership",
                    link="https://example.com/rust",
                    snippet="Rust ownership has three rules. It prevents data races in safe code.",
                )
            ],
        )


@pytest.fixture
def api(settings):
    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings
    app.state.auth = None
    app.state.orchestrator = FakeOrchestrator()
    app.state.scraper_client = None
    app.state.cache = None
    app.state.graph_service = GraphService(
        settings, app.state.orchestrator, None, None
    )
    with TestClient(app) as client:
        yield client


def test_plan_endpoint_returns_valid_graph(api):
    response = api.post("/v1/graph/plan", json={"query": "what is rust ownership"})
    assert response.status_code == 200
    data = response.json()
    assert data["graph"]["schema"] == "jiro.graph/v1"
    assert data["graph"]["graph_id"].startswith("g_")
    assert data["estimate"]["nodes"] >= 3
    assert data["alternatives"]


def test_search_endpoint_returns_packet(api):
    response = api.post(
        "/v1/graph/search",
        json={"query": "what is rust ownership", "max_tokens": 300, "include_plan": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["packet"]["claims"]
    assert data["packet"]["used_tokens"] <= data["packet"]["budget_tokens"]
    assert data["plan"]["graph"]["schema"] == "jiro.graph/v1"
    assert data["engine_attribution"]["fake"] == 2


def test_search_endpoint_rejects_bad_request(api):
    assert api.post("/v1/graph/search", json={"query": ""}).status_code == 422
    assert (
        api.post("/v1/graph/search", json={"query": "x", "max_tokens": 999999}).status_code
        == 422
    )


def test_service_respects_disabled_flag(settings):
    settings.raw["graph"]["enabled"] = False
    service = GraphService(settings, FakeOrchestrator(), None, None)
    with pytest.raises(GraphDisabledError):
        service.plan("x")
