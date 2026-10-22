"""Shared fixtures for the durable runtime test suite.

Every fixture here uses a real SQLite file in ``tmp_path`` and the real
:class:`~jiro.runtime.sqlite_repo.SqlTaskRepository`. No test in this suite
substitutes an in-memory mock for persistence — only handler bodies are
scripted, which is the one thing the runtime genuinely delegates.

Because pytest only auto-discovers ``conftest.py``, each runtime test module
re-exports the fixtures it needs::

    from tests.conftest_runtime import repo, runtime_db  # noqa: F401
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any, Callable, Dict, List, Optional

import pytest
import pytest_asyncio

from jiro.config import Settings
from jiro.db import Database
from jiro.runtime import (
    HandlerRegistry,
    RuntimeConfig,
    SqlTaskRepository,
    TaskRuntime,
)
from jiro.runtime.handlers import TaskContext
from jiro.runtime.types import TaskState
from jiro.runtime.validation import Field, TaskSchema

#: Fast limits so timeouts and retries are observable in a unit test without
#: the suite sleeping for seconds. Production defaults live in DEFAULT_CONFIG.
TEST_RUNTIME_CONFIG: Dict[str, Any] = {
    "runtime": {
        "enabled": True,
        "db_path": "",
        "max_concurrency": 2,
        "poll_interval_seconds": 0.02,
        "lease_ttl_seconds": 30,
        "default_attempt_timeout_seconds": 1.0,
        "max_attempt_timeout_seconds": 5.0,
        "default_max_attempts": 2,
        "max_max_attempts": 5,
        "retry_backoff_base_seconds": 0.01,
        "retry_backoff_max_seconds": 0.05,
        "retry_jitter_ratio": 0.0,  # deterministic backoff for assertions
        "shutdown_grace_seconds": 1.0,
        "max_result_bytes": 8192,
        "event_retention_days": 0,  # pruning off unless a test opts in
        "reconcile_interval_seconds": 0.5,  # keep the periodic sweep quick
    },
}


class FakePath:
    """Minimal ``pathlib`` stand-in so config can be built without a fixture.

    ``RuntimeConfig.from_settings`` only reads ``db.path`` as a string, so this
    avoids forcing every config test to depend on ``tmp_path``.
    """

    def __truediv__(self, other: str) -> str:
        return str(other)


def make_settings(
    tmp_path: Any = None,
    *,
    runtime: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
    **overrides: Any,
) -> Settings:
    """Hermetic Settings pointed at a temp database.

    Built on top of the project's own ``DEFAULT_CONFIG`` so every other
    section (``server``, ``scraping``, …) is present and real — a test that
    builds a partial config fails later with an unrelated ``KeyError``.

    Args:
        tmp_path: a pytest ``tmp_path``; its ``jiro.db`` is used when
            ``db_path`` is omitted.
        runtime: overrides merged into the ``runtime:`` section.
        db_path: explicit database path, for tests that need a second runtime
            bound to the file an existing runtime already opened.
    """
    from jiro.config import DEFAULT_CONFIG

    raw = copy.deepcopy(DEFAULT_CONFIG)
    _deep_update(raw, copy.deepcopy(TEST_RUNTIME_CONFIG))
    base = tmp_path if tmp_path is not None else FakePath()
    raw["db"]["path"] = db_path if db_path is not None else f"{base}/jiro.db"
    raw["cache"]["type"] = "sqlite"
    raw["cache"]["ttl_seconds"] = 60
    raw["auth"]["enabled"] = False
    raw["logging"]["level"] = "critical"
    raw["logging"]["file"] = ""
    raw["privacy"]["log_queries"] = False
    if runtime:
        _deep_update(raw["runtime"], runtime)
    if overrides:
        _deep_update(raw, overrides)
    return Settings(raw=raw)


def config_from(settings: Settings) -> RuntimeConfig:
    """Validated runtime configuration from Settings."""
    return RuntimeConfig.from_settings(settings)


async def make_runtime(
    db: Database, registry: HandlerRegistry, settings: Settings, *, start: bool = True
) -> TaskRuntime:
    """Build a TaskRuntime over an already-open database.

    Used by tests that need a second runtime instance bound to the same file,
    which is how restart behaviour is exercised without a real process kill.
    """
    instance = TaskRuntime(SqlTaskRepository(db), registry, RuntimeConfig.from_settings(settings))
    if start:
        await instance.start()
    return instance


def _deep_update(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


@pytest_asyncio.fixture
async def runtime_db(tmp_path) -> Any:
    """A real SQLite Database on a temp file, closed on teardown."""
    database = Database(str(tmp_path / "jiro.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def runtime_settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def registry() -> HandlerRegistry:
    return HandlerRegistry()


@pytest_asyncio.fixture
async def repo(runtime_db) -> Any:
    """Real SQLite task repository."""
    return SqlTaskRepository(runtime_db)


@pytest_asyncio.fixture
async def runtime(runtime_db, runtime_settings, registry) -> Any:
    """A started :class:`TaskRuntime` with no handlers registered yet."""
    instance = TaskRuntime(
        SqlTaskRepository(runtime_db), registry, RuntimeConfigFrom(runtime_settings)
    )
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


def RuntimeConfigFrom(settings: Settings):
    """Deprecated alias kept for readability of older call sites."""
    return RuntimeConfig.from_settings(settings)


class RecordingHandler:
    """A handler whose behaviour each test scripts directly.

    Records every call so attempt counts and context evolution can be asserted
    without depending on logs.
    """

    def __init__(self, behaviour: Any) -> None:
        self.behaviour = behaviour
        self.calls: List[TaskContext] = []

    async def __call__(self, ctx: TaskContext) -> Dict[str, Any]:
        self.calls.append(ctx)
        result = self.behaviour
        if callable(result):
            return await result(ctx)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, type) and issubclass(result, BaseException):
            raise result("scripted failure")
        return dict(result or {})

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def attempts(self) -> List[int]:
        return [c.attempt for c in self.calls]


def make_handler(
    result: Any,
    registry: HandlerRegistry,
    type_name: str = "test_task",
    fields: Optional[List[Field]] = None,
    **kwargs: Any,
) -> RecordingHandler:
    """Register a scripted handler and return it for call inspection."""
    handler = RecordingHandler(result)
    schema_fields = fields if fields is not None else [Field("value", "str", default="")]
    registry.register(
        type_name,
        handler,
        TaskSchema(type_name, schema_fields),
        description=f"scripted handler for {type_name}",
        **kwargs,
    )
    return handler


async def wait_until(
    predicate: Callable[[], Any], *, timeout: float = 5.0, interval: float = 0.01
) -> Any:
    """Poll ``predicate`` until it returns truthy. Returns its final value.

    Raises:
        AssertionError: on timeout, so a failing test reports what never became
            true rather than hanging forever.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    final = await predicate()
    raise AssertionError(f"condition not met within {timeout}s; last value: {final!r}")


async def await_terminal(
    runtime: TaskRuntime, task_id: str, owner: Optional[str] = None, timeout: float = 5.0
) -> Any:
    """Await a task reaching a terminal state and return the record."""

    async def check():
        record = await runtime.get(task_id, owner_key_id=owner, enforce_owner=False)
        return record if record.is_terminal else None

    return await wait_until(check, timeout=timeout)


async def await_state(
    runtime: TaskRuntime,
    task_id: str,
    state: TaskState,
    owner: Optional[str] = None,
    timeout: float = 5.0,
) -> Any:
    """Await a task reaching ``state`` and return the record."""

    async def check():
        record = await runtime.get(task_id, owner_key_id=owner, enforce_owner=False)
        return record if record.state is state else None

    return await wait_until(check, timeout=timeout)


__all__ = [
    "FakePath",
    "RecordingHandler",
    "TEST_RUNTIME_CONFIG",
    "TaskState",
    "await_state",
    "await_terminal",
    "make_handler",
    "make_runtime",
    "make_settings",
    "repo",
    "runtime",
    "runtime_db",
    "runtime_settings",
    "registry",
    "wait_until",
]
