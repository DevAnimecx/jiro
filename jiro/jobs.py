"""Async background jobs with optional webhook delivery (PRD Phase 3).

Job types:
* ``ai_search``   — agentic research (payload: AISearchRequest fields)
* ``ai_agent``    — multi-step autonomous research (payload: AgentRequest fields)
* ``batch_scrape``— batch URL scrape (payload: {"urls": [...], "format": "..."})

On completion the result is POSTed to ``webhook_url`` (3 retries with backoff).

Phase 1 (v0.4.0)
---------------
Execution is delegated to the durable runtime in :mod:`jiro.runtime`, so jobs
now survive a restart, honour attempt budgets, and can be cancelled. The
``jobs`` table remains the compatibility record; this class maps the runtime's
vocabulary onto the one ``/jobs`` has always returned.

Mapping, deliberately fixed so the public contract does not move:

===================  ==================
runtime state        ``/jobs`` status
===================  ==================
``succeeded``        ``completed``
``failed``           ``failed``
``cancelled``        ``failed``
``queued``           ``queued``
``running``          ``running``
``retry_wait``       ``queued``
===================  ==================

A cancelled job reports ``failed`` because ``/jobs`` has no cancelled status and
inventing one would break every existing client.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from jiro.db import Database
from jiro.log import get_logger
from jiro.runtime.runtime import TaskRuntime
from jiro.runtime.types import TaskState

log = get_logger("jiro.jobs")

JOB_TYPES = ("ai_search", "ai_agent", "batch_scrape")

#: Runtime state -> the status string ``/jobs`` has always returned.
_STATE_TO_JOB_STATUS: Dict[TaskState, str] = {
    TaskState.QUEUED: "queued",
    TaskState.RETRY_WAIT: "queued",
    TaskState.RUNNING: "running",
    TaskState.SUCCEEDED: "completed",
    TaskState.FAILED: "failed",
    TaskState.CANCELLED: "failed",
}


def _to_job_id(task_id: str) -> str:
    """Present a runtime task id under the ``job_`` prefix clients already see."""
    if task_id.startswith("task_"):
        return "job_" + task_id[len("task_") :]
    return task_id


def _from_job_id(job_id: str) -> str:
    if job_id.startswith("job_"):
        return "task_" + job_id[len("job_") :]
    return job_id


class JobManager:
    """Compatibility facade over the durable task runtime.

    Args:
        db: the shared database.
        runtime: the durable runtime. Optional so the manager can still be
            constructed (and unit tested) before startup completes; without it
            the manager reports every job as failed rather than pretending to
            have run one.
    """

    def __init__(
        self,
        db: Optional[Database] = None,
        *,
        runtime: Optional[TaskRuntime] = None,
    ) -> None:
        self.db = db
        self.runtime = runtime
        self._tasks: Dict[str, asyncio.Task] = {}
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._max_concurrent = 8
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

    def bind(self, runtime: TaskRuntime) -> None:
        """Attach the runtime once startup has built it."""
        self.runtime = runtime

    # ---------------------------------------------------------------- submit
    async def submit(
        self,
        job_type: str,
        payload: Dict[str, Any],
        *,
        webhook_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        runner: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Queue a job.

        Two execution modes, selected automatically:

        * **Durable** — once a runtime is bound (which is what application
          startup does). The job is persisted, budgeted, retried, and survives a
          restart. ``runner`` is ignored.
        * **In-process** — no runtime bound. The legacy closure-based path runs
          the job in a bare ``asyncio.Task``. Retained because ``JobManager`` is
          a public class that existing callers construct directly and its
          behaviour is covered by existing tests. Jobs run this way do **not**
          survive a restart, are not retried, and cannot be cancelled.
        """
        if job_type not in JOB_TYPES:
            raise ValueError(f"unknown job type '{job_type}'")

        if self.runtime is None:
            if runner is None:
                raise ValueError("runner required when no durable runtime is bound")
            return await self._submit_in_process(
                job_type, payload, webhook_url, webhook_secret, runner
            )

        record = await self.runtime.submit(job_type, payload)
        job_id = _to_job_id(record.id)
        self._memory[job_id] = {
            "id": job_id,
            "type": record.type,
            "status": "queued",
            "payload": payload,
            "result": None,
            "error": None,
            "progress": "queued",
            "webhook_url": webhook_url,
            "webhook_secret": webhook_secret or "",
            "webhook_delivered": False,
            "created_at": record.created_at,
            "completed_at": None,
        }
        if self.db is not None:
            await self.db.execute(
                "INSERT OR REPLACE INTO jobs (id, type, status, payload, webhook_url,"
                " webhook_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    job_type,
                    "queued",
                    json.dumps(payload, default=str),
                    webhook_url,
                    webhook_secret or "",
                    record.created_at,
                ),
            )
        self._memory[job_id]["_task_id"] = record.id

        webhook = {
            "url": webhook_url,
            "secret": webhook_secret or "",
            "job_id": job_id,
            "task_id": record.id,
        }
        task = asyncio.create_task(self._finish_and_notify(record.id, webhook))
        self._tasks[job_id] = task
        return dict(self._memory[job_id])

    async def _submit_in_process(
        self,
        job_type: str,
        payload: Dict[str, Any],
        webhook_url: Optional[str],
        webhook_secret: Optional[str],
        runner: Any,
    ) -> Dict[str, Any]:
        """Legacy closure-based execution, used when no runtime is bound."""
        job_id = "job_" + uuid.uuid4().hex[:16]
        record: Dict[str, Any] = {
            "id": job_id,
            "type": job_type,
            "status": "queued",
            "payload": payload,
            "result": None,
            "error": None,
            "progress": "queued",
            "webhook_url": webhook_url,
            "webhook_secret": webhook_secret or "",
            "webhook_delivered": False,
            "created_at": time.time(),
            "completed_at": None,
        }
        self._memory[job_id] = record
        if self.db is not None:
            await self.db.execute(
                "INSERT INTO jobs (id, type, status, payload, webhook_url,"
                " webhook_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    job_type,
                    "queued",
                    json.dumps(payload, default=str),
                    webhook_url,
                    webhook_secret or "",
                    record["created_at"],
                ),
            )
        task = asyncio.create_task(self._run_legacy(job_id, job_type, payload, runner))
        self._tasks[job_id] = task
        return record

    async def _run_legacy(
        self, job_id: str, job_type: str, payload: Dict[str, Any], runner: Any
    ) -> None:
        """Execute a job in-process. Bounded by the manager's own semaphore."""
        async with self._semaphore:
            await self._update(job_id, status="running", progress="running")
            started = time.perf_counter()
            try:
                result = await runner(job_type, payload)
                result["time_taken"] = round(time.perf_counter() - started, 3)
                await self._update(
                    job_id,
                    status="completed",
                    progress="completed",
                    result=result,
                )
                await self._deliver_legacy_webhook(job_id)
            except asyncio.CancelledError:
                await self._update(job_id, status="failed", error="cancelled", progress="failed")
                raise
            except Exception as exc:
                log.exception("job failed", extra={"job_id": job_id, "type": job_type})
                await self._update(job_id, status="failed", error=str(exc), progress="failed")

    async def _update(self, job_id: str, **fields: Any) -> None:
        """Update the in-memory record and the compatibility table."""
        record = self._memory.get(job_id)
        if record is not None:
            record.update(fields)
        if self.db is None:
            return
        sets: List[str] = []
        params: List[Any] = []
        for column, value in (
            ("status", fields.get("status")),
            ("result", json.dumps(fields["result"], default=str) if "result" in fields else None),
            ("error", fields.get("error")),
            ("progress", fields.get("progress")),
            (
                "webhook_delivered",
                int(fields["webhook_delivered"]) if "webhook_delivered" in fields else None,
            ),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        if fields.get("status") in ("completed", "failed"):
