"""Observability and debugging for agent orchestration.

Records workflow execution events, provides inspection interfaces,
and tracks metrics without logging sensitive content.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Awaitable

from jiro.log import get_logger

log = get_logger("jiro.orchestration.observability")


class EventType(str, Enum):
    """Types of orchestration events."""

    GRAPH_STARTED = "graph_started"
    GRAPH_COMPLETED = "graph_completed"
    GRAPH_FAILED = "graph_failed"
    GRAPH_CANCELLED = "graph_cancelled"
    NODE_QUEUED = "node_queued"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    NODE_RETRIED = "node_retried"
    NODE_CANCELLED = "node_cancelled"
    DELEGATION_SUBMITTED = "delegation_submitted"
    DELEGATION_COMPLETED = "delegation_completed"
    DELEGATION_FAILED = "delegation_failed"
    PERMISSION_DENIED = "permission_denied"
    VALIDATION_FAILED = "validation_failed"
    DISAGREEMENT_DETECTED = "disagreement_detected"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    CHECKPOINT_SAVED = "checkpoint_saved"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETED = "recovery_completed"


@dataclass
class OrchestrationEvent:
    """A single orchestration event for audit trail."""

    event_id: str
    event_type: EventType
    graph_id: str
    execution_id: str
    node_id: Optional[str] = None
    agent_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # No sensitive content in events

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "graph_id": self.graph_id,
            "execution_id": self.execution_id,
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


class EventRecorder:
    """Records orchestration events to an event store."""

    def __init__(self, max_events: int = 10000):
        self._events: deque = deque(maxlen=max_events)
        self._event_handlers: List[Callable[[OrchestrationEvent], Awaitable[None]]] = []

    def register_handler(self, handler: Callable[[OrchestrationEvent], Awaitable[None]]) -> None:
        """Register an async handler for events."""
        self._event_handlers.append(handler)

    async def record(self, event: OrchestrationEvent) -> None:
        """Record an event and notify handlers."""
        self._events.append(event)

        # Notify handlers (fire and forget)
        for handler in self._event_handlers:
            try:
                await handler(event)
            except Exception as e:
                log.warning("Event handler failed: %s", e)

    def get_events(
        self,
        graph_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        node_id: Optional[str] = None,
        event_type: Optional[EventType] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> List[OrchestrationEvent]:
        """Query events with filters."""
        results = []
        for event in reversed(self._events):
            if len(results) >= limit:
                break
            if graph_id and event.graph_id != graph_id:
                continue
            if execution_id and event.execution_id != execution_id:
                continue
            if node_id and event.node_id != node_id:
                continue
            if event_type and event.event_type != event_type:
                continue
            if since and event.timestamp < since:
                continue
            results.append(event)
        return results

    def get_execution_timeline(self, execution_id: str) -> List[OrchestrationEvent]:
        """Get complete timeline for an execution."""
        return self.get_events(execution_id=execution_id, limit=1000)


class MetricsCollector:
    """Collects and aggregates orchestration metrics."""

    def __init__(self):
        self._counters: Dict[str, int] = defaultdict(int)
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._gauges: Dict[str, float] = {}

    def increment(self, name: str, value: int = 1, labels: Optional[Dict[str, str]] = None) -> None:
        key = self._make_key(name, labels)
        self._counters[key] += value

    def observe(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = self._make_key(name, labels)
        self._histograms[key].append(value)
        # Keep only last 1000 observations
        if len(self._histograms[key]) > 1000:
            self._histograms[key] = self._histograms[key][-1000:]

    def gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = self._make_key(name, labels)
        self._gauges[key] = value

    def _make_key(self, name: str, labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def get_counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> int:
        return self._counters.get(self._make_key(name, labels), 0)

    def get_histogram_stats(self, name: str, labels: Optional[Dict[str, str]] = None) -> Dict[str, float]:
        key = self._make_key(name, labels)
        values = self._histograms.get(key, [])
        if not values:
            return {"count": 0, "min": 0, "max": 0, "mean": 0, "p50": 0, "p95": 0, "p99": 0}

        sorted_vals = sorted(values)
        n = len(sorted_vals)
        return {
            "count": n,
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "mean": sum(sorted_vals) / n,
            "p50": sorted_vals[n // 2],
            "p95": sorted_vals[int(n * 0.95)],
            "p99": sorted_vals[int(n * 0.99)],
        }

    def get_gauge(self, name: str, labels: Optional[Dict[str, str]] = None) -> Optional[float]:
        return self._gauges.get(self._make_key(name, labels))

    def get_all_metrics(self) -> Dict[str, Any]:
        return {
            "counters": dict(self._counters),
            "histograms": {
                k: self.get_histogram_stats(k.split("{")[0], {})
                for k in self._histograms
            },
            "gauges": dict(self._gauges),
        }


class OrchestrationInspector:
    """Provides inspection of workflow state for debugging."""

    def __init__(
        self,
        event_recorder: EventRecorder,
        metrics: MetricsCollector,
    ):
        self.events = event_recorder
        self.metrics = metrics

    def get_workflow_summary(self, execution_id: str) -> Dict[str, Any]:
        """Get a summary of a workflow execution."""
        events = self.events.get_execution_timeline(execution_id)

        if not events:
            return {"error": "No events found for execution"}

        node_events = defaultdict(list)
        for e in events:
            if e.node_id:
                node_events[e.node_id].append(e)

        nodes = {}
        for node_id, node_events_list in node_events.items():
            started = [e for e in node_events_list if e.event_type == EventType.NODE_STARTED]
            completed = [e for e in node_events_list if e.event_type == EventType.NODE_COMPLETED]
            failed = [e for e in node_events_list if e.event_type == EventType.NODE_FAILED]

            nodes[node_id] = {
                "agent_id": started[0].agent_id if started else None,
                "started_at": started[0].timestamp if started else None,
                "completed_at": completed[0].timestamp if completed else None,
                "failed_at": failed[0].timestamp if failed else None,
                "duration_ms": completed[0].duration_ms if completed else None,
                "retries": len([e for e in node_events_list if e.event_type == EventType.NODE_RETRIED]),
                "status": self._infer_status(started, completed, failed),
            }

        graph_events = [e for e in events if e.node_id is None]
        graph_started = [e for e in graph_events if e.event_type == EventType.GRAPH_STARTED]
        graph_completed = [e for e in graph_events if e.event_type == EventType.GRAPH_COMPLETED]
        graph_failed = [e for e in graph_events if e.event_type == EventType.GRAPH_FAILED]
        graph_cancelled = [e for e in graph_events if e.event_type == EventType.GRAPH_CANCELLED]

        return {
            "execution_id": execution_id,
            "graph_id": events[0].graph_id,
            "status": self._infer_graph_status(graph_completed, graph_failed, graph_cancelled),
            "started_at": graph_started[0].timestamp if graph_started else None,
            "completed_at": (
                graph_completed[0].timestamp or graph_failed[0].timestamp or graph_cancelled[0].timestamp
            ) if (graph_completed or graph_failed or graph_cancelled) else None,
            "nodes": nodes,
            "total_nodes": len(nodes),
            "completed_nodes": sum(1 for n in nodes.values() if n["status"] == "completed"),
            "failed_nodes": sum(1 for n in nodes.values() if n["status"] == "failed"),
        }

    def _infer_status(
        self,
        started: List[OrchestrationEvent],
        completed: List[OrchestrationEvent],
        failed: List[OrchestrationEvent],
    ) -> str:
        if completed:
            return "completed"
        if failed:
            return "failed"
        if started:
            return "running"
        return "pending"

    def _infer_graph_status(
        self,
        completed: List[OrchestrationEvent],
        failed: List[OrchestrationEvent],
        cancelled: List[OrchestrationEvent],
    ) -> str:
        if completed:
            return "completed"
        if failed:
            return "failed"
        if cancelled:
            return "cancelled"
        return "running"

    def get_agent_performance(self, agent_id: str) -> Dict[str, Any]:
        """Get performance metrics for a specific agent."""
        return {
            "invocations": self.metrics.get_counter("orchestration.node.started", {"agent": agent_id}),
            "completed": self.metrics.get_counter("orchestration.node.completed", {"agent": agent_id}),
            "failed": self.metrics.get_counter("orchestration.node.failed", {"agent": agent_id}),
            "avg_duration_ms": self.metrics.get_histogram_stats(
                "orchestration.node.duration_ms", {"agent": agent_id}
            ).get("mean", 0),
        }

    def get_permission_denials(self, execution_id: Optional[str] = None) -> List[OrchestrationEvent]:
        """Get all permission denial events."""
        return self.events.get_events(
            execution_id=execution_id,
            event_type=EventType.PERMISSION_DENIED,
            limit=100,
        )

    def get_validation_failures(self, execution_id: Optional[str] = None) -> List[OrchestrationEvent]:
        """Get all validation failure events."""
        return self.events.get_events(
            execution_id=execution_id,
            event_type=EventType.VALIDATION_FAILED,
            limit=100,
        )

    def get_disagreements(self, execution_id: Optional[str] = None) -> List[OrchestrationEvent]:
        """Get all detected disagreements."""
        return self.events.get_events(
            execution_id=execution_id,
            event_type=EventType.DISAGREEMENT_DETECTED,
            limit=100,
        )


# Global instances
_event_recorder: Optional[EventRecorder] = None
_metrics_collector: Optional[MetricsCollector] = None
_inspector: Optional[OrchestrationInspector] = None


def get_event_recorder() -> EventRecorder:
    global _event_recorder
    if _event_recorder is None:
        _event_recorder = EventRecorder()
    return _event_recorder


def get_metrics_collector() -> MetricsCollector:
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def get_inspector() -> OrchestrationInspector:
    global _inspector
    if _inspector is None:
        _inspector = OrchestrationInspector(get_event_recorder(), get_metrics_collector())
    return _inspector


async def record_event(
    event_type: EventType,
    graph_id: str,
    execution_id: str,
    node_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    duration_ms: Optional[float] = None,
    **metadata,
) -> None:
    """Convenience function to record an event."""
    import uuid
    event = OrchestrationEvent(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        graph_id=graph_id,
        execution_id=execution_id,
        node_id=node_id,
        agent_id=agent_id,
        duration_ms=duration_ms,
        metadata=metadata,
    )
    await get_event_recorder().record(event)
    # Update metrics
    metrics = get_metrics_collector()
    metrics.increment(f"orchestration.{event_type.value}")
    if duration_ms is not None:
        metrics.observe("orchestration.duration_ms", duration_ms, {"event": event_type.value})
    if node_id and agent_id:
        metrics.increment("orchestration.node.started", labels={"agent": agent_id})
        if event_type == EventType.NODE_COMPLETED:
            metrics.increment("orchestration.node.completed", labels={"agent": agent_id})
        elif event_type == EventType.NODE_FAILED:
            metrics.increment("orchestration.node.failed", labels={"agent": agent_id})


__all__ = [
    "EventRecorder",
    "EventType",
    "MetricsCollector",
    "OrchestrationEvent",
    "OrchestrationInspector",
    "get_event_recorder",
    "get_inspector",
    "get_metrics_collector",
    "record_event",
]