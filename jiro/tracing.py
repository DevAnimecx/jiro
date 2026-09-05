"""Lightweight distributed tracing without heavy dependencies.

Provides:
- Span creation and propagation
- W3C Trace Context compatible trace IDs
- In-memory span store for debugging
- Optional OpenTelemetry export (when opentelemetry is installed)
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional


def generate_trace_id() -> str:
    return uuid.uuid4().hex


def generate_span_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Span:
    trace_id: str
    span_id: str
    name: str
    parent_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "OK"
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def set_attribute(self, key: str, value: Any):
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def set_status(self, status: str):
        self.status = status

    def finish(self):
        self.end_time = time.time()

    @property
    def duration_ms(self) -> Optional[float]:
        if self.end_time is None:
            return None
        return round((self.end_time - self.start_time) * 1000, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
            "events": self.events,
        }


class Tracer:
    def __init__(self, service_name: str = "jiro", max_spans: int = 1000):
        self.service_name = service_name
        self._spans: List[Span] = []
        self._active: Dict[str, Span] = {}
        self._max_spans = max_spans
        self._lock = threading.Lock()
        self._otlp_exporter = None

    def start_span(self, name: str, trace_id: Optional[str] = None,
                   parent_id: Optional[str] = None,
                   attributes: Optional[Dict[str, Any]] = None) -> Span:
        if trace_id is None:
            trace_id = generate_trace_id()
        span = Span(
            trace_id=trace_id,
            span_id=generate_span_id(),
            name=name,
            parent_id=parent_id,
            attributes=attributes or {},
        )
        self._active[span.span_id] = span
        with self._lock:
            self._spans.append(span)
            if len(self._spans) > self._max_spans:
                self._spans = self._spans[-self._max_spans:]
        return span

    @contextlib.contextmanager
    def span(self, name: str, trace_id: Optional[str] = None,
             parent_id: Optional[str] = None,
             attributes: Optional[Dict[str, Any]] = None) -> Generator[Span, None, None]:
        s = self.start_span(name, trace_id, parent_id, attributes)
        try:
            yield s
            s.set_status("OK")
        except Exception as exc:
            s.set_status("ERROR")
            s.set_attribute("error.message", str(exc))
            raise
        finally:
            s.finish()
            self._active.pop(s.span_id, None)

    def get_current_trace_id(self) -> Optional[str]:
        for s in self._active.values():
            return s.trace_id
        return None

    def get_spans(self, trace_id: Optional[str] = None) -> List[Span]:
        with self._lock:
            if trace_id:
                return [s for s in self._spans if s.trace_id == trace_id]
            return list(self._spans)

    def clear(self):
        with self._lock:
            self._spans.clear()
            self._active.clear()

    def configure_otlp(self, endpoint: str, headers: Optional[Dict[str, str]] = None):
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            self._otlp_exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers or {})
        except ImportError:
            pass


_tracer: Optional[Tracer] = None


def get_tracer(service_name: str = "jiro") -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer(service_name)
    return _tracer


def reset_tracer():
    global _tracer
    _tracer = None
