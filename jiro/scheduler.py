"""Scheduled searches with cron-like scheduling and notifications.

Provides:
- Cron-like schedule definitions
- Recurring search jobs
- Email/webhook notifications on results
- Schedule management (create, pause, resume, delete)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class ScheduleStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class NotificationType(str, Enum):
    EMAIL = "email"
    WEBHOOK = "webhook"
    NONE = "none"


@dataclass
class CronExpression:
    """Cron-like schedule expression."""
    minute: str = "*"  # 0-59
    hour: str = "*"    # 0-23
    day: str = "*"     # 1-31
    month: str = "*"   # 1-12
    weekday: str = "*" # 0-6 (0=Sunday)

    def matches(self, timestamp: Optional[float] = None) -> bool:
        """Check if current time matches the cron expression."""
        import datetime
        
        ts = timestamp or time.time()
        dt = datetime.datetime.fromtimestamp(ts)
        
        return (
            self._match_field(self.minute, dt.minute, 0, 59) and
            self._match_field(self.hour, dt.hour, 0, 23) and
            self._match_field(self.day, dt.day, 1, 31) and
            self._match_field(self.month, dt.month, 1, 12) and
            self._match_field(self.weekday, dt.weekday(), 0, 6)
        )

    def _match_field(self, pattern: str, value: int, min_val: int, max_val: int) -> bool:
        """Check if a field value matches a cron pattern."""
        if pattern == "*":
            return True
        
        if pattern.isdigit():
            return int(pattern) == value
        
        if "/" in pattern:
            start, step = pattern.split("/")
            start = int(start) if start else min_val
            step = int(step)
            return (value - start) % step == 0
        
        if "-" in pattern:
            start, end = map(int, pattern.split("-"))
            return start <= value <= end
        
        if "," in pattern:
            values = [int(v) for v in pattern.split(",")]
            return value in values
        
        return False

    def to_string(self) -> str:
        """Convert to cron string."""
        return f"{self.minute} {self.hour} {self.day} {self.month} {self.weekday}"

    @classmethod
    def from_string(cls, cron_string: str) -> CronExpression:
        """Parse cron string."""
        parts = cron_string.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_string}")
        return cls(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            weekday=parts[4],
        )


@dataclass
class Notification:
    """Notification configuration."""
    type: NotificationType
    target: str  # email address or webhook URL
    template: Optional[str] = None


@dataclass
class ScheduledSearch:
    """A scheduled search job."""
    job_id: str
    name: str
    query: str
    schedule: CronExpression
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    last_run: Optional[float] = None
    next_run: Optional[float] = None
    last_result_count: int = 0
    notifications: List[Notification] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "query": self.query,
            "schedule": self.schedule.to_string(),
            "status": self.status.value,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "last_result_count": self.last_result_count,
            "notifications": [
                {"type": n.type.value, "target": n.target}
                for n in self.notifications
            ],
            "metadata": self.metadata,
        }


class ScheduleManager:
    """Manage scheduled searches."""

    def __init__(self) -> None:
        self._jobs: Dict[str, ScheduledSearch] = {}
        self._callbacks: Dict[str, Callable] = {}

    def create_job(
        self,
        name: str,
        query: str,
        cron: str,
        notifications: Optional[List[Dict[str, str]]] = None,
        callback: Optional[Callable] = None,
    ) -> ScheduledSearch:
        """Create a new scheduled search job."""
        job_id = str(uuid.uuid4())[:8]
        schedule = CronExpression.from_string(cron)
        
        notif_list = []
        for n in (notifications or []):
            notif_list.append(Notification(
                type=NotificationType(n.get("type", "none")),
                target=n.get("target", ""),
            ))
        
        job = ScheduledSearch(
            job_id=job_id,
            name=name,
            query=query,
            schedule=schedule,
            notifications=notif_list,
        )
        
        self._jobs[job_id] = job
        if callback:
            self._callbacks[job_id] = callback
        
        return job

    def get_job(self, job_id: str) -> Optional[ScheduledSearch]:
        """Get a job by ID."""
        return self._jobs.get(job_id)

    def list_jobs(self, status: Optional[ScheduleStatus] = None) -> List[ScheduledSearch]:
        """List all jobs, optionally filtered by status."""
        jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def pause_job(self, job_id: str) -> bool:
        """Pause a job."""
        if job_id in self._jobs:
            self._jobs[job_id].status = ScheduleStatus.PAUSED
            return True
        return False

    def resume_job(self, job_id: str) -> bool:
        """Resume a paused job."""
        if job_id in self._jobs:
            self._jobs[job_id].status = ScheduleStatus.ACTIVE
            return True
        return False

    def delete_job(self, job_id: str) -> bool:
        """Delete a job."""
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._callbacks.pop(job_id, None)
            return True
        return False

    def get_due_jobs(self) -> List[ScheduledSearch]:
        """Get jobs that are due to run."""
        now = time.time()
        due_jobs = []
        
        for job in self._jobs.values():
            if job.status != ScheduleStatus.ACTIVE:
                continue
            
            if job.next_run and job.next_run <= now:
                due_jobs.append(job)
            elif job.schedule.matches(now):
                due_jobs.append(job)
        
        return due_jobs

    def mark_job_run(self, job_id: str, result_count: int) -> None:
        """Mark a job as run and update next run time."""
        if job_id in self._jobs:
            job = self._jobs[job_id]
            job.last_run = time.time()
            job.last_result_count = result_count
            # Calculate next run (simplified - in production would use croniter)
            job.next_run = job.last_run + 3600  # Default 1 hour

    def trigger_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Manually trigger a job."""
        job = self._jobs.get(job_id)
        if not job:
            return None
        
        callback = self._callbacks.get(job_id)
        if callback:
            try:
                result = callback(job.query)
                self.mark_job_run(job_id, len(result) if isinstance(result, list) else 0)
                return {"status": "triggered", "result_count": job.last_result_count}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        
        return {"status": "no_callback"}

    def export_jobs(self) -> List[Dict[str, Any]]:
        """Export all jobs."""
        return [job.to_dict() for job in self._jobs.values()]

    def import_jobs(self, jobs_data: List[Dict[str, Any]]) -> int:
        """Import jobs from data."""
        count = 0
        for data in jobs_data:
            try:
                self.create_job(
                    name=data["name"],
                    query=data["query"],
                    cron=data["schedule"],
                    notifications=data.get("notifications"),
                )
                count += 1
            except Exception:
                continue
        return count


# Global instance
_schedule_manager = ScheduleManager()


def get_schedule_manager() -> ScheduleManager:
    """Get the global schedule manager."""
    return _schedule_manager
