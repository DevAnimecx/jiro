"""Compliance endpoints: GDPR DSAR, data retention, audit exports.

Provides:
- GET /compliance/dsar/{user_id} - Export all user data (GDPR Article 15)
- DELETE /compliance/dsar/{user_id} - Delete all user data (GDPR Article 17)
- GET /compliance/audit/export - Export audit logs with hash chain verification
- POST /compliance/data-retention - Configure data retention policies
- GET /compliance/status - Compliance status and configuration
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from jiro.server.deps import get_db, get_settings, require_admin_dep as require_admin
from jiro.auth import AuthContext
from jiro.audit_chain import AuditChain, get_audit_chain
from jiro.db import Database
from jiro.encryption import get_encryption
from jiro.errors import JiroPermissionError
from jiro.server.deps import get_db, get_settings

router = APIRouter(tags=["compliance"])


@router.get("/compliance/dsar/{user_id}", summary="Export all user data (GDPR Article 15)")
async def export_user_data(
    request: Request,
    user_id: str,
    ctx: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
    settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Export all data associated with a user for GDPR DSAR.
    
    Returns a comprehensive JSON export including:
    - API keys and metadata
    - Usage history
    - Search history
    - Social cache (if applicable)
    - ToS acknowledgments
    - OIDC identities (if applicable)
    """
    # Verify DSAR is enabled
    if not settings.get("compliance.dsar_enabled", True):
        raise JiroPermissionError("DSAR exports are disabled")
    
    # Collect all user data
    export_data = {
        "exported_at": time.time(),
        "user_id": user_id,
        "export_type": "gdpr_dsar",
        "data": {
            "api_keys": await _collect_api_keys(db, user_id),
            "usage_history": await _collect_usage(db, user_id),
            "search_history": await _collect_search_history(db, user_id),
            "social_cache": await _collect_social_cache(db, user_id),
            "tos_acknowledgments": await _collect_tos_acks(db, user_id),
            "oidc_identities": await _collect_oidc_identities(db, user_id),
            "sessions": await _collect_sessions(db, user_id),
            "rbac_permissions": await _collect_rbac_permissions(db, user_id),
        },
        "statistics": {
            "total_api_keys": 0,
            "total_requests": 0,
            "date_range": {},
        },
    }
    
    # Compute statistics
    stats = export_data["statistics"]
    stats["total_api_keys"] = len(export_data["data"]["api_keys"])
    stats["total_requests"] = len(export_data["data"]["usage_history"])
    if export_data["data"]["usage_history"]:
        timestamps = [u["timestamp"] for u in export_data["data"]["usage_history"]]
        stats["date_range"] = {
            "first": min(timestamps),
            "last": max(timestamps),
        }
    
    # Log DSAR export in audit chain
    audit = get_audit_chain(settings)
    audit.append(
        event_type="compliance",
        actor=ctx.key_id or "unknown",
        action="dsar_export",
        target=user_id,
        result="success",
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
        details={"export_size_bytes": len(json.dumps(export_data))},
    )
    
    return export_data


