"""Async background jobs with optional webhook delivery (PRD Phase 3).

Job types:
* ``ai_search``   — agentic research (payload: AISearchRequest fields)
* ``ai_agent``    — multi-step autonomous research (payload: AgentRequest fields)
* ``batch_scrape``— batch URL scrape (payload: {"urls": [...], "format": "..."})

Jobs run in an asyncio task; ``GET /jobs/{id}`` returns status/progress/result.
On completion the result is POSTed to ``webhook_url`` (3 retries with backoff).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, Optional

import httpx

from jiro.db import Database
from jiro.log import get_logger

log = get_logger("jiro.jobs")

JOB_TYPES = ("ai_search", "ai_agent", "batch_scrape")


class JobManager:
    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db
        self._tasks: Dict[str, asyncio.Task] = {}
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._max_concurrent = 8
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

    # ---------------------------------------------------------------- submit
    async def submit(self, job_type: str, payload: Dict[str, Any], *,
                     webhook_url: Optional[str] = None,
                     webhook_secret: Optional[str] = None,
                     runner: Optional[Any] = None) -> Dict[str, Any]:
        """Queue a job. ``runner`` is an async callable(job_type, payload) -> dict."""
        if job_type not in JOB_TYPES:
            raise ValueError(f"unknown job type '{job_type}'")
        job_id = "job_" + uuid.uuid4().hex[:16]
        record: Dict[str, Any] = {
            "id": job_id, "type": job_type, "status": "queued",
            "payload": payload, "result": None, "error": None, "progress": "queued",
            "webhook_url": webhook_url, "webhook_secret": webhook_secret or "",
            "webhook_delivered": False, "created_at": time.time(), "completed_at": None,
        }
        self._memory[job_id] = record
        if self.db is not None:
            await self.db.execute(
                "INSERT INTO jobs (id, type, status, payload, webhook_url,"
                " webhook_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, job_type, "queued", json.dumps(payload, default=str),
                 webhook_url, webhook_secret or "", time.time()),
            )

        if runner is None:
            raise ValueError("runner required to execute jobs")
        task = asyncio.create_task(self._run(job_id, job_type, payload, runner))
        self._tasks[job_id] = task
        return record

    # ------------------------------------------------------------------ run
    async def _run(self, job_id: str, job_type: str, payload: Dict[str, Any],
                   runner: Any) -> None:
        async with self._semaphore:
            await self._update(job_id, status="running", progress="running")
            started = time.perf_counter()
            try:
                result = await runner(job_type, payload)
                latency = round(time.perf_counter() - started, 3)
                result["time_taken"] = latency
                await self._update(job_id, status="completed", progress="completed",
                                   result=result)
                await self._deliver_webhook(job_id)
            except Exception as exc:
                log.exception("job failed", extra={"job_id": job_id, "type": job_type})
                await self._update(job_id, status="failed", error=str(exc))

    async def _update(self, job_id: str, **fields: Any) -> None:
        record = self._memory.get(job_id)
        if record is not None:
            record.update(fields)
        if self.db is not None:
            sets, params = [], []
            if "status" in fields:
                sets.append("status = ?")
                params.append(fields["status"])
            if "result" in fields:
                sets.append("result = ?")
                params.append(json.dumps(fields["result"], default=str))
            if "error" in fields:
                sets.append("error = ?")
                params.append(fields["error"])
            if "progress" in fields:
                sets.append("progress = ?")
                params.append(fields["progress"])
            if "webhook_delivered" in fields:
                sets.append("webhook_delivered = ?")
                params.append(int(fields["webhook_delivered"]))
            if fields.get("status") in ("completed", "failed"):
                sets.append("completed_at = ?")
                params.append(time.time())
            if sets:
                params.append(job_id)
                await self.db.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?",
                                      tuple(params))

    # ---------------------------------------------------------------- lookup
    async def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        if job_id in self._memory:
            return self._to_out(self._memory[job_id])
        if self.db is not None:
            row = await self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
            if row is None:
                return None
            return {
                "id": row["id"], "type": row["type"], "status": row["status"],
                "payload": json.loads(row["payload"] or "{}"),
                "result": json.loads(row["result"]) if row.get("result") else None,
                "error": row.get("error"),
                "progress": row.get("progress", ""),
                "webhook_url": row.get("webhook_url"),
                "webhook_delivered": bool(row.get("webhook_delivered")),
                "created_at": row["created_at"],
                "completed_at": row.get("completed_at"),
            }
        return None

    async def list_jobs(self, limit: int = 20) -> list:
        jobs = [self._to_out(r) for r in
                sorted(self._memory.values(), key=lambda r: r["created_at"], reverse=True)]
        return jobs[:limit]

    @staticmethod
    def _to_out(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": record["id"], "type": record["type"], "status": record["status"],
            "payload": record.get("payload", {}),
            "result": record.get("result"),
            "error": record.get("error"),
            "progress": record.get("progress", ""),
            "webhook_url": record.get("webhook_url"),
            "webhook_delivered": bool(record.get("webhook_delivered")),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime(record.get("created_at", 0))),
            "completed_at": (time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(record["completed_at"]))
                             if record.get("completed_at") else None),
        }

    # ---------------------------------------------------------------- webhook
    async def _deliver_webhook(self, job_id: str) -> None:
        record = self._memory.get(job_id)
        if record is None or not record.get("webhook_url"):
            return
        url = record["webhook_url"]
        secret = record.get("webhook_secret") or ""
        body = json.dumps({
            "job_id": job_id, "type": record["type"], "status": "completed",
            "result": record.get("result"), "completed_at": time.time(),
        }, default=str)
        signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json",
                   "X-Jiro-Webhook-Sig": signature,
                   "X-Jiro-Job-ID": job_id}
        max_retries = 5
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(url, content=body, headers=headers)
                    if resp.status_code < 400:
                        await self._update(job_id, webhook_delivered=True)
                        log.info("webhook delivered", extra={
                            "job_id": job_id, "status": resp.status_code,
                            "attempt": attempt + 1,
                        })
                        return
                    log.warning("webhook delivery rejected", extra={
                        "job_id": job_id, "status": resp.status_code,
                        "attempt": attempt + 1,
                    })
            except Exception as exc:
                log.warning("webhook delivery error", extra={
                    "job_id": job_id, "error": str(exc), "attempt": attempt + 1,
                })
            # Exponential backoff with jitter
            import random
            base_delay = 2 ** attempt
            jitter = random.uniform(0, base_delay * 0.5)
            await asyncio.sleep(base_delay + jitter)
        log.warning("webhook delivery failed after %d attempts", max_retries,
                    extra={"job_id": job_id, "url": url})
