"""Phase 6: Analytics & Observability — tests for analytics engine, alerts,
audit logging, and analytics endpoints.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiro.alerts import AlertManager, AlertSeverity, AlertType
from jiro.analytics import AnalyticsEngine
from jiro.audit import AuditEntry, AuditLogger


# ── Fixtures ────────────────────────────────────────────────────────────

def _make_mock_db():
    """Create a mock Database with realistic return values."""
    db = AsyncMock()
    db.usage_summary.return_value = {
        "requests": 100,
        "tokens_in": 5000,
        "tokens_out": 3000,
        "cached": 40,
        "by_endpoint": [
            {"endpoint": "/search", "n": 60},
            {"endpoint": "/scrape", "n": 30},
        ],
    }
    db.fetchall.return_value = [
        {"engine": "google", "n": 50, "avg_latency": 800, "min_latency": 200,
         "max_latency": 2000, "errors": 5, "cached": 20},
        {"engine": "bing", "n": 30, "avg_latency": 600, "min_latency": 150,
         "max_latency": 1500, "errors": 2, "cached": 15},
        {"engine": "brave", "n": 20, "avg_latency": 700, "min_latency": 180,
         "max_latency": 1800, "errors": 1, "cached": 5},
    ]
    db.fetchone.return_value = {"status": 200, "n": 80}
    return db


# ── Analytics Engine ─────────────────────────────────────────────────────

class TestAnalyticsEngine:
    @pytest.mark.asyncio
    async def test_overview(self):
        db = _make_mock_db()
        engine = AnalyticsEngine(db)
        result = await engine.overview(since=time.time() - 86400)
        assert result["total_requests"] == 100
        assert result["tokens_in"] == 5000
        assert result["cached_requests"] == 40
        assert result["cache_hit_ratio"] == 0.4

    @pytest.mark.asyncio
    async def test_engine_metrics(self):
        db = _make_mock_db()
        engine = AnalyticsEngine(db)
        result = await engine.engine_metrics(since=time.time() - 86400)
        assert result["count"] == 3
        assert "google" in result["engines"]
        assert result["engines"]["google"]["requests"] == 50
        assert result["engines"]["google"]["error_rate"] == 0.1

    @pytest.mark.asyncio
    async def test_latency_percentiles_empty(self):
        db = _make_mock_db()
        db.fetchall.return_value = []
        engine = AnalyticsEngine(db)
        result = await engine.latency_percentiles(since=time.time() - 86400)
        assert result["p50"] == 0
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_latency_percentiles(self):
        db = _make_mock_db()
        db.fetchall.return_value = [
            {"latency_ms": 100}, {"latency_ms": 200}, {"latency_ms": 300},
            {"latency_ms": 400}, {"latency_ms": 500},
        ]
        engine = AnalyticsEngine(db)
        result = await engine.latency_percentiles(since=time.time() - 86400)
        assert result["count"] == 5
        assert result["min"] == 100
        assert result["max"] == 500
        assert result["avg"] == 300

    @pytest.mark.asyncio
    async def test_error_rates(self):
        db = _make_mock_db()
        db.fetchall.side_effect = [
            [{"status": 200, "n": 80}, {"status": 404, "n": 10}, {"status": 500, "n": 5}],
            [{"engine": "google", "n": 50, "errors": 5},
             {"engine": "bing", "n": 30, "errors": 2}],
        ]
        engine = AnalyticsEngine(db)
        result = await engine.error_rates(since=time.time() - 86400)
        assert result["total_requests"] == 95
        assert result["total_errors"] == 15

    @pytest.mark.asyncio
    async def test_usage_patterns(self):
        db = _make_mock_db()
        db.fetchall.side_effect = [
            [{"query": "python tutorial", "n": 10},
             {"query": "javascript guide", "n": 5}],
            [{"hour_bucket": 10, "n": 20}, {"hour_bucket": 14, "n": 30}],
            [{"engine": "google", "n": 50}, {"engine": "bing", "n": 30}],
        ]
        engine = AnalyticsEngine(db)
        result = await engine.usage_patterns(since=time.time() - 86400)
        assert len(result["top_queries"]) == 2
        assert len(result["peak_hours"]) == 2

    @pytest.mark.asyncio
    async def test_dashboard(self):
        db = _make_mock_db()
        # dashboard calls engine_metrics, latency_percentiles, error_rates, usage_patterns
        # Each calls fetchall with different expected keys
        db.fetchall.side_effect = [
            # engine_metrics
            [{"engine": "google", "n": 50, "avg_latency": 800,
              "min_latency": 200, "max_latency": 2000,
              "errors": 5, "cached": 20}],
            # latency_percentiles
            [{"latency_ms": 100}, {"latency_ms": 200}, {"latency_ms": 300}],
            # error_rates by_status
            [{"status": 200, "n": 80}, {"status": 500, "n": 5}],
            # error_rates by_engine
            [{"engine": "google", "n": 50, "errors": 5}],
            # usage_patterns top_queries
            [{"query": "test", "n": 10}],
            # usage_patterns hourly
            [{"hour_bucket": 10, "n": 20}],
            # usage_patterns engine_popularity
            [{"engine": "google", "n": 50}],
        ]
        engine = AnalyticsEngine(db)
        result = await engine.dashboard(since=time.time() - 86400)
        assert "overview" in result
        assert "engines" in result
        assert "latency" in result
        assert "errors" in result
        assert "patterns" in result


# ── Alerts ───────────────────────────────────────────────────────────────

class TestAlertManager:
    def test_init_defaults(self):
        am = AlertManager()
        assert am.thresholds["latency_p95_ms"] == 5000.0
        assert am.thresholds["error_rate"] == 0.3

    def test_fire_alert(self):
        am = AlertManager()
        alert = am.fire(AlertType.ENGINE_DOWN, "Engine google is down",
                        severity=AlertSeverity.CRITICAL, engine="google")
        assert alert is not None
        assert alert.type == AlertType.ENGINE_DOWN
        assert alert.severity == AlertSeverity.CRITICAL
        assert alert.engine == "google"

    def test_fire_deduplication(self):
        am = AlertManager()
        a1 = am.fire(AlertType.ENGINE_DOWN, "down", engine="google")
        a2 = am.fire(AlertType.ENGINE_DOWN, "down", engine="google")
        assert a1 is not None
        assert a2 is None  # suppressed

    def test_check_engine_down(self):
        am = AlertManager()
        alert = am.check_engine_down("google", is_open=True, failures=3)
        assert alert is not None
        assert alert.engine == "google"

    def test_check_engine_down_not_open(self):
        am = AlertManager()
        alert = am.check_engine_down("google", is_open=False)
        assert alert is None

    def test_check_latency_spike(self):
        am = AlertManager()
        alert = am.check_latency_spike("google", p95_ms=6000)
        assert alert is not None
        assert alert.value == 6000

    def test_check_latency_normal(self):
        am = AlertManager()
        alert = am.check_latency_spike("google", p95_ms=2000)
        assert alert is None

    def test_check_error_rate_high(self):
        am = AlertManager()
        alert = am.check_error_rate("google", error_rate=0.5)
        assert alert is not None

    def test_check_error_rate_normal(self):
        am = AlertManager()
        alert = am.check_error_rate("google", error_rate=0.1)
        assert alert is None

    def test_check_cache_degraded(self):
        am = AlertManager()
        alert = am.check_cache_degraded(0.05)
        assert alert is not None

    def test_list_alerts(self):
        am = AlertManager()
        am.fire(AlertType.ENGINE_DOWN, "down1", engine="google")
        am.fire(AlertType.LATENCY_SPIKE, "slow", engine="bing")
        items = am.list_alerts()
        assert len(items) == 2

    def test_list_alerts_filter_severity(self):
        am = AlertManager()
        am.fire(AlertType.ENGINE_DOWN, "down", severity=AlertSeverity.CRITICAL)
        am.fire(AlertType.LATENCY_SPIKE, "slow", severity=AlertSeverity.WARNING)
        items = am.list_alerts(severity="critical")
        assert len(items) == 1

    def test_acknowledge(self):
        am = AlertManager()
        alert = am.fire(AlertType.ENGINE_DOWN, "down", engine="google")
        ok = am.acknowledge(alert.id)
        assert ok is True
        items = am.list_alerts(unacknowledged_only=True)
        assert len(items) == 0

    def test_summary(self):
        am = AlertManager()
        am.fire(AlertType.ENGINE_DOWN, "down", engine="google")
        am.fire(AlertType.LATENCY_SPIKE, "slow", engine="bing")
        s = am.summary()
        assert s["total"] == 2
        assert s["by_severity"]["warning"] == 2

    def test_clear(self):
        am = AlertManager()
        am.fire(AlertType.ENGINE_DOWN, "down")
        n = am.clear()
        assert n == 1
        assert am.summary()["total"] == 0


# ── Audit Logger ─────────────────────────────────────────────────────────

class TestAuditLogger:
    def test_log_entry(self):
        al = AuditLogger()
        entry = AuditEntry(method="GET", path="/search", status_code=200,
                           latency_ms=50.0, client_ip="127.0.0.1")
        al.log(entry)
        assert al._total_requests == 1

    def test_log_error(self):
        al = AuditLogger()
        entry = AuditEntry(method="GET", path="/search", status_code=500,
                           latency_ms=100.0, client_ip="127.0.0.1", error="test")
        al.log(entry)
        assert al._total_errors == 1

    def test_recent(self):
        al = AuditLogger()
        for i in range(5):
            al.log(AuditEntry(method="GET", path=f"/search?q={i}",
                              status_code=200, latency_ms=50.0))
        items = al.recent(limit=3)
        assert len(items) == 3

    def test_recent_filter_method(self):
        al = AuditLogger()
        al.log(AuditEntry(method="GET", path="/search", status_code=200, latency_ms=50))
        al.log(AuditEntry(method="POST", path="/scrape", status_code=200, latency_ms=50))
        items = al.recent(method="POST")
        assert len(items) == 1
        assert items[0]["method"] == "POST"

    def test_recent_filter_status(self):
        al = AuditLogger()
        al.log(AuditEntry(method="GET", path="/search", status_code=200, latency_ms=50))
        al.log(AuditEntry(method="GET", path="/search", status_code=500, latency_ms=50))
        items = al.recent(status_min=400)
        assert len(items) == 1
        assert items[0]["status_code"] == 500

    def test_by_key(self):
        al = AuditLogger()
        al.log(AuditEntry(method="GET", path="/search", status_code=200,
                           latency_ms=50, key_id="key_123"))
        al.log(AuditEntry(method="GET", path="/search", status_code=200,
                           latency_ms=50, key_id="key_456"))
        items = al.by_key("key_123")
        assert len(items) == 1

    def test_by_ip(self):
        al = AuditLogger()
        al.log(AuditEntry(method="GET", path="/search", status_code=200,
                           latency_ms=50, client_ip="1.2.3.4"))
        al.log(AuditEntry(method="GET", path="/search", status_code=200,
                           latency_ms=50, client_ip="5.6.7.8"))
        items = al.by_ip("1.2.3.4")
        assert len(items) == 1

    def test_summary(self):
        al = AuditLogger()
        al.log(AuditEntry(method="GET", path="/search", status_code=200,
                           latency_ms=50, key_id="k1", client_ip="1.1.1.1"))
        al.log(AuditEntry(method="GET", path="/search", status_code=500,
                           latency_ms=50, key_id="k1", client_ip="1.1.1.1"))
        s = al.summary()
        assert s["total_requests"] == 2
        assert s["total_errors"] == 1
        assert s["unique_keys"] == 1
        assert s["unique_ips"] == 1

    def test_max_entries_trim(self):
        al = AuditLogger(max_entries=5)
        for i in range(10):
            al.log(AuditEntry(method="GET", path=f"/{i}", status_code=200,
                               latency_ms=50))
        assert len(al._entries) == 5

    def test_clear(self):
        al = AuditLogger()
        al.log(AuditEntry(method="GET", path="/", status_code=200, latency_ms=50))
        n = al.clear()
        assert n == 1
        assert len(al._entries) == 0

    def test_entry_to_dict(self):
        entry = AuditEntry(method="GET", path="/search", status_code=200,
                           latency_ms=50.0, client_ip="127.0.0.1",
                           key_id="k1", user_agent="test-agent",
                           request_size=100, response_size=500)
        d = entry.to_dict()
        assert d["method"] == "GET"
        assert d["path"] == "/search"
        assert d["status_code"] == 200
        assert d["client_ip"] == "127.0.0.1"
        assert d["key_id"] == "k1"
        assert d["request_size"] == 100
        assert d["response_size"] == 500
