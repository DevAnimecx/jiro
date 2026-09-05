"""Fine-grained Role-Based Access Control (RBAC).

Provides:
- Permission definitions for all API endpoints
- Role templates (viewer, editor, admin, auditor)
- Per-user/role permission checking
- Database-backed role_permissions table
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from jiro.config import Settings
from jiro.errors import JiroPermissionError

log = logging.getLogger("jiro.rbac")

if TYPE_CHECKING:
    from jiro.db import Database


class Permission(str, Enum):
    """Atomic permissions for API endpoints."""
    SEARCH_READ = "search:read"
    SEARCH_WRITE = "search:write"
    SCRAPE_READ = "scrape:read"
    SCRAPE_WRITE = "scrape:write"
    AI_READ = "ai:read"
    AI_WRITE = "ai:write"
    SOCIAL_READ = "social:read"
    SOCIAL_WRITE = "social:write"
    ADMIN_READ = "admin:read"
    ADMIN_WRITE = "admin:write"
    COMPLIANCE_READ = "compliance:read"
    COMPLIANCE_WRITE = "compliance:write"
    AUDIT_READ = "audit:read"
    WEBHOOK_MANAGE = "webhook:manage"
    KEY_MANAGE = "key:manage"
    BILLING_READ = "billing:read"
    BILLING_WRITE = "billing:write"


# Default role templates
ROLE_TEMPLATES: Dict[str, Set[str]] = {
    "viewer": {
        Permission.SEARCH_READ,
        Permission.SCRAPE_READ,
        Permission.AI_READ,
        Permission.SOCIAL_READ,
    },
    "editor": {
        Permission.SEARCH_READ,
        Permission.SEARCH_WRITE,
        Permission.SCRAPE_READ,
        Permission.SCRAPE_WRITE,
        Permission.AI_READ,
        Permission.AI_WRITE,
        Permission.SOCIAL_READ,
        Permission.SOCIAL_WRITE,
    },
    "admin": {
        Permission.SEARCH_READ,
        Permission.SEARCH_WRITE,
        Permission.SCRAPE_READ,
        Permission.SCRAPE_WRITE,
        Permission.AI_READ,
        Permission.AI_WRITE,
        Permission.SOCIAL_READ,
        Permission.SOCIAL_WRITE,
        Permission.ADMIN_READ,
        Permission.ADMIN_WRITE,
        Permission.COMPLIANCE_READ,
        Permission.COMPLIANCE_WRITE,
        Permission.AUDIT_READ,
        Permission.WEBHOOK_MANAGE,
        Permission.KEY_MANAGE,
    },
    "auditor": {
        Permission.SEARCH_READ,
        Permission.SCRAPE_READ,
        Permission.AI_READ,
        Permission.SOCIAL_READ,
        Permission.ADMIN_READ,
        Permission.COMPLIANCE_READ,
        Permission.AUDIT_READ,
        Permission.BILLING_READ,
    },
}


@dataclass
class RBACManager:
    """Manages role-based access control.
    
    Stores permissions in database with support for:
    - Role templates (default permissions per role)
    - Custom per-user overrides
    - Per-API-key permissions
    """
    
    def __init__(self, db: Database, settings: Optional[Settings] = None) -> None:
        self.db = db
        self.settings = settings or Settings.load()
        self._cache: Dict[str, Set[str]] = {}
        self._cache_ttl = 60  # seconds
    
    async def get_permissions(self, identity: str) -> Set[str]:
        """Get all permissions for a user/API key.
        
        Args:
            identity: User ID, API key ID, or OIDC subject
            
        Returns:
            Set of permission strings
        """
        # Check cache
        import time
        cached = self._cache.get(identity)
        if cached is not None:
            return cached
        
        # Load from DB
        rows = await self.db.fetchall(
            "SELECT permission FROM role_permissions WHERE identity = ?",
            (identity,),
        )
        perms = {row["permission"] for row in rows}
        
        # Cache
        self._cache[identity] = perms
        return perms
    
    async def has_permission(self, identity: str, permission: str) -> bool:
        """Check if identity has a specific permission."""
        perms = await self.get_permissions(identity)
        return permission in perms
    
    async def require_permission(self, identity: str, permission: str) -> None:
        """Raise JiroPermissionError if identity lacks permission."""
        if not await self.has_permission(identity, permission):
            from jiro.errors import JiroPermissionError
            raise JiroPermissionError(
                f"permission denied: {permission}",
                details={"required": permission, "identity": identity},
            )
    
    async def grant_permission(self, identity: str, permission: str) -> None:
        """Grant a permission to an identity."""
        await self.db.execute(
            "INSERT OR IGNORE INTO role_permissions (identity, permission, granted_at)"
            " VALUES (?, ?, ?)",
            (identity, permission, time.time()),
        )
        self._cache.pop(identity, None)
    
    async def revoke_permission(self, identity: str, permission: str) -> None:
        """Revoke a permission from an identity."""
        await self.db.execute(
            "DELETE FROM role_permissions WHERE identity = ? AND permission = ?",
            (identity, permission),
        )
        self._cache.pop(identity, None)
    
    async def set_role_permissions(self, identity: str, role: str) -> None:
        """Set all permissions for a role template.
        
        Replaces existing permissions with the role template.
        """
        perms = ROLE_TEMPLATES.get(role, set())
        await self.db.execute(
            "DELETE FROM role_permissions WHERE identity = ?", (identity,)
        )
        now = time.time()
        for perm in perms:
            await self.db.execute(
                "INSERT INTO role_permissions (identity, permission, granted_at)"
                " VALUES (?, ?, ?)",
                (identity, perm, now),
            )
        self._cache[identity] = perms
    
    async def list_identities_with_permission(self, permission: str) -> List[str]:
        """List all identities that have a specific permission."""
        rows = await self.db.fetchall(
            "SELECT identity FROM role_permissions WHERE permission = ?",
            (permission,),
        )
        return [row["identity"] for row in rows]
    
    def clear_cache(self) -> None:
        """Clear permission cache."""
        self._cache.clear()


# Global instance
_rbac: Optional[RBACManager] = None


def get_rbac_manager(db: Optional[Database] = None,
                     settings: Optional[Settings] = None) -> RBACManager:
    """Get or create the global RBACManager instance."""
    global _rbac
    if _rbac is None:
        if db is None:
            from jiro.db import Database as DB
            from jiro.config import Settings as S
            db = DB(S.load().db_path)
        _rbac = RBACManager(db, settings)
    return _rbac
