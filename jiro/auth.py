"""Authentication & authorization.

* API keys — full keys look like ``jsk_<32 hex>``; only SHA-256 digests are
  stored at rest (PRD §7.1). Lookup by 8-char prefix + full digest.
* JWT — optional bearer tokens minted from an admin/user API key.
* Rate limiting — sliding window per key / per IP (in-memory).
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional

import jwt

from jiro.config import Settings
from jiro.errors import AuthError, PermissionError, RateLimitError
from jiro.db import Database

KEY_PREFIX = "jsk_"
KEY_ID_PREFIX = "key_"


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return KEY_PREFIX + secrets.token_hex(16)


def generate_key_id() -> str:
    return KEY_ID_PREFIX + uuid.uuid4().hex[:16]


def key_prefix_of(api_key: str) -> str:
    return api_key[: len(KEY_PREFIX) + 8]


class AuthManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._windows: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------ keys
    async def create_key(self, name: str, role: str = "user",
                         scopes: Optional[List[str]] = None,
                         rate_limit_rpm: int = 0) -> Dict[str, Any]:
        role = role if role in ("admin", "user") else "user"
        api_key = generate_api_key()
        key_id = generate_key_id()
        await self.db.key_create(
            key_id=key_id,
            name=name,
            key_hash=hash_key(api_key),
            prefix=key_prefix_of(api_key),
            role=role,
            scopes=scopes or ["search", "scrape", "ai"],
            rate_limit_rpm=rate_limit_rpm,
        )
        return {"id": key_id, "api_key": api_key, "name": name, "role": role}

    async def authenticate(self, api_key: str) -> Dict[str, Any]:
        """Resolve an API key to its record; raises AuthError if invalid."""
        key_hash = hash_key(api_key)
        record = await self.db.key_get_by_hash(key_hash)
        if record is None:
            raise AuthError("invalid API key", details={"hint": "check your key"})
        await self.db.key_touch(record["id"])
        return record

    async def authorize(self, record: Dict[str, Any], *, scope: str = "search") -> None:
        if record.get("revoked"):
            raise AuthError("API key revoked")
        if record.get("role") == "admin":
            return
        if scope == "admin":
            raise PermissionError(
                "admin scope requires an admin role",
                details={"required": scope, "role": record.get("role")},
            )
        scopes: List[str] = record.get("scopes") or []
        if scope not in scopes:
            raise PermissionError(
                f"API key lacks scope '{scope}'",
                details={"required": scope, "scopes": scopes},
            )

    def require_admin(self, record: Dict[str, Any]) -> None:
        if record.get("role") != "admin":
            raise PermissionError("admin role required")

    # ------------------------------------------------------------------- jwt
    async def issue_token(self, api_key: str) -> Dict[str, Any]:
        record = await self.authenticate(api_key)
        secret = self.settings.jwt_secret
        if not secret:
            raise AuthError("JWT auth is not configured (set auth.jwt_secret)")
        ttl = self.settings.jwt_ttl_minutes * 60
        now = int(time.time())
        payload = {
            "sub": record["id"],
            "role": record["role"],
            "iat": now,
            "exp": now + ttl,
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")
        return {"access_token": token, "token_type": "bearer",
                "expires_in": ttl, "scope": record["role"]}

    def decode_token(self, token: str) -> Dict[str, Any]:
        secret = self.settings.jwt_secret
        if not secret:
            raise AuthError("JWT auth is not configured (set auth.jwt_secret)")
        try:
            return jwt.decode(token, secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError("invalid token") from exc

    # ----------------------------------------------------------- rate limiting
    def check_rate_limit(self, bucket: str, rpm: Optional[int] = None) -> None:
        """Sliding-window rate limit. bucket is e.g. 'key:abc123' or 'ip:1.2.3.4'."""
        limit = rpm if rpm is not None else self.settings.rate_limit_rpm
        if limit <= 0:
            return
        now = time.time()
        window = self._windows.setdefault(bucket, [])
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= limit:
            raise RateLimitError(
                f"rate limit exceeded: {limit} requests per minute",
                details={"bucket": bucket, "limit": limit},
            )
        window.append(now)

    def reset_rate_limits(self) -> None:
        self._windows.clear()


# --------------------------------------------------------------------------
# FastAPI dependency helpers
# --------------------------------------------------------------------------
def _extract_key(request: Any) -> Optional[str]:
    header = request.headers.get("X-API-Key")
    if header:
        return header
    query = request.query_params.get("api_key")
    if query:
        return query
    body = getattr(request, "_json_body", None)
    if isinstance(body, dict) and body.get("api_key"):
        return body["api_key"]
    return None


class AuthContext:
    """Carries the resolved identity through a request."""

    def __init__(self, record: Optional[Dict[str, Any]] = None, *,
                 via_jwt: bool = False, bucket: str = "") -> None:
        self.record = record
        self.via_jwt = via_jwt
        self.bucket = bucket

    @property
    def key_id(self) -> Optional[str]:
        return self.record.get("id") if self.record else None

    @property
    def role(self) -> str:
        return (self.record or {}).get("role", "anonymous")

    @property
    def is_authenticated(self) -> bool:
        return self.record is not None


async def build_auth_context(request: Any, auth: AuthManager,
                             require: bool = True) -> AuthContext:
    """Resolve identity from API key header/param or Bearer JWT."""
    api_key = _extract_key(request)
    if api_key:
        record = await auth.authenticate(api_key)
        return AuthContext(record, bucket=f"key:{record['id']}")

    authz = request.headers.get("Authorization", "")
    if authz.lower().startswith("bearer "):
        claims = auth.decode_token(authz[7:].strip())
        record = await auth.db.key_get(claims.get("sub", ""))
        if record is None or record.get("revoked"):
            raise AuthError("token subject revoked")
        return AuthContext(record, via_jwt=True, bucket=f"key:{record['id']}")

    if require:
        raise AuthError("missing credentials: send X-API-Key or Authorization: Bearer")
    return AuthContext(bucket=f"ip:{request.client.host if request.client else 'unknown'}")
