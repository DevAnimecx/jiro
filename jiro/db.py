"""SQLite persistence layer (aiosqlite, WAL mode).

Tables:
  * ``cache``    — key/value search & scrape cache with TTL
  * ``api_keys`` — hashed API keys with roles/scopes
  * ``usage``    — per-request usage accounting
  * ``cookies``  — per-engine persistent cookies
  * ``tos_acknowledgments`` — legal ToS acceptance records
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from jiro.errors import CacheError

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    engine     TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'search',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);

CREATE TABLE IF NOT EXISTS api_keys (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    key_hash       TEXT NOT NULL UNIQUE,
    key_prefix     TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'user',
    scopes         TEXT NOT NULL DEFAULT '["search","scrape","ai"]',
    rate_limit_rpm INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    revoked        INTEGER NOT NULL DEFAULT 0,
    last_used_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_keys_prefix ON api_keys(key_prefix);

CREATE TABLE IF NOT EXISTS usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    key_id     TEXT,
    endpoint   TEXT NOT NULL,
    engine     TEXT,
    query      TEXT,
    status     INTEGER,
    latency_ms REAL,
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
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_created ON semantic_cache(created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued',
    payload          TEXT NOT NULL DEFAULT '{}',
    result           TEXT,
    error            TEXT,
    progress         TEXT DEFAULT '',
    webhook_url      TEXT,
    webhook_secret   TEXT,
    webhook_delivered INTEGER DEFAULT 0,
    created_at       REAL NOT NULL,
    completed_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS cookies (
    engine     TEXT NOT NULL,
    name       TEXT NOT NULL,
    value      TEXT NOT NULL,
    domain     TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT '/',
    expires_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    PRIMARY KEY (engine, name, domain)
);
CREATE INDEX IF NOT EXISTS idx_cookies_engine ON cookies(engine);
CREATE INDEX IF NOT EXISTS idx_cookies_expires ON cookies(expires_at);

CREATE TABLE IF NOT EXISTS proxy_costs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy_url  TEXT NOT NULL,
    request_ts REAL NOT NULL,
    latency_ms REAL NOT NULL DEFAULT 0,
    status     INTEGER NOT NULL DEFAULT 200,
    cost       REAL NOT NULL DEFAULT 0.0,
    engine     TEXT,
    success    INTEGER NOT NULL DEFAULT 1
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
    acknowledged_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tos_user ON tos_acknowledgments(user_id);
CREATE INDEX IF NOT EXISTS idx_tos_user_engine ON tos_acknowledgments(user_id, engine);
"""


