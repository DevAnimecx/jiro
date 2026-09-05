"""Enterprise admin endpoints: tenants, SLA, compliance, webhooks, batch jobs."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request

from jiro.auth import AuthContext
from jiro.server.deps import get_auth_context, record_usage, require_tier

router = APIRouter(tags=["enterprise"])


# ---------------------------------------------------------------------------
# Tenant Management
# ---------------------------------------------------------------------------

@router.get("/enterprise/tenants", summary="List all tenants")
async def list_tenants(
    request: Request,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.tenants import get_tenant_manager
    mgr = get_tenant_manager()
    await record_usage(request, endpoint="/enterprise/tenants", status=200)
    return {"tenants": mgr.list_tenants()}


@router.post("/enterprise/tenants", summary="Create a tenant")
async def create_tenant(
    request: Request,
    body: Dict[str, Any],
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.tenants import get_tenant_manager
    mgr = get_tenant_manager()
    tenant = mgr.create_tenant(
        tenant_id=body["tenant_id"],
        name=body["name"],
        tier=body.get("tier", "free"),
        rate_limit_rpm=body.get("rate_limit_rpm", 60),
        rate_limit_rpd=body.get("rate_limit_rpd", 10000),
    )
    await record_usage(request, endpoint="/enterprise/tenants", status=200)
    return tenant.to_dict()


@router.get("/enterprise/tenants/{tenant_id}", summary="Get tenant details")
async def get_tenant(
    request: Request,
    tenant_id: str,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.tenants import get_tenant_manager
    mgr = get_tenant_manager()
    tenant = mgr.get_tenant(tenant_id)
    if not tenant:
        return {"error": "tenant not found"}
    await record_usage(request, endpoint="/enterprise/tenants", status=200)
    return tenant.to_dict()


@router.delete("/enterprise/tenants/{tenant_id}", summary="Delete a tenant")
async def delete_tenant(
    request: Request,
    tenant_id: str,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.tenants import get_tenant_manager
    mgr = get_tenant_manager()
    deleted = mgr.delete_tenant(tenant_id)
    await record_usage(request, endpoint="/enterprise/tenants", status=200)
    return {"deleted": deleted, "tenant_id": tenant_id}


# ---------------------------------------------------------------------------
# SLA Monitoring
# ---------------------------------------------------------------------------

@router.get("/enterprise/sla", summary="SLA metrics overview")
async def get_sla_overview(
    request: Request,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.tenants import get_tenant_manager
    mgr = get_tenant_manager()
    await record_usage(request, endpoint="/enterprise/sla", status=200)
    return mgr.get_sla_summary()


@router.get("/enterprise/sla/{endpoint:path}", summary="SLA for specific endpoint")
async def get_endpoint_sla(
    request: Request,
    endpoint: str,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.tenants import get_tenant_manager
    mgr = get_tenant_manager()
    sla = mgr.get_sla(f"/{endpoint}")
    await record_usage(request, endpoint="/enterprise/sla", status=200)
    return sla


# ---------------------------------------------------------------------------
# SOC2 Compliance
# ---------------------------------------------------------------------------

@router.get("/enterprise/compliance/soc2", summary="SOC2 control status")
async def get_soc2_controls(
    request: Request,
    domain: Optional[str] = Query(None),
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_compliance import get_enterprise_compliance
    compliance = get_enterprise_compliance()
    controls = compliance.get_controls(domain)
    posture = compliance.get_compliance_posture()
    await record_usage(request, endpoint="/enterprise/compliance/soc2", status=200)
    return {"controls": controls, "posture": posture}


@router.get("/enterprise/compliance/posture", summary="Overall compliance posture")
async def get_compliance_posture(
    request: Request,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_compliance import get_enterprise_compliance
    compliance = get_enterprise_compliance()
    await record_usage(request, endpoint="/enterprise/compliance/posture", status=200)
    return compliance.get_compliance_posture()


@router.get("/enterprise/compliance/retention", summary="Data retention policies")
async def get_retention_policies(
    request: Request,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_compliance import get_enterprise_compliance
    compliance = get_enterprise_compliance()
    await record_usage(request, endpoint="/enterprise/compliance/retention", status=200)
    return {"policies": compliance.get_retention_policies()}


@router.get("/enterprise/compliance/residency", summary="Data residency config")
async def get_data_residency(
    request: Request,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_compliance import get_enterprise_compliance
    compliance = get_enterprise_compliance()
    residency = compliance.get_data_residency()
    await record_usage(request, endpoint="/enterprise/compliance/residency", status=200)
    return residency or {"configured": False}


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

@router.get("/enterprise/webhooks", summary="List webhooks")
async def list_webhooks(
    request: Request,
    event: Optional[str] = Query(None),
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_api import get_webhook_manager
    mgr = get_webhook_manager()
    webhooks = mgr.list_webhooks(event)
    await record_usage(request, endpoint="/enterprise/webhooks", status=200)
    return {"webhooks": webhooks}


@router.post("/enterprise/webhooks", summary="Create webhook")
async def create_webhook(
    request: Request,
    body: Dict[str, Any],
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_api import get_webhook_manager
    mgr = get_webhook_manager()
    webhook = mgr.create_webhook(
        url=body["url"],
        events=body.get("events", ["*"]),
        metadata=body.get("metadata", {}),
    )
    await record_usage(request, endpoint="/enterprise/webhooks", status=200)
    return webhook.to_dict()


@router.delete("/enterprise/webhooks/{webhook_id}", summary="Delete webhook")
async def delete_webhook(
    request: Request,
    webhook_id: str,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_api import get_webhook_manager
    mgr = get_webhook_manager()
    deleted = mgr.delete_webhook(webhook_id)
    await record_usage(request, endpoint="/enterprise/webhooks", status=200)
    return {"deleted": deleted, "webhook_id": webhook_id}


@router.get("/enterprise/webhooks/{webhook_id}/deliveries", summary="Webhook deliveries")
async def get_webhook_deliveries(
    request: Request,
    webhook_id: str,
    limit: int = Query(50, ge=1, le=200),
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_api import get_webhook_manager
    mgr = get_webhook_manager()
    deliveries = mgr.get_deliveries(webhook_id, limit)
    await record_usage(request, endpoint="/enterprise/webhooks/deliveries", status=200)
    return {"deliveries": deliveries}


# ---------------------------------------------------------------------------
# Batch Jobs
# ---------------------------------------------------------------------------

@router.get("/enterprise/jobs", summary="List batch jobs")
async def list_jobs(
    request: Request,
    status: Optional[str] = Query(None),
    job_type: Optional[str] = Query(None),
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_api import get_batch_manager
    mgr = get_batch_manager()
    jobs = mgr.list_jobs(status, job_type)
    await record_usage(request, endpoint="/enterprise/jobs", status=200)
    return {"jobs": jobs}


@router.post("/enterprise/jobs", summary="Create batch job")
async def create_job(
    request: Request,
    body: Dict[str, Any],
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_api import get_batch_manager
    mgr = get_batch_manager()
    job = mgr.create_job(
        job_type=body["job_type"],
        input_data=body.get("input_data", {}),
        total_items=body.get("total_items", 0),
        metadata=body.get("metadata", {}),
    )
    await record_usage(request, endpoint="/enterprise/jobs", status=200)
    return job.to_dict()


@router.get("/enterprise/jobs/{job_id}", summary="Get job status")
async def get_job(
    request: Request,
    job_id: str,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_api import get_batch_manager
    mgr = get_batch_manager()
    job = mgr.get_job(job_id)
    if not job:
        return {"error": "job not found"}
    await record_usage(request, endpoint="/enterprise/jobs", status=200)
    return job.to_dict()


@router.post("/enterprise/jobs/{job_id}/cancel", summary="Cancel batch job")
async def cancel_job(
    request: Request,
    job_id: str,
    ctx: AuthContext = Depends(require_tier("enterprise")),
) -> Dict[str, Any]:
    from jiro.enterprise_api import get_batch_manager
    mgr = get_batch_manager()
    cancelled = mgr.cancel_job(job_id)
    await record_usage(request, endpoint="/enterprise/jobs/cancel", status=200)
    return {"cancelled": cancelled, "job_id": job_id}
