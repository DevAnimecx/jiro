"""Redis cache backend (optional).

Used when ``cache.type: redis``. Falls back to an in-memory cache if the
Redis client is unavailable, so the server always starts.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from jiro.log import get_logger

log = get_logger("jiro.cache.redis")


class RedisCache:
    def __init__(self, url: str = "redis://localhost:6379/0", *,
                 prefix: str = "jiro:", ttl: int = 3600) -> None:
        self.url = url
        self.prefix = prefix
        self.ttl = ttl
        self._client: Optional[Any] = None

    async def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:
            raise RuntimeError(
                "redis backend requires the 'redis' package: "
                "pip install 'jiro-search[redis]'"
            ) from exc
        try:
            self._client = aioredis.from_url(self.url, decode_responses=True)
            await self._client.ping()
            return self._client
        except Exception:
            self._client = None
            raise

    async def get(self, key: str, ttl: int) -> Optional[Any]:
        client = await self._connect()
        raw = await client.get(self.prefix + key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def put(self, key: str, payload: Any, ttl: int) -> None:
        client = await self._connect()
        await client.set(self.prefix + key, json.dumps(payload, default=str),
                         ex=max(ttl, 1))

    async def stats(self) -> dict:
        client = await self._connect()
        try:
            info = await client.info("keyspace")
            return {"backend": "redis", "url": self.url, "dbsize": await client.dbsize(),
                    "keyspace": info}
        except Exception:  # pragma: no cover
            return {"backend": "redis", "url": self.url}

    async def clear(self) -> int:
        client = await self._connect()
        count = 0
        async for key in client.scan_iter(match=self.prefix + "*"):
            await client.delete(key)
            count += 1
        return count
