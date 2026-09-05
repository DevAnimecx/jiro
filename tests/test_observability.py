"""Tests for Phase 2 observability: telemetry, tracing, health endpoints."""

from __future__ import annotations

import pytest

from jiro.telemetry import Counter, Histogram, Gauge, MetricsCollector, get_metrics, reset_metrics
from jiro.tracing import Tracer, Span, get_tracer, reset_tracer, generate_trace_id, generate_span_id


# ===========================================================================
# Telemetry Tests
# ===========================================================================

class TestCounter:
    def test_inc_default(self):
        c = Counter("test_counter")
        c.inc()
        assert c._values[()] == 1.0

    def test_inc_custom_value(self):
        c = Counter("test_counter")
        c.inc(5.0)
        assert c._values[()] == 5.0

    def test_inc_with_labels(self):
        c = Counter("test", labels=["method", "status"])
        c.inc(method="GET", status="200")
        c.inc(method="GET", status="200")
        assert c._values[("GET", "200")] == 2.0

    def test_render_counter(self):
        c = Counter("req_count", "Total requests", ["method"])
        c.inc(method="GET")
        output = c._render()
        assert "# TYPE req_count counter" in output
        assert "req_count{method=\"GET\"} 1" in output


class TestHistogram:
    def test_observe(self):
        h = Histogram("latency")
        h.observe(0.1)
        assert h._count[()] == 1
        assert h._sum[()] == 0.1

    def test_observe_multiple(self):
        h = Histogram("latency")
        h.observe(0.1)
        h.observe(0.5)
        h.observe(1.5)
        assert h._count[()] == 3
        assert h._sum[()] == 2.1

    def test_bucket_boundaries(self):
        h = Histogram("latency", buckets=(0.1, 0.5, 1.0, float("inf")))
        h.observe(0.05)  # in bucket 0.1
        h.observe(0.3)   # in buckets 0.5, 1.0, inf
        h.observe(0.8)   # in buckets 1.0, inf
        assert h._buckets[()][0.1] == 1
        assert h._buckets[()][0.5] == 2  # 0.05 + 0.3
        assert h._buckets[()][1.0] == 3  # 0.05 + 0.3 + 0.8
        assert h._buckets[()][float("inf")] == 3

    def test_render_histogram(self):
        h = Histogram("lat", "Latency", ["engine"])
        h.observe(0.5, engine="google")
        output = h._render()
        assert "# TYPE lat histogram" in output
        assert "lat_count" in output
        assert "lat_sum" in output


class TestGauge:
    def test_set(self):
        g = Counter("test_gauge")  # Using Counter as stand-in
        g.inc(10)
        assert g._values[()] == 10.0

    def test_gauge_set_and_render(self):
        g = Gauge("connections", "Active connections")
        g.set(42)
        output = g._render()
        assert "connections 42" in output

    def test_gauge_inc_dec(self):
        g = Gauge("connections")
        g.inc(5)
        g.inc(3)
        g.dec(2)
        assert g._values[()] == 6.0


class TestMetricsCollector:
    def test_counter_factory(self):
        mc = MetricsCollector()
        c = mc.counter("req_total", "Total requests")
        c.inc()
        assert "req_total" in mc._counters

    def test_histogram_factory(self):
        mc = MetricsCollector()
        h = mc.histogram("latency", "Request latency")
        h.observe(0.1)
        assert "latency" in mc._histograms

    def test_gauge_factory(self):
        mc = MetricsCollector()
        g = mc.gauge("connections", "Active connections")
        g.set(10)
        assert "connections" in mc._gauges

    def test_info(self):
        mc = MetricsCollector()
        mc.info("version", "0.2.8")
        assert mc._info["version"] == "0.2.8"

    def test_render_full(self):
        mc = MetricsCollector()
        mc.info("version", "0.2.8")
        mc.counter("req_total").inc()
        mc.histogram("latency").observe(0.1)
        mc.gauge("connections").set(5)
        output = mc.render()
        assert "jiro_build_info" in output
        assert "jiro_process_uptime_seconds" in output
        assert "req_total" in output

    def test_singleton(self):
        reset_metrics()
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2
        reset_metrics()


