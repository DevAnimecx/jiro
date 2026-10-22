"""Observability bridge for the memory system.

Mirrors :mod:`jiro.runtime.metrics`: the memory layer takes no dependency on a
metrics implementation, and this adapter publishes to the project's existing
hand-rolled collector in :mod:`jiro.telemetry`.

Cardinality is the discipline here. Label values are drawn from closed sets —
memory type, outcome, degradation reason — never from a title, a query string or
a memory id. An unbounded label turns a memory of user-supplied text into
unbounded time series.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from jiro.log import get_logger

log = get_logger("jiro.memory.metrics")

#: Every metric this module may emit. Exposed so a test can assert the names are
#: actually registered rather than silently skipped.
MEMORY_METRIC_NAMES = (
    "jiro_memory_working_written",
    "jiro_memory_working_evicted",
    "jiro_memory_working_forgotten",
    "jiro_memory_episodic_written",
    "jiro_memory_semantic_written",
    "jiro_memory_procedural_written",
    "jiro_memory_procedures_proposed",
    "jiro_memory_procedures_promoted",
    "jiro_memory_contradictions_detected",
    "jiro_memory_contradictions_resolved",
    "jiro_memory_superseded",
    "jiro_memory_retrievals",
    "jiro_memory_retrieval_degraded",
    "jiro_memory_expired_swept",
    "jiro_memory_retention_purged",
    "jiro_memory_deleted",
    "jiro_memory_source_deleted",
    "jiro_memory_embedded",
    "jiro_memory_embed_failed",
    "jiro_memory_outbox_enqueued",
    "jiro_memory_outbox_drained",
    "jiro_memory_outbox_failed",
    "jiro_memory_write_failures",
    "jiro_memory_store_total",
)


def _metrics() -> Optional[Any]:
    try:
        from jiro.telemetry import get_metrics

        return get_metrics()
    except Exception:  # pragma: no cover - telemetry is optional at runtime
        return None


def counter(name: str, labels: Optional[Dict[str, str]] = None,
            value: float = 1.0) -> None:
    """Increment a counter. Never raises: metrics must not break memory."""
    try:
        metrics = _metrics()
        if metrics is None:
            return
        labels = labels or {}
        metrics.counter(
            name, f"Jiro memory metric {name}", sorted(labels)
        ).inc(value=value, **labels)
    except Exception:  # pragma: no cover
        log.debug("memory metric failed", extra={"metric": name})


def gauge(name: str, value: float,
          labels: Optional[Dict[str, str]] = None) -> None:
    """Set a gauge. Never raises."""
    try:
        metrics = _metrics()
        if metrics is None:
            return
        labels = labels or {}
        metrics.gauge(
            name, f"Jiro memory metric {name}", sorted(labels)
        ).set(value, **labels)
    except Exception:  # pragma: no cover
        log.debug("memory metric failed", extra={"metric": name})


def count(name: str, value: float = 1.0) -> None:
    """Convenience wrapper for a label-free counter."""
    counter(name, None, value)


def publish_store_stats(stats: Dict[str, Any]) -> None:
    """Publish per-type and per-status memory counts.

    ``stats`` keys come from a closed set produced by the repository, so this
    cannot introduce unbounded cardinality.
    """
    by_type = stats.get("by_type") or {}
    for memory_type, total in by_type.items():
        gauge("jiro_memory_store_total", float(total), {"type": str(memory_type)})
    for status, total in (stats.get("by_status") or {}).items():
        gauge("jiro_memory_store_total", float(total), {"type": str(status)})


def publish_embedding_health(health: Dict[str, Any]) -> None:
    """Publish whether the optional vector path is usable."""
    gauge("jiro_memory_store_total",
          1.0 if health.get("enabled") else 0.0, {"type": "embedding_enabled"})


__all__ = [
    "MEMORY_METRIC_NAMES",
    "count",
    "counter",
    "gauge",
    "publish_embedding_health",
    "publish_store_stats",
]