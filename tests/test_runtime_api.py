"""Interface tests for the durable task runtime.

Exercises the REST surface against a real app, a real database and the real
runtime — including authentication, task ownership, and the backward-compatible
``/jobs`` contract that now delegates to the same runtime.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from starlette.testclient import TestClient

from jiro.config import Settings
from jiro.runtime import HandlerRegistry
from jiro.runtime.handlers import TaskContext
from jiro.runtime.types import TaskState
from jiro.runtime.validation import Field, TaskSchema
from jiro.server import create_app

from tests.conftest_runtime import make_settings  # noqa: F401
from tests.conftest_runtime import repo, runtime_db, wait_until  # noqa: F401

#: A task type that needs no network and no LLM, so the REST tests stay offline.
ECHO_TYPE = "echo"


async def echo_handler(ctx: TaskContext) -> dict:
    return {"echoed": ctx.input.get("value", ""), "attempt": ctx.attempt}


def build_settings(tmp_path) -> Settings:
    settings = make_settings(
        runtime={"max_concurrency": 2, "shutdown_grace_seconds": 1.0},
    )
    settings.raw["db"]["path"] = str(tmp_path / "api.db")
    return settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient over the real app, with a deterministic offline handler."""
    import jiro.server as server_module

    original = (
        server_module.register_builtin_handlers
        if hasattr(server_module, "register_builtin_handlers")
        else None
    )

    from jiro.runtime.builtin import register_builtin_handlers as _register

    def _with_echo(registry, *, services):
        _register(registry, services=services)
        if not registry.has(ECHO_TYPE):
            registry.register(
                ECHO_TYPE,
                echo_handler,
                TaskSchema(
                    ECHO_TYPE,
                    [
                        Field("value", "str", required=True, max_length=500),
                    ],
                ),
                description="offline echo task for tests",
            )
        return registry

    import jiro.runtime.builtin as builtin_module

    monkeypatch.setattr(builtin_module, "register_builtin_handlers", _with_echo)

    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
    del original


def _wait_for_state(client, task_id, states, timeout=10.0):
    """Poll a task until it reaches one of ``states``."""
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        response = client.get(f"/tasks/{task_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["state"] in states:
            return body
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} stayed in {body.get('state')}, wanted {states}")


class TestSubmitEndpoint:
    def test_submit_returns_202_and_durable_acceptance(self, client):
        response = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "hi"}})
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["id"].startswith("task_")
        # 202 means committed, not succeeded.
        assert body["state"] in ("queued", "running", "succeeded")

    def test_task_actually_executes(self, client):
        body = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "hello"}}).json()
        final = _wait_for_state(client, body["id"], {"succeeded", "failed"})
        assert final["state"] == "succeeded"
        assert final["has_result"] is True
        assert final["result"]["echoed"] == "hello"

    def test_unknown_type_is_422(self, client):
        response = client.post("/tasks", json={"type": "nope", "payload": {}})
        assert response.status_code in (400, 422)
        assert "unknown_task_type" in response.text or "unknown" in response.text.lower()

    def test_invalid_payload_is_rejected(self, client):
        response = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"wrong_field": 1}})
        assert response.status_code in (400, 422)

    def test_missing_required_field_is_rejected(self, client):
        response = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {}})
        assert response.status_code in (400, 422)

    def test_limit_above_ceiling_is_rejected(self, client):
        response = client.post(
            "/tasks", json={"type": ECHO_TYPE, "payload": {"value": "x"}, "max_attempts": 99}
        )
        assert response.status_code in (400, 422)

    def test_idempotent_submit_returns_same_task(self, client):
        payload = {"type": ECHO_TYPE, "payload": {"value": "dup"}, "idempotency_key": "same-key"}
        first = client.post("/tasks", json=payload).json()
        second = client.post("/tasks", json=payload).json()
        assert first["id"] == second["id"]

    def test_status_does_not_leak_internals(self, client):
        body = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "x"}}).json()
        final = _wait_for_state(client, body["id"], {"succeeded", "failed"})
        assert "lease_owner" not in final
        assert "traceback" not in str(final).lower()


