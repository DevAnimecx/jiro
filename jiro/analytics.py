"""Advanced query analytics, trend detection, and anomaly alerts.

Provides:
- Query frequency analysis and trend detection
- Search pattern recognition
- Anomaly detection for usage spikes and errors
- Popular queries and trending topics
- User behavior analytics
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class QueryStats:
    query: str
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    engines: Dict[str, int] = field(default_factory=dict)
    avg_latency_ms: float = 0.0
    error_count: int = 0
    users: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "engines": self.engines,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "error_count": self.error_count,
            "users": self.users,
        }


@dataclass
class TrendAlert:
    trend_type: str
    query: str
    severity: str
    message: str
    timestamp: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)


class QueryAnalytics:
    def __init__(self, max_queries: int = 100000):
        self._queries: Dict[str, QueryStats] = {}
        self._hourly_counts: Dict[str, int] = defaultdict(int)
        self._user_queries: Dict[str, set] = defaultdict(set)
        self._alerts: List[TrendAlert] = []
        self._max_queries = max_queries
        self._error_threshold = 0.3
        self._spike_multiplier = 3.0

    def record_query(self, query: str, engine: str, latency_ms: float,
                     user_id: str = "", success: bool = True) -> None:
        now = time.time()
        hour_key = time.strftime("%Y%m%d%H", time.localtime(now))

        if query not in self._queries:
            if len(self._queries) >= self._max_queries:
                self._evict_old()
            self._queries[query] = QueryStats(
                query=query, first_seen=now, last_seen=now
            )

        stats = self._queries[query]
        stats.count += 1
        stats.last_seen = now
        stats.engines[engine] = stats.engines.get(engine, 0) + 1
        stats.avg_latency_ms = (
            (stats.avg_latency_ms * (stats.count - 1) + latency_ms) / stats.count
        )
        if not success:
            stats.error_count += 1
        if user_id:
            self._user_queries[user_id].add(query)
            stats.users = len(self._user_queries[user_id])

        self._hourly_counts[hour_key] += 1
        self._check_anomalies(query, stats)

    def _evict_old(self) -> None:
        if not self._queries:
            return
        sorted_q = sorted(self._queries.items(), key=lambda x: x[1].last_seen)
        to_remove = len(sorted_q) // 4
        for q, _ in sorted_q[:to_remove]:
            del self._queries[q]

    def _check_anomalies(self, query: str, stats: QueryStats) -> None:
        if stats.count > 10:
            error_rate = stats.error_count / stats.count
            if error_rate > self._error_threshold:
                self._alerts.append(TrendAlert(
                    trend_type="high_error_rate",
                    query=query,
                    severity="warning",
                    message=f"High error rate for '{query}': {error_rate:.1%}",
                    details={"error_rate": error_rate, "count": stats.count},
                ))

    def get_trending(self, top_n: int = 10, window_hours: int = 24) -> List[Dict[str, Any]]:
        now = time.time()
        cutoff = now - (window_hours * 3600)
        recent = [
            s for s in self._queries.values()
            if s.last_seen >= cutoff
        ]
        recent.sort(key=lambda s: s.count, reverse=True)
        return [s.to_dict() for s in recent[:top_n]]

    def get_popular_queries(self, top_n: int = 20) -> List[Dict[str, Any]]:
        sorted_q = sorted(self._queries.values(), key=lambda s: s.count, reverse=True)
        return [s.to_dict() for s in sorted_q[:top_n]]

    def get_engine_distribution(self) -> Dict[str, int]:
        dist: Dict[str, int] = Counter()
        for stats in self._queries.values():
            for eng, cnt in stats.engines.items():
                dist[eng] += cnt
        return dict(dist)

    def get_user_stats(self, user_id: str) -> Dict[str, Any]:
        queries = self._user_queries.get(user_id, set())
        total = sum(self._queries[q].count for q in queries if q in self._queries)
        return {
            "user_id": user_id,
            "unique_queries": len(queries),
            "total_requests": total,
        }

    def get_hourly_volume(self, hours: int = 24) -> Dict[str, int]:
        now = time.time()
        result = {}
        for i in range(hours):
            t = time.localtime(now - i * 3600)
            key = time.strftime("%Y%m%d%H", t)
            result[key] = self._hourly_counts.get(key, 0)
        return dict(sorted(result.items()))

    def get_anomalies(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [
            {
                "trend_type": a.trend_type,
                "query": a.query,
                "severity": a.severity,
                "message": a.message,
                "timestamp": a.timestamp,
                "details": a.details,
            }
            for a in self._alerts[-limit:]
        ]

    def get_summary(self) -> Dict[str, Any]:
        total_queries = sum(s.count for s in self._queries.values())
        total_errors = sum(s.error_count for s in self._queries.values())
        return {
            "total_queries": total_queries,
            "unique_queries": len(self._queries),
            "total_errors": total_errors,
            "error_rate": total_errors / max(total_queries, 1),
            "active_users": len(self._user_queries),
            "engines_used": len(set(
                e for s in self._queries.values() for e in s.engines
            )),
        }


_analytics: Optional[QueryAnalytics] = None


def get_analytics() -> QueryAnalytics:
    global _analytics
    if _analytics is None:
        _analytics = QueryAnalytics()
    return _analytics


def reset_analytics():
    global _analytics
    _analytics = None


class AnalyticsEngine:
    """Database-backed analytics engine for usage metrics."""

    def __init__(self, db):
        self.db = db

    async def overview(self, since: float = 0) -> Dict[str, Any]:
        row = await self.db.usage_summary(since=since)
        total = row.get("requests", 0)
        cached = row.get("cached", 0)
        return {
            "total_requests": total,
            "tokens_in": row.get("tokens_in", 0),
            "tokens_out": row.get("tokens_out", 0),
            "cached_requests": cached,
            "cache_hit_ratio": cached / max(total, 1),
            "by_endpoint": row.get("by_endpoint", []),
        }

    async def engine_metrics(self, since: float = 0) -> Dict[str, Any]:
        rows = await self.db.fetchall(
            "SELECT engine, n, avg_latency, min_latency, max_latency, errors, cached "
            "FROM (SELECT engine, COUNT(*) as n, AVG(latency_ms) as avg_latency, "
            "MIN(latency_ms) as min_latency, MAX(latency_ms) as max_latency, "
            "SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) as errors, "
            "SUM(CASE WHEN cached THEN 1 ELSE 0 END) as cached "
            "FROM usage WHERE ts >= ? GROUP BY engine)",
            (since,),
        )
        engines = {}
        for r in rows:
            engines[r["engine"]] = {
                "requests": r["n"],
                "avg_latency": r["avg_latency"],
                "min_latency": r["min_latency"],
                "max_latency": r["max_latency"],
                "error_rate": r["errors"] / max(r["n"], 1),
                "cached": r["cached"],
            }
        return {"count": len(engines), "engines": engines}

    async def latency_percentiles(self, engine: Optional[str] = None,
                                   since: float = 0) -> Dict[str, Any]:
        rows = await self.db.fetchall(
            "SELECT latency_ms FROM usage WHERE ts >= ? AND latency_ms > 0",
            (since,),
        )
        if not rows:
            return {"p50": 0, "count": 0}
        latencies = sorted(r["latency_ms"] for r in rows)
        n = len(latencies)
        return {
            "count": n,
            "min": latencies[0],
            "max": latencies[-1],
            "avg": sum(latencies) / n,
            "p50": latencies[int(n * 0.5)],
            "p95": latencies[min(int(n * 0.95), n - 1)],
            "p99": latencies[min(int(n * 0.99), n - 1)],
        }

    async def error_rates(self, since: float = 0) -> Dict[str, Any]:
        by_status = await self.db.fetchall(
            "SELECT status, COUNT(*) as n FROM usage WHERE ts >= ? GROUP BY status",
            (since,),
        )
        by_engine = await self.db.fetchall(
            "SELECT engine, COUNT(*) as n, SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) as errors "
            "FROM usage WHERE ts >= ? GROUP BY engine",
            (since,),
        )
        total_requests = sum(r["n"] for r in by_status)
        total_errors = sum(r["n"] for r in by_status if r["status"] >= 400)
        return {
            "total_requests": total_requests,
            "total_errors": total_errors,
            "by_status": {str(r["status"]): r["n"] for r in by_status},
        }

    async def usage_patterns(self, since: float = 0) -> Dict[str, Any]:
        top_queries = await self.db.fetchall(
            "SELECT query, COUNT(*) as n FROM usage WHERE ts >= ? AND query IS NOT NULL "
            "GROUP BY query ORDER BY n DESC LIMIT 10",
            (since,),
        )
        hourly = await self.db.fetchall(
            "SELECT CAST((ts - ?) / 3600 AS INTEGER) as hour_bucket, COUNT(*) as n "
            "FROM usage WHERE ts >= ? GROUP BY hour_bucket ORDER BY n DESC",
            (since, since),
        )
        engine_pop = await self.db.fetchall(
            "SELECT engine, COUNT(*) as n FROM usage WHERE ts >= ? AND engine IS NOT NULL "
            "GROUP BY engine ORDER BY n DESC",
            (since,),
        )
        return {
            "top_queries": [{"query": r["query"], "count": r["n"]} for r in top_queries],
            "peak_hours": [{"hour": r["hour_bucket"], "count": r["n"]} for r in hourly],
            "engine_popularity": [{"engine": r["engine"], "count": r["n"]} for r in engine_pop],
        }

    async def dashboard(self, since: float = 0) -> Dict[str, Any]:
        engines = await self.engine_metrics(since)
        latency = await self.latency_percentiles(since=since)
        errors = await self.error_rates(since)
        patterns = await self.usage_patterns(since)
        return {
            "overview": {
                "total_requests": errors.get("total_requests", 0),
                "total_errors": errors.get("total_errors", 0),
            },
            "engines": engines,
            "latency": latency,
            "errors": errors,
            "patterns": patterns,
        }
