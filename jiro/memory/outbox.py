"""Durable outbox for deferred memory writes.

Why this exists
---------------
The task runtime commits a task result in one transaction. Recording the episode
that describes it is a second write. Doing that inline means a crash between the
two loses the episode permanently, and doing it after the fact with a bare
``try/except`` means a transient storage error loses it just as quietly.

Anything deferred goes into ``memory_outbox`` first, so the intent is durable
before the work is attempted. Handlers are idempotent, so draining twice is
safe, and a crash mid-drain is recoverable because entries stuck in
``processing`` are reclaimed.

This is the documented "recoverable pattern" rather than an atomic commit
across two subsystems — which SQLite cannot offer, since the task result and the
memory row are written by different layers against the same writer lock.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, Optional

from jiro.log import get_logger
from jiro.memory.config import MemoryConfig
from jiro.memory.errors import redact_text
from jiro.memory.metrics import count
from jiro.memory.repository import MemoryRepository

log = get_logger("jiro.memory.outbox")

#: An entry claimed by a process that then died is reclaimed after this long.
STALE_PROCESSING_SECONDS = 300.0


class MemoryOutbox:
    """Durable queue of deferred memory writes."""

    def __init__(
        self,
        repo: MemoryRepository,
        config: MemoryConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repo = repo
        self._config = config
        self._clock = clock
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}

    def register(self, kind: str, handler: Callable[[Dict[str, Any]], Any]) -> None:
        """Register the writer for one entry kind.

        Registration is in-memory, so a restarted process must re-register
        before draining — :func:`jiro.memory.app.build_memory_service` does this
        by constructing the bridge. An entry whose kind has no handler is
        dead-lettered rather than retried, because retrying an entry nothing can
        ever process would block the queue forever.
        """
        """Register a handler for one entry kind.

        A handler must be idempotent: the outbox may deliver the same entry more
        than once after a crash.
        """
        self._handlers[kind] = handler

    # ------------------------------------------------------------------ enqueue
    async def enqueue(
        self, kind: str, *, dedup_key: str, payload: Dict[str, Any],
        available_at: Optional[float] = None,
    ) -> bool:
        """Queue a deferred write. Idempotent on ``(kind, dedup_key)``."""
        if not self._config.outbox_enabled:
            return False
        now = self._clock()
        created = await self._repo.enqueue(
            kind=kind, dedup_key=dedup_key, payload=payload, now=now,
            available_at=available_at,
        )
        if created:
            count("jiro_memory_outbox_enqueued")
        return created

    # -------------------------------------------------------------------- drain
    async def drain(self, *, limit: Optional[int] = None) -> Dict[str, int]:
        """Process due entries once.

        Returns counts so a caller (and the tests) can see what happened rather
        than infer it.
        """
        now = self._clock()
        batch = limit or self._config.outbox_batch_size
        entries = await self._repo.dequeue(now=now, limit=batch)
        processed = succeeded = failed = 0
        for entry in entries:
            processed += 1
            handler = self._handlers.get(str(entry["kind"]))
            if handler is None:
                # Unknown kind: fail it rather than retrying forever.
                await self._repo.fail_outbox(
                    int(entry["id"]), error=f"no handler for kind {entry['kind']}",
                    now=now, max_attempts=1,
                )
                failed += 1
                continue
            try:
                await handler(entry["payload"] or {})
            except asyncio.CancelledError:
                # Leave the entry claimed; `reclaim_stale_outbox` will recover it.
                raise
            except Exception as exc:
                failed += 1
                state = await self._repo.fail_outbox(
                    int(entry["id"]), error=redact_text(str(exc), max_chars=400),
                    now=self._clock(),
                    max_attempts=self._config.outbox_max_attempts,
                )
                log.warning("memory outbox entry failed", extra={
                    "entry_id": entry["id"], "kind": entry["kind"],
                    "state": state,
                })
                count("jiro_memory_outbox_failed")
                continue
            await self._repo.complete_outbox(int(entry["id"]),
                                              now=self._clock())
            succeeded += 1
        if processed:
            count("jiro_memory_outbox_drained", float(succeeded))
        return {"processed": processed, "succeeded": succeeded, "failed": failed}

    async def reclaim_stale(self) -> int:
        """Return entries abandoned by a crashed drainer to ``pending``."""
        reclaimed = await self._repo.reclaim_stale_outbox(
            now=self._clock(), stale_after=STALE_PROCESSING_SECONDS)
        if reclaimed:
            log.info("reclaimed stale memory outbox entries",
                     extra={"count": reclaimed})
        return reclaimed

    async def depth(self) -> Dict[str, int]:
        return await self._repo.outbox_depth()

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Begin periodic draining. Idempotent."""
        if self._task is not None or not self._config.outbox_enabled:
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._loop(), name="jiro-memory-outbox")
        log.info("memory outbox started", extra={
            "interval": self._config.outbox_drain_interval_seconds,
        })

    async def stop(self) -> None:
        """Stop the drain loop and await it, so no task is left pending."""
        self._stopping = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        log.info("memory outbox stopped")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _loop(self) -> None:
        interval = self._config.outbox_drain_interval_seconds
        while not self._stopping:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            try:
                await self.reclaim_stale()
                await self.drain()
            except asyncio.CancelledError:
                return
            except Exception:
                # A drain failure must never take the loop down; the next tick
                # retries against the same durable entries.
                log.exception("memory outbox drain failed")


__all__ = ["MemoryOutbox", "STALE_PROCESSING_SECONDS"]