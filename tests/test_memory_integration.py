"""Integration tests: the durable task runtime and cognitive memory together.

These exercise the path memory actually has to survive in production — a real
SQLite file, a real worker, a crash between "task finished" and "episode
written", and a process restart — rather than a mocked boundary.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Dict, List

import pytest

from jiro.db import Database
from jiro.memory import (
    MemoryOutbox,
    MemoryScope,
    MemoryService,
    SqlMemoryRepository,
    ScopeKind,
)
from jiro.memory.embeddings import EmbeddingAdapter
from jiro.memory.errors import MemoryNotFound
from jiro.memory.repository import RetrievalQuery
from jiro.memory.runtime_integration import TaskMemoryBridge
from jiro.runtime.config import RuntimeConfig
from jiro.runtime.errors import TaskError
from jiro.runtime.sqlite_repo import SqlTaskRepository
from jiro.runtime.runtime import TaskRuntime
from jiro.runtime.types import TaskState
from jiro.runtime.validation import Field, TaskSchema

from tests.conftest_memory import memory_config, source
from tests.conftest_runtime import (  # noqa: F401  (fixture re-exports)
    await_terminal,
    make_handler,
    make_settings,
    registry,
    wait_until,
)

pytestmark = pytest.mark.asyncio


def _memory(settings: Any, db: Database) -> Any:
    """A memory service over the same database the runtime uses."""
    cfg = memory_config()
    repo = SqlMemoryRepository(db)
    service = MemoryService(repo, cfg, embeddings=EmbeddingAdapter(provider=None))
    return service, repo


class DownstreamFailure(TaskError):
    """A non-retryable handler failure, as a real handler would raise."""

    code = "downstream"
    status_code = 502


async def _wait_pending(stack: "Stack", *, above: int, timeout: float) -> int:
    """Poll the outbox depth until the pending count is above ``above``.

    ``above=-1`` means "until it reaches zero", i.e. fully drained.
    """
    import asyncio

    deadline = time.monotonic() + timeout
    while True:
        depth = (await stack.outbox.depth()).get("pending", 0)
        if depth > above:
            return depth
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"pending outbox count never went above {above} in {timeout}s "
                f"(last: {depth})")
        await asyncio.sleep(0.01)


async def _pump(outbox: Any, *, timeout: float = 5.0) -> None:
    """Drain until nothing is pending, without relying on the background loop.

    Waiting on the loop's own tick would make these tests depend on a
    configuration value that is not what they are about.
    """
    import asyncio

    deadline = time.monotonic() + timeout
    while True:
        await outbox.drain()
        if (await outbox.depth()).get("pending", 0) == 0:
            return
        if time.monotonic() >= deadline:
            raise AssertionError("outbox did not drain within the timeout")
        await asyncio.sleep(0.01)


class Stack:
    """Runtime, memory and outbox sharing one real database file."""

    def __init__(self, db: Any, runtime: Any, memory: Any, outbox: Any,
                 bridge: Any, settings: Any, db_path: str,
                 registry: Any) -> None:
        self.db = db
        self.runtime = runtime
        self.memory = memory
        self.outbox = outbox
        self.bridge = bridge
        self.settings = settings
        self.db_path = db_path
        self.registry = registry

    async def pending(self) -> int:
        return (await self.outbox.depth()).get("pending", 0)

    async def drain(self) -> None:
        """Let the worker finish queuing, then write everything queued.

        The terminal hook is awaited by the worker, not by the test, so a
        drain issued the instant a task reports terminal can legitimately find
        an empty outbox. Waiting for the entry to land first keeps the test
        about behaviour rather than scheduling.
        """
        try:
            await _wait_pending(self, above=0, timeout=2.0)
        except AssertionError:
            return  # nothing was queued; a genuine assertion will say so
        await self.outbox.drain()
        await _wait_pending(self, above=-1, timeout=5.0)

    async def records(self, owner_key_id: str, *, scope: MemoryScope = None
                      ) -> List[Any]:
        query = RetrievalQuery(owner_key_id=owner_key_id, limit=100)
        if scope is not None:
            query = replace(query, scopes=(scope,))
        page = await self.memory.repo.search(query)
        # The FTS path returns scored hits; the filter-only path returns raw
        # records. Normalise so callers see records either way.
        return [getattr(hit, "record", hit) for hit in page.hits]

    async def stop(self) -> None:
        await self.outbox.stop()
        await self.runtime.stop()
        await self.db.close()


async def _full_stack(tmp_path, registry, *, owner_key_id: str = "key_a",
                      start_outbox: bool = True) -> Stack:
    settings = make_settings(tmp_path)
    db = Database(settings.raw['db']['path'])
    await db.connect()

    memory, repo = _memory(settings, db)
    await memory.initialize()
    outbox = MemoryOutbox(repo, memory.config)
    bridge = TaskMemoryBridge(memory, outbox, enabled=True)

    async def on_terminal(record: Any) -> None:
        await bridge.enqueue_episode(record)

    runtime = TaskRuntime(SqlTaskRepository(db), registry,
                          RuntimeConfig.from_settings(settings),
                          on_terminal=on_terminal)
    runtime.memory_service = memory
    await runtime.start()
    if start_outbox:
        await outbox.start()
    return Stack(db, runtime, memory, outbox, bridge, settings,
                 settings.raw["db"]["path"], registry)


def _failing_handler(registry: Any, type_name: str = "work") -> None:
    """Register a task type whose handler always fails."""

    async def boom(ctx: Any) -> Dict[str, Any]:
        raise DownstreamFailure("downstream exploded")

    registry.register(
        type_name, boom, TaskSchema(type_name, [Field("value", "str", default="")]),
        description="always fails")


class TestTaskOutcomeBecomesEpisode:
    async def test_succeeded_task_records_an_episode(self, tmp_path, registry):
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            submitted = await stack.runtime.submit(
                "work", {"value": "x"}, owner_key_id="key_a")
            await await_terminal(stack.runtime, submitted.id, owner="key_a")
            await stack.drain()

            records = [r for r in await stack.records("key_a")
                       if r.content.get("task_state") == "succeeded"]
            assert len(records) == 1
            assert records[0].content["task_id"] == submitted.id
            assert records[0].memory_type.value == "episodic"
        finally:
            await stack.stop()

    async def test_failed_task_records_an_episode(self, tmp_path, registry):
        _failing_handler(registry)
        stack = await _full_stack(tmp_path, registry)
        try:
            submitted = await stack.runtime.submit(
                "work", {"value": "x"}, owner_key_id="key_a")
            await await_terminal(stack.runtime, submitted.id, owner="key_a")
            await stack.drain()

            records = [r for r in await stack.records("key_a")
                       if r.content.get("task_state") == "failed"]
            assert len(records) == 1
            assert records[0].content["error_code"] == "downstream"
            assert "downstream" in records[0].content["outcome"]
        finally:
            await stack.stop()

    async def test_episode_keeps_the_submitting_owner(self, tmp_path, registry):
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            submitted = await stack.runtime.submit(
                "work", {"value": "x"}, owner_key_id="key_a")
            await await_terminal(stack.runtime, submitted.id, owner="key_a")
            await stack.drain()

            assert await stack.records("key_a") != []
            # The episode is invisible to any other key.
            assert await stack.records("key_b") == []
        finally:
            await stack.stop()

    async def test_replay_does_not_duplicate_an_episode(self, tmp_path, registry):
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            submitted = await stack.runtime.submit(
                "work", {"value": "x"}, owner_key_id="key_a")
            record = await await_terminal(
                stack.runtime, submitted.id, owner="key_a")

            # Replay the terminal hook twice, as a crash-recovery drainer would.
            await stack.bridge.enqueue_episode(record)
            await stack.bridge.enqueue_episode(record)
            await stack.drain()
            await stack.drain()

            records = [r for r in await stack.records("key_a")
                       if r.content.get("task_state") == "succeeded"]
            assert len(records) == 1
        finally:
            await stack.stop()

    async def test_two_owners_do_not_mix(self, tmp_path, registry):
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            for owner in ("key_a", "key_b", "key_a"):
                submitted = await stack.runtime.submit(
                    "work", {"value": owner}, owner_key_id=owner)
                await await_terminal(stack.runtime, submitted.id, owner=owner)
            await stack.drain()
            assert len(await stack.records("key_a")) == 2
            assert len(await stack.records("key_b")) == 1
        finally:
            await stack.stop()


class TestRecallAfterCompletion:
    async def test_completed_work_is_recallable(self, tmp_path, registry):
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            submitted = await stack.runtime.submit(
                "work", {"value": "x"}, owner_key_id="key_a")
            await await_terminal(stack.runtime, submitted.id, owner="key_a")
            await stack.drain()

            scope = MemoryScope(kind=ScopeKind.TASK, scope_id=submitted.id,
                                owner_key_id="key_a")
            hits = await stack.memory.retrieve("task succeeded", scope=scope)
            assert hits, "a completed task left no recallable episode"
        finally:
            await stack.stop()

    async def test_recall_respects_the_owning_key(self, tmp_path, registry):
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            submitted = await stack.runtime.submit(
                "work", {"value": "x"}, owner_key_id="key_a")
            await await_terminal(stack.runtime, submitted.id, owner="key_a")
            await stack.drain()

            scope = MemoryScope(kind=ScopeKind.TASK, scope_id=submitted.id,
                                owner_key_id="key_b")
            assert await stack.memory.retrieve("task succeeded", scope=scope) == []
        finally:
            await stack.stop()

    async def test_a_failed_task_is_never_recalled_as_success(self, tmp_path,
                                                              registry):
        _failing_handler(registry)
        stack = await _full_stack(tmp_path, registry)
        try:
            submitted = await stack.runtime.submit(
                "work", {"value": "x"}, owner_key_id="key_a")
            await await_terminal(stack.runtime, submitted.id, owner="key_a")
            await stack.drain()

            scope = MemoryScope(kind=ScopeKind.TASK, scope_id=submitted.id,
                                owner_key_id="key_a")
            hits = await stack.memory.retrieve("task", scope=scope)
            assert hits
            assert all(h.record.content.get("task_state") == "failed" for h in hits)
        finally:
            await stack.stop()


class TestMemoryTaskHandlers:
    async def test_remember_then_recall(self, tmp_path, registry):
        from jiro.memory.app import register_memory_handlers

        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            register_memory_handlers(stack.registry, service=stack.memory)

            first = await stack.runtime.submit(
                "memory_remember",
                {"text": "the api base url is example.test"},
                owner_key_id="key_a")
            await await_terminal(stack.runtime, first.id, owner="key_a")

            # A different task, same owner: recall must be able to name the
            # scope the note was filed under.
            second = await stack.runtime.submit(
                "memory_recall",
                {"query": "api base url", "scope_kind": "task",
                 "scope_id": first.id},
                owner_key_id="key_a")
            record = await await_terminal(
                stack.runtime, second.id, owner="key_a")
