"""PostgreSQL persistence layer (asyncpg).

Provides the same interface as SQLite Database for production deployments.
Requires: asyncpg, psycopg2 (for connection pooling).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

from jiro.errors import CacheError


SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    engine     TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'search',
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);

CREATE TABLE IF NOT EXISTS api_keys (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    key_hash       TEXT NOT NULL UNIQUE,
    key_prefix     TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'user',
    scopes         JSONB NOT NULL DEFAULT '[]',
    rate_limit_rpm INTEGER NOT NULL DEFAULT 0,
    created_at     DOUBLE PRECISION NOT NULL,
    revoked        BOOLEAN NOT NULL DEFAULT FALSE,
    last_used_at   DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_keys_prefix ON api_keys(key_prefix);

CREATE TABLE IF NOT EXISTS usage (
    id         SERIAL PRIMARY KEY,
    ts         DOUBLE PRECISION NOT NULL,
    key_id     TEXT,
    endpoint   TEXT NOT NULL,
    engine     TEXT,
    query      TEXT,
    status     INTEGER,
    latency_ms DOUBLE PRECISION,
    tokens_in  INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cached     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS idx_usage_key ON usage(key_id);

CREATE TABLE IF NOT EXISTS semantic_cache (
    query      TEXT PRIMARY KEY,
    embedding  TEXT NOT NULL,
    result_key TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_created ON semantic_cache(created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued',
    payload          JSONB NOT NULL DEFAULT '{}',
    result           JSONB,
    error            TEXT,
    progress         TEXT DEFAULT '',
    webhook_url      TEXT,
    webhook_secret   TEXT,
    webhook_delivered BOOLEAN DEFAULT FALSE,
    created_at       DOUBLE PRECISION NOT NULL,
    completed_at     DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS cookies (
    engine     TEXT NOT NULL,
    name       TEXT NOT NULL,
    value      TEXT NOT NULL,
    domain     TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT '/',
    expires_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (engine, name, domain)
);
CREATE INDEX IF NOT EXISTS idx_cookies_engine ON cookies(engine);
CREATE INDEX IF NOT EXISTS idx_cookies_expires ON cookies(expires_at);

CREATE TABLE IF NOT EXISTS proxy_costs (
    id         SERIAL PRIMARY KEY,
    proxy_url  TEXT NOT NULL,
    request_ts DOUBLE PRECISION NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    status     INTEGER NOT NULL DEFAULT 200,
    cost       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    engine     TEXT,
    success    BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_proxy_costs_url ON proxy_costs(proxy_url);
CREATE INDEX IF NOT EXISTS idx_proxy_costs_ts ON proxy_costs(request_ts);

CREATE TABLE IF NOT EXISTS tos_acknowledgments (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    engine      TEXT NOT NULL,
    tos_version TEXT NOT NULL,
    ip_address  TEXT,
    user_agent  TEXT,
    acknowledged_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tos_user ON tos_acknowledgments(user_id);
CREATE INDEX IF NOT EXISTS idx_tos_user_engine ON tos_acknowledgments(user_id, engine);

-- v0.2 tables
CREATE TABLE IF NOT EXISTS social_cache (
    id           SERIAL PRIMARY KEY,
    platform     TEXT NOT NULL,
    url          TEXT NOT NULL,
    data         JSONB NOT NULL,
    created_at   DOUBLE PRECISION NOT NULL,
    expires_at   DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_cache_platform ON social_cache(platform);
CREATE INDEX IF NOT EXISTS idx_social_cache_url ON social_cache(url);
CREATE INDEX IF NOT EXISTS idx_social_cache_expires ON social_cache(expires_at);

CREATE TABLE IF NOT EXISTS search_history (
    id           SERIAL PRIMARY KEY,
    query        TEXT NOT NULL,
    intent       TEXT,
    target       TEXT,
    engine       TEXT,
    result_count INTEGER DEFAULT 0,
    latency_ms   DOUBLE PRECISION DEFAULT 0,
    user_id      TEXT,
    created_at   DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_history_query ON search_history(query);
CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history(user_id);
CREATE INDEX IF NOT EXISTS idx_search_history_created ON search_history(created_at);
"""


