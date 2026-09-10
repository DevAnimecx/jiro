"""Monitoring and observability with metrics, health checks, and request tracing.

Provides:
- Prometheus-compatible metrics endpoint
- Health check endpoints (liveness, readiness)
- Request tracing with correlation IDs
- Performance metrics collection
- Custom metrics registry
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheck:
    """Result of a health check."""
    name: str
    status: HealthStatus
    message: str = ""
    latency_ms: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Metric:
    """A single metric data point."""
    name: str
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metric_type: str = "gauge"  # gauge, counter, histogram


class MetricsRegistry:
    """Registry for collecting and querying metrics."""

    def __init__(self) -> None:
        self._metrics: Dict[str, List[Metric]] = defaultdict(list)
        self._counters: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._gauges: Dict[str, float] = {}

    def record(self, metric: Metric) -> None:
        """Record a metric."""
        self._metrics[metric.name].append(metric)
        
        if metric.metric_type == "counter":
            self._counters[metric.name] += metric.value
        elif metric.metric_type == "histogram":
            self._histograms[metric.name].append(metric.value)
        elif metric.metric_type == "gauge":
            self._gauges[metric.name] = metric.value

    def increment(self, name: str, value: float = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """Increment a counter."""
        self._counters[name] += value
        self.record(Metric(name=name, value=value, labels=labels or {}, metric_type="counter"))

    def gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Set a gauge value."""
        self._gauges[name] = value
        self.record(Metric(name=name, value=value, labels=labels or {}, metric_type="gauge"))

    def histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Record a histogram value."""
        self._histograms[name].append(value)
        self.record(Metric(name=name, value=value, labels=labels or {}, metric_type="histogram"))

    def get_counter(self, name: str) -> float:
        """Get counter value."""
        return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        """Get gauge value."""
        return self._gauges.get(name, 0)

    def get_histogram(self, name: str) -> Dict[str, float]:
        """Get histogram statistics."""
        values = self._histograms.get(name, [])
        if not values:
            return {"count": 0, "sum": 0, "avg": 0, "min": 0, "max": 0, "p50": 0, "p95": 0, "p99": 0}
        
        values_sorted = sorted(values)
        count = len(values)
        return {
            "count": count,
            "sum": sum(values),
            "avg": sum(values) / count,
            "min": values_sorted[0],
            "max": values_sorted[-1],
            "p50": values_sorted[count // 2],
            "p95": values_sorted[int(count * 0.95)],
            "p99": values_sorted[int(count * 0.99)],
        }

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []
        
        for name, value in self._counters.items():
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")
        
        for name, value in self._gauges.items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")
        
        for name, values in self._histograms.items():
            stats = self.get_histogram(name)
            lines.append(f"# TYPE {name} histogram")
            lines.append(f"{name}_count {stats['count']}")
            lines.append(f"{name}_sum {stats['sum']}")
            lines.append(f"{name}_avg {stats['avg']}")
        
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Export metrics as dictionary."""
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {name: self.get_histogram(name) for name in self._histograms},
        }


class RequestTracer:
    """Request tracing with correlation IDs."""

    def __init__(self) -> None:
        self._traces: Dict[str, Dict[str, Any]] = {}

    def start_trace(self, operation: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Start a new trace and return correlation ID."""
        correlation_id = str(uuid.uuid4())
        self._traces[correlation_id] = {
            "operation": operation,
            "start_time": time.time(),
            "metadata": metadata or {},
            "spans": [],
        }
        return correlation_id

    def add_span(self, correlation_id: str, name: str, duration_ms: float, 
                 metadata: Optional[Dict[str, Any]] = None) -> None:
        """Add a span to an existing trace."""
        if correlation_id in self._traces:
            self._traces[correlation_id]["spans"].append({
                "name": name,
                "duration_ms": duration_ms,
                "metadata": metadata or {},
            })

    def end_trace(self, correlation_id: str, status: str = "ok") -> Dict[str, Any]:
        """End a trace and return trace summary."""
        if correlation_id not in self._traces:
            return {"error": "trace not found"}
        
        trace = self._traces[correlation_id]
        trace["end_time"] = time.time()
        trace["duration_ms"] = (trace["end_time"] - trace["start_time"]) * 1000
        trace["status"] = status
        
        return trace

    def get_trace(self, correlation_id: str) -> Optional[Dict[str, Any]]:
        """Get trace by correlation ID."""
        return self._traces.get(correlation_id)

    def get_recent_traces(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent traces."""
        traces = list(self._traces.values())
        return sorted(traces, key=lambda t: t.get("start_time", 0), reverse=True)[:limit]


class HealthChecker:
    """Health check manager."""

    def __init__(self) -> None:
        self._checks: Dict[str, Callable[[], HealthCheck]] = {}

    def register(self, name: str, check_fn: Callable[[], HealthCheck]) -> None:
        """Register a health check."""
        self._checks[name] = check_fn

    def check(self, name: Optional[str] = None) -> List[HealthCheck]:
        """Run health checks."""
        if name:
            if name in self._checks:
                return [self._checks[name]()]
            return []
        
        return [check() for check in self._checks.values()]

    def overall_status(self) -> HealthStatus:
        """Get overall health status."""
        results = self.check()
        if not results:
            return HealthStatus.HEALTHY
        
        statuses = [r.status for r in results]
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY


@contextmanager
def trace_operation(tracer: RequestTracer, operation: str, 
                    metadata: Optional[Dict[str, Any]] = None) -> Generator[str, None, None]:
    """Context manager for tracing operations."""
    correlation_id = tracer.start_trace(operation, metadata)
    try:
        yield correlation_id
    except Exception as e:
        tracer.end_trace(correlation_id, status="error")
        raise
    finally:
        pass


# Global instances
_metrics_registry = MetricsRegistry()
_request_tracer = RequestTracer()
_health_checker = HealthChecker()


def get_metrics_registry() -> MetricsRegistry:
    """Get the global metrics registry."""
    return _metrics_registry


def get_request_tracer() -> RequestTracer:
    """Get the global request tracer."""
    return _request_tracer


def get_health_checker() -> HealthChecker:
    """Get the global health checker."""
    return _health_checker