class Database:
    """Async SQLite wrapper with a small connection pool.

    SQLite allows many concurrent *readers* but only one *writer* at a time.
    We keep a pool of connections (so reads can run concurrently) and serialize
    writes through a single async lock. For horizontally-scaled (multi-replica)
    deployments use PostgreSQL — see the roadmap — and run the Helm chart with a
    single replica when using the built-in SQLite backend.
    """

    # Schema migrations are applied in order, once each, tracked by `schema_version`.
    MIGRATIONS: List[str] = [
        # Future ALTER TABLE statements go here, e.g.:
        # "ALTER TABLE api_keys ADD COLUMN notes TEXT NOT NULL DEFAULT '';",
    ]

    def __init__(self, path: str, *, pool_size: int = 4) -> None:
        self.path = path
        # `:memory:` databases are per-connection; force a single shared
        # connection so writes are visible to subsequent reads.
        self._is_memory = path == ":memory:"
        self._pool_size = 1 if self._is_memory else max(1, pool_size)
        self._pool: List[aiosqlite.Connection] = []
        self._available: Optional["asyncio.Queue[aiosqlite.Connection]"] = None
        self._writer_lock = asyncio.Lock()
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        if not self._is_memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._available = asyncio.Queue()
        try:
            for _ in range(self._pool_size):
                conn = await aiosqlite.connect(self.path)
                conn.row_factory = aiosqlite.Row
                if not self._is_memory:
                    await conn.execute("PRAGMA journal_mode=WAL")
                    await conn.execute("PRAGMA synchronous=NORMAL")
                await conn.execute("PRAGMA foreign_keys=ON")
                self._pool.append(conn)
                await self._available.put(conn)
            self._connected = True
            # Apply schema + migrations on a single connection.
            primary = await self._acquire()
            try:
                await primary.executescript(SCHEMA)
                await self._apply_migrations(primary)
                await primary.commit()
            finally:
                await self._release(primary)
        except Exception as exc:  # pragma: no cover - defensive
            self._connected = False
            raise CacheError(f"failed to open database {self.path}: {exc}")

    async def _apply_migrations(self, conn: "aiosqlite.Connection") -> None:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        row = await (await conn.execute("SELECT MAX(version) AS v FROM schema_version")).fetchone()
        current = int(row["v"]) if row and row["v"] is not None else 0
        for idx, sql in enumerate(self.MIGRATIONS, start=1):
            if idx <= current:
                continue
            await conn.executescript(sql)
            await conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (idx, time.time()),
            )

    async def close(self) -> None:
        if self._available is None:
            self._pool.clear()
            self._connected = False
            return
        while not self._available.empty():
            try:
                conn = self._available.get_nowait()
            except Exception:
                break
            try:
                await conn.close()
            except Exception:
                pass
        self._pool.clear()
        self._connected = False

    async def _acquire(self) -> "aiosqlite.Connection":
        if not self._connected:
            raise CacheError("database not connected")
        return await self._available.get()

    async def _release(self, conn: "aiosqlite.Connection") -> None:
        await self._available.put(conn)

    async def execute(self, sql: str, params: tuple = ()) -> None:
        async with self._writer_lock:
            conn = await self._acquire()
            try:
                async with conn.execute(sql, params):
                    await conn.commit()
            finally:
                await self._release(conn)

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        conn = await self._acquire()
        try:
            cur = await conn.execute(sql, params)
            row = await cur.fetchone()
            await cur.close()
            return dict(row) if row else None
        finally:
            await self._release(conn)

    async def fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        conn = await self._acquire()
        try:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
            await cur.close()
            return [dict(r) for r in rows]
        finally:
            await self._release(conn)

    async def _require(self) -> None:
        if not self._connected:
            raise CacheError("database not connected")

    # ------------------------------------------------------------------ cache
    async def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone(
            "SELECT payload, created_at FROM cache WHERE key = ? AND expires_at > ?",
            (key, time.time()),
        )
        if row is None:
            return None
        return {"payload": json.loads(row["payload"]), "created_at": row["created_at"]}

    async def cache_put(self, key: str, payload: Any, *, engine: str, kind: str,
                        ttl: int) -> None:
        now = time.time()
        await self.execute(
            "INSERT OR REPLACE INTO cache (key, payload, engine, kind, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (key, json.dumps(payload, default=str), engine, kind, now, now + ttl),
        )

    async def cache_stats(self) -> Dict[str, Any]:
        row = await self.fetchone(
            "SELECT COUNT(*) AS entries, COALESCE(SUM(expires_at > ?), 0) AS live FROM cache",
            (time.time(),),
        )
        return {"entries": row.get("entries", 0) if row else 0,
                "live": row.get("live", 0) if row else 0}

    async def cache_clear(self) -> int:
        await self.execute("DELETE FROM cache")
        row = await self.fetchone("SELECT changes() AS n")
        return int(row.get("n", 0)) if row else 0

    # ----------------------------------------------------------------- api keys
    async def key_create(self, key_id: str, name: str, key_hash: str, prefix: str,
                         role: str, scopes: List[str], rate_limit_rpm: int) -> None:
        await self.execute(
            "INSERT INTO api_keys (id, name, key_hash, key_prefix, role, scopes,"
            " rate_limit_rpm, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key_id, name, key_hash, prefix, role, json.dumps(scopes),
             rate_limit_rpm, time.time()),
        )

    async def key_get_by_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0", (key_hash,)
        )
        return self._key_row(row)

    async def key_get(self, key_id: str) -> Optional[Dict[str, Any]]:
        row = await self.fetchone("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        return self._key_row(row)

    async def key_list(self, include_revoked: bool = False) -> List[Dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT * FROM api_keys" + ("" if include_revoked else " WHERE revoked = 0")
            + " ORDER BY created_at DESC"
        )
        return [self._key_row(r) for r in rows]

    async def key_revoke(self, key_id: str) -> bool:
        await self.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
        return True

    async def key_touch(self, key_id: str) -> None:
        await self.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                           (time.time(), key_id))

    @staticmethod
    def _key_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        row["scopes"] = json.loads(row.get("scopes") or "[]")
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
            " tokens_in, tokens_out, cached) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), key_id, endpoint, engine, query, status, latency_ms,
             tokens_in, tokens_out, int(cached)),
        )

    async def usage_summary(self, key_id: Optional[str] = None,
                            since: float = 0.0) -> Dict[str, Any]:
        where, params = " WHERE ts >= ?", (since,)
        if key_id:
            where += " AND key_id = ?"
            params += (key_id,)
        total = await self.fetchone(
            f"SELECT COUNT(*) AS n, SUM(tokens_in) AS ti, SUM(tokens_out) AS to_,"
            f" SUM(cached) AS c FROM usage{where}",
            params,
        )
        by_endpoint = await self.fetchall(
            f"SELECT endpoint, COUNT(*) AS n FROM usage{where} GROUP BY endpoint"
            f" ORDER BY n DESC",
            params,
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
        """Load all non-expired cookies grouped by engine."""
        rows = await self.fetchall(
            "SELECT engine, name, value FROM cookies WHERE expires_at = 0 OR expires_at > ?",
            (time.time(),),
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
        """Save cookies for an engine with a TTL."""
        now = time.time()
        expires = now + ttl
        for name, value in cookies.items():
            await self.execute(
                "INSERT OR REPLACE INTO cookies (engine, name, value, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (engine, name, value, now, expires),
            )

    async def cookie_clear(self, engine: Optional[str] = None) -> int:
        """Delete cookies for an engine or all engines."""
        if engine:
            await self.execute("DELETE FROM cookies WHERE engine = ?", (engine,))
        else:
            await self.execute("DELETE FROM cookies")
        row = await self.fetchone("SELECT changes() AS n")
        return int(row.get("n", 0)) if row else 0

    # ------------------------------------------------------------- proxy costs
    async def proxy_cost_add(self, *, proxy_url: str, latency_ms: float,
                             status: int, cost: float, engine: Optional[str] = None,
                             success: bool = True) -> None:
        await self.execute(
            "INSERT INTO proxy_costs (proxy_url, request_ts, latency_ms, status,"
            " cost, engine, success) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (proxy_url, time.time(), latency_ms, status, cost, engine, int(success)),
        )

    async def proxy_cost_summary(self, proxy_url: Optional[str] = None,
                                 since: float = 0.0) -> Dict[str, Any]:
        where, params = " WHERE request_ts >= ?", (since,)
        if proxy_url:
            where += " AND proxy_url = ?"
            params += (proxy_url,)
        total = await self.fetchone(
            f"SELECT COUNT(*) AS n, SUM(cost) AS total_cost,"
            f" AVG(latency_ms) AS avg_latency,"
            f" SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes"
            f" FROM proxy_costs{where}",
            params,
        )
        by_proxy = await self.fetchall(
            f"SELECT proxy_url, COUNT(*) AS n, SUM(cost) AS total_cost,"
            f" AVG(latency_ms) AS avg_latency"
            f" FROM proxy_costs{where} GROUP BY proxy_url ORDER BY n DESC",
            params,
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
        """Persist a ToS acknowledgment record (legal record-keeping)."""
        await self.execute(
            "INSERT OR REPLACE INTO tos_acknowledgments"
            " (id, user_id, engine, tos_version, ip_address, user_agent, acknowledged_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ack.get("id"),
                ack.get("user_id"),
                ack.get("engine"),
                ack.get("tos_version"),
                ack.get("ip_address"),
                ack.get("user_agent"),
                ack.get("acknowledged_at", time.time()),
            ),
        )

    async def tos_ack_get(self, user_id: str, engine: str) -> Optional[Dict[str, Any]]:
        """Return the latest ToS acknowledgment for a user+engine, if any."""
        row = await self.fetchone(
            "SELECT * FROM tos_acknowledgments WHERE user_id = ? AND engine = ?"
            " ORDER BY acknowledged_at DESC LIMIT 1",
            (user_id, engine),
        )
        return dict(row) if row else None
