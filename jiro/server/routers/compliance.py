"""Compliance endpoints: ToS info, acknowledgments, reports."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request

from jiro.auth import AuthContext
from jiro.compliance import (
    ComplianceManager,
    get_engine_warning,
    get_startup_warning,
)
from jiro.server.deps import get_auth_context

router = APIRouter(tags=["compliance"])


def get_compliance_manager(request: Request) -> ComplianceManager:
    return request.app.state.compliance_manager


@router.get("/engines/compliance", summary="Get ToS compliance info for all engines")
async def get_compliance_info(
    compliance: ComplianceManager = Depends(get_compliance_manager),
) -> Dict[str, Any]:
    """Get Terms of Service compliance information for all configured engines."""
    tos_all = compliance.get_all_tos()
    return {
        "engines": {engine: tos.to_dict() for engine, tos in tos_all.items()},
        "startup_warning": get_startup_warning(),
        "note": "This information is for reference only. Always review current ToS at provided URLs.",
    }


@router.get("/engines/{engine}/compliance", summary="Get ToS compliance for specific engine")
async def get_engine_compliance(
    engine: str,
    compliance: ComplianceManager = Depends(get_compliance_manager),
) -> Dict[str, Any]:
    """Get detailed ToS compliance for a specific engine."""
    tos = compliance.get_tos(engine)
    if not tos:
        return {"error": f"No ToS information for engine: {engine}"}

    result = tos.to_dict()
    result["warning"] = get_engine_warning(engine)
    return result


@router.post("/engines/{engine}/acknowledge", summary="Acknowledge engine ToS")
async def acknowledge_tos(
    engine: str,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    compliance: ComplianceManager = Depends(get_compliance_manager),
) -> Dict[str, Any]:
    """Acknowledge ToS for an engine (required for commercial use)."""
    if not ctx.key_id:
        return {"error": "Authentication required to acknowledge ToS"}

    ip = request.client.host if request.client else None
    ua = request.headers.get("User-Agent")

    ack = await compliance.acknowledge_tos(ctx.key_id, engine, ip=ip, user_agent=ua)
    return {
        "acknowledged": True,
        "engine": engine,
        "tos_version": ack.tos_version,
        "acknowledged_at": ack.acknowledged_at,
    }


@router.get("/engines/{engine}/acknowledged", summary="Check ToS acknowledgment status")
async def check_acknowledgment(
    engine: str,
    ctx: AuthContext = Depends(get_auth_context),
    compliance: ComplianceManager = Depends(get_compliance_manager),
) -> Dict[str, Any]:
    """Check if current user has acknowledged ToS for an engine."""
    if not ctx.key_id:
        return {"acknowledged": False, "reason": "not_authenticated"}

    acknowledged = await compliance.has_acknowledged_async(ctx.key_id, engine)
    ack = compliance.get_acknowledgment(ctx.key_id, engine)

    return {
        "acknowledged": acknowledged,
        "engine": engine,
        "details": ack.to_dict() if ack else None,
    }


@router.get("/compliance/report", summary="Generate full compliance report")
async def generate_compliance_report(
    ctx: AuthContext = Depends(get_auth_context),
    compliance: ComplianceManager = Depends(get_compliance_manager),
) -> Dict[str, Any]:
    """Generate a full compliance report (admin only)."""
    if ctx.record is not None and ctx.record.get("role") != "admin":
        return {"error": "Admin access required"}

    return compliance.generate_compliance_report()


@router.get("/compliance/markdown", summary="Export ToS as Markdown")
async def export_tos_markdown(
    compliance: ComplianceManager = Depends(get_compliance_manager),
) -> Dict[str, Any]:
    """Export ToS information as Markdown for documentation."""
    return {"markdown": compliance.export_tos_markdown()}


@router.post("/compliance/check", summary="Check use case compliance")
async def check_use_case_compliance(
    engines: List[str],
    use_case: str,
    compliance: ComplianceManager = Depends(get_compliance_manager),
) -> Dict[str, Any]:
    """Check if a use case is compliant with engine ToS."""
    results = {}
    for engine in engines:
        results[engine] = compliance.check_compliance(engine, use_case)

    overall_compliant = all(r["compliant"] for r in results.values())
    return {
        "use_case": use_case,
        "overall_compliant": overall_compliant,
        "engines": results,
    }