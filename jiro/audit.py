"""Audit logging.

Two complementary layers:

1. **Request audit** (``AuditEntry`` / ``AuditLogger`` / ``AuditMiddleware``):
   high-level per-HTTP-request trail kept in memory for ops dashboards
   (``recent``, ``by_key``, ``by_ip``, ``summary``).

2. **Compliance event trail** (``AuditEvent`` / ``ComplianceLogger``):
   structured security/compliance events (auth, scraping, ToS checks,
   config changes) buffered and persisted to a JSONL file.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, Dict, List, Optional

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.audit")

_SEVERITY_LEVEL = {
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


# ===========================================================================
# Layer 1 — per-request audit trail
# ===========================================================================

@dataclass
class AuditEntry:
    """One audited HTTP request."""
    method: str
    path: str
    status_code: int
    latency_ms: float
    client_ip: Optional[str] = None
    key_id: Optional[str] = None
    user_agent: Optional[str] = None
    error: Optional[str] = None
    request_size: int = 0
    response_size: int = 0
    engine: Optional[str] = None
    query: Optional[str] = None
    cached: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "iso_time": datetime.fromtimestamp(
                self.timestamp, tz=timezone.utc).isoformat(),
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "client_ip": self.client_ip,
            "key_id": self.key_id,
            "user_agent": self.user_agent,
            "error": self.error,
            "request_size": self.request_size,
            "response_size": self.response_size,
            "engine": self.engine,
            "cached": self.cached,
        }


class AuditLogger:
    """In-memory bounded audit trail of recent requests."""

    def __init__(self, max_entries: int = 10000) -> None:
        self.max_entries = max_entries
        self._entries: Deque[AuditEntry] = deque(maxlen=max_entries)
        self._total_requests = 0
        self._total_errors = 0

    def log(self, entry: AuditEntry) -> None:
        self._entries.append(entry)
        self._total_requests += 1
        if entry.status_code >= 400:
            self._total_errors += 1

    def recent(self, *, limit: int = 100, method: Optional[str] = None,
               status_min: Optional[int] = None) -> List[Dict[str, Any]]:
        items = list(self._entries)[-limit:]
        if method:
            items = [e for e in items if e.method == method]
        if status_min is not None:
            items = [e for e in items if e.status_code >= status_min]
        return [e.to_dict() for e in items]

    def by_key(self, key_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        items = [e for e in self._entries if e.key_id == key_id]
        return [e.to_dict() for e in items[-limit:]]

    def by_ip(self, ip: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        items = [e for e in self._entries if e.client_ip == ip]
        return [e.to_dict() for e in items[-limit:]]

    def summary(self) -> Dict[str, Any]:
        keys = {e.key_id for e in self._entries if e.key_id}
        ips = {e.client_ip for e in self._entries if e.client_ip}
        return {
            "total_requests": self._total_requests,
            "total_errors": self._total_errors,
            "error_rate": round(self._total_errors / max(self._total_requests, 1), 4),
            "unique_keys": len(keys),
            "unique_ips": len(ips),
            "buffered": len(self._entries),
            "max_entries": self.max_entries,
        }

    def clear(self) -> int:
        n = len(self._entries)
        self._entries.clear()
        return n


class AuditMiddleware:
    """ASGI middleware recording every HTTP request into :class:`AuditLogger`."""

    def __init__(self, app: Any, audit_logger: AuditLogger) -> None:
        self.app = app
        self.audit_logger = audit_logger

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        headers = {k.decode("latin-1"): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        client = scope.get("client")
        ip = client[0] if client else None
        ua = headers.get("user-agent")

        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            latency = (time.perf_counter() - started) * 1000
            self.audit_logger.log(AuditEntry(
                method=method, path=path, status_code=500, latency_ms=latency,
                client_ip=ip, key_id=None, user_agent=ua, error=str(exc),
            ))
            raise

        latency = (time.perf_counter() - started) * 1000
        # skip health/metrics noise
        if path not in ("/health", "/metrics"):
            self.audit_logger.log(AuditEntry(
                method=method, path=path, status_code=status_holder["status"],
                latency_ms=round(latency, 1), client_ip=ip, user_agent=ua,
            ))


# ===========================================================================
# Layer 2 — compliance/security event trail
# ===========================================================================

class AuditEventType(str, Enum):
    """Types of compliance events."""
    # Auth events
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_FAILED = "auth.failed"
    KEY_CREATED = "key.created"
    KEY_REVOKED = "key.revoked"
    TOKEN_ISSUED = "token.issued"

    # Scraping events
    SEARCH_REQUEST = "search.request"
    SEARCH_RESPONSE = "search.response"
    SEARCH_FAILED = "search.failed"
    SCRAPE_REQUEST = "scrape.request"
    SCRAPE_RESPONSE = "scrape.response"
    SCRAPE_FAILED = "scrape.failed"
    ENGINE_FALLBACK = "engine.fallback"
    ENGINE_BLOCKED = "engine.blocked"
    ROBOTS_TXT_CHECK = "robots_txt.check"
    ROBOTS_TXT_DENIED = "robots_txt.denied"

    # AI events
    AI_SEARCH = "ai.search"
    AI_AGENT = "ai.agent"
    AI_EXTRACT = "ai.extract"
    LLM_REQUEST = "llm.request"

    # Admin events
    CONFIG_CHANGED = "config.changed"
    USER_CREATED = "user.created"
    ROLE_CHANGED = "role.changed"
    RATE_LIMIT_EXCEEDED = "rate_limit.exceeded"

    # System events
    STARTUP = "system.startup"
    SHUTDOWN = "system.shutdown"
    ERROR = "system.error"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """Structured compliance event."""
    event_type: AuditEventType
    severity: AuditSeverity = AuditSeverity.INFO
    timestamp: str = field(default_factory=lambda: datetime.now(
        timezone.utc).isoformat().replace("+00:00", "Z"))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Identity
    user_id: Optional[str] = None
    key_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    # Request context
    endpoint: Optional[str] = None
    method: Optional[str] = None
    request_id: Optional[str] = None

    # Engine/scraping context
    engine: Optional[str] = None
    query: Optional[str] = None  # Only stored if privacy.log_queries enabled
    search_type: Optional[str] = None

    # Result
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    cached: bool = False
    results_count: Optional[int] = None

    # Error details
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    # Compliance
    robots_txt_checked: bool = False
    robots_txt_allowed: Optional[bool] = None
    crawl_delay_applied: Optional[float] = None

    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "user_id": self.user_id,
            "key_id": self.key_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "endpoint": self.endpoint,
            "method": self.method,
            "request_id": self.request_id,
            "engine": self.engine,
            "query": self.query,
            "search_type": self.search_type,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "results_count": self.results_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "robots_txt_checked": self.robots_txt_checked,
            "robots_txt_allowed": self.robots_txt_allowed,
            "crawl_delay_applied": self.crawl_delay_applied,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class ComplianceLogger:
    """Buffers structured compliance events and persists them to JSONL."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._enabled = bool(settings.get("audit.enabled", True))
        self._log_file: Optional[str] = settings.get("audit.log_file")
        self._log_queries = settings.log_queries()
        self._buffer: List[AuditEvent] = []
        self._buffer_size = int(settings.get("audit.buffer_size", 100))
        self._flush_interval = float(settings.get("audit.flush_interval_seconds", 5))
        self._last_flush = time.time()

    def log(self, event: AuditEvent) -> None:
        if not self._enabled:
            return

        if not self._log_queries and event.query:
            event.query = "[REDACTED]"

        self._buffer.append(event)
        level = _SEVERITY_LEVEL.get(event.severity.value, logging.INFO)
        log.log(level, f"AUDIT: {event.event_type.value}",
                extra=event.to_dict())

        if len(self._buffer) >= self._buffer_size or \
           time.time() - self._last_flush > self._flush_interval:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        events = self._buffer.copy()
        self._buffer.clear()
        self._last_flush = time.time()

        if self._log_file:
            path = os.path.expanduser(self._log_file)
            try:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    for event in events:
                        f.write(event.to_json() + "\n")
            except Exception as exc:
                log.error("failed to write audit log", extra={"error": str(exc)})

    # -- convenience -----------------------------------------------------

    def log_search(self, *, event_type: AuditEventType, engine: str, query: str,
                   search_type: str, status_code: int, latency_ms: float,
                   cached: bool, results_count: int, key_id: Optional[str] = None,
                   ip: Optional[str] = None, error: Optional[str] = None,
                   robots_checked: bool = False, robots_allowed: Optional[bool] = None,
                   crawl_delay: Optional[float] = None) -> None:
        severity = AuditSeverity.INFO
        if status_code >= 400:
            severity = AuditSeverity.WARNING
        if status_code >= 500:
            severity = AuditSeverity.ERROR
        self.log(AuditEvent(
            event_type=event_type, severity=severity,
            key_id=key_id, ip_address=ip,
            endpoint="/search", method="GET",
            engine=engine, query=query, search_type=search_type,
            status_code=status_code, latency_ms=latency_ms, cached=cached,
            results_count=results_count, error_message=error,
            robots_txt_checked=robots_checked, robots_txt_allowed=robots_allowed,
            crawl_delay_applied=crawl_delay,
        ))

    def log_auth(self, *, event_type: AuditEventType, key_id: Optional[str],
                 ip: Optional[str], user_agent: Optional[str],
                 success: bool, error: Optional[str] = None) -> None:
        severity = AuditSeverity.INFO if success else AuditSeverity.WARNING
        if event_type == AuditEventType.AUTH_FAILED:
            severity = AuditSeverity.ERROR
        self.log(AuditEvent(
            event_type=event_type, severity=severity,
            key_id=key_id, ip_address=ip, user_agent=user_agent,
            endpoint="/auth", method="POST",
            status_code=200 if success else 401, error_message=error,
        ))

    def log_rate_limit(self, *, bucket: str, limit: int,
                       key_id: Optional[str] = None, ip: Optional[str] = None) -> None:
        self.log(AuditEvent(
            event_type=AuditEventType.RATE_LIMIT_EXCEEDED,
            severity=AuditSeverity.WARNING,
            key_id=key_id, ip_address=ip,
            metadata={"bucket": bucket, "limit": limit},
        ))

    def log_config_change(self, *, key_id: str, changes: Dict[str, Any]) -> None:
        self.log(AuditEvent(
            event_type=AuditEventType.CONFIG_CHANGED, severity=AuditSeverity.INFO,
            key_id=key_id, metadata={"changes": changes},
        ))

    def log_engine_fallback(self, *, from_engine: str, to_engine: str,
                            reason: str, key_id: Optional[str] = None) -> None:
        self.log(AuditEvent(
            event_type=AuditEventType.ENGINE_FALLBACK, severity=AuditSeverity.INFO,
            key_id=key_id, engine=to_engine,
            metadata={"from_engine": from_engine, "reason": reason},
        ))

    def log_robots_check(self, *, engine: str, allowed: bool,
                         crawl_delay: Optional[float],
                         key_id: Optional[str] = None) -> None:
        self.log(AuditEvent(
            event_type=AuditEventType.ROBOTS_TXT_CHECK if allowed
                      else AuditEventType.ROBOTS_TXT_DENIED,
            severity=AuditSeverity.INFO if allowed else AuditSeverity.WARNING,
            key_id=key_id, engine=engine,
            robots_txt_checked=True, robots_txt_allowed=allowed,
            crawl_delay_applied=crawl_delay,
        ))


# Global compliance logger instance
_compliance_logger: Optional[ComplianceLogger] = None


def get_compliance_logger(settings: Optional[Settings] = None) -> ComplianceLogger:
    global _compliance_logger
    if _compliance_logger is None:
        _compliance_logger = ComplianceLogger(settings or Settings.load())
    return _compliance_logger


def set_compliance_logger(logger: ComplianceLogger) -> None:
    global _compliance_logger
    _compliance_logger = logger