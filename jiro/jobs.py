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
            sets.append("completed_at = ?")
            params.append(time.time())
        if sets:
            params.append(job_id)
            await self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", tuple(params))

    async def _finish_and_notify(self, task_id: str, webhook: Dict[str, Any]) -> None:
        """Wait for a job to reach a terminal state, then deliver its webhook."""
        runtime = self.runtime
        if runtime is None:  # pragma: no cover - only if unbound mid-flight
            log.warning("job observation skipped: no runtime bound",
                        extra={"task_id": task_id})
            return
        try:
            record = await runtime.wait_for(task_id, timeout=3600)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("job observation failed", extra={"task_id": task_id})
            return

        job_id = webhook["job_id"]
        cached = self._memory.get(job_id)
        if cached is not None:
            cached.update(
                {
                    "status": _STATE_TO_JOB_STATUS.get(record.state, "failed"),
                    "result": record.result,
                    "error": record.error_message,
                    "progress": record.state.value,
                    "completed_at": record.completed_at,
                }
            )
        await self._persist(job_id, record)

        if webhook["url"] and record.state is TaskState.SUCCEEDED:
            await self._deliver_webhook(webhook, record)

    async def _persist(self, job_id: str, record: Any) -> None:
        if self.db is None:
            return
        await self.db.execute(
            "UPDATE jobs SET status = ?, result = ?, error = ?, progress = ?,"
            " completed_at = ? WHERE id = ?",
            (
                _STATE_TO_JOB_STATUS.get(record.state, "failed"),
                json.dumps(record.result, default=str) if record.result else None,
                record.error_message,
                record.state.value,
                record.completed_at,
                job_id,
            ),
        )

    # ---------------------------------------------------------------- lookup
    async def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one job by its ``job_`` id, or ``None``."""
        cached = self._memory.get(job_id)
        if cached is not None:
            return self._to_out(cached)
        if self.runtime is None:
            return None
        try:
            record = await self.runtime.get(
                _from_job_id(job_id), owner_key_id=None, enforce_owner=False
            )
        except Exception:
            return None
        return self._record_to_out(record, job_id)

    async def list_jobs(self, limit: int = 20) -> list:
        """Recent jobs, newest first.

        Reads the durable store rather than the in-process cache, so jobs
        submitted before a restart are still listed.
        """
        if self.runtime is not None:
            try:
                records = await self.runtime.repo.list_by_state(TaskState.QUEUED, limit=limit)
                for state in (
                    TaskState.RUNNING,
                    TaskState.RETRY_WAIT,
                    TaskState.SUCCEEDED,
                    TaskState.FAILED,
                    TaskState.CANCELLED,
                ):
                    records.extend(await self.runtime.repo.list_by_state(state, limit=limit))
                records.sort(key=lambda r: r.created_at, reverse=True)
                return [self._record_to_out(r, _to_job_id(r.id)) for r in records[:limit]]
            except Exception:
                log.exception("failed to list jobs from the runtime")
        return [
            self._to_out(r)
            for r in sorted(self._memory.values(), key=lambda r: r["created_at"], reverse=True)
        ][:limit]

    def _record_to_out(self, record: Any, job_id: str) -> Dict[str, Any]:
        cached = self._memory.get(job_id, {})
        out = self._to_out(
            {
                "id": job_id,
                "type": record.type,
                "status": _STATE_TO_JOB_STATUS.get(record.state, "failed"),
                "payload": record.input,
                "result": record.result,
                "error": record.error_message,
                "progress": record.state.value,
                "webhook_url": cached.get("webhook_url"),
                "webhook_delivered": bool(cached.get("webhook_delivered")),
                "created_at": record.created_at,
                "completed_at": record.completed_at,
            }
        )
        return out

    @staticmethod
    def _to_out(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": record["id"],
            "type": record["type"],
            "status": record.get("status", "queued"),
            "payload": record.get("payload", {}),
            "result": record.get("result"),
            "error": record.get("error"),
            "progress": record.get("progress", ""),
            "webhook_url": record.get("webhook_url"),
            "webhook_delivered": bool(record.get("webhook_delivered")),
            "created_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.get("created_at", 0))
            ),
            "completed_at": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record["completed_at"]))
                if record.get("completed_at")
                else None
            ),
        }

    # ---------------------------------------------------------------- webhook
    async def _deliver_legacy_webhook(self, job_id: str) -> None:
        """POST a completed job's result, with backoff, in in-process mode."""
        record = self._memory.get(job_id)
        if record is None or not record.get("webhook_url"):
            return
        await self._post_webhook(
            {
                "url": record["webhook_url"],
                "secret": record.get("webhook_secret") or "",
                "job_id": job_id,
            },
            {
                "job_id": job_id,
                "type": record["type"],
                "status": "completed",
                "result": record.get("result"),
                "completed_at": time.time(),
            },
        )

    async def _deliver_webhook(self, webhook: Dict[str, Any], record: Any) -> None:
        """POST a completed job's result for the durable path."""
        await self._post_webhook(
            webhook,
            {
                "job_id": webhook["job_id"],
                "type": record.type,
                "status": "completed",
                "result": record.result,
                "completed_at": record.completed_at,
            },
        )

    async def _post_webhook(self, webhook: Dict[str, Any], payload: Dict[str, Any]) -> None:
        """HMAC-signed webhook POST with bounded exponential backoff.

        The retry counter is a local loop variable in both modes. That is a
        known limitation, not an oversight: a crash mid-delivery loses the
        attempt count and the delivery flag, and the webhook is not re-sent on
        restart. See docs/adr/0006.
        """
        url = webhook["url"]
        secret = webhook["secret"]
        body = json.dumps(payload, default=str)
        signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Jiro-Webhook-Sig": signature,
            "X-Jiro-Job-ID": webhook["job_id"],
        }
        max_retries = 5
        import random

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(url, content=body, headers=headers)
                    if resp.status_code < 400:
                        await self._mark_webhook_delivered(webhook["job_id"])
                        log.info(
                            "webhook delivered",
                            extra={
                                "job_id": webhook["job_id"],
                                "status": resp.status_code,
                                "attempt": attempt + 1,
                            },
                        )
                        return
                    log.warning(
                        "webhook delivery rejected",
                        extra={
                            "job_id": webhook["job_id"],
                            "status": resp.status_code,
                            "attempt": attempt + 1,
                        },
                    )
            except Exception as exc:
                log.warning(
                    "webhook delivery error",
                    extra={
                        "job_id": webhook["job_id"],
                        "error": str(exc),
                        "attempt": attempt + 1,
                    },
                )
            # Exponential backoff with jitter
            base_delay = 2**attempt
            jitter = random.uniform(0, base_delay * 0.5)
            await asyncio.sleep(base_delay + jitter)
        log.warning(
            "webhook delivery failed after %d attempts",
            max_retries,
            extra={"job_id": webhook["job_id"], "url": url},
        )

    async def _mark_webhook_delivered(self, job_id: str) -> None:
        cached = self._memory.get(job_id)
        if cached is not None:
            cached["webhook_delivered"] = True
        if self.db is not None:
            await self.db.execute("UPDATE jobs SET webhook_delivered = 1 WHERE id = ?", (job_id,))
