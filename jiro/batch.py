"""Batch operations for search and scrape.

Provides:
- Batch search with multiple queries
- Batch scrape with multiple URLs
- Concurrent execution with rate limiting
- Progress tracking and result aggregation
"""

from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class BatchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class BatchItem:
    """A single item in a batch operation."""
    item_id: str
    query: Optional[str] = None
    url: Optional[str] = None
    status: str = "pending"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: float = 0


@dataclass
class BatchJob:
    """A batch operation job."""
    job_id: str
    operation: str  # "search" or "scrape"
    status: BatchStatus = BatchStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    items: List[BatchItem] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def progress(self) -> float:
        if self.total_items == 0:
            return 0
        return (self.completed_items / self.total_items) * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "operation": self.operation,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_items": self.total_items,
            "completed_items": self.completed_items,
            "failed_items": self.failed_items,
            "progress": f"{self.progress:.1f}%",
            "items": [
                {
                    "item_id": item.item_id,
                    "query": item.query,
                    "url": item.url,
                    "status": item.status,
                    "error": item.error,
                    "duration_ms": item.duration_ms,
                }
                for item in self.items
            ],
        }


class BatchProcessor:
    """Process batch operations."""

    def __init__(self, max_workers: int = 5) -> None:
        self.max_workers = max_workers
        self._jobs: Dict[str, BatchJob] = {}

    def batch_search(
        self,
        queries: List[str],
        search_fn: Callable[[str], Dict[str, Any]],
        engine: str = "google",
        num_results: int = 10,
        max_concurrent: int = 5,
    ) -> BatchJob:
        """Execute multiple searches in batch."""
        job_id = str(uuid.uuid4())[:8]
        job = BatchJob(
            job_id=job_id,
            operation="search",
            total_items=len(queries),
        )

        for query in queries:
            item = BatchItem(
                item_id=str(uuid.uuid4())[:8],
                query=query,
            )
            job.items.append(item)

        self._jobs[job_id] = job

        # Execute in thread pool
        def process_item(item: BatchItem) -> None:
            if not item.query:
                return
            
            start = time.time()
            try:
                result = search_fn(item.query)
                item.result = result
                item.status = "completed"
                item.duration_ms = (time.time() - start) * 1000
                job.completed_items += 1
            except Exception as e:
                item.error = str(e)
                item.status = "failed"
                item.duration_ms = (time.time() - start) * 1000
                job.failed_items += 1

        job.status = BatchStatus.RUNNING
        job.started_at = time.time()

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {executor.submit(process_item, item): item for item in job.items}
            for future in as_completed(futures):
                future.result()  # Raise any exceptions

        job.completed_at = time.time()
        job.status = BatchStatus.COMPLETED if job.failed_items == 0 else BatchStatus.PARTIAL

        return job

    def batch_scrape(
        self,
        urls: List[str],
        scrape_fn: Callable[[str], Dict[str, Any]],
        format: str = "markdown",
        max_concurrent: int = 5,
    ) -> BatchJob:
        """Execute multiple scrapes in batch."""
        job_id = str(uuid.uuid4())[:8]
        job = BatchJob(
            job_id=job_id,
            operation="scrape",
            total_items=len(urls),
        )

        for url in urls:
            item = BatchItem(
                item_id=str(uuid.uuid4())[:8],
                url=url,
            )
            job.items.append(item)

        self._jobs[job_id] = job

        def process_item(item: BatchItem) -> None:
            if not item.url:
                return
            
            start = time.time()
            try:
                result = scrape_fn(item.url)
                item.result = result
                item.status = "completed"
                item.duration_ms = (time.time() - start) * 1000
                job.completed_items += 1
            except Exception as e:
                item.error = str(e)
                item.status = "failed"
                item.duration_ms = (time.time() - start) * 1000
                job.failed_items += 1

        job.status = BatchStatus.RUNNING
        job.started_at = time.time()

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {executor.submit(process_item, item): item for item in job.items}
            for future in as_completed(futures):
                future.result()

        job.completed_at = time.time()
        job.status = BatchStatus.COMPLETED if job.failed_items == 0 else BatchStatus.PARTIAL

        return job

    def batch_mixed(
        self,
        operations: List[Dict[str, Any]],
        search_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        scrape_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        max_concurrent: int = 5,
    ) -> BatchJob:
        """Execute mixed operations (search + scrape) in batch."""
        job_id = str(uuid.uuid4())[:8]
        job = BatchJob(
            job_id=job_id,
            operation="mixed",
            total_items=len(operations),
        )

        for op in operations:
            item = BatchItem(
                item_id=str(uuid.uuid4())[:8],
                query=op.get("query"),
                url=op.get("url"),
            )
            job.items.append(item)

        self._jobs[job_id] = job

        def process_item(item: BatchItem) -> None:
            start = time.time()
            try:
                if item.query and search_fn:
                    result = search_fn(item.query)
                    item.result = result
                    item.status = "completed"
                elif item.url and scrape_fn:
                    result = scrape_fn(item.url)
                    item.result = result
                    item.status = "completed"
                else:
                    item.error = "No function provided for operation"
                    item.status = "failed"
                
                item.duration_ms = (time.time() - start) * 1000
                if item.status == "completed":
                    job.completed_items += 1
                else:
                    job.failed_items += 1
            except Exception as e:
                item.error = str(e)
                item.status = "failed"
                item.duration_ms = (time.time() - start) * 1000
                job.failed_items += 1

        job.status = BatchStatus.RUNNING
        job.started_at = time.time()

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {executor.submit(process_item, item): item for item in job.items}
            for future in as_completed(futures):
                future.result()

        job.completed_at = time.time()
        job.status = BatchStatus.COMPLETED if job.failed_items == 0 else BatchStatus.PARTIAL

        return job

    def get_job(self, job_id: str) -> Optional[BatchJob]:
        """Get a batch job by ID."""
        return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> List[BatchJob]:
        """List recent batch jobs."""
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def get_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Get results from a batch job."""
        job = self._jobs.get(job_id)
        if not job:
            return []
        
        return [
            {
                "item_id": item.item_id,
                "query": item.query,
                "url": item.url,
                "status": item.status,
                "result": item.result,
                "error": item.error,
                "duration_ms": item.duration_ms,
            }
            for item in job.items
            if item.status == "completed"
        ]

    def get_summary(self, job_id: str) -> Dict[str, Any]:
        """Get summary of a batch job."""
        job = self._jobs.get(job_id)
        if not job:
            return {"error": "Job not found"}
        
        total_duration = 0
        if job.completed_at and job.started_at:
            total_duration = (job.completed_at - job.started_at) * 1000
        
        return {
            "job_id": job.job_id,
            "operation": job.operation,
            "status": job.status.value,
            "total_items": job.total_items,
            "completed_items": job.completed_items,
            "failed_items": job.failed_items,
            "progress": f"{job.progress:.1f}%",
            "total_duration_ms": round(total_duration, 1),
            "avg_item_duration_ms": round(
                sum(i.duration_ms for i in job.items) / max(len(job.items), 1), 1
            ),
        }


# Global instance
_batch_processor = BatchProcessor()


def get_batch_processor() -> BatchProcessor:
    """Get the global batch processor."""
    return _batch_processor
