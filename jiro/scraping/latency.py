"""Rolling per-engine latency observations.

The graph planner has to decide how long a search node may take before it
commits to a plan. A fixed constant cannot answer that: real search latency
varies by engine, by network and over the course of a session. This keeps a
bounded window of what each engine has actually been taking so the planner can
budget from measurement instead of from a guess.

Deliberately dependency-free and in-process. It is a heuristic input, never a
correctness input: an empty recorder simply yields no advice, and callers fall
back to their static defaults.
"""

from __future__ import annotations

import statistics
import threading
from collections import deque
from typing import Deque, Dict, List, Optional


class LatencyRecorder:
    """Bounded rolling latency samples, keyed by engine (and optionally op).

    Thread-safe: the orchestrator records from the event loop while the
    synchronous planner may read from a worker thread.
    """

    def __init__(self, window: int = 50) -> None:
        # A failure band is what matters for a timeout, so keep enough samples
        # to see one without letting a stale morning's latency dominate.
        self._window = max(5, window)
        self._samples: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def record(self, key: str, latency_ms: float) -> None:
        """Record one observation. Non-finite and negative values are ignored."""
        if latency_ms != latency_ms or latency_ms < 0:  # NaN or nonsense
            return
        with self._lock:
            bucket = self._samples.get(key)
            if bucket is None:
                bucket = self._samples[key] = deque(maxlen=self._window)
            bucket.append(float(latency_ms))

    def percentile(self, key: str, pct: float,
                   minimum_samples: int = 3) -> Optional[float]:
        """Return the ``pct`` percentile for ``key``, or None if too few samples.

        ``minimum_samples`` exists so a single lucky fast request cannot make
        the planner budget an unrealistically tight timeout.
        """
        with self._lock:
            bucket = self._samples.get(key)
            if bucket is None or len(bucket) < minimum_samples:
                return None
            data = sorted(bucket)

        if len(data) == 1:
            return data[0]
        # Linear interpolation between the two nearest ranks.
        rank = (pct / 100.0) * (len(data) - 1)
        low = int(rank)
        high = min(low + 1, len(data) - 1)
        if low == high:
            return data[low]
        return data[low] + (data[high] - data[low]) * (rank - low)

    def suggest_timeout_ms(self, key: str, *, floor_ms: int, ceiling_ms: int,
                           default_ms: int) -> int:
        """A per-request timeout derived from observed latency.

        Uses a high percentile, not the mean: a timeout must clear the slow
        requests, not the average one. Falls back to ``default_ms`` when there
        is nothing to go on, which keeps cold-start behaviour unchanged.
        """
        p95 = self.percentile(key, 95)
        if p95 is None:
            return default_ms
        # Headroom over the observed tail; the p95 is already a slow request.
        return int(min(ceiling_ms, max(floor_ms, p95 * 1.25)))

    def stats(self, key: str) -> Optional[Dict[str, float]]:
        with self._lock:
            bucket = self._samples.get(key)
            if not bucket:
                return None
            data = list(bucket)
        return {
            "samples": len(data),
            "min_ms": min(data),
            "p50_ms": statistics.median(data),
            "max_ms": max(data),
        }

    def keys(self) -> List[str]:
        with self._lock:
            return sorted(self._samples)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


# Process-wide recorder used by the search orchestrator. A module-level
# singleton is the point: the planner and the orchestrator are constructed
# independently (and the planner may run in a different request), so the
# observations have to outlive either object.
_default_recorder: Optional[LatencyRecorder] = None
_default_lock = threading.Lock()


def default_recorder() -> LatencyRecorder:
    global _default_recorder
    if _default_recorder is None:
        with _default_lock:
            if _default_recorder is None:
                _default_recorder = LatencyRecorder()
    return _default_recorder
