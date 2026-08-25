"""Auth & API key tests."""

from __future__ import annotations

import pytest

from jiro.auth import AuthManager, generate_api_key, hash_key, key_prefix_of
from jiro.db import Database
from jiro.errors import AuthError, PermissionError, RateLimitError


@pytest.mark.asyncio
async def test_key_lifecycle(settings):
    db = Database(settings.db_path)
    await db.connect()
    try:
        auth = AuthManager(settings, db)
        created = await auth.create_key("test", role="user")
        api_key = created["api_key"]
        assert api_key.startswith("jsk_")
        record = await auth.authenticate(api_key)
        assert record["id"] == created["id"]
        assert record["role"] == "user"
        await auth.authorize(record, scope="search")  # allowed
        with pytest.raises(PermissionError):
            await auth.authorize(record, scope="admin")
        # revoke
        await db.key_revoke(created["id"])
        with pytest.raises(AuthError):
            await auth.authenticate(api_key)
    finally:
        await db.close()


def test_hash_storage():
    key = generate_api_key()
    assert hash_key(key) != key
    assert len(hash_key(key)) == 64
    assert key_prefix_of(key) == key[: len("jsk_") + 8]


@pytest.mark.asyncio
async def test_rate_limit(settings):
    db = Database(settings.db_path)
    await db.connect()
    try:
        settings.raw["auth"]["rate_limit_rpm"] = 3
        auth = AuthManager(settings, db)
        for _ in range(3):
            auth.check_rate_limit("key:x")
        with pytest.raises(RateLimitError):
            auth.check_rate_limit("key:x")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_jwt_issue_and_decode(settings):
    db = Database(settings.db_path)
    await db.connect()
    try:
        settings.raw["auth"]["jwt_secret"] = "test-secret"
        auth = AuthManager(settings, db)
        created = await auth.create_key("jwt-user", role="user")
        token = await auth.issue_token(created["api_key"])
        claims = auth.decode_token(token["access_token"])
        assert claims["sub"] == created["id"]
        assert claims["role"] == "user"
    finally:
        await db.close()