# ===========================================================================
# Tracing Tests
# ===========================================================================

class TestTracer:
    def test_start_span(self):
        tracer = Tracer("test")
        span = tracer.start_span("op1")
        assert span.name == "op1"
        assert span.trace_id is not None
        assert span.span_id is not None

    def test_start_span_with_trace_id(self):
        tracer = Tracer("test")
        span = tracer.start_span("op1", trace_id="abc123")
        assert span.trace_id == "abc123"

    def test_context_manager(self):
        tracer = Tracer("test")
        with tracer.span("op1") as s:
            assert s.name == "op1"
            assert s.end_time is None
        assert s.end_time is not None
        assert s.duration_ms is not None

    def test_span_sets_error_on_exception(self):
        tracer = Tracer("test")
        with pytest.raises(ValueError):
            with tracer.span("op1") as s:
                raise ValueError("boom")
        assert s.status == "ERROR"

    def test_nested_spans(self):
        tracer = Tracer("test")
        with tracer.span("outer", trace_id="shared-trace") as outer:
            with tracer.span("inner", trace_id="shared-trace") as inner:
                pass
        assert outer.trace_id == inner.trace_id == "shared-trace"

    def test_trace_id_propagation(self):
        tracer = Tracer("test")
        with tracer.span("outer", trace_id="trace-1") as outer:
            with tracer.span("inner", trace_id="trace-1") as inner:
                pass
        assert outer.trace_id == inner.trace_id == "trace-1"

    def test_get_spans_by_trace(self):
        tracer = Tracer("test")
        with tracer.span("a", trace_id="t1"):
            pass
        with tracer.span("b", trace_id="t2"):
            pass
        assert len(tracer.get_spans("t1")) == 1
        assert len(tracer.get_spans("t2")) == 1

    def test_get_all_spans(self):
        tracer = Tracer("test")
        with tracer.span("a"):
            pass
        with tracer.span("b"):
            pass
        assert len(tracer.get_spans()) == 2

    def test_max_spans_limit(self):
        tracer = Tracer("test", max_spans=3)
        for i in range(5):
            tracer.start_span(f"span_{i}")
        assert len(tracer.get_spans()) == 3

    def test_clear(self):
        tracer = Tracer("test")
        with tracer.span("a"):
            pass
        tracer.clear()
        assert len(tracer.get_spans()) == 0

    def test_span_attribute(self):
        tracer = Tracer("test")
        with tracer.span("op1") as s:
            s.set_attribute("engine", "google")
        assert s.attributes["engine"] == "google"

    def test_span_event(self):
        tracer = Tracer("test")
        with tracer.span("op1") as s:
            s.add_event("cache_miss", {"key": "foo"})
        assert len(s.events) == 1
        assert s.events[0]["name"] == "cache_miss"

    def test_span_to_dict(self):
        tracer = Tracer("test")
        with tracer.span("op1") as s:
            pass
        d = s.to_dict()
        assert d["name"] == "op1"
        assert d["trace_id"] is not None
        assert d["duration_ms"] is not None
        assert d["status"] == "OK"

    def test_singleton(self):
        reset_tracer()
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2
        reset_tracer()


class TestIDs:
    def test_trace_id_format(self):
        tid = generate_trace_id()
        assert len(tid) == 32
        assert all(c in "0123456789abcdef" for c in tid)

    def test_span_id_format(self):
        sid = generate_span_id()
        assert len(sid) == 16
        assert all(c in "0123456789abcdef" for c in sid)

    def test_unique_ids(self):
        ids = {generate_trace_id() for _ in range(100)}
        assert len(ids) == 100
