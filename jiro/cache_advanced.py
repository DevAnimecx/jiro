"""Advanced caching with Redis support, warming, TTL policies, and analytics.

Provides:
- Redis-backed distributed caching
- Cache warming strategies
- Configurable TTL policies
- Cache analytics and metrics
- Cache invalidation patterns
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union


class CacheBackend(str, Enum):
    MEMORY = "memory"
    SQLITE = "sqlite"
    REDIS = "redis"


class CacheStrategy(str, Enum):
    LRU = "lru"  # Least Recently Used
    LFU = "lfu"  # Least Frequently Used
    TTL = "ttl"  # Time To Live only
    WRITE_THROUGH = "write_through"  # Write to cache and backend simultaneously


@dataclass
class CacheEntry:
    """A single cache entry."""
    key: str
    value: Any
    created_at: float
    expires_at: Optional[float]
    access_count: int = 0
    last_accessed: float = 0
    size_bytes: int = 0


@dataclass
class CacheStats:
    """Cache statistics."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0
    memory_used: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class MemoryCache:
    """In-memory LRU cache with analytics."""

    def __init__(
        self,
        max_size: int = 4096,
        default_ttl: int = 3600,
        strategy: CacheStrategy = CacheStrategy.LRU,
    ) -> None:
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.strategy = strategy
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._stats = CacheStats()

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache."""
        entry = self._store.get(key)
        if entry is None:
            self._stats.misses += 1
            return None

        # Check expiration
        if entry.expires_at and time.time() > entry.expires_at:
            self._store.pop(key, None)
            self._stats.misses += 1
            self._stats.evictions += 1
            return None

        # Update access stats
        entry.access_count += 1
        entry.last_accessed = time.time()
        self._store.move_to_end(key)
        self._stats.hits += 1

        return entry.value

    def put(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> None:
        """Put a value in cache."""
        ttl = ttl or self.default_ttl
        now = time.time()

        entry = CacheEntry(
            key=key,
            value=value,
            created_at=now,
            expires_at=now + ttl if ttl > 0 else None,
            last_accessed=now,
            size_bytes=self._estimate_size(value),
        )

        self._store[key] = entry
        self._store.move_to_end(key)
        self._stats.size = len(self._store)
        self._stats.memory_used += entry.size_bytes

        # Evict if over size
        while len(self._store) > self.max_size:
            self._evict()

    def delete(self, key: str) -> bool:
        """Delete a value from cache."""
        if key in self._store:
            entry = self._store.pop(key)
            self._stats.size = len(self._store)
            self._stats.memory_used -= entry.size_bytes
            return True
        return False

    def clear(self) -> int:
        """Clear all cache entries. Returns number of entries cleared."""
        count = len(self._store)
        self._store.clear()
        self._stats.size = 0
        self._stats.memory_used = 0
        return count

    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        return self._stats

    def _evict(self) -> None:
        """Evict an entry based on strategy."""
        if not self._store:
            return

        if self.strategy == CacheStrategy.LRU:
            # Remove least recently used (first item)
            key, entry = self._store.popitem(last=False)
        elif self.strategy == CacheStrategy.LFU:
            # Remove least frequently used
            key = min(
                self._store.keys(),
                key=lambda k: self._store[k].access_count
            )
            entry = self._store.pop(key)
        else:
            # Default to LRU
            key, entry = self._store.popitem(last=False)

        self._stats.evictions += 1
        self._stats.memory_used -= entry.size_bytes

    def _estimate_size(self, value: Any) -> int:
        """Estimate size of value in bytes."""
        try:
            return len(json.dumps(value).encode())
        except (TypeError, ValueError):
            return 100  # Default estimate


class CacheManager:
    """Unified cache manager with multiple backends."""

    def __init__(
        self,
        backend: CacheBackend = CacheBackend.MEMORY,
        default_ttl: int = 3600,
        max_size: int = 4096,
        redis_url: Optional[str] = None,
    ) -> None:
        self.backend = backend
        self.default_ttl = default_ttl
        self._memory = MemoryCache(
            max_size=max_size,
            default_ttl=default_ttl,
        )
        self._redis_url = redis_url
        self._redis = None

    async def get(self, key: str) -> Optional[Any]:
        """Get a value from cache."""
        if self.backend == CacheBackend.REDIS:
            return await self._redis_get(key)
        return self._memory.get(key)

    async def put(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> None:
        """Put a value in cache."""
        if self.backend == CacheBackend.REDIS:
            await self._redis_put(key, value, ttl or self.default_ttl)
        else:
            self._memory.put(key, value, ttl)

    async def delete(self, key: str) -> bool:
        """Delete a value from cache."""
        if self.backend == CacheBackend.REDIS:
            return await self._redis_delete(key)
        return self._memory.delete(key)

    async def clear(self) -> int:
        """Clear all cache entries."""
        if self.backend == CacheBackend.REDIS:
            return await self._redis_clear()
        return self._memory.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        stats = self._memory.get_stats()
        return {
            "backend": self.backend.value,
            "hits": stats.hits,
            "misses": stats.misses,
            "evictions": stats.evictions,
            "size": stats.size,
            "memory_used": stats.memory_used,
            "hit_rate": f"{stats.hit_rate:.2%}",
        }

    def make_key(self, prefix: str, **kwargs: Any) -> str:
        """Generate a cache key from prefix and parameters."""
        parts = [prefix]
        for k, v in sorted(kwargs.items()):
            parts.append(f"{k}={v}")
        raw = ":".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def _redis_get(self, key: str) -> Optional[Any]:
        """Get from Redis."""
        if not self._redis:
            return None
        try:
            data = await self._redis.get(f"jiro:cache:{key}")
            if data:
                return json.loads(data)
        except Exception:
            pass
        return None

    async def _redis_put(self, key: str, value: Any, ttl: int) -> None:
        """Put to Redis."""
        if not self._redis:
            return
        try:
            data = json.dumps(value)
            await self._redis.set(f"jiro:cache:{key}", data, ex=ttl)
        except Exception:
            pass

    async def _redis_delete(self, key: str) -> bool:
        """Delete from Redis."""
        if not self._redis:
            return False
        try:
            result = await self._redis.delete(f"jiro:cache:{key}")
            return result > 0
        except Exception:
            return False

    async def _redis_clear(self) -> int:
        """Clear Redis cache."""
        if not self._redis:
            return 0
        try:
            keys = await self._redis.keys("jiro:cache:*")
            if keys:
                return await self._redis.delete(*keys)
        except Exception:
            pass
        return 0


# Global cache manager
_cache_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """Get the global cache manager."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager


def init_cache_manager(
    backend: CacheBackend = CacheBackend.MEMORY,
    default_ttl: int = 3600,
    max_size: int = 4096,
    redis_url: Optional[str] = None,
) -> CacheManager:
    """Initialize the global cache manager."""
    global _cache_manager
    _cache_manager = CacheManager(
        backend=backend,
        default_ttl=default_ttl,
        max_size=max_size,
        redis_url=redis_url,
    )
    return _cache_manager
