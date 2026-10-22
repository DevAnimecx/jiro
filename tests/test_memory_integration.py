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
            assert record.state is TaskState.SUCCEEDED, record.error_message
            assert record.result["count"] >= 1
        finally:
            await stack.stop()

    async def test_handler_cannot_target_another_owner(self, tmp_path, registry):
        """A payload naming another key is rejected before it can be honoured."""
        from jiro.memory.app import register_memory_handlers
        from jiro.runtime.errors import TaskValidationError

        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            register_memory_handlers(stack.registry, service=stack.memory)
            with pytest.raises(TaskValidationError):
                await stack.runtime.submit(
                    "memory_remember",
                    {"text": "spoofed owner", "owner_key_id": "key_b"},
                    owner_key_id="key_a")

            assert await stack.records("key_b") == []
            assert await stack.records("key_a") == []
        finally:
            await stack.stop()

    async def test_handler_cannot_write_to_a_global_scope(self, tmp_path, registry):
        from jiro.memory.app import register_memory_handlers
        from jiro.runtime.errors import TaskValidationError

        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            register_memory_handlers(stack.registry, service=stack.memory)
            # `memory_remember` declares no scope fields at all: a task may only
            # ever write into its own task scope.
            with pytest.raises(TaskValidationError):
                await stack.runtime.submit(
                    "memory_remember",
                    {"text": "escalate", "scope_kind": "global", "scope_id": ""},
                    owner_key_id="key_a")

            scope = MemoryScope(kind=ScopeKind.GLOBAL, owner_key_id="key_a")
            assert await stack.records("key_a", scope=scope) == []
        finally:
            await stack.stop()

    async def test_recall_may_widen_scope_but_not_owner(self, tmp_path, registry):
        """A task can read wider than its own scope, still only its own key."""
        from jiro.memory.app import register_memory_handlers

        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            register_memory_handlers(stack.registry, service=stack.memory)
            writer = await stack.runtime.submit(
                "memory_remember", {"text": "shared note for key_a"},
                owner_key_id="key_a")
            await await_terminal(stack.runtime, writer.id, owner="key_a")

            reader = await stack.runtime.submit(
                "memory_recall",
                {"query": "shared note", "scope_kind": "task",
                 "scope_id": writer.id},
                owner_key_id="key_a")
            mine = await await_terminal(stack.runtime, reader.id, owner="key_a")
            assert mine.state is TaskState.SUCCEEDED, mine.error_message
            assert mine.result["count"] >= 1

            # The same read by another key finds nothing.
            other = await stack.runtime.submit(
                "memory_recall",
                {"query": "shared note", "scope_kind": "task",
                 "scope_id": writer.id},
                owner_key_id="key_b")
            theirs = await await_terminal(stack.runtime, other.id, owner="key_b")
            assert theirs.result["count"] == 0
        finally:
            await stack.stop()

    async def test_forget_clears_only_its_own_task_scope(self, tmp_path, registry):
        from jiro.memory.app import register_memory_handlers

        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            register_memory_handlers(stack.registry, service=stack.memory)

            keeper = await stack.runtime.submit(
                "memory_remember", {"text": "keep me"},
                owner_key_id="key_a")
            await await_terminal(stack.runtime, keeper.id, owner="key_a")
            forgetter = await stack.runtime.submit(
                "memory_forget", {}, owner_key_id="key_a")
            await await_terminal(stack.runtime, forgetter.id, owner="key_a")

            scope = MemoryScope(kind=ScopeKind.TASK, scope_id=keeper.id,
                                owner_key_id="key_a")
            assert await stack.memory.retrieve("keep me", scope=scope), (
                "one task's forget wiped another task's working memory")
        finally:
            await stack.stop()


