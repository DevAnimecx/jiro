"""Self-learning layer for social scrapers.

Tracks performance across engines, platforms, and query types to
automatically rank and select the best-performing configurations.

Features:
- Success rate tracking per platform/engine/method
- Latency percentile monitoring (p50, p95, p99)
- Automatic engine ranking
- Credential/cookie health monitoring
- Adaptive timeout tuning based on observed latency
- Query hash success tracking
- Memory-bounded record storage with async persistence
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("jiro.scraping.social.learning")

# Maximum records kept in memory.
MAX_MEMORY_RECORDS = 10_000
# Maximum latency samples kept per engine/method.
MAX_LATENCY_SAMPLES = 500
# Prune records older than this (hours).
DEFAULT_PRUNE_AGE_HOURS = 72.0


@dataclass
class ScrapeRecord:
    platform: str
    engine: str
    method: str  # scrape, search, profile, timeline, etc.
    success: bool
    latency_ms: float
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    query_hash_valid: bool = True
    timestamp: float = field(default_factory=time.time)


@dataclass
class EngineStats:
    """Aggregated stats for a single platform+engine+method combo."""
    platform: str
    engine: str
    method: str = "scrape"
    total_attempts: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    latencies: deque = field(default_factory=lambda: deque(maxlen=MAX_LATENCY_SAMPLES))
    error_counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
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
    def p50_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = math.ceil(0.50 * len(sorted_lat)) - 1
        return sorted_lat[max(0, idx)]

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = math.ceil(0.95 * len(sorted_lat)) - 1
        return sorted_lat[max(0, idx)]

    @property
    def p99_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = math.ceil(0.99 * len(sorted_lat)) - 1
        return sorted_lat[max(0, idx)]

    @property
    def recommended_timeout_ms(self) -> int:
        """Adaptive timeout based on p95 latency with 50% headroom."""
        p95 = self.p95_latency_ms
        if p95 <= 0:
            return 15_000  # 15s default
        return min(max(int(p95 * 1.5), 5_000), 60_000)  # clamp 5s–60s

    @property
    def health_score(self) -> float:
        """Composite health score (0.0–1.0).

        Weights: success_rate (60%), latency (25%), recency (15%).
        """
        if self.total_attempts == 0:
            return 0.5

        success_weight = 0.60
        latency_weight = 0.25
        recency_weight = 0.15

        success_score = self.success_rate
        p95 = self.p95_latency_ms
        latency_score = max(0.0, min(1.0, 1.0 - (p95 / 10_000.0)))
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
            "method": self.method,
            "total_attempts": self.total_attempts,
            "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "p50_latency_ms": round(self.p50_latency_ms, 1),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "p99_latency_ms": round(self.p99_latency_ms, 1),
            "recommended_timeout_ms": self.recommended_timeout_ms,
            "health_score": round(self.health_score, 3),
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
        }


class LearningStore:
    """Persist and query learning data with bounded memory.

    Uses a JSON file for persistence with async writes to avoid blocking
    the event loop. Memory is bounded to ``MAX_MEMORY_RECORDS``.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(
            os.path.expanduser("~"), ".jiro", "social_learning.json"
        )
        self._records: deque = deque(maxlen=MAX_MEMORY_RECORDS)
        self._engine_stats: Dict[Tuple[str, str, str], EngineStats] = {}
        self._save_lock = asyncio.Lock()
        self._dirty = False
        self._load()

    def _key(self, platform: str, engine: str, method: str) -> Tuple[str, str, str]:
        return (platform, engine, method)

    def _get_or_create_stats(self, platform: str, engine: str, method: str) -> EngineStats:
        key = self._key(platform, engine, method)
        if key not in self._engine_stats:
            self._engine_stats[key] = EngineStats(
                platform=platform, engine=engine, method=method,
            )
        return self._engine_stats[key]

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for rec in data.get("records", []):
                    self._records.append(ScrapeRecord(**rec))
                for key_str, stats in data.get("engine_stats", {}).items():
                    parts = key_str.split("::")
                    if len(parts) != 3:
                        continue
                    plat, eng, method = parts
                    es = EngineStats(platform=plat, engine=eng, method=method)
                    for k, v in stats.items():
                        if k == "latencies":
                            es.latencies = deque(v, maxlen=MAX_LATENCY_SAMPLES)
                        elif k == "error_counts":
                            es.error_counts = defaultdict(int, v)
                        elif hasattr(es, k):
                            setattr(es, k, v)
                    self._engine_stats[self._key(plat, eng, method)] = es
        except Exception as exc:
            log.warning("failed to load learning store: %s", exc)

    async def save(self) -> None:
        """Async save to disk with lock to prevent concurrent writes."""
        async with self._save_lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                # Write to temp file then rename for atomicity
                tmp_path = self.path + ".tmp"
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
                        for r in list(self._records)[-10_000:]
                    ],
                    "engine_stats": {
                        f"{es.platform}::{es.engine}::{es.method}": es.to_dict()
                        for es in self._engine_stats.values()
                    },
                }
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, default=str)
                os.replace(tmp_path, self.path)
                self._dirty = False
            except Exception as exc:
                log.warning("failed to save learning store: %s", exc)

    def record(self, record: ScrapeRecord) -> None:
        """Record a scrape outcome (sync, for use in sync contexts)."""
        self._records.append(record)
        es = self._get_or_create_stats(record.platform, record.engine, record.method)
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
                es.error_counts[record.error_type] += 1
        es.total_latency_ms += record.latency_ms
        es.latencies.append(record.latency_ms)
        self._dirty = True

    async def record_async(self, record: ScrapeRecord) -> None:
        """Record a scrape outcome (async, with debounced save)."""
        self.record(record)
        if self._dirty:
            await self.save()

    def get_engine_stats(self, platform: str, engine: str, method: str = "scrape") -> EngineStats:
        return self._engine_stats.get(
            self._key(platform, engine, method),
            EngineStats(platform=platform, engine=engine, method=method),
        )

    def rank_engines(self, platform: str, method: str = "scrape") -> List[Tuple[str, float]]:
        """Return engines ranked by health score (highest first) for a platform+method."""
        ranked = []
        for (plat, eng, meth), es in self._engine_stats.items():
            if plat == platform and meth == method:
                ranked.append((eng, es.health_score))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def rank_engines_all_methods(self, platform: str) -> List[Tuple[str, str, float]]:
        """Return (engine, method, score) tuples ranked by health score."""
        ranked = []
        for (plat, eng, meth), es in self._engine_stats.items():
            if plat == platform:
                ranked.append((eng, meth, es.health_score))
        ranked.sort(key=lambda x: x[2], reverse=True)
        return ranked

    def prune(self, max_age_hours: float = DEFAULT_PRUNE_AGE_HOURS) -> int:
        """Remove records older than ``max_age_hours``. Returns count pruned."""
        cutoff = time.time() - (max_age_hours * 3600)
        before = len(self._records)
        self._records = deque(
            (r for r in self._records if r.timestamp > cutoff),
            maxlen=MAX_MEMORY_RECORDS,
        )
        pruned = before - len(self._records)
        if pruned:
            self._dirty = True
        return pruned


