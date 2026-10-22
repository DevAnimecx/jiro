"""Bridge from the runtime to Jiro's existing instrumentation.

The runtime must not import a metrics implementation directly — it takes a
single callable sink. This module adapts that sink to
:mod:`jiro.telemetry`, the hand-rolled Prometheus collector the project already
ships, so no new dependency is introduced.

Cardinality
-----------
Label values are drawn from a closed set of task states and fixed metric names.
Task ids, inputs and error text are never used as labels; a single unbounded
label (``request.url.path``) in the HTTP middleware is already enough of a
problem without the runtime adding more.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from jiro.log import get_logger
from jiro.runtime.types import TaskState

log = get_logger("jiro.runtime.metrics")

#: Metric names registered on first use. Fixed set, so cardinality is bounded.
_COUNTER = "counter"
_HISTOGRAM = "histogram"

_STATE_LABEL = "state"
_RESULT_LABEL = "result"


def make_metrics_sink() -> Callable[[str, str, Dict[str, float]], None]:
    """Build the sink handed to :class:`~jiro.runtime.runtime.TaskRuntime`.

    The returned callable matches the runtime's ``(name, kind, values)``
    signature. It never raises: a metrics failure must never affect execution.
    """

    def sink(name: str, kind: str, values: Dict[str, float]) -> None:
        try:
            metrics = get_metrics()
            value = float(values.get("value", 1.0))
            if kind == _HISTOGRAM:
                metrics.histogram(name, f"Runtime histogram {name}", [_STATE_LABEL]).observe(
                    value, state=str(values.get("state", "unknown"))
                )
            else:
                metrics.counter(name, f"Runtime counter {name}", [_RESULT_LABEL]).inc(
                    value=value, result=str(values.get("result", "")) or "all"
                )
        except Exception:  # pragma: no cover - instrumentation must never break work
            log.debug("metrics sink failed for %s", name, exc_info=True)

    return sink


def get_metrics() -> Any:
    from jiro.telemetry import get_metrics as _get

    return _get()


def publish_queue_depth(depth: Dict[str, int]) -> None:
    """Expose queue depth as a gauge per state.

    States not present in ``depth`` are published as zero so a series does not
    silently disappear once its queue drains.
    """
    try:
        metrics = get_metrics()
        for state in TaskState:
            metrics.gauge(
                "jiro_runtime_queue_depth",
                "Tasks currently in each lifecycle state",
                [_STATE_LABEL],
            ).set(float(depth.get(state.value, 0)), state=state.value)
    except Exception:  # pragma: no cover
        log.debug("failed to publish queue depth", exc_info=True)


def publish_worker_state(active: int, total: int) -> None:
    """Expose pool utilisation."""
    try:
        metrics = get_metrics()
        metrics.gauge("jiro_runtime_workers_active", "Attempts executing now").set(float(active))
        metrics.gauge("jiro_runtime_workers_total", "Configured worker slots").set(float(total))
    except Exception:  # pragma: no cover
        log.debug("failed to publish worker state", exc_info=True)


#: Metric names this module is responsible for, exposed so a test can assert
#: they are actually registered rather than silently skipped.
RUNTIME_METRIC_NAMES: List[str] = [
    "jiro_runtime_tasks_submitted",
    "jiro_runtime_tasks_succeeded",
    "jiro_runtime_tasks_failed",
    "jiro_runtime_tasks_cancelled",
    "jiro_runtime_task_retries",
    "jiro_runtime_task_timeouts",
    "jiro_runtime_worker_errors",
    "jiro_runtime_recovery_events",
    "jiro_runtime_cancellations_requested",
    "jiro_runtime_queue_depth",
    "jiro_runtime_workers_active",
    "jiro_runtime_workers_total",
    "jiro_runtime_task_duration_seconds",
]


__all__ = [
    "RUNTIME_METRIC_NAMES",
    "make_metrics_sink",
    "publish_queue_depth",
    "publish_worker_state",
]
