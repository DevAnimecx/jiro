"""JWT session management with revocation support.

Provides:
- Session creation and tracking
- JWT revocation (blacklist)
- Session cleanup (expired sessions)
- Redis-backed distributed session store for multi-worker deployments
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from jiro.config import Settings
from jiro.db import Database
from jiro.errors import AuthError
from jiro.log import get_logger

log = get_logger("jiro.session")


class SessionManager:
    """Manages JWT sessions with revocation support.
    
    Features:
    - Tracks active sessions in database
    - Supports JWT revocation (logout, security incident)
    - Automatic cleanup of expired sessions
    - Redis-backed distribution for multi-worker deployments
    """
    
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._redis = None
        self._redis_url = (
            settings.get("cache.url", "redis://localhost:6379/0")
            if settings.cache_type == "redis" else None
        )
    
    async def create_session(self, key_id: str, jti: str, expires_at: float,
                            user_agent: str = "", ip_address: str = "") -> None:
        """Create a new session record."""
        await self.db.session_create(jti, key_id, expires_at, user_agent, ip_address)
        # Also cache in Redis for fast lookup
        if self._redis_url:
            await self._redis_set(jti, key_id, expires_at)
    
    async def revoke_session(self, jti: str) -> None:
        """Revoke a session by JTI (logout/security)."""
        await self.db.session_revoke(jti)
        # Also remove from Redis
        if self._redis_url:
            await self._redis_delete(jti)
    
    async def revoke_all_sessions(self, key_id: str) -> int:
        """Revoke all sessions for an API key.
        
        Returns:
            Number of sessions revoked
        """
        sessions = await self.db.session_list_for_key(key_id)
        count = 0
        for session in sessions:
            if not session.get("revoked"):
                await self.revoke_session(session["jti"])
                count += 1
        return count
    
    async def is_session_valid(self, jti: str) -> bool:
        """Check if a session is still valid.
        
        Checks Redis first (fast), then falls back to database.
        """
        # Check Redis first if available
        if self._redis_url:
            redis_valid = await self._redis_is_valid(jti)
            if redis_valid is not None:
                return redis_valid
        
        # Fallback to database
        return await self.db.session_is_valid(jti)
    
    async def cleanup_expired(self) -> int:
        """Remove expired sessions from database.
        
        Returns:
            Number of sessions deleted
        """
        return await self.db.session_cleanup()
    
    async def get_active_sessions(self, key_id: str) -> List[Dict[str, Any]]:
        """Get all active (non-revoked) sessions for an API key."""
        sessions = await self.db.session_list_for_key(key_id)
        now = time.time()
        return [
            s for s in sessions
            if not s.get("revoked") and s.get("expires_at", 0) > now
        ]
    
    async def _redis_set(self, jti: str, key_id: str, expires_at: float) -> None:
        """Store session in Redis."""
        try:
            import redis.asyncio as aioredis
            client = await self._get_redis()
            if client:
                ttl = max(1, int(expires_at - time.time()))
                await client.setex(
                    f"jiro:session:{jti}",
                    ttl,
                    key_id,
                )
        except Exception:
            pass
    
    async def _redis_delete(self, jti: str) -> None:
        """Remove session from Redis."""
        try:
            import redis.asyncio as aioredis
            client = await self._get_redis()
            if client:
                await client.delete(f"jiro:session:{jti}")
        except Exception:
            pass
    
    async def _redis_is_valid(self, jti: str) -> Optional[bool]:
        """Check session validity in Redis. Returns None if not in Redis."""
        try:
            import redis.asyncio as aioredis
            client = await self._get_redis()
            if client:
                value = await client.get(f"jiro:session:{jti}")
                if value is not None:
                    return True
                return False  # Key doesn't exist = revoked or expired
        except Exception:
            pass
        return None
    
    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            return self._redis
        except Exception:
            self._redis = None
            return None
