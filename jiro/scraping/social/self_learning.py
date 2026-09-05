"""Self-learning layer for social scrapers.

Tracks performance across engines, platforms, and query types to
automatically rank and select the best-performing configurations.

Features:
- Success rate tracking per platform/engine
- Latency percentile monitoring
- Automatic engine ranking
- Credential/cookie health monitoring
- Adaptive timeout tuning
- Query hash success tracking
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("jiro.scraping.social.learning")


@dataclass
class ScrapeRecord:
    platform: str
    engine: str
    method: str  # scrape, search, profile, etc.
    success: bool
    latency_ms: float
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    query_hash_valid: bool = True
    timestamp: float = field(default_factory=time.time)


@dataclass
class EngineStats:
    """Aggregated stats for a single platform+engine combo."""
    platform: str
    engine: str
    total_attempts: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    latencies: List[float] = field(default_factory=list)
    error_counts: Dict[str, int] = field(default_factory=dict)
    last_success: float = 0.0
    last_failure: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 1.0
        return self.successes / self.total_attempts

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return sum(self.latencies) / len(self.latencies)

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = math.ceil(0.95 * len(sorted_lat)) - 1
        return sorted_lat[max(0, idx)]

    @property
    def health_score(self) -> float:
        """Composite health score (0.0–1.0).

        Weights: success_rate (60%), latency (25%), recency (15%).
        """
        if self.total_attempts == 0:
            return 0.5  # neutral for never-tested engines

        success_weight = 0.60
        latency_weight = 0.25
        recency_weight = 0.15

        # Success rate component
        success_score = self.success_rate

        # Latency component (lower is better; normalize to 0–1)
        p95 = self.p95_latency_ms
        latency_score = max(0.0, min(1.0, 1.0 - (p95 / 10_000.0)))

        # Recency component (decay based on time since last success)
        now = time.time()
        hours_since_success = (now - self.last_success) / 3600.0 if self.last_success else 999.0
        recency_score = max(0.0, math.exp(-hours_since_success / 24.0))

        return (
            success_weight * success_score +
            latency_weight * latency_score +
            recency_weight * recency_score
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "engine": self.engine,
            "total_attempts": self.total_attempts,
            "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "health_score": round(self.health_score, 3),
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
        }


class LearningStore:
    """Persist and query learning data.

    Uses a simple JSON file by default; can be swapped for SQLite.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(
            os.path.expanduser("~"), ".jiro", "social_learning.json"
        )
        self._records: List[ScrapeRecord] = []
        self._engine_stats: Dict[Tuple[str, str], EngineStats] = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for rec in data.get("records", []):
                    self._records.append(ScrapeRecord(**rec))
                for key, stats in data.get("engine_stats", {}).items():
                    plat, eng = key.split("::")
                    es = EngineStats(platform=plat, engine=eng)
                    for k, v in stats.items():
                        if k in ("latencies",):
                            setattr(es, k, v)
                        elif k in ("error_counts",):
                            setattr(es, k, defaultdict(int, v))
                        else:
                            setattr(es, k, v)
                    self._engine_stats[(plat, eng)] = es
        except Exception as exc:
            log.warning("failed to load learning store: %s", exc)

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            # Keep only the last 10 000 records to bound file size
            recent = self._records[-10_000:]
            data = {
                "records": [
                    {
                        "platform": r.platform,
                        "engine": r.engine,
                        "method": r.method,
                        "success": r.success,
                        "latency_ms": r.latency_ms,
                        "error_type": r.error_type,
                        "error_message": r.error_message,
                        "query_hash_valid": r.query_hash_valid,
                        "timestamp": r.timestamp,
                    }
                    for r in recent
                ],
                "engine_stats": {
                    f"{es.platform}::{es.engine}": es.to_dict()
                    for es in self._engine_stats.values()
                },
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as exc:
            log.warning("failed to save learning store: %s", exc)

    def record(self, record: ScrapeRecord) -> None:
        self._records.append(record)
        key = (record.platform, record.engine)
        if key not in self._engine_stats:
            self._engine_stats[key] = EngineStats(
                platform=record.platform, engine=record.engine,
            )
        es = self._engine_stats[key]
        es.total_attempts += 1
        if record.success:
            es.successes += 1
            es.consecutive_successes += 1
            es.consecutive_failures = 0
            es.last_success = record.timestamp
        else:
            es.failures += 1
            es.consecutive_failures += 1
            es.consecutive_successes = 0
            es.last_failure = record.timestamp
            if record.error_type:
                es.error_counts[record.error_type] = es.error_counts.get(record.error_type, 0) + 1
        es.total_latency_ms += record.latency_ms
        es.latencies.append(record.latency_ms)
        # Keep only recent latencies for memory
        if len(es.latencies) > 500:
            es.latencies = es.latencies[-500:]
        self.save()

    def get_engine_stats(self, platform: str, engine: str) -> EngineStats:
        return self._engine_stats.get((platform, engine), EngineStats(platform=platform, engine=engine))

    def rank_engines(self, platform: str, method: str) -> List[Tuple[str, float]]:
        """Return engines ranked by health score (highest first)."""
        ranked = []
        for (plat, eng), es in self._engine_stats.items():
            if plat == platform:
                ranked.append((eng, es.health_score))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def prune(self, max_age_hours: float = 72.0) -> int:
        """Remove records older than ``max_age_hours``. Returns count pruned."""
        cutoff = time.time() - (max_age_hours * 3600)
        before = len(self._records)
        self._records = [r for r in self._records if r.timestamp > cutoff]
        pruned = before - len(self._records)
        if pruned:
            self.save()
        return pruned


# Global store instance
_store = LearningStore()


def record_scrape(platform: str, engine: str, method: str,
                  success: bool, latency_ms: float,
                  error_type: Optional[str] = None,
                  error_message: Optional[str] = None,
                  query_hash_valid: bool = True) -> None:
    """Record a scrape outcome for learning."""
    try:
        _store.record(ScrapeRecord(
            platform=platform,
            engine=engine,
            method=method,
            success=success,
            latency_ms=latency_ms,
            error_type=error_type,
            error_message=error_message,
            query_hash_valid=query_hash_valid,
        ))
    except Exception as exc:
        log.debug("learning record failed: %s", exc)


def get_engine_ranking(platform: str, method: str = "scrape") -> List[Dict[str, Any]]:
    """Get ranked engines for a platform, best first."""
    ranked = _store.rank_engines(platform, method)
    return [
        {"engine": eng, "health_score": score,
         "success_rate": _store.get_engine_stats(platform, eng).success_rate}
        for eng, score in ranked
    ]


def get_platform_stats(platform: str) -> Dict[str, Any]:
    """Get aggregated stats for a platform."""
    engines = {}
    for (plat, eng), es in _store._engine_stats.items():
        if plat == platform:
            engines[eng] = es.to_dict()
    total_records = sum(
        1 for r in _store._records if r.platform == platform
    )
    recent = [r for r in _store._records[-1000:] if r.platform == platform]
    success_count = sum(1 for r in recent if r.success)
    return {
        "platform": platform,
        "total_records": total_records,
        "recent_attempts": len(recent),
        "recent_success_rate": round(success_count / len(recent), 3) if recent else 0.0,
        "engines": engines,
    }


def get_best_engine(platform: str, method: str = "scrape") -> str:
    """Return the highest-ranked engine for a platform/method."""
    ranked = _store.rank_engines(platform, method)
    if ranked:
        return ranked[0][0]
    return "auto"


def should_refresh_credentials(platform: str) -> bool:
    """Return True if credentials look stale and should be refreshed."""
    stats = _store.get_engine_stats(platform, "credentials")
    # Refresh if consecutive auth failures > 2 in the last hour
    if stats.consecutive_failures >= 3:
        return True
    return False


def prune_old_records(max_age_hours: float = 72.0) -> int:
    """Prune old learning records. Returns count pruned."""
    return _store.prune(max_age_hours)


def get_learning_summary() -> Dict[str, Any]:
    """Return a summary of learning data for monitoring."""
    platforms = set(r.platform for r in _store._records)
    return {
        "total_records": len(_store._records),
        "platforms_tracked": sorted(platforms),
        "engine_count": len(_store._engine_stats),
        "top_engines": {
            p: _store.rank_engines(p, "scrape")[:3]
            for p in platforms
        },
    }
