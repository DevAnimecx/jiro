"""Enterprise webhooks, batch jobs, and data export/import.

Provides:
- Webhook management (CRUD, delivery tracking)
- Batch job orchestration with status tracking
- Data export/import for migration and backup
- Admin UI API endpoints
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class WebhookStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Webhook:
    webhook_id: str
    url: str
    secret: str
    events: List[str]
    status: WebhookStatus = WebhookStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    last_triggered: Optional[float] = None
    failure_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "webhook_id": self.webhook_id,
            "url": self.url,
            "events": self.events,
            "status": self.status.value,
            "created_at": self.created_at,
            "last_triggered": self.last_triggered,
            "failure_count": self.failure_count,
            "metadata": self.metadata,
        }

    def sign_payload(self, payload: bytes) -> str:
        return hmac.new(
            self.secret.encode(), payload, hashlib.sha256
        ).hexdigest()


@dataclass
class WebhookDelivery:
    delivery_id: str
    webhook_id: str
    event: str
    payload: Dict[str, Any]
    status: str = "pending"
    status_code: int = 0
    response_body: str = ""
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    delivered_at: Optional[float] = None
    next_retry_at: Optional[float] = None
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "webhook_id": self.webhook_id,
            "event": self.event,
            "status": self.status,
            "status_code": self.status_code,
            "attempts": self.attempts,
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
            "next_retry_at": self.next_retry_at,
            "error": self.error,
        }


@dataclass
class BatchJob:
    job_id: str
    job_type: str
    status: JobStatus = JobStatus.PENDING
    input_data: Dict[str, Any] = field(default_factory=dict)
    output_data: Dict[str, Any] = field(default_factory=dict)
    progress: float = 0.0
    total_items: int = 0
    processed_items: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status.value,
            "progress": round(self.progress, 2),
            "total_items": self.total_items,
            "processed_items": self.processed_items,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "metadata": self.metadata,
        }


class WebhookManager:
    def __init__(self):
        self._webhooks: Dict[str, Webhook] = {}
        self._deliveries: Dict[str, WebhookDelivery] = {}
        self._handlers: Dict[str, Callable] = {}

    def create_webhook(self, url: str, events: List[str],
                       metadata: Optional[Dict[str, Any]] = None) -> Webhook:
        webhook_id = f"wh_{uuid.uuid4().hex[:12]}"
        secret = f"whsec_{uuid.uuid4().hex}"
        webhook = Webhook(
            webhook_id=webhook_id, url=url, secret=secret,
            events=events, metadata=metadata or {},
        )
        self._webhooks[webhook_id] = webhook
        return webhook

    def get_webhook(self, webhook_id: str) -> Optional[Webhook]:
        return self._webhooks.get(webhook_id)

    def update_webhook(self, webhook_id: str, **kwargs) -> Optional[Webhook]:
        webhook = self._webhooks.get(webhook_id)
        if not webhook:
            return None
        for k, v in kwargs.items():
            if hasattr(webhook, k):
                setattr(webhook, k, v)
        return webhook

    def delete_webhook(self, webhook_id: str) -> bool:
        return self._webhooks.pop(webhook_id, None) is not None

    def list_webhooks(self, event: Optional[str] = None) -> List[Dict[str, Any]]:
        hooks = list(self._webhooks.values())
        if event:
            hooks = [h for h in hooks if event in h.events]
        return [h.to_dict() for h in hooks]

    def register_handler(self, event: str, handler: Callable) -> None:
        self._handlers[event] = handler

    def trigger_event(self, event: str, payload: Dict[str, Any]) -> List[str]:
        delivery_ids = []
        for webhook in self._webhooks.values():
            if webhook.status != WebhookStatus.ACTIVE:
                continue
            if event not in webhook.events and "*" not in webhook.events:
                continue
            delivery = WebhookDelivery(
                delivery_id=f"del_{uuid.uuid4().hex[:12]}",
                webhook_id=webhook.webhook_id,
                event=event,
                payload=payload,
            )
            self._deliveries[delivery.delivery_id] = delivery
            delivery_ids.append(delivery.delivery_id)
            webhook.last_triggered = time.time()
        return delivery_ids

    def get_delivery(self, delivery_id: str) -> Optional[WebhookDelivery]:
        return self._deliveries.get(delivery_id)

    def get_deliveries(self, webhook_id: Optional[str] = None,
                       limit: int = 100) -> List[Dict[str, Any]]:
        deliveries = list(self._deliveries.values())
        if webhook_id:
            deliveries = [d for d in deliveries if d.webhook_id == webhook_id]
        deliveries.sort(key=lambda d: d.created_at, reverse=True)
        return [d.to_dict() for d in deliveries[:limit]]


class BatchJobManager:
    def __init__(self):
        self._jobs: Dict[str, BatchJob] = {}
        self._handlers: Dict[str, Callable] = {}

    def create_job(self, job_type: str, input_data: Dict[str, Any],
                   total_items: int = 0, metadata: Optional[Dict[str, Any]] = None) -> BatchJob:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        job = BatchJob(
            job_id=job_id, job_type=job_type,
            input_data=input_data, total_items=total_items,
            metadata=metadata or {},
        )
        self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[BatchJob]:
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return False
        job.status = JobStatus.CANCELLED
        return True

    def list_jobs(self, status: Optional[str] = None,
                  job_type: Optional[str] = None) -> List[Dict[str, Any]]:
        jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.status.value == status]
        if job_type:
            jobs = [j for j in jobs if j.job_type == job_type]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [j.to_dict() for j in jobs]

    def update_progress(self, job_id: str, processed: int,
                        output_data: Optional[Dict[str, Any]] = None) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.processed_items = processed
        if job.total_items > 0:
            job.progress = (processed / job.total_items) * 100
        if output_data:
            job.output_data.update(output_data)
        return True

    def complete_job(self, job_id: str, output_data: Optional[Dict[str, Any]] = None) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.status = JobStatus.COMPLETED
        job.completed_at = time.time()
        job.progress = 100.0
        if output_data:
            job.output_data.update(output_data)
        return True

    def fail_job(self, job_id: str, error: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.status = JobStatus.FAILED
        job.completed_at = time.time()
        job.error = error
        return True


_webhook_manager: Optional[WebhookManager] = None
_batch_manager: Optional[BatchJobManager] = None


def get_webhook_manager() -> WebhookManager:
    global _webhook_manager
    if _webhook_manager is None:
        _webhook_manager = WebhookManager()
    return _webhook_manager


def get_batch_manager() -> BatchJobManager:
    global _batch_manager
    if _batch_manager is None:
        _batch_manager = BatchJobManager()
    return _batch_manager


def reset_enterprise_api():
    global _webhook_manager, _batch_manager
    _webhook_manager = None
    _batch_manager = None