class TestOutboxCrashRecovery:
    async def test_task_finishes_but_the_write_is_deferred(self, tmp_path, registry):
        """The outbox is what keeps a slow memory write off the task path."""
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry, start_outbox=False)
        try:
            # No background drainer: the only route to memory is the outbox.
            submitted = await stack.runtime.submit(
                "work", {"value": "x"}, owner_key_id="key_a")
            record = await await_terminal(
                stack.runtime, submitted.id, owner="key_a")
            await stack.bridge.enqueue_episode(record)

            assert (await stack.outbox.depth()).get("pending", 0) == 1
            assert await stack.records("key_a") == []

            await stack.outbox.drain()
            assert await stack.records("key_a") != []
        finally:
            await stack.stop()

    async def test_intent_survives_a_restart(self, tmp_path, registry):
        """Crash between task completion and memory write: nothing is lost."""
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry, start_outbox=False)
        db_path = stack.db_path
        submitted = await stack.runtime.submit(
            "work", {"value": "x"}, owner_key_id="key_a")
        record = await await_terminal(stack.runtime, submitted.id, owner="key_a")
        await stack.bridge.enqueue_episode(record)
        await stack.stop()

        # --- restart: a brand new process against the same file ---
        settings = make_settings(tmp_path, db_path=db_path)
        db = Database(settings.raw['db']['path'])
        await db.connect()
        try:
            memory, repo = _memory(settings, db)
            await memory.initialize()
            outbox = MemoryOutbox(repo, memory.config)
            # A restart rebuilds the same three objects `build_memory_service`
            # builds: the bridge is what re-registers the episode writer, so a
            # process that skips it would dead-letter everything it drains.
            bridge = TaskMemoryBridge(memory, outbox, enabled=True)
            assert bridge is not None
            try:
                await _pump(outbox)

                scope = MemoryScope(kind=ScopeKind.TASK, scope_id=submitted.id,
                                    owner_key_id="key_a")
                hits = await memory.retrieve("task succeeded", scope=scope)
                assert hits, "the queued episode was lost across a restart"
            finally:
                pass
        finally:
            await db.close()

    async def test_a_claimed_entry_is_reclaimed_after_a_crash(self, tmp_path,
                                                               registry):
        """An entry claimed by a process that then died must not be stranded."""
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            # An entry stuck in `processing`: its drainer died mid-write.
            await stack.memory.repo._db.execute(
                """INSERT INTO memory_outbox
                   (kind, dedup_key, payload, state, attempts,
                    available_at, created_at, updated_at)
                   VALUES (?, 'stuck', ?, 'processing', 1, ?, ?, ?)""",
                (TaskMemoryBridge.KIND_EPISODE,
                 '{"task_id": "t-stuck", "state": "succeeded", '
                 '"scope": {"kind": "task", "scope_id": "t-stuck", '
                 '"owner_key_id": "key_a"}}',
                 time.time() - 3600, time.time() - 3600, time.time() - 3600))

            reclaimed = await stack.outbox.reclaim_stale()
            assert reclaimed >= 1
            await _pump(stack.outbox)
            assert await stack.records("key_a") != [], (
                "the reclaimed entry was dropped instead of written")
        finally:
            await stack.stop()

    async def test_permanently_failing_entry_is_dead_lettered(self, tmp_path,
                                                              registry):
        make_handler({"ok": True}, registry, "work")
        stack = await _full_stack(tmp_path, registry)
        try:
            await stack.memory.repo._db.execute(
                """INSERT INTO memory_outbox
                   (kind, dedup_key, payload, state, attempts,
                    available_at, created_at, updated_at)
                   VALUES (?, 'bad', ?, 'pending', 99, ?, ?, ?)""",
                (TaskMemoryBridge.KIND_EPISODE,
                 # An unparseable scope kind: this write can never succeed, so
                 # it must end up dead-lettered rather than retried forever.
                 # `attempts` starts past the limit so the first failure is
                 # terminal without the test waiting out a real backoff.
                 '{"task_id": "t-bad", "state": "succeeded", '
                 '"scope": {"kind": "not-a-scope", "scope_id": "x"}}',
                 time.time(), time.time(), time.time()))

            await stack.outbox.drain()

            depth = await stack.outbox.depth()
            assert depth.get("pending", 0) == 0
            assert depth.get("failed", 0) >= 1, depth
        finally:
            await stack.stop()


class TestRestartDurability:
    async def test_memories_survive_a_restart(self, tmp_path):
        settings = make_settings(tmp_path)
        db = Database(settings.raw['db']['path'])
        await db.connect()
        scope = MemoryScope(kind=ScopeKind.TASK, scope_id="t1",
                            owner_key_id="key_a")
        try:
            memory, _ = _memory(settings, db)
            await memory.initialize()
            await memory.remember({"text": "durable working note"}, scope=scope)
            await memory.record_fact("svc", "retries", "3", scope=scope,
                                     provenance=[source()])
        finally:
            await db.close()

        db2 = Database(settings.raw['db']['path'])
        await db2.connect()
        try:
            memory2, _ = _memory(settings, db2)
            await memory2.initialize()
            assert await memory2.retrieve("durable working note", scope=scope)
            assert await memory2.retrieve("svc retries", scope=scope)
        finally:
            await db2.close()

    async def test_deletion_survives_a_restart(self, tmp_path):
        settings = make_settings(tmp_path)
        db = Database(settings.raw['db']['path'])
        await db.connect()
        scope = MemoryScope(kind=ScopeKind.TASK, scope_id="t1",
                            owner_key_id="key_a")
        try:
            memory, _ = _memory(settings, db)
            await memory.initialize()
            record, _ = await memory.record_episode(
                {"event": "to be removed", "task_id": "t"}, scope=scope)
            await memory.delete(record.id, owner_key_id="key_a", hard=True)
        finally:
            await db.close()

        db2 = Database(settings.raw['db']['path'])
        await db2.connect()
        try:
            memory2, _ = _memory(settings, db2)
            await memory2.initialize()
            assert await memory2.retrieve("to be removed", scope=scope) == []
            with pytest.raises(MemoryNotFound):
                await memory2.get(record.id, owner_key_id="key_a")
        finally:
            await db2.close()

    async def test_superseded_fact_stays_hidden_after_a_restart(self, tmp_path):
        settings = make_settings(tmp_path)
        db = Database(settings.raw['db']['path'])
        await db.connect()
        scope = MemoryScope(kind=ScopeKind.TASK, scope_id="t1",
                            owner_key_id="key_a")
        try:
            memory, _ = _memory(settings, db)
            await memory.initialize()
            await memory.record_fact("db", "host", "old.example", scope=scope,
                                     provenance=[source()])
            await memory.record_fact("db", "host", "new.example", scope=scope,
                                     provenance=[source()])
        finally:
            await db.close()

        db2 = Database(settings.raw['db']['path'])
        await db2.connect()
        try:
            memory2, _ = _memory(settings, db2)
            await memory2.initialize()
            hits = await memory2.retrieve("db host", scope=scope)
            current = {h.record.title for h in hits}
            assert "old.example" not in current, (
                "a superseded fact resurfaced as a current belief after restart")
        finally:
            await db2.close()