class AsyncLearningStore:
    """Async wrapper around LearningStore for use in async code.

    Provides the same interface but all mutators are async and writes
    are debounced.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._store = LearningStore(path)
        self._save_task: Optional[asyncio.Task] = None
        self._save_delay = 2.0  # seconds to debounce saves

    async def _debounced_save(self) -> None:
        if self._save_task is not None and not self._save_task.done():
            self._save_task.cancel()
        self._save_task = asyncio.create_task(self._delayed_save())

    async def _delayed_save(self) -> None:
        try:
            await asyncio.sleep(self._save_delay)
            if self._store._dirty:
                await self._store.save()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("debounced save failed: %s", exc)

    def record(self, record: ScrapeRecord) -> None:
        self._store.record(record)

    async def record_async(self, record: ScrapeRecord) -> None:
        self.record(record)
        await self._debounced_save()

    def get_engine_stats(self, platform: str, engine: str, method: str = "scrape") -> EngineStats:
        return self._store.get_engine_stats(platform, engine, method)

    def rank_engines(self, platform: str, method: str = "scrape") -> List[Tuple[str, float]]:
        return self._store.rank_engines(platform, method)

    def rank_engines_all_methods(self, platform: str) -> List[Tuple[str, str, float]]:
        return self._store.rank_engines_all_methods(platform)

    async def save(self) -> None:
        if self._save_task is not None and not self._save_task.done():
            self._save_task.cancel()
        await self._store.save()

    def prune(self, max_age_hours: float = DEFAULT_PRUNE_AGE_HOURS) -> int:
        return self._store.prune(max_age_hours)

    @property
    def records(self) -> deque:
        return self._store._records

    @property
    def engine_stats(self) -> Dict[Tuple[str, str, str], EngineStats]:
        return self._store._engine_stats


# Global async store instance (preferred for async code)
_async_store = AsyncLearningStore()
# Global sync store instance (for sync contexts)
_store = _async_store._store


def record_scrape(platform: str, engine: str, method: str,
                  success: bool, latency_ms: float,
                  error_type: Optional[str] = None,
                  error_message: Optional[str] = None,
                  query_hash_valid: bool = True) -> None:
    """Record a scrape outcome for learning (sync)."""
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


async def record_scrape_async(platform: str, engine: str, method: str,
                              success: bool, latency_ms: float,
                              error_type: Optional[str] = None,
                              error_message: Optional[str] = None,
                              query_hash_valid: bool = True) -> None:
    """Record a scrape outcome for learning (async with debounced save)."""
    try:
        await _async_store.record_async(ScrapeRecord(
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
        log.debug("async learning record failed: %s", exc)


def get_engine_ranking(platform: str, method: str = "scrape") -> List[Dict[str, Any]]:
    """Get ranked engines for a platform+method, best first."""
    ranked = _store.rank_engines(platform, method)
    return [
        {"engine": eng, "health_score": score,
         "success_rate": _store.get_engine_stats(platform, eng, method).success_rate,
         "p95_latency_ms": _store.get_engine_stats(platform, eng, method).p95_latency_ms,
         "recommended_timeout_ms": _store.get_engine_stats(platform, eng, method).recommended_timeout_ms,
         }
        for eng, score in ranked
    ]


def get_platform_stats(platform: str) -> Dict[str, Any]:
    """Get aggregated stats for a platform across all methods."""
    engines = {}
    methods = set()
    for (plat, eng, meth), es in _store._engine_stats.items():
        if plat == platform:
            engines[f"{eng}::{meth}"] = es.to_dict()
            methods.add(meth)
    total_records = sum(1 for r in _store._records if r.platform == platform)
    recent = [r for r in list(_store._records)[-1000:] if r.platform == platform]
    success_count = sum(1 for r in recent if r.success)
    by_method = {}
    for m in methods:
        method_records = [r for r in recent if r.method == m]
        method_success = sum(1 for r in method_records if r.success)
        by_method[m] = {
            "attempts": len(method_records),
            "success_rate": round(method_success / len(method_records), 3) if method_records else 0.0,
        }
    return {
        "platform": platform,
        "total_records": total_records,
        "recent_attempts": len(recent),
        "recent_success_rate": round(success_count / len(recent), 3) if recent else 0.0,
        "by_method": by_method,
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
    stats = _store.get_engine_stats(platform, "credentials", "auth")
    if stats.consecutive_failures >= 3:
        return True
    return False


def prune_old_records(max_age_hours: float = DEFAULT_PRUNE_AGE_HOURS) -> int:
    """Prune old learning records. Returns count pruned."""
    return _store.prune(max_age_hours)


async def async_prune_old_records(max_age_hours: float = DEFAULT_PRUNE_AGE_HOURS) -> int:
    """Async prune old learning records. Returns count pruned."""
    count = _store.prune(max_age_hours)
    if count > 0:
        await _async_store.save()
    return count


def get_learning_summary() -> Dict[str, Any]:
    """Return a summary of learning data for monitoring."""
    platforms = set(r.platform for r in _store._records)
    return {
        "total_records": len(_store._records),
        "max_memory_records": MAX_MEMORY_RECORDS,
        "platforms_tracked": sorted(platforms),
        "engine_count": len(_store._engine_stats),
        "top_engines": {
            p: _store.rank_engines(p, "scrape")[:3]
            for p in platforms
        },
        "top_all_methods": {
            p: _store.rank_engines_all_methods(p)[:5]
            for p in platforms
        },
    }


async def get_async_learning_summary() -> Dict[str, Any]:
    """Async version of get_learning_summary with pending save."""
    if _async_store._store._dirty:
        await _async_store.save()
    return get_learning_summary()


def reset_learning() -> None:
    """Reset all learning data (admin operation)."""
    global _store
    _store = LearningStore()


async def async_reset_learning() -> None:
    """Reset all learning data (async)."""
    global _store
    _store = LearningStore()
    await _async_store.save()