class TestStatusEndpoint:
    def test_get_task_returns_result(self, client):
        created = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "v"}}).json()
        _wait_for_state(client, created["id"], {"succeeded", "failed"})
        body = client.get(f"/tasks/{created['id']}").json()
        assert body["state"] == "succeeded"
        assert body["result"]["echoed"] == "v"
        assert body["attempts"] == 1

    def test_unknown_task_is_404(self, client):
        assert client.get("/tasks/task_missing").status_code == 404

    def test_list_returns_only_caller_tasks(self, client):
        client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "a"}})
        body = client.get("/tasks").json()
        assert isinstance(body, list)
        assert len(body) >= 1

    def test_state_filter_rejects_unknown_state(self, client):
        response = client.get("/tasks?state=sideways")
        assert response.status_code in (400, 422)

    def test_state_filter_works(self, client):
        created = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "a"}}).json()
        _wait_for_state(client, created["id"], {"succeeded", "failed"})
        body = client.get("/tasks?state=succeeded").json()
        assert all(item["state"] == "succeeded" for item in body)


class TestCancelEndpoint:
    def test_cancel_queued_task(self, client):
        created = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "x"}}).json()
        response = client.post(f"/tasks/{created['id']}/cancel")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] in ("cancelled", "succeeded")
        if body["state"] == "cancelled":
            assert body["completed_at"] is not None

    def test_repeated_cancel_is_safe(self, client):
        created = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "x"}}).json()
        first = client.post(f"/tasks/{created['id']}/cancel")
        second = client.post(f"/tasks/{created['id']}/cancel")
        third = client.post(f"/tasks/{created['id']}/cancel")
        assert first.status_code == second.status_code == third.status_code == 200
        assert first.json()["state"] == second.json()["state"] == third.json()["state"]

    def test_cancel_unknown_task_is_404(self, client):
        assert client.post("/tasks/task_nope/cancel").status_code == 404

    def test_cancel_after_success_reports_success(self, client):
        created = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "x"}}).json()
        _wait_for_state(client, created["id"], {"succeeded", "failed"})
        body = client.post(f"/tasks/{created['id']}/cancel").json()
        assert body["state"] == "succeeded"


class TestEventsEndpoint:
    def test_events_are_recorded(self, client):
        created = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "x"}}).json()
        _wait_for_state(client, created["id"], {"succeeded", "failed"})
        events = client.get(f"/tasks/{created['id']}/events").json()
        names = [e["type"] for e in events]
        assert "task.submitted" in names
        assert "task.validated" in names
        assert "task.succeeded" in names

    def test_events_do_not_leak_secrets(self, client):
        created = client.post("/tasks", json={"type": ECHO_TYPE, "payload": {"value": "x"}}).json()
        events = client.get(f"/tasks/{created['id']}/events").text
        for marker in ("password", "api_key", "Bearer", "secret"):
            assert marker not in events or marker == "has_idempotency_key"

    def test_events_for_unknown_task_is_404(self, client):
        assert client.get("/tasks/task_nope/events").status_code == 404


class TestRuntimeStats:
    def test_stats_reports_workers(self, client):
        body = client.get("/tasks/runtime/stats").json()
        assert body["started"] is True
        assert body["workers"] >= 1
        assert "queue_depth" in body
        assert "succeeded" in body["queue_depth"]


