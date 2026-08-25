"""Alerting system — detects anomalies and generates alerts for:

- Engine down (circuit breaker open)
- Latency spikes (p95 > threshold)
- Error rate thresholds
- Cache degradation
- Proxy failures

Alerts are stored in-memory (last 500) and can be queried via API.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from jiro.log import get_logger

log = get_logger("jiro.alerts")


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    ENGINE_DOWN = "engine_down"
    ENGINE_RECOVERED = "engine_recovered"
    LATENCY_SPIKE = "latency_spike"
    ERROR_RATE_HIGH = "error_rate_high"
    CACHE_DEGRADED = "cache_degraded"
    PROXY_FAILURE = "proxy_failure"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"


@dataclass
class Alert:
    id: str
    type: AlertType
    severity: AlertSeverity
    message: str
    engine: str = ""
    value: float = 0.0
    threshold: float = 0.0
    timestamp: float = field(default_factory=time.time)
    acknowledged: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "severity": self.severity.value,
            "message": self.message,
            "engine": self.engine,
            "value": self.value,
            "threshold": self.threshold,
            "timestamp": self.timestamp,
            "acknowledged": self.acknowledged,
            "metadata": self.metadata,
        }


# ── Thresholds ──────────────────────────────────────────────────────────

DEFAULT_THRESHOLDS = {
    "latency_p95_ms": 5000.0,      # alert if p95 > 5s
    "error_rate": 0.3,              # alert if > 30% errors
    "cache_hit_ratio_min": 0.1,     # alert if cache ratio < 10%
    "consecutive_failures": 3,      # alert after 3 consecutive failures
}


class AlertManager:
    """Manages alert detection, storage, and querying."""

    def __init__(self, thresholds: Optional[Dict[str, float]] = None) -> None:
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._alerts: deque[Alert] = deque(maxlen=500)
        self._alert_id_counter = 0
        self._suppress_until: Dict[str, float] = {}  # suppress duplicate alerts
        self._suppress_window = 300.0  # 5 minutes between duplicate alerts

    def _next_id(self) -> str:
        self._alert_id_counter += 1
        return f"alert_{self._alert_id_counter:06d}"

    def _should_suppress(self, alert_key: str) -> bool:
        """Check if this alert was recently fired (deduplication)."""
        until = self._suppress_until.get(alert_key, 0)
        if time.time() < until:
            return True
        self._suppress_until[alert_key] = time.time() + self._suppress_window
        return False

    def fire(self, alert_type: AlertType, message: str, *,
             severity: AlertSeverity = AlertSeverity.WARNING,
             engine: str = "", value: float = 0.0, threshold: float = 0.0,
             metadata: Optional[Dict[str, Any]] = None) -> Optional[Alert]:
        """Fire an alert (with deduplication). Returns the alert or None if suppressed."""
        alert_key = f"{alert_type.value}:{engine}"
        if self._should_suppress(alert_key):
            return None

        alert = Alert(
            id=self._next_id(),
            type=alert_type,
            severity=severity,
            message=message,
            engine=engine,
            value=value,
            threshold=threshold,
            metadata=metadata or {},
        )
        self._alerts.appendleft(alert)
        log.warning("alert fired: %s [%s] %s",
                    alert_type.value, severity.value, message)
        return alert

    # ── Detection Methods ────────────────────────────────────────────────

    def check_engine_down(self, engine: str, is_open: bool,
                          failures: int = 0) -> Optional[Alert]:
        """Fire alert if circuit breaker is open for an engine."""
        if is_open:
            return self.fire(
                AlertType.ENGINE_DOWN,
                f"Engine '{engine}' circuit breaker open ({failures} failures)",
                severity=AlertSeverity.CRITICAL,
                engine=engine,
                value=failures,
                threshold=self.thresholds["consecutive_failures"],
            )
        return None

    def check_engine_recovered(self, engine: str) -> Optional[Alert]:
        """Fire alert when an engine recovers from circuit breaker."""
        return self.fire(
            AlertType.ENGINE_RECOVERED,
            f"Engine '{engine}' recovered",
            severity=AlertSeverity.INFO,
            engine=engine,
        )

    def check_latency_spike(self, engine: str, p95_ms: float) -> Optional[Alert]:
        """Fire alert if p95 latency exceeds threshold."""
        threshold = self.thresholds["latency_p95_ms"]
        if p95_ms > threshold:
            return self.fire(
                AlertType.LATENCY_SPIKE,
                f"Engine '{engine}' p95 latency {p95_ms:.0f}ms > {threshold:.0f}ms",
                severity=AlertSeverity.WARNING,
                engine=engine,
                value=p95_ms,
                threshold=threshold,
            )
        return None

    def check_error_rate(self, engine: str, error_rate: float) -> Optional[Alert]:
        """Fire alert if error rate exceeds threshold."""
        threshold = self.thresholds["error_rate"]
        if error_rate > threshold:
            return self.fire(
                AlertType.ERROR_RATE_HIGH,
                f"Engine '{engine}' error rate {error_rate:.1%} > {threshold:.1%}",
                severity=AlertSeverity.WARNING,
                engine=engine,
                value=error_rate,
                threshold=threshold,
            )
        return None

    def check_cache_degraded(self, hit_ratio: float) -> Optional[Alert]:
        """Fire alert if cache hit ratio drops below threshold."""
        threshold = self.thresholds["cache_hit_ratio_min"]
        if hit_ratio < threshold and hit_ratio > 0:
            return self.fire(
                AlertType.CACHE_DEGRADED,
                f"Cache hit ratio {hit_ratio:.1%} < {threshold:.1%}",
                severity=AlertSeverity.WARNING,
                value=hit_ratio,
                threshold=threshold,
            )
        return None

    def check_proxy_failure(self, proxy_url: str, failures: int) -> Optional[Alert]:
        """Fire alert on proxy failure."""
        if failures >= 3:
            return self.fire(
                AlertType.PROXY_FAILURE,
                f"Proxy {proxy_url[:50]}... has {failures} failures",
                severity=AlertSeverity.WARNING,
                value=failures,
                threshold=3,
                metadata={"proxy_url": proxy_url},
            )
        return None

    # ── Query Methods ────────────────────────────────────────────────────

    def list_alerts(self, limit: int = 50, severity: Optional[str] = None,
                    engine: Optional[str] = None,
                    unacknowledged_only: bool = False) -> List[Dict[str, Any]]:
        """List recent alerts with optional filters."""
        alerts = list(self._alerts)
        if severity:
            alerts = [a for a in alerts if a.severity.value == severity]
        if engine:
            alerts = [a for a in alerts if a.engine == engine]
        if unacknowledged_only:
            alerts = [a for a in alerts if not a.acknowledged]
        return [a.to_dict() for a in alerts[:limit]]

    def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge an alert by ID."""
        for alert in self._alerts:
            if alert.id == alert_id:
                alert.acknowledged = True
                return True
        return False

    def summary(self) -> Dict[str, Any]:
        """Alert summary counts."""
        alerts = list(self._alerts)
        by_severity = {}
        by_type = {}
        for a in alerts:
            by_severity[a.severity.value] = by_severity.get(a.severity.value, 0) + 1
            by_type[a.type.value] = by_type.get(a.type.value, 0) + 1
        unack = sum(1 for a in alerts if not a.acknowledged)
        return {
            "total": len(alerts),
            "unacknowledged": unack,
            "by_severity": by_severity,
            "by_type": by_type,
        }

    def clear(self) -> int:
        """Clear all alerts. Returns count cleared."""
        n = len(self._alerts)
        self._alerts.clear()
        return n
