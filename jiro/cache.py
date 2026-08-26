"""Cache manager — SQLite by default, in-memory LRU optional.

Keys are sha256 digests of the normalized request, so cached responses can be
served in well under 50 ms (PRD §6.7).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Optional

from jiro.db import Database
from jiro.log import get_logger

if TYPE_CHECKING:
    from jiro.redis_cache import RedisCache

log = get_logger("jiro.cache")


class MemoryCache:
    def __init__(self, max_entries: int = 4096) -> None:
        self._store: "OrderedDict[str, tuple[Any, float]]" = OrderedDict()
        self.max_entries = max_entries

    def get(self, key: str, ttl: int) -> Optional[Any]:
        item = self._store.get(key)
        if item is None:
            return None
        payload, expires = item
        if time.time() > expires:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return payload

    def put(self, key: str, payload: Any, ttl: int) -> None:
        self._store[key] = (payload, time.time() + ttl)
        self._store.move_to_end(key)
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)


class CacheManager:
    """SQLite / Redis / in-memory cache. Redis falls back to memory if unavailable."""

    def __init__(self, db: Optional[Database] = None, *, memory: bool = False,
                 redis: Optional["RedisCache"] = None, ttl: int = 3600) -> None:
        self.db = db
        self.memory = memory
        self.ttl = ttl
        self._mem: Optional[MemoryCache] = MemoryCache() if memory else None
        self._redis: Optional[Any] = redis
        self._backend = "memory" if memory else ("redis" if redis else "sqlite")

    @staticmethod
    def make_key(*parts: Any) -> str:
        raw = "|".join(str(p) for p in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def get(self, key: str, ttl: Optional[int] = None) -> Optional[Any]:
        ttl = ttl if ttl is not None else self.ttl
        if self._redis is not None:
            try:
                hit = await self._redis.get(key, ttl)
                if hit is not None:
                    return hit
            except Exception as exc:  # pragma: no cover - degraded mode
                log.warning("redis cache read failed, degrading to memory", extra={"error": str(exc)})
                self._redis = None
                self._mem = self._mem or MemoryCache()
        if self._mem is not None:
            hit = self._mem.get(key, ttl)
            if hit is not None:
                return hit
        if self.db is not None:
            row = await self.db.cache_get(key)
            if row is not None:
                return row["payload"]
        return None

    async def put(self, key: str, payload: Any, *, engine: str = "",
                  kind: str = "search", ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self.ttl
        if self._redis is not None:
            try:
                await self._redis.put(key, payload, ttl)
                return
            except Exception as exc:  # pragma: no cover
                log.warning("redis cache write failed, degrading to memory", extra={"error": str(exc)})
                self._redis = None
                self._mem = self._mem or MemoryCache()
        if self._mem is not None:
            self._mem.put(key, payload, ttl)
        if self.db is not None:
            await self.db.cache_put(key, payload, engine=engine, kind=kind, ttl=ttl)

    async def stats(self) -> dict:
        if self._redis is not None:
            try:
                return await self._redis.stats()
            except Exception:
                pass
        stats: dict = {"backend": "memory" if self.memory else "sqlite", "ttl": self.ttl}
        if self.db is not None:
            stats.update(await self.db.cache_stats())
        return stats

    async def clear(self) -> int:
        if self._redis is not None:
            try:
                return await self._redis.clear()
            except Exception:
                pass
        if self._mem is not None:
            self._mem._store.clear()  # noqa: SLF001
        if self.db is not None:
            return await self.db.cache_clear()
        return 0

    def to_json(self) -> str:
        return json.dumps({"ttl": self.ttl})
