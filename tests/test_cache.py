"""Cache tests."""

from __future__ import annotations

import time

import pytest

from jiro.cache import CacheManager
from jiro.db import Database


@pytest.mark.asyncio
async def test_cache_roundtrip(settings):
    db = Database(settings.db_path)
    await db.connect()
    try:
        cache = CacheManager(db, ttl=60)
        key = cache.make_key("search", "bing", "hello", 3)
        assert await cache.get(key) is None
        await cache.put(key, {"organic_results": [1]}, engine="bing")
        assert await cache.get(key) == {"organic_results": [1]}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cache_ttl(settings):
    db = Database(settings.db_path)
    await db.connect()
    try:
        cache = CacheManager(db, ttl=1)
        key = cache.make_key("k", 1)
        await cache.put(key, "value")
        assert await cache.get(key) == "value"
        time.sleep(1.2)
        assert await cache.get(key) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_memory_cache(settings):
    cache = CacheManager(None, memory=True, ttl=60)
    key = cache.make_key("a", 1)
    await cache.put(key, {"x": 1})
    assert await cache.get(key) == {"x": 1}


def test_cache_key_stable():
    k1 = CacheManager.make_key("search", "bing", "hello world", 3, 0)
    k2 = CacheManager.make_key("search", "bing", "hello world", 3, 0)
    k3 = CacheManager.make_key("search", "bing", "hello world", 5, 0)
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 64
