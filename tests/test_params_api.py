"""Live Parameter Layer API tests (no network)."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from jiro.parametric.packet import build_packet
from jiro.params.store import ParameterStore
from jiro.server.routers.params import router


def _packet(text: str, url: str = "https://a.example/x") -> dict:
    return build_packet(
        "topic",
        [],
        [
            {
                "url": url,
                "title": "A",
                "content": text,
                "status_code": 200,
                "content_hash": "a",
                "fetched_at": "2026-10-21T00:00:00Z",
            }
        ],
        max_tokens=400,
    )


def _client(settings, tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings
    app.state.auth = None
    app.state.parameter_store = ParameterStore(tmp_path)
    return TestClient(app)


def test_parameter_round_trip(settings, tmp_path):
    client = _client(settings, tmp_path)
    packet = _packet("Rust ownership prevents data races in safe code at compile time.")
    created = client.post(
        "/v1/params/from-packet",
        json={"session_id": "s1", "packet": packet},
    )
    assert created.status_code == 200
    data = created.json()
    assert data["schema"] == "jiro.params/v1"
    set_id = data["id"]

    fetched = client.get(f"/v1/params/{set_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == set_id

    listed = client.get("/v1/params", params={"session_id": "s1"})
    assert listed.json()["count"] == 1

    bound = client.post(f"/v1/params/{set_id}/bind", json={"max_tokens": 200})
    assert bound.status_code == 200
    binding = bound.json()
    assert binding["schema"] == "jiro.binding/v1"
    assert binding["used_tokens"] <= 200
    assert "https://a.example/x" in binding["prompt"]


def test_decay_diff_merge_and_delete(settings, tmp_path):
    client = _client(settings, tmp_path)
    a = client.post(
        "/v1/params/from-packet",
        json={
            "session_id": "s1",
            "packet": _packet("Alpha fact is supported by a source.", url="https://a.example/x"),
        },
    ).json()
    b = client.post(
        "/v1/params/from-packet",
        json={
            "session_id": "s1",
            "packet": _packet("Beta fact is supported by another source.", url="https://b.example/y"),
        },
    ).json()

    diffed = client.post("/v1/params/diff", json={"from_id": a["id"], "to_id": b["id"]})
    assert diffed.status_code == 200
    assert diffed.json()["counts"]["added"] >= 1

    merged = client.post(
        "/v1/params/merge", json={"left_id": a["id"], "right_id": b["id"], "save": True}
    )
    assert merged.status_code == 200
    assert merged.json()["version"] == 2

    decayed = client.post(
        f"/v1/params/{a['id']}/decay",
        json={"now": "2030-01-01T00:00:00Z", "save": False},
    )
    assert decayed.status_code == 200
    assert all(value < 0.5 for value in decayed.json()["freshness"].values())

    deleted = client.delete(f"/v1/params/{a['id']}")
    assert deleted.json()["deleted"] is True
    assert client.get(f"/v1/params/{a['id']}").status_code == 404


def test_bad_packet_is_422(settings, tmp_path):
    client = _client(settings, tmp_path)
    response = client.post(
        "/v1/params/from-packet",
        json={"session_id": "s1", "packet": {"schema": "jiro.packet/v1"}},
    )
    assert response.status_code == 422
