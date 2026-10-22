"""Startup reconciliation of durable task state.

A process can die at any point. Whatever it was doing, the database is left in
one of a handful of shapes, and this module decides what each one means.

What "interrupted" means here
-----------------------------
A task left in ``running`` is only *presumed* dead once its lease has lapsed.
That distinction matters: in a multi-process deployment a live worker in another
process holds a valid lease, and stealing its work would cause duplicate
execution. A lapsing lease is the signal that nobody is coming back.

The guarantee is **at-least-once**. A task interrupted after its handler
performed an external side effect but before the result was committed will be
re-executed. Handlers performing non-idempotent work must key on the
``idempotency_key`` exposed through ``TaskContext``; exactly-once execution of
an external effect is not achievable from a database alone, and this module does
not pretend otherwise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from jiro.log import get_logger
from jiro.runtime.errors import sanitize_metadata
from jiro.runtime.repository import TaskRepository
from jiro.runtime.retry import RetryPolicy
from jiro.runtime.types import EventType, TaskEvent, TaskRecord, TaskState

log = get_logger("jiro.runtime.recovery")


@dataclass
class RecoveryReport:
    """Auditable summary of one reconciliation pass."""

    started_at: float
    finished_at: float = 0.0
    requeued: int = 0
    failed_deadline: int = 0
    failed_attempts: int = 0
    cancelled: int = 0
    retries_due: int = 0
    leases_skipped: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def total_changed(self) -> int:
        return (
            self.requeued
            + self.failed_deadline
            + self.failed_attempts
            + self.cancelled
            + self.retries_due
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "requeued": self.requeued,
            "failed_deadline": self.failed_deadline,
            "failed_attempts": self.failed_attempts,
            "cancelled": self.cancelled,
            "retries_due": self.retries_due,
            "leases_skipped": self.leases_skipped,
            "total_changed": self.total_changed,
            "errors": list(self.errors),
        }


class RecoveryManager:
    """Reconciles durable state against reality at startup.

    Args:
        repo: durable storage.
        clock: epoch-seconds source, injectable for deterministic tests.
        metrics: optional counter sink; must never raise.
    """

    def __init__(
        self,
        repo: TaskRepository,
        *,
        clock: Callable[[], float] = time.time,
        metrics: Optional[Callable[[str, str, Dict[str, float]], None]] = None,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._metrics = metrics

    async def reconcile(self, *, batch_size: int = 200, max_rounds: int = 50) -> RecoveryReport:
        """Bring durable state back in line with what actually happened.

        Args:
            batch_size: tasks examined per sweep, bounding memory on a large
                backlog.
            max_rounds: hard cap on sweeps, so a pathological backlog cannot
                stall application startup indefinitely.

        Returns:
            A :class:`RecoveryReport` recording every decision taken.
        """
        now = self._clock()
        report = RecoveryReport(started_at=now)
        log.info("runtime recovery starting", extra={"instance": id(self)})

        for _ in range(max_rounds):
            changed = 0
            changed += await self._sweep_interrupted(now, batch_size, report)
            changed += await self._sweep_pending_cancellation(now, batch_size, report)
            changed += await self._sweep_due_retries(now, batch_size, report)
            changed += await self._sweep_expired_deadlines(now, batch_size, report)
            if changed == 0:
                break

        report.finished_at = self._clock()
        if report.total_changed or report.errors:
            log.info(
                "runtime recovery applied",
                extra={
                    "report": report.to_dict(),
                },
            )
        else:
            log.info(
                "runtime recovery found nothing to reconcile",
                extra={
                    "checked_at": report.finished_at,
                },
            )
        self._count("recovery_events", float(report.total_changed))
        return report

    # ------------------------------------------------------------------ sweeps
    async def _sweep_interrupted(self, now: float, batch: int, report: RecoveryReport) -> int:
        """Tasks stuck in ``running`` behind a lapsed lease."""
        changed = 0
        for record in await self._repo.expired_leases(now=now, limit=batch):
            try:
                decision = self._decide_interrupted(record, now)
                outcome = await self._apply(record, decision, now)
                if outcome is not None:
                    _tally(report, outcome)
                    changed += 1
                    await self._event(
                        record.id, EventType.RECOVERY_APPLIED, record.attempts, decision
                    )
            except Exception as exc:  # one bad row must not stop recovery
                report.errors.append(f"{record.id}: {exc}")
                log.exception("recovery failed for task", extra={"task_id": record.id})
        return changed

    def _decide_interrupted(self, record: TaskRecord, now: float) -> Dict[str, object]:
        """Choose the honest outcome for an interrupted running task.

        Precedence: deadline first, then exhausted budget, then a requeue with
        backoff. Deadline wins because a restart must never silently reset the
        total execution budget.
        """
        if record.cancel_requested:
            return {"state": TaskState.CANCELLED, "reason": "cancelled_before_crash"}
        if record.deadline_at is not None and now >= record.deadline_at:
            return {"state": TaskState.FAILED, "reason": "deadline_exceeded"}
        if record.limits.remaining_attempts(record.attempts) <= 0:
            return {"state": TaskState.FAILED, "reason": "attempts_exhausted"}

        # Respect the *remaining* attempt budget rather than restarting it: the
        # attempts already burned stay burned across the restart.
        policy = RetryPolicy(record.limits, clock=self._clock)
        delay = policy.backoff_for(record.attempts)
        return {
            "state": TaskState.RETRY_WAIT,
            "reason": "interrupted_lease_expired",
            "delay": delay,
            "available_at": now + delay,
        }

    async def _sweep_pending_cancellation(
        self, now: float, batch: int, report: RecoveryReport
    ) -> int:
        """Tasks flagged for cancellation that never reached a terminal state."""
        rows = await self._repo.list_by_state(
            TaskState.QUEUED, limit=batch
        ) + await self._repo.list_by_state(TaskState.RETRY_WAIT, limit=batch)
        changed = 0
        for record in rows:
            if not record.cancel_requested:
                continue
            if await self._repo.mark_cancelled(
                task_id=record.id,
                now=now,
                from_states=[TaskState.QUEUED, TaskState.RETRY_WAIT],
            ):
                report.cancelled += 1
                changed += 1
                await self._event(
                    record.id,
                    EventType.CANCELLED,
                    record.attempts,
                    {"reason": "cancelled_before_crash", "state": "cancelled"},
                )
        return changed

    async def _sweep_due_retries(self, now: float, batch: int, report: RecoveryReport) -> int:
        """Retry-waiting tasks whose backoff elapsed while the process was down."""
        due = await self._repo.due_retries(now=now, limit=batch)
        changed = 0
        for record in due:
            if record.cancel_requested:
                if await self._repo.mark_cancelled(
                    task_id=record.id,
                    now=now,
                    from_states=[TaskState.RETRY_WAIT],
                ):
                    report.cancelled += 1
                    changed += 1
                continue
            if record.deadline_exceeded(now):
                if await self._repo.set_state(
                    task_id=record.id,
                    state=TaskState.FAILED,
                    now=now,
                    reason="deadline_exceeded",
                ):
                    report.failed_deadline += 1
                    changed += 1
                    await self._event(record.id, EventType.DEADLINE_EXCEEDED, record.attempts, {})
                continue
            if record.limits.remaining_attempts(record.attempts) <= 0:
                if await self._repo.set_state(
                    task_id=record.id,
                    state=TaskState.FAILED,
                    now=now,
                    reason="attempts_exhausted",
                ):
                    report.failed_attempts += 1
                    changed += 1
                continue
            if await self._repo.set_state(
                task_id=record.id,
                state=TaskState.QUEUED,
                now=now,
                reason="retry_backoff_elapsed",
            ):
                report.retries_due += 1
                changed += 1
        return changed

    async def _sweep_expired_deadlines(self, now: float, batch: int, report: RecoveryReport) -> int:
        """Queued tasks whose deadline passed while the process was down."""
        rows = await self._repo.list_by_state(TaskState.QUEUED, limit=batch)
        changed = 0
        for record in rows:
            if not record.deadline_exceeded(now):
                continue
            if await self._repo.set_state(
                task_id=record.id,
                state=TaskState.FAILED,
                now=now,
                reason="deadline_exceeded",
            ):
                report.failed_deadline += 1
                changed += 1
                await self._event(
                    record.id,
                    EventType.DEADLINE_EXCEEDED,
                    record.attempts,
                    {"reason": "deadline_exceeded", "state": "failed"},
                )
        return changed

    # ------------------------------------------------------------------ apply
    async def _apply(
        self, record: TaskRecord, decision: Dict[str, object], now: float
    ) -> Optional[str]:
        """Persist a recovery decision.

        Returns:
            The outcome label (``requeued``, ``failed``, ``cancelled``) when the
            transition was applied, or ``None`` when the task had already moved
            on and recovery correctly declined to touch it.
        """
        state: TaskState = decision["state"]  # type: ignore[assignment]
        reason = str(decision.get("reason") or "recovery")

        # Only meaningful when requeueing; `set_state` writes it verbatim and
        # leaves an existing wake-up untouched for terminal transitions.
        available_at = decision.get("available_at")
        rescheduled: Optional[float] = None
        if state is TaskState.RETRY_WAIT and available_at is not None:
            # `decision` is a heterogeneous dict; only this one key is numeric,
            # and `_decide_interrupted` is the only producer that sets it.
            rescheduled = float(available_at)  # type: ignore[arg-type]
        applied = await self._repo.set_state(
            task_id=record.id,
            state=state,
            now=now,
            reason=reason,
            available_at=rescheduled,
        )
        if not applied:
            return None

        if state is TaskState.RETRY_WAIT:
            outcome = "requeued"
        elif state is TaskState.FAILED:
            outcome = "failed_deadline" if reason == "deadline_exceeded" else "failed_attempts"
        else:
            outcome = "cancelled"

        log.info(
            "recovery decision",
            extra={
                "task_id": record.id,
                "type": record.type,
                "from": record.state.value,
                "to": state.value,
                "outcome": outcome,
                "reason": reason,
                "attempts": record.attempts,
            },
        )
        return outcome

    async def _event(
        self,
        task_id: str,
        event_type: EventType,
        attempt: Optional[int],
        decision: Dict[str, object],
    ) -> None:
        metadata = sanitize_metadata(
            {
                "reason": str(decision.get("reason") or ""),
                "delay": decision.get("delay"),
                "state": getattr(decision.get("state"), "value", None),
            }
        )
        try:
            await self._repo.append_event(
                TaskEvent(
                    task_id=task_id,
                    type=event_type,
                    ts=self._clock(),
                    attempt=attempt,
                    metadata=metadata,
                )
            )
        except Exception:
            log.exception("failed to record recovery event", extra={"task_id": task_id})

    def _count(self, name: str, value: float) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics(f"jiro_runtime_{name}", "", {"value": value})
        except Exception:
            log.debug("metrics sink raised", exc_info=True)


def _tally(report: RecoveryReport, outcome: str) -> None:
    """Record one recovery outcome on the report."""
    if outcome == "requeued":
        report.requeued += 1
    elif outcome == "failed_deadline":
        report.failed_deadline += 1
    elif outcome == "failed_attempts":
        report.failed_attempts += 1
    elif outcome == "cancelled":
        report.cancelled += 1


__all__ = ["RecoveryManager", "RecoveryReport"]
