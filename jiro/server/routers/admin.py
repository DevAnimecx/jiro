"""Admin endpoints: API key management, token issuance, usage stats."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from jiro.auth import AuthContext, AuthManager
from jiro.db import Database
from jiro.models import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, LoginRequest, TokenResponse
from jiro.server.deps import get_auth_manager, get_db, require_scope

router = APIRouter(tags=["admin"])


@router.post("/api-keys", response_model=ApiKeyCreated,
             summary="Create an API key (admin)")
async def create_key(
    body: ApiKeyCreate,
    ctx: AuthContext = Depends(require_scope("admin")),
    auth: AuthManager = Depends(get_auth_manager),
) -> Dict[str, Any]:
    auth.require_admin(ctx.record)
    created = await auth.create_key(
        name=body.name, role=body.role, scopes=body.scopes,
        rate_limit_rpm=body.rate_limit_rpm,
    )
    return created


@router.get("/api-keys", response_model=List[ApiKeyOut],
            summary="List API keys (admin)")
async def list_keys(
    ctx: AuthContext = Depends(require_scope("admin")),
    auth: AuthManager = Depends(get_auth_manager),
    include_revoked: bool = False,
    db: Database = Depends(get_db),
) -> List[Dict[str, Any]]:
    auth.require_admin(ctx.record)
    keys = await db.key_list(include_revoked=include_revoked)
    out = []
    for key in keys:
        out.append({
            "id": key["id"], "name": key["name"], "key_prefix": key["key_prefix"],
            "role": key["role"], "scopes": key["scopes"],
            "rate_limit_rpm": key["rate_limit_rpm"],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(key["created_at"])),
            "revoked": key["revoked"],
            "last_used_at": (time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(key["last_used_at"]))
                             if key.get("last_used_at") else None),
        })
    return out


@router.delete("/api-keys/{key_id}", summary="Revoke an API key (admin)")
async def revoke_key(
    key_id: str,
    ctx: AuthContext = Depends(require_scope("admin")),
    auth: AuthManager = Depends(get_auth_manager),
    db: Database = Depends(get_db),
) -> Dict[str, Any]:
    auth.require_admin(ctx.record)
    await db.key_revoke(key_id)
    return {"id": key_id, "revoked": True}


@router.post("/auth/token", response_model=TokenResponse,
             summary="Exchange an API key for a JWT")
async def issue_token(
    body: LoginRequest,
    auth: AuthManager = Depends(get_auth_manager),
) -> Dict[str, Any]:
    return await auth.issue_token(body.api_key)


@router.get("/usage", summary="Usage statistics (admin)")
async def usage(
    ctx: AuthContext = Depends(require_scope("admin")),
    auth: AuthManager = Depends(get_auth_manager),
    db: Database = Depends(get_db),
    days: int = Query(7, ge=1, le=365),
    key_id: Optional[str] = None,
) -> Dict[str, Any]:
    auth.require_admin(ctx.record)
    since = time.time() - days * 86400
    summary = await db.usage_summary(key_id=key_id, since=since)
    summary["since_days"] = days
    return summary