class PostgresDatabase:
    """Async PostgreSQL wrapper using asyncpg connection pool."""

    def __init__(self, dsn: str, *, min_connections: int = 2, max_connections: int = 10) -> None:
        if not HAS_ASYNCPG:
            raise ImportError("asyncpg is required for PostgreSQL backend: pip install asyncpg")
        self.dsn = dsn
        self._min_connections = min_connections
        self._max_connections = max_connections
        self._pool: Optional[asyncpg.Pool] = None
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        try:
            self._pool = await asyncpg.create_pool(
                self.dsn,
                min_size=self._min_connections,
                max_size=self._max_connections,
            )
            self._connected = True
            # Apply schema
            async with self._pool.acquire() as conn:
                await conn.execute(SCHEMA)
        except Exception as exc:
            self._connected = False
            raise CacheError(f"failed to connect to PostgreSQL: {exc}")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._connected = False

    async def execute(self, sql: str, *args: Any) -> str:
        async with self._pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetchone(self, sql: str, *args: Any) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
            return dict(row) if row else None

    async def fetchall(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------ cache
    async def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone(
            "SELECT payload, created_at FROM cache WHERE key = $1 AND expires_at > $2",
            key, time.time(),
        )
        if row is None:
            return None
        return {"payload": json.loads(row["payload"]), "created_at": row["created_at"]}

    async def cache_put(self, key: str, payload: Any, *, engine: str, kind: str,
                        ttl: int) -> None:
        now = time.time()
        await self.execute(
            "INSERT INTO cache (key, payload, engine, kind, created_at, expires_at)"
            " VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (key) DO UPDATE SET"
            " payload = $2, engine = $3, kind = $4, created_at = $5, expires_at = $6",
            key, json.dumps(payload, default=str), engine, kind, now, now + ttl,
        )

    async def cache_stats(self) -> Dict[str, Any]:
        row = await self.fetchone(
            "SELECT COUNT(*) AS entries, SUM(CASE WHEN expires_at > $1 THEN 1 ELSE 0 END) AS live FROM cache",
            time.time(),
        )
        return {"entries": row.get("entries", 0) if row else 0,
                "live": row.get("live", 0) if row else 0}

    async def cache_clear(self) -> int:
        result = await self.execute("DELETE FROM cache")
        # Parse result like "DELETE 5"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    # ----------------------------------------------------------------- api keys
    async def key_create(self, key_id: str, name: str, key_hash: str, prefix: str,
                         role: str, scopes: List[str], rate_limit_rpm: int) -> None:
        await self.execute(
            "INSERT INTO api_keys (id, name, key_hash, key_prefix, role, scopes,"
            " rate_limit_rpm, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            key_id, name, key_hash, prefix, role, json.dumps(scopes),
            rate_limit_rpm, time.time(),
        )

    async def key_get_by_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone(
            "SELECT * FROM api_keys WHERE key_hash = $1 AND revoked = FALSE", key_hash
        )
        return self._key_row(row)

    async def key_get(self, key_id: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone("SELECT * FROM api_keys WHERE id = $1", key_id)
        return self._key_row(row)

    async def key_list(self, include_revoked: bool = False) -> List[Dict[str, Any]]:
        where = "" if include_revoked else " WHERE revoked = FALSE"
        rows = await self.fetchall(f"SELECT * FROM api_keys{where} ORDER BY created_at DESC")
        return [out for out in (self._key_row(r) for r in rows) if out is not None]

    async def key_revoke(self, key_id: str) -> bool:
        await self.execute("UPDATE api_keys SET revoked = TRUE WHERE id = $1", key_id)
        return True

    async def key_touch(self, key_id: str) -> None:
        await self.execute("UPDATE api_keys SET last_used_at = $1 WHERE id = $2",
                           time.time(), key_id)

    @staticmethod
    def _key_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        if isinstance(row.get("scopes"), str):
            row["scopes"] = json.loads(row["scopes"])
        row["revoked"] = bool(row.get("revoked"))
        return row

    # ------------------------------------------------------------------- usage
    async def usage_add(self, *, key_id: Optional[str], endpoint: str,
                        engine: Optional[str] = None, query: Optional[str] = None,
                        status: int = 200, latency_ms: float = 0.0,
                        tokens_in: int = 0, tokens_out: int = 0,
                        cached: bool = False) -> None:
        await self.execute(
            "INSERT INTO usage (ts, key_id, endpoint, engine, query, status, latency_ms,"
            " tokens_in, tokens_out, cached) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            time.time(), key_id, endpoint, engine, query, status, latency_ms,
            tokens_in, tokens_out, int(cached),
        )

    async def usage_summary(self, key_id: Optional[str] = None,
                            since: float = 0.0) -> Dict[str, Any]:
        where = " WHERE ts >= $1"
        params: list[Any] = [since]
        if key_id:
            where += " AND key_id = $2"
            params.append(key_id)
        total = await self.fetchone(
            f"SELECT COUNT(*) AS n, SUM(tokens_in) AS ti, SUM(tokens_out) AS to_,"
            f" SUM(cached) AS c FROM usage{where}",
            *params,
        )
        by_endpoint = await self.fetchall(
            f"SELECT endpoint, COUNT(*) AS n FROM usage{where} GROUP BY endpoint"
            f" ORDER BY n DESC",
            *params,
        )
        return {
            "requests": total["n"] if total else 0,
            "tokens_in": total["ti"] if total else 0,
            "tokens_out": total["to_"] if total else 0,
            "cached": total["c"] if total else 0,
            "by_endpoint": by_endpoint,
        }

    # ------------------------------------------------------------------ cookies
    async def cookie_load_all(self) -> Dict[str, Dict[str, str]]:
        rows = await self.fetchall(
            "SELECT engine, name, value FROM cookies WHERE expires_at = 0 OR expires_at > $1",
            time.time(),
        )
        result: Dict[str, Dict[str, str]] = {}
        for row in rows:
            eng = row["engine"]
            if eng not in result:
                result[eng] = {}
            result[eng][row["name"]] = row["value"]
        return result

    async def cookie_save(self, engine: str, cookies: Dict[str, str],
                          ttl: int = 86400) -> None:
        now = time.time()
        expires = now + ttl
        for name, value in cookies.items():
            await self.execute(
                "INSERT INTO cookies (engine, name, value, created_at, expires_at)"
                " VALUES ($1, $2, $3, $4, $5) ON CONFLICT (engine, name, domain) DO UPDATE SET"
                " value = $3, created_at = $4, expires_at = $5",
                engine, name, value, now, expires,
            )

    async def cookie_clear(self, engine: Optional[str] = None) -> int:
        if engine:
            result = await self.execute("DELETE FROM cookies WHERE engine = $1", engine)
        else:
            result = await self.execute("DELETE FROM cookies")
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    # ------------------------------------------------------------- proxy costs
    async def proxy_cost_add(self, *, proxy_url: str, latency_ms: float,
                             status: int, cost: float, engine: Optional[str] = None,
                             success: bool = True) -> None:
        await self.execute(
            "INSERT INTO proxy_costs (proxy_url, request_ts, latency_ms, status,"
            " cost, engine, success) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            proxy_url, time.time(), latency_ms, status, cost, engine, success,
        )

    async def proxy_cost_summary(self, proxy_url: Optional[str] = None,
                                 since: float = 0.0) -> Dict[str, Any]:
        where = " WHERE request_ts >= $1"
        params: list[Any] = [since]
        if proxy_url:
            where += " AND proxy_url = $2"
            params.append(proxy_url)
        total = await self.fetchone(
            f"SELECT COUNT(*) AS n, SUM(cost) AS total_cost,"
            f" AVG(latency_ms) AS avg_latency,"
            f" SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successes"
            f" FROM proxy_costs{where}",
            *params,
        )
        by_proxy = await self.fetchall(
            f"SELECT proxy_url, COUNT(*) AS n, SUM(cost) AS total_cost,"
            f" AVG(latency_ms) AS avg_latency"
            f" FROM proxy_costs{where} GROUP BY proxy_url ORDER BY n DESC",
            *params,
        )
        return {
            "total_requests": total["n"] if total else 0,
            "total_cost": round(total["total_cost"] or 0, 6) if total else 0,
            "avg_latency_ms": round(total["avg_latency"] or 0, 1) if total else 0,
            "successes": total["successes"] if total else 0,
            "by_proxy": by_proxy,
        }

    # ---------------------------------------------------- tos acknowledgments
    async def tos_ack_create(self, ack: Dict[str, Any]) -> None:
        await self.execute(
            "INSERT INTO tos_acknowledgments"
            " (id, user_id, engine, tos_version, ip_address, user_agent, acknowledged_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (id) DO NOTHING",
            ack.get("id"),
            ack.get("user_id"),
            ack.get("engine"),
            ack.get("tos_version"),
            ack.get("ip_address"),
            ack.get("user_agent"),
            ack.get("acknowledged_at", time.time()),
        )

    async def tos_ack_get(self, user_id: str, engine: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone(
            "SELECT * FROM tos_acknowledgments WHERE user_id = $1 AND engine = $2"
            " ORDER BY acknowledged_at DESC LIMIT 1",
            user_id, engine,
        )
        return dict(row) if row else None

    # ----------------------------------------------------------- social cache (v0.2)
    async def social_cache_get(self, url: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone(
            "SELECT data, created_at FROM social_cache WHERE url = $1 AND expires_at > $2",
            url, time.time(),
        )
        if row is None:
            return None
        return {"data": json.loads(row["data"]) if isinstance(row["data"], str) else row["data"],
                "created_at": row["created_at"]}

    async def social_cache_put(self, platform: str, url: str, data: Any,
                               ttl: int = 3600) -> None:
        now = time.time()
        await self.execute(
            "INSERT INTO social_cache (platform, url, data, created_at, expires_at)"
            " VALUES ($1, $2, $3, $4, $5) ON CONFLICT (url) DO UPDATE SET"
            " platform = $1, data = $3, created_at = $4, expires_at = $5",
            platform, url, json.dumps(data, default=str), now, now + ttl,
        )

    async def social_cache_clear(self, platform: Optional[str] = None) -> int:
        if platform:
            result = await self.execute("DELETE FROM social_cache WHERE platform = $1", platform)
        else:
            result = await self.execute("DELETE FROM social_cache")
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    # ----------------------------------------------------------- search history (v0.2)
    async def search_history_add(self, *, query: str, intent: Optional[str] = None,
                                 target: Optional[str] = None, engine: Optional[str] = None,
                                 result_count: int = 0, latency_ms: float = 0,
                                 user_id: Optional[str] = None) -> None:
        await self.execute(
            "INSERT INTO search_history (query, intent, target, engine, result_count,"
            " latency_ms, user_id, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            query, intent, target, engine, result_count, latency_ms, user_id, time.time(),
        )

    async def search_history_recent(self, limit: int = 50,
                                    user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if user_id:
            return await self.fetchall(
                "SELECT * FROM search_history WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
                user_id, limit,
            )
        return await self.fetchall(
            "SELECT * FROM search_history ORDER BY created_at DESC LIMIT $1", limit
        )

    async def search_history_stats(self, since: float = 0.0) -> Dict[str, Any]:
        row = await self.fetchone(
            "SELECT COUNT(*) AS n, AVG(latency_ms) AS avg_latency,"
            " SUM(result_count) AS total_results FROM search_history WHERE created_at >= $1",
            since,
        )
        return {
            "total_searches": row["n"] if row else 0,
            "avg_latency_ms": round(row["avg_latency"] or 0, 1) if row else 0,
            "total_results": row["total_results"] if row else 0,
        }