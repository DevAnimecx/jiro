"""Prometheus-compatible metrics collection for Jiro.

Provides:
- Counter, Histogram, Gauge, Summary metric types
- Auto-generated /metrics endpoint output (OpenMetrics format)
- Per-endpoint request counters, latency histograms
- Cache hit/miss, rate limit, error counters
- Engine-specific metrics
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _sanitize_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_:]", "_", name)


class Counter:
    def __init__(self, name: str, help_text: str = "", labels: Optional[List[str]] = None):
        self.name = _sanitize_name(name)
        self.help_text = help_text
        self.label_names = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, **label_values: str):
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            self._values[key] += value

    def _render(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}"]
        lines.append(f"# TYPE {self.name} counter")
        with self._lock:
            for key, val in self._values.items():
                if self.label_names:
                    labels = ",".join(f'{n}="{v}"' for n, v in zip(self.label_names, key))
                    lines.append(f"{self.name}{{{labels}}} {val}")
                else:
                    lines.append(f"{self.name} {val}")
        return "\n".join(lines)


class Histogram:
    BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))

    def __init__(self, name: str, help_text: str = "", labels: Optional[List[str]] = None,
                 buckets: Optional[tuple] = None):
        self.name = _sanitize_name(name)
        self.help_text = help_text
        self.label_names = labels or []
        self.buckets = buckets or self.BUCKETS
        self._count: Dict[tuple, float] = defaultdict(float)
        self._sum: Dict[tuple, float] = defaultdict(float)
        self._buckets: Dict[tuple, Dict[float, float]] = defaultdict(lambda: defaultdict(float))
        self._lock = threading.Lock()

    def observe(self, value: float, **label_values: str):
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            self._count[key] += 1
            self._sum[key] += value
            for b in self.buckets:
                if value <= b:
                    self._buckets[key][b] += 1

    def _render(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}"]
        lines.append(f"# TYPE {self.name} histogram")
        with self._lock:
            for key in self._count:
                label_suffix = ""
                if self.label_names:
                    labels = ",".join(f'{n}="{v}"' for n, v in zip(self.label_names, key))
                    label_suffix = "{" + labels + "}"
                c = self._count[key]
                s = self._sum[key]
                lines.append(f"{self.name}_count{label_suffix} {int(c)}")
                lines.append(f"{self.name}_sum{label_suffix} {s}")
                for b in self.buckets:
                    le = "+Inf" if b == float("inf") else str(b)
                    if self.label_names:
                        labels = ",".join(f'{n}="{v}"' for n, v in zip(self.label_names, key))
                        bucket_line = f'{self.name}_bucket{{le="{le}",{labels}}} {self._buckets[key][b]}'
                    else:
                        bucket_line = f'{self.name}_bucket{{le="{le}"}} {self._buckets[key][b]}'
                    lines.append(bucket_line)
        return "\n".join(lines)


class Gauge:
    def __init__(self, name: str, help_text: str = "", labels: Optional[List[str]] = None):
        self.name = _sanitize_name(name)
        self.help_text = help_text
        self.label_names = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()

    def set(self, value: float, **label_values: str):
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            self._values[key] = value

    def inc(self, value: float = 1.0, **label_values: str):
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            self._values[key] += value

    def dec(self, value: float = 1.0, **label_values: str):
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            self._values[key] -= value

    def _render(self) -> str:
        lines = [f"# HELP {self.name} {self.help_text}"]
        lines.append(f"# TYPE {self.name} gauge")
        with self._lock:
            for key, val in self._values.items():
                if self.label_names:
                    labels = ",".join(f'{n}="{v}"' for n, v in zip(self.label_names, key))
                    lines.append(f"{self.name}{{{labels}}} {val}")
                else:
                    lines.append(f"{self.name} {val}")
        return "\n".join(lines)


class MetricsCollector:
    def __init__(self):
        self._counters: Dict[str, Counter] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._info: Dict[str, str] = {}
        self._start_time = time.time()

    def counter(self, name: str, help_text: str = "", labels: Optional[List[str]] = None) -> Counter:
        if name not in self._counters:
            self._counters[name] = Counter(name, help_text, labels)
        return self._counters[name]

    def histogram(self, name: str, help_text: str = "", labels: Optional[List[str]] = None,
                  buckets: Optional[tuple] = None) -> Histogram:
        if name not in self._histograms:
            self._histograms[name] = Histogram(name, help_text, labels, buckets)
        return self._histograms[name]

    def gauge(self, name: str, help_text: str = "", labels: Optional[List[str]] = None) -> Gauge:
        if name not in self._gauges:
            self._gauges[name] = Gauge(name, help_text, labels)
        return self._gauges[name]

    def info(self, name: str, value: str):
        self._info[name] = value

    def render(self) -> str:
        lines = [
            f"# jiro_build_info 1",
            f"jiro_build_info{{version=\"{self._info.get('version', 'unknown')}\"}} 1",
            f"jiro_process_uptime_seconds {time.time() - self._start_time:.2f}",
            "",
        ]
        for c in self._counters.values():
            lines.append(c._render())
            lines.append("")
        for h in self._histograms.values():
            lines.append(h._render())
            lines.append("")
        for g in self._gauges.values():
            lines.append(g._render())
            lines.append("")
        return "\n".join(lines)


_collector: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector


def reset_metrics():
    global _collector
    _collector = MetricsCollector()