@router.delete("/compliance/dsar/{user_id}", summary="Delete all user data (GDPR Article 17)")
async def delete_user_data(
    request: Request,
    user_id: str,
    ctx: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
    settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Delete all data associated with a user (right to erasure).
    
    This is a destructive operation that cannot be undone.
    Anonymizes rather than hard-deletes usage data to maintain
    referential integrity with analytics.
    """
    if not settings.get("compliance.dsar_enabled", True):
        raise JiroPermissionError("DSAR deletions are disabled")
    
    deleted_counts = {}
    
    # Revoke all API keys
    keys = await db.fetchall("SELECT id FROM api_keys WHERE id = ?", (user_id,))
    for key in keys:
        await db.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key["id"],))
    deleted_counts["api_keys_revoked"] = len(keys)
    
    # Anonymize usage data (keep for analytics but remove PII)
    await db.execute(
        "UPDATE usage SET query = '[REDACTED]', key_id = NULL WHERE key_id = ?",
        (user_id,),
    )
    deleted_counts["usage_records_anonymized"] = db._last_insert_id if hasattr(db, '_last_insert_id') else 0
    
    # Delete search history
    if "search_history" in [t[0] for t in await db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")]:
        await db.execute("DELETE FROM search_history WHERE user_id = ?", (user_id,))
    
    # Delete social cache
    if "social_cache" in [t[0] for t in await db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")]:
        await db.execute("DELETE FROM social_cache WHERE user_id = ?", (user_id,))
    
    # Revoke all sessions
    sessions = await db.session_list_for_key(user_id)
    for session in sessions:
        await db.session_revoke(session["jti"])
    deleted_counts["sessions_revoked"] = len(sessions)
    
    # Delete OIDC identities
    await db.execute("DELETE FROM oidc_identities WHERE id = ?", (user_id,))
    
    # Delete RBAC permissions
    await db.execute("DELETE FROM role_permissions WHERE identity = ?", (user_id,))
    
    # Log deletion in audit chain
    audit = get_audit_chain(settings)
    audit.append(
        event_type="compliance",
        actor=ctx.key_id or "unknown",
        action="dsar_deletion",
        target=user_id,
        result="success",
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
        details={"deleted_counts": deleted_counts},
    )
    
    return {
        "status": "deleted",
        "user_id": user_id,
        "deleted_counts": deleted_counts,
        "deleted_at": time.time(),
    }


@router.get("/compliance/audit/export", summary="Export audit logs with hash chain verification")
async def export_audit_logs(
    request: Request,
    format: str = "json",
    ctx: AuthContext = Depends(require_admin),
    settings = Depends(get_settings),
) -> Response:
    """Export audit logs with cryptographic verification.
    
    Formats:
    - json: Full JSON export with chain verification
    - csv: CSV format for spreadsheet analysis
    - signed: JSON export with HMAC signature for SIEM ingestion
    """
    audit = get_audit_chain(settings)
    
    if format == "signed":
        import tempfile
        output_path = f"{settings.get('audit.chain_log_path', '~/.jiro/audit_chain.jsonl')}.export"
        file_hash = audit.export_signed(output_path, sign_key=settings.jwt_secret)
        return Response(
            content=json.dumps({
                "export_path": output_path,
                "file_hash": file_hash,
                "chain_valid": audit.verify_chain()[0],
            }),
            media_type="application/json",
        )
    
    # Default JSON export
    stats = audit.get_stats()
    is_valid, broken_at = audit.verify_chain()
    
    return Response(
        content=json.dumps({
            "audit_chain": stats,
            "chain_integrity": {
                "valid": is_valid,
                "broken_at": broken_at,
            },
            "exported_at": time.time(),
            "exported_by": ctx.key_id,
        }),
        media_type="application/json",
    )


@router.get("/compliance/status", summary="Compliance status and configuration")
async def compliance_status(
    request: Request,
    ctx: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
    settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Get compliance status, retention policies, and configuration."""
    audit = get_audit_chain(settings)
    chain_valid, chain_broken = audit.verify_chain()
    
    return {
        "dsar_enabled": settings.get("compliance.dsar_enabled", True),
        "retention_days": settings.get("compliance.retention_days", 365),
        "auto_purge_enabled": settings.get("compliance.auto_purge_enabled", False),
        "audit_chain": {
            "enabled": True,
            "valid": chain_valid,
            "broken_at": chain_broken,
            "stats": audit.get_stats(),
        },
        "encryption": {
            "enabled": get_encryption(settings).is_enabled(),
            "algorithm": settings.get("security.encryption_algorithm", "aes-256-gcm"),
        },
        "rbac": {
            "enabled": settings.get("auth.rbac.enabled", True),
            "default_role": settings.get("auth.rbac.default_role", "editor"),
        },
        "sessions": {
            "enabled": settings.get("auth.session.enabled", True),
        },
    }


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------

async def _collect_api_keys(db: Database, user_id: str) -> List[Dict[str, Any]]:
    """Collect API keys for a user."""
    rows = await db.fetchall(
        "SELECT id, name, key_prefix, role, scopes, rate_limit_rpm, "
        "created_at, revoked, last_used_at FROM api_keys WHERE id = ?",
        (user_id,),
    )
    return [dict(r) for r in rows]


async def _collect_usage(db: Database, user_id: str) -> List[Dict[str, Any]]:
    """Collect usage history for a user."""
    rows = await db.fetchall(
        "SELECT ts, endpoint, engine, query, status, latency_ms, tokens_in, tokens_out, cached"
        " FROM usage WHERE key_id = ? ORDER BY ts DESC",
        (user_id,),
    )
    return [dict(r) for r in rows]


async def _collect_search_history(db: Database, user_id: str) -> List[Dict[str, Any]]:
    """Collect search history for a user (PostgreSQL only)."""
    try:
        rows = await db.fetchall(
            "SELECT query, engine, timestamp, intent, results_count"
            " FROM search_history WHERE user_id = ? ORDER BY timestamp DESC",
            (user_id,),
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


async def _collect_social_cache(db: Database, user_id: str) -> List[Dict[str, Any]]:
    """Collect social cache for a user (PostgreSQL only)."""
    try:
        rows = await db.fetchall(
            "SELECT platform, data, created_at, expires_at"
            " FROM social_cache WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


async def _collect_tos_acks(db: Database, user_id: str) -> List[Dict[str, Any]]:
    """Collect ToS acknowledgments for a user."""
    rows = await db.fetchall(
        "SELECT engine, tos_version, ip_address, user_agent, acknowledged_at"
        " FROM tos_acknowledgments WHERE user_id = ?",
        (user_id,),
    )
    return [dict(r) for r in rows]


async def _collect_oidc_identities(db: Database, user_id: str) -> List[Dict[str, Any]]:
    """Collect OIDC identities for a user."""
    rows = await db.fetchall(
        "SELECT provider, subject, email, name, created_at, last_login"
        " FROM oidc_identities WHERE id = ?",
        (user_id,),
    )
    return [dict(r) for r in rows]


async def _collect_sessions(db: Database, user_id: str) -> List[Dict[str, Any]]:
    """Collect sessions for a user."""
    return await db.session_list_for_key(user_id)


async def _collect_rbac_permissions(db: Database, user_id: str) -> List[str]:
    """Collect RBAC permissions for a user."""
    rows = await db.fetchall(
        "SELECT permission FROM role_permissions WHERE identity = ?",
        (user_id,),
    )
    return [row["permission"] for row in rows]
