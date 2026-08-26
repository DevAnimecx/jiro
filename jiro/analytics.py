"""Analytics engine — computes metrics from usage data for dashboard display.

Provides engine performance metrics, latency percentiles, cache hit ratios,
error rates, and usage patterns from the SQLite usage table.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from jiro.db import Database
from jiro.log import get_logger

log = get_logger("jiro.analytics")


class AnalyticsEngine:
    """Computes analytics from usage data stored in SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Overview ─────────────────────────────────────────────────────────

    async def overview(self, since: float = 0.0) -> Dict[str, Any]:
        """High-level overview: total requests, tokens, cache ratio, uptime."""
        summary = await self.db.usage_summary(since=since)
        total = summary.get("requests", 0)
        cached = summary.get("cached", 0)
        cache_ratio = round(cached / total, 4) if total > 0 else 0.0

        return {
            "total_requests": total,
            "tokens_in": summary.get("tokens_in", 0),
            "tokens_out": summary.get("tokens_out", 0),
            "cached_requests": cached,
            "cache_hit_ratio": cache_ratio,
            "by_endpoint": summary.get("by_endpoint", []),
        }

    # ── Engine Metrics ───────────────────────────────────────────────────

    async def engine_metrics(self, since: float = 0.0) -> Dict[str, Any]:
        """Per-engine performance: request count, avg latency, error rate, cache ratio."""
        where = "WHERE ts >= ?" if since else ""
        params: tuple = (since,) if since else ()

        rows = await self.db.fetchall(
            f"SELECT engine, COUNT(*) AS n,"
            f" AVG(latency_ms) AS avg_latency,"
            f" MIN(latency_ms) AS min_latency,"
            f" MAX(latency_ms) AS max_latency,"
            f" SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors,"
            f" SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) AS cached"
            f" FROM usage {where} AND engine IS NOT NULL AND engine != ''"
            f" GROUP BY engine ORDER BY n DESC",
            params,
        )

        engines = {}
        for row in rows:
            eng = row["engine"]
            n = row["n"]
            engines[eng] = {
                "requests": n,
                "avg_latency_ms": round(row["avg_latency"] or 0, 1),
                "min_latency_ms": round(row["min_latency"] or 0, 1),
                "max_latency_ms": round(row["max_latency"] or 0, 1),
                "errors": row["errors"],
                "error_rate": round(row["errors"] / n, 4) if n > 0 else 0.0,
                "cached": row["cached"],
                "cache_ratio": round(row["cached"] / n, 4) if n > 0 else 0.0,
            }

        return {"engines": engines, "count": len(engines)}

    # ── Latency Percentiles ──────────────────────────────────────────────

    async def latency_percentiles(self, since: float = 0.0,
                                  engine: Optional[str] = None) -> Dict[str, Any]:
        """Compute p50, p95, p99 latency from usage data."""
        where_parts = ["ts >= ?"]
        params: list = [since]
        if engine:
            where_parts.append("engine = ?")
            params.append(engine)
        where = " WHERE " + " AND ".join(where_parts)

        rows = await self.db.fetchall(
            f"SELECT latency_ms FROM usage {where} ORDER BY latency_ms",
            tuple(params),
        )

        latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
        n = len(latencies)
        if n == 0:
            return {"p50": 0, "p95": 0, "p99": 0, "count": 0, "avg": 0}

        def percentile(pct: float) -> float:
            idx = int(n * pct / 100)
            idx = min(idx, n - 1)
            return round(latencies[idx], 1)

        return {
            "p50": percentile(50),
            "p95": percentile(95),
            "p99": percentile(99),
            "min": round(latencies[0], 1),
            "max": round(latencies[-1], 1),
            "avg": round(sum(latencies) / n, 1),
            "count": n,
        }

    # ── Error Rates ──────────────────────────────────────────────────────

    async def error_rates(self, since: float = 0.0) -> Dict[str, Any]:
        """Error rate by HTTP status code and by engine."""
        where = "WHERE ts >= ?" if since else ""
        params: tuple = (since,) if since else ()

        by_status = await self.db.fetchall(
            f"SELECT status, COUNT(*) AS n FROM usage {where}"
            f" GROUP BY status ORDER BY n DESC",
            params,
        )

        by_engine = await self.db.fetchall(
            f"SELECT engine, COUNT(*) AS n,"
            f" SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors"
            f" FROM usage {where} AND engine IS NOT NULL"
            f" GROUP BY engine ORDER BY errors DESC",
            params,
        )

        total = sum(r["n"] for r in by_status)
        errors = sum(r["n"] for r in by_status if r["status"] >= 400)

        return {
            "total_requests": total,
            "total_errors": errors,
            "error_rate": round(errors / total, 4) if total > 0 else 0.0,
            "by_status": {str(r["status"]): r["n"] for r in by_status},
            "by_engine": [
                {"engine": r["engine"], "requests": r["n"],
                 "errors": r["errors"],
                 "error_rate": round(r["errors"] / r["n"], 4) if r["n"] > 0 else 0}
                for r in by_engine if r["errors"] > 0
            ],
        }

    # ── Usage Patterns ───────────────────────────────────────────────────

    async def usage_patterns(self, since: float = 0.0) -> Dict[str, Any]:
        """Top queries, hourly distribution, engine popularity."""
        where = "WHERE ts >= ?" if since else ""
        params: tuple = (since,) if since else ()

        # Top queries (only when query logging is enabled)
        top_queries = await self.db.fetchall(
            f"SELECT query, COUNT(*) AS n FROM usage {where}"
            f" AND query IS NOT NULL AND query != ''"
            f" GROUP BY query ORDER BY n DESC LIMIT 20",
            params,
        )

        # Hourly distribution
        hourly = await self.db.fetchall(
            f"SELECT CAST((ts - 0) / 3600 AS INTEGER) AS hour_bucket,"
            f" COUNT(*) AS n FROM usage {where}"
            f" GROUP BY hour_bucket ORDER BY hour_bucket",
            params,
        )

        # Engine popularity
        engine_pop = await self.db.fetchall(
            f"SELECT engine, COUNT(*) AS n FROM usage {where}"
            f" AND engine IS NOT NULL AND engine != ''"
            f" GROUP BY engine ORDER BY n DESC",
            params,
        )

        # Peak hours (top 5)
        peak_hours = sorted(hourly, key=lambda r: r["n"], reverse=True)[:5]

        return {
            "top_queries": [{"query": r["query"], "count": r["n"]}
                            for r in top_queries],
            "engine_popularity": [{"engine": r["engine"], "count": r["n"]}
                                  for r in engine_pop],
            "peak_hours": [{"hour": r["hour_bucket"], "count": r["n"]}
                           for r in peak_hours],
            "total_queries_with_text": sum(1 for r in top_queries if r["query"]),
        }

    # ── Full Dashboard ───────────────────────────────────────────────────

    async def dashboard(self, since: float = 0.0) -> Dict[str, Any]:
        """Complete analytics dashboard combining all metrics."""
        overview = await self.overview(since=since)
        engines = await self.engine_metrics(since=since)
        latency = await self.latency_percentiles(since=since)
        errors = await self.error_rates(since=since)
        patterns = await self.usage_patterns(since=since)

        return {
            "overview": overview,
            "engines": engines,
            "latency": latency,
            "errors": errors,
            "patterns": patterns,
        }
