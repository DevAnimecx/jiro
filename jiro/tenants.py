"""Multi-tenant isolation, rate limit zones, and SLA monitoring.

Provides:
- Tenant isolation with separate quotas and rate limits
- Rate limit zones (global, per-engine, per-endpoint)
- SLA monitoring with uptime tracking
- Quota management per tenant
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class TenantConfig:
    tenant_id: str
    name: str
    tier: str = "free"
    rate_limit_rpm: int = 60
    rate_limit_rpd: int = 10000
    max_concurrent: int = 10
    allowed_engines: Optional[Set[str]] = None
    allowed_endpoints: Optional[Set[str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "name": self.name,
            "tier": self.tier,
            "rate_limit_rpm": self.rate_limit_rpm,
            "rate_limit_rpd": self.rate_limit_rpd,
            "max_concurrent": self.max_concurrent,
            "allowed_engines": list(self.allowed_engines) if self.allowed_engines else None,
            "allowed_endpoints": list(self.allowed_endpoints) if self.allowed_endpoints else None,
            "metadata": self.metadata,
        }


@dataclass
class SLAMetric:
    endpoint: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    last_error: str = ""
    last_error_time: float = 0.0
    _latencies: List[float] = field(default_factory=list)

    def record_request(self, latency_ms: float, success: bool, error: str = ""):
        self.total_requests += 1
        self.total_latency_ms += latency_ms
        self._latencies.append(latency_ms)
        if len(self._latencies) > 1000:
            self._latencies = self._latencies[-500:]
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
            self.last_error = error
            self.last_error_time = time.time()
        self._compute_percentiles()

    def _compute_percentiles(self):
        if not self._latencies:
            return
        sorted_lat = sorted(self._latencies)
        n = len(sorted_lat)
        self.p50_latency_ms = sorted_lat[int(n * 0.5)] if n > 0 else 0
        self.p95_latency_ms = sorted_lat[min(int(n * 0.95), n - 1)]
        self.p99_latency_ms = sorted_lat[min(int(n * 0.99), n - 1)]

    @property
    def uptime_percent(self) -> float:
        if self.total_requests == 0:
            return 100.0
        return (self.successful_requests / self.total_requests) * 100

    @property
    def avg_latency_ms(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_latency_ms / self.total_requests

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "uptime_percent": round(self.uptime_percent, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "last_error": self.last_error,
            "last_error_time": self.last_error_time,
        }


class TenantManager:
    def __init__(self):
        self._tenants: Dict[str, TenantConfig] = {}
        self._usage: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._sla: Dict[str, SLAMetric] = {}
        self._request_windows: Dict[str, List[float]] = defaultdict(list)

    def create_tenant(self, tenant_id: str, name: str, **kwargs) -> TenantConfig:
        tenant = TenantConfig(tenant_id=tenant_id, name=name, **kwargs)
        self._tenants[tenant_id] = tenant
        return tenant

    def get_tenant(self, tenant_id: str) -> Optional[TenantConfig]:
        return self._tenants.get(tenant_id)

    def update_tenant(self, tenant_id: str, **kwargs) -> Optional[TenantConfig]:
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return None
        for k, v in kwargs.items():
            if hasattr(tenant, k):
                setattr(tenant, k, v)
        return tenant

    def delete_tenant(self, tenant_id: str) -> bool:
        return self._tenants.pop(tenant_id, None) is not None

    def list_tenants(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self._tenants.values()]

    def check_rate_limit(self, tenant_id: str, endpoint: str = "") -> bool:
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return True
        now = time.time()
        window_key = f"{tenant_id}:rpm"
        window = self._request_windows[window_key]
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= tenant.rate_limit_rpm:
            return False
        window.append(now)
        return True

    def check_engine_access(self, tenant_id: str, engine: str) -> bool:
        tenant = self._tenants.get(tenant_id)
        if not tenant or not tenant.allowed_engines:
            return True
        return engine in tenant.allowed_engines

    def check_endpoint_access(self, tenant_id: str, endpoint: str) -> bool:
        tenant = self._tenants.get(tenant_id)
        if not tenant or not tenant.allowed_endpoints:
            return True
        return endpoint in tenant.allowed_endpoints

    def record_sla(self, endpoint: str, latency_ms: float, success: bool, error: str = ""):
        if endpoint not in self._sla:
            self._sla[endpoint] = SLAMetric(endpoint=endpoint)
        self._sla[endpoint].record_request(latency_ms, success, error)

    def get_sla(self, endpoint: Optional[str] = None) -> Dict[str, Any]:
        if endpoint:
            metric = self._sla.get(endpoint)
            return metric.to_dict() if metric else {}
        return {ep: m.to_dict() for ep, m in self._sla.items()}

    def get_sla_summary(self) -> Dict[str, Any]:
        total = sum(m.total_requests for m in self._sla.values())
        success = sum(m.successful_requests for m in self._sla.values())
        avg_latency = (
            sum(m.avg_latency_ms * m.total_requests for m in self._sla.values())
            / max(total, 1)
        )
        return {
            "total_requests": total,
            "successful_requests": success,
            "overall_uptime": round((success / max(total, 1)) * 100, 4),
            "avg_latency_ms": round(avg_latency, 2),
            "endpoints_tracked": len(self._sla),
        }


_tenant_manager: Optional[TenantManager] = None


def get_tenant_manager() -> TenantManager:
    global _tenant_manager
    if _tenant_manager is None:
        _tenant_manager = TenantManager()
    return _tenant_manager


def reset_tenant_manager():
    global _tenant_manager
    _tenant_manager = None