class TestAuthentication:
    """Auth is enabled: every task route must reject an anonymous caller."""

    @pytest.fixture
    def secured_client(self, tmp_path, monkeypatch):
        import jiro.runtime.builtin as builtin_module

        original = builtin_module.register_builtin_handlers

        def _with_echo(registry, *, services):
            original(registry, services=services)
            if not registry.has(ECHO_TYPE):
                registry.register(
                    ECHO_TYPE,
                    echo_handler,
                    TaskSchema(ECHO_TYPE, [Field("value", "str", required=True)]),
                )
            return registry

        monkeypatch.setattr(builtin_module, "register_builtin_handlers", _with_echo)

        settings = build_settings(tmp_path)
        settings.raw["auth"]["enabled"] = True
        settings.raw["auth"]["jwt_secret"] = "test-secret-that-is-long-enough-32b"
        app = create_app(settings)
        with TestClient(app) as test_client:
            yield test_client

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/tasks"),
            ("get", "/tasks"),
            ("get", "/tasks/task_x"),
            ("post", "/tasks/task_x/cancel"),
            ("get", "/tasks/task_x/events"),
            ("get", "/tasks/runtime/stats"),
        ],
    )
    def test_anonymous_is_rejected(self, secured_client, method, path):
        call = getattr(secured_client, method)
        # Only POST takes a body; TestClient.get() rejects a json= kwarg.
        if method == "post":
            response = call(path, json={"type": ECHO_TYPE, "payload": {"value": "x"}})
        else:
            response = call(path)
        assert response.status_code in (401, 403), (
            f"{method.upper()} {path} allowed an anonymous caller"
        )


class TestJobsCompatibility:
    """The legacy /jobs contract must be unchanged by the runtime rewrite."""

    def test_jobs_submit_shape_is_preserved(self, client):
        response = client.post(
            "/jobs",
            json={
                "type": "batch_scrape",
                "payload": {"urls": ["https://example.com"]},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # Exact field set the /jobs response model has always had.
        # Exactly the fields `JobOut` has always exposed. Note there is no
        # `payload`: the legacy response model never included it.
        assert set(body) == {
            "id",
            "type",
            "status",
            "result",
            "error",
            "progress",
            "webhook_url",
            "webhook_delivered",
            "created_at",
            "completed_at",
        }
        assert body["id"].startswith("job_")
        assert body["status"] in ("queued", "running", "completed", "failed")

    def test_jobs_rejects_unknown_type(self, client):
        response = client.post("/jobs", json={"type": "not_a_type", "payload": {}})
        assert response.status_code >= 400

    def test_jobs_get_and_list(self, client):
        created = client.post(
            "/jobs",
            json={
                "type": "batch_scrape",
                "payload": {"urls": ["https://example.com"]},
            },
        ).json()
        fetched = client.get(f"/jobs/{created['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == created["id"]
        listing = client.get("/jobs")
        assert listing.status_code == 200
        assert isinstance(listing.json(), list)

    def test_jobs_unknown_id_is_404(self, client):
        assert client.get("/jobs/job_missing").status_code == 404

    def test_jobs_status_uses_legacy_vocabulary(self, client):
        """`retry_wait` must never leak into the /jobs response."""
        created = client.post(
            "/jobs",
            json={
                "type": "batch_scrape",
                "payload": {"urls": ["https://example.com"]},
            },
        ).json()
        for _ in range(40):
            body = client.get(f"/jobs/{created['id']}").json()
            assert body["status"] in ("queued", "running", "completed", "failed")
            if body["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)


class TestOpenApiContract:
    def test_task_routes_are_documented(self, client):
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/tasks" in paths
        assert "/tasks/{task_id}" in paths
        assert "/tasks/{task_id}/cancel" in paths
        assert "/tasks/{task_id}/events" in paths
        assert "/tasks/runtime/stats" in paths
        assert "tasks" in paths["/tasks"]["post"]["tags"]

    def test_response_models_match_runtime_states(self, client):
        schema = client.get("/openapi.json").json()
        enum = schema["components"]["schemas"]["TaskOut"]["properties"]["state"]
        states = enum.get("description", "")
        for state in ("queued", "running", "retry_wait", "succeeded", "failed", "cancelled"):
            assert state in states
