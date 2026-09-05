"""Comprehensive tests for all enterprise phases (3-6)."""

from __future__ import annotations

import time

import pytest

from jiro.analytics import QueryAnalytics, get_analytics, reset_analytics
from jiro.tenants import TenantManager, get_tenant_manager, reset_tenant_manager
from jiro.enterprise_compliance import (
    ComplianceManager, SOC2_CONTROLS, DataResidencyConfig, RetentionPolicy,
    get_enterprise_compliance, reset_enterprise_compliance,
)
from jiro.enterprise_api import (
    WebhookManager, BatchJobManager, WebhookStatus, JobStatus,
    get_webhook_manager, get_batch_manager, reset_enterprise_api,
)


# ===========================================================================
# Phase 3: Analytics Tests
# ===========================================================================

class TestQueryAnalytics:
    def test_record_query(self):
        a = QueryAnalytics()
        a.record_query("python tutorial", "google", 150.0, user_id="u1")
        assert a.get_summary()["total_queries"] == 1

    def test_multiple_queries(self):
        a = QueryAnalytics()
        a.record_query("q1", "google", 100.0, user_id="u1")
        a.record_query("q1", "bing", 200.0, user_id="u2")
        a.record_query("q2", "google", 150.0, user_id="u1")
        summary = a.get_summary()
        assert summary["total_queries"] == 3
        assert summary["unique_queries"] == 2

    def test_trending(self):
        a = QueryAnalytics()
        for i in range(10):
            a.record_query("popular", "google", 100.0)
        a.record_query("rare", "google", 100.0)
        trending = a.get_trending(top_n=5)
        assert trending[0]["query"] == "popular"
        assert trending[0]["count"] == 10

    def test_popular_queries(self):
        a = QueryAnalytics()
        a.record_query("q1", "google", 100.0)
        a.record_query("q1", "google", 100.0)
        a.record_query("q2", "google", 100.0)
        popular = a.get_popular_queries(top_n=5)
        assert popular[0]["query"] == "q1"
        assert popular[0]["count"] == 2

    def test_engine_distribution(self):
        a = QueryAnalytics()
        a.record_query("q1", "google", 100.0)
        a.record_query("q2", "bing", 100.0)
        a.record_query("q3", "google", 100.0)
        dist = a.get_engine_distribution()
        assert dist["google"] == 2
        assert dist["bing"] == 1

    def test_user_stats(self):
        a = QueryAnalytics()
        a.record_query("q1", "google", 100.0, user_id="u1")
        a.record_query("q2", "google", 100.0, user_id="u1")
        stats = a.get_user_stats("u1")
        assert stats["unique_queries"] == 2
        assert stats["total_requests"] == 2

    def test_anomaly_detection(self):
        a = QueryAnalytics()
        for _ in range(15):
            a.record_query("failing", "google", 100.0, success=True)
        for _ in range(10):
            a.record_query("failing", "google", 100.0, success=False)
        anomalies = a.get_anomalies()
        assert len(anomalies) > 0
        assert anomalies[0]["trend_type"] == "high_error_rate"

    def test_summary(self):
        a = QueryAnalytics()
        a.record_query("q1", "google", 100.0, user_id="u1")
        summary = a.get_summary()
        assert summary["total_queries"] == 1
        assert summary["active_users"] == 1
        assert summary["engines_used"] == 1

    def test_singleton(self):
        reset_analytics()
        a1 = get_analytics()
        a2 = get_analytics()
        assert a1 is a2
        reset_analytics()


# ===========================================================================
# Phase 4: Tenant Tests
# ===========================================================================

class TestTenantManager:
    def test_create_tenant(self):
        mgr = TenantManager()
        tenant = mgr.create_tenant("t1", "Acme Corp", tier="enterprise")
        assert tenant.tenant_id == "t1"
        assert tenant.tier == "enterprise"

    def test_get_tenant(self):
        mgr = TenantManager()
        mgr.create_tenant("t1", "Acme Corp")
        tenant = mgr.get_tenant("t1")
        assert tenant is not None
        assert tenant.name == "Acme Corp"

    def test_update_tenant(self):
        mgr = TenantManager()
        mgr.create_tenant("t1", "Acme Corp")
        updated = mgr.update_tenant("t1", tier="enterprise", rate_limit_rpm=1000)
        assert updated.tier == "enterprise"
        assert updated.rate_limit_rpm == 1000

    def test_delete_tenant(self):
        mgr = TenantManager()
        mgr.create_tenant("t1", "Acme Corp")
        assert mgr.delete_tenant("t1") is True
        assert mgr.get_tenant("t1") is None

    def test_list_tenants(self):
        mgr = TenantManager()
        mgr.create_tenant("t1", "Acme")
        mgr.create_tenant("t2", "Beta")
        tenants = mgr.list_tenants()
        assert len(tenants) == 2

    def test_rate_limit(self):
        mgr = TenantManager()
        mgr.create_tenant("t1", "Acme", rate_limit_rpm=3)
        for _ in range(3):
            assert mgr.check_rate_limit("t1") is True
        assert mgr.check_rate_limit("t1") is False

    def test_engine_access(self):
        mgr = TenantManager()
        mgr.create_tenant("t1", "Acme", allowed_engines={"google", "bing"})
        assert mgr.check_engine_access("t1", "google") is True
        assert mgr.check_engine_access("t1", "duckduckgo") is False

    def test_sla_tracking(self):
        mgr = TenantManager()
        mgr.record_sla("/search", 100.0, True)
        mgr.record_sla("/search", 200.0, True)
        mgr.record_sla("/search", 150.0, False, "timeout")
        sla = mgr.get_sla("/search")
        assert sla["total_requests"] == 3
        assert sla["successful_requests"] == 2
        assert sla["failed_requests"] == 1

    def test_sla_summary(self):
        mgr = TenantManager()
        mgr.record_sla("/search", 100.0, True)
        mgr.record_sla("/scrape", 200.0, True)
        summary = mgr.get_sla_summary()
        assert summary["total_requests"] == 2
        assert summary["endpoints_tracked"] == 2

    def test_singleton(self):
        reset_tenant_manager()
        m1 = get_tenant_manager()
        m2 = get_tenant_manager()
        assert m1 is m2
        reset_tenant_manager()


# ===========================================================================
# Phase 5: Compliance Tests
# ===========================================================================

class TestCompliance:
    def test_soc2_controls_exist(self):
        assert len(SOC2_CONTROLS) >= 10
        domains = {c.domain for c in SOC2_CONTROLS}
        assert "Access Control" in domains
        assert "Privacy" in domains

    def test_get_controls(self):
        mgr = ComplianceManager()
        controls = mgr.get_controls()
        assert len(controls) >= 10

    def test_get_controls_by_domain(self):
        mgr = ComplianceManager()
        controls = mgr.get_controls(domain="Privacy")
        assert all(c["domain"] == "Privacy" for c in controls)

    def test_update_control_status(self):
        mgr = ComplianceManager()
        assert mgr.update_control_status("CC6.1", "tested", ["pentest report"]) is True
        control = mgr.get_control("CC6.1")
        assert control["status"] == "tested"
        assert "pentest report" in control["evidence"]

    def test_compliance_posture(self):
        mgr = ComplianceManager()
        posture = mgr.get_compliance_posture()
        assert posture["total_controls"] >= 10
        assert posture["compliance_score"] > 0

    def test_data_residency(self):
        mgr = ComplianceManager()
        config = DataResidencyConfig(
            region="us-east-1",
            storage_backend="postgresql",
            encryption_key_region="us-east-1",
            allowed_regions=["us-east-1", "us-west-2"],
        )
        mgr.set_data_residency(config)
        residency = mgr.get_data_residency()
        assert residency["region"] == "us-east-1"

    def test_retention_policies(self):
        mgr = ComplianceManager()
        policies = mgr.get_retention_policies()
        assert len(policies) >= 5
        types = {p["data_type"] for p in policies}
        assert "search_history" in types
        assert "audit_chain" in types

    def test_set_retention_policy(self):
        mgr = ComplianceManager()
        policy = RetentionPolicy("custom_data", 30, True)
        mgr.set_retention_policy(policy)
        result = mgr.get_retention_policy("custom_data")
        assert result["retention_days"] == 30

    def test_compliance_events(self):
        mgr = ComplianceManager()
        mgr.record_compliance_event("access_granted", {"user": "u1"})
        events = mgr.get_compliance_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "access_granted"

    def test_singleton(self):
        reset_enterprise_compliance()
        c1 = get_enterprise_compliance()
        c2 = get_enterprise_compliance()
        assert c1 is c2
        reset_enterprise_compliance()


# ===========================================================================
# Phase 6: Enterprise API Tests
# ===========================================================================

class TestWebhooks:
    def test_create_webhook(self):
        mgr = WebhookManager()
        wh = mgr.create_webhook("https://example.com/hook", ["search.completed"])
        assert wh.webhook_id.startswith("wh_")
        assert wh.url == "https://example.com/hook"

    def test_get_webhook(self):
        mgr = WebhookManager()
        wh = mgr.create_webhook("https://example.com/hook", ["*"])
        got = mgr.get_webhook(wh.webhook_id)
        assert got is not None

    def test_update_webhook(self):
        mgr = WebhookManager()
        wh = mgr.create_webhook("https://example.com/hook", ["*"])
        mgr.update_webhook(wh.webhook_id, status=WebhookStatus.PAUSED)
        assert mgr.get_webhook(wh.webhook_id).status == WebhookStatus.PAUSED

    def test_delete_webhook(self):
        mgr = WebhookManager()
        wh = mgr.create_webhook("https://example.com/hook", ["*"])
        assert mgr.delete_webhook(wh.webhook_id) is True
        assert mgr.get_webhook(wh.webhook_id) is None

    def test_list_webhooks(self):
        mgr = WebhookManager()
        mgr.create_webhook("https://a.com", ["search"])
        mgr.create_webhook("https://b.com", ["scrape"])
        all_hooks = mgr.list_webhooks()
        assert len(all_hooks) == 2
        search_hooks = mgr.list_webhooks(event="search")
        assert len(search_hooks) == 1

    def test_trigger_event(self):
        mgr = WebhookManager()
        wh = mgr.create_webhook("https://example.com/hook", ["search.completed"])
        deliveries = mgr.trigger_event("search.completed", {"query": "test"})
        assert len(deliveries) == 1

    def test_trigger_filters_events(self):
        mgr = WebhookManager()
        mgr.create_webhook("https://example.com/hook", ["search.completed"])
        deliveries = mgr.trigger_event("scrape.completed", {})
        assert len(deliveries) == 0

    def test_wildcard_event(self):
        mgr = WebhookManager()
        mgr.create_webhook("https://example.com/hook", ["*"])
        deliveries = mgr.trigger_event("any.event", {})
        assert len(deliveries) == 1

    def test_get_deliveries(self):
        mgr = WebhookManager()
        wh = mgr.create_webhook("https://example.com/hook", ["*"])
        mgr.trigger_event("test", {})
        deliveries = mgr.get_deliveries(wh.webhook_id)
        assert len(deliveries) == 1

    def test_sign_payload(self):
        mgr = WebhookManager()
        wh = mgr.create_webhook("https://example.com/hook", ["*"])
        sig = wh.sign_payload(b"test payload")
        assert len(sig) == 64


class TestBatchJobs:
    def test_create_job(self):
        mgr = BatchJobManager()
        job = mgr.create_job("search_batch", {"queries": ["q1", "q2"]}, total_items=2)
        assert job.job_id.startswith("job_")
        assert job.status == JobStatus.PENDING

    def test_get_job(self):
        mgr = BatchJobManager()
        job = mgr.create_job("search", {})
        got = mgr.get_job(job.job_id)
        assert got is not None

    def test_cancel_job(self):
        mgr = BatchJobManager()
        job = mgr.create_job("search", {})
        assert mgr.cancel_job(job.job_id) is True
        assert mgr.get_job(job.job_id).status == JobStatus.CANCELLED

    def test_complete_job(self):
        mgr = BatchJobManager()
        job = mgr.create_job("search", {})
        assert mgr.complete_job(job.job_id, {"results": []}) is True
        assert mgr.get_job(job.job_id).status == JobStatus.COMPLETED

    def test_fail_job(self):
        mgr = BatchJobManager()
        job = mgr.create_job("search", {})
        assert mgr.fail_job(job.job_id, "timeout") is True
        assert mgr.get_job(job.job_id).status == JobStatus.FAILED

    def test_update_progress(self):
        mgr = BatchJobManager()
        job = mgr.create_job("search", {}, total_items=100)
        mgr.update_progress(job.job_id, 50)
        updated = mgr.get_job(job.job_id)
        assert updated.progress == 50.0
        assert updated.processed_items == 50

    def test_list_jobs(self):
        mgr = BatchJobManager()
        mgr.create_job("search", {})
        mgr.create_job("scrape", {})
        all_jobs = mgr.list_jobs()
        assert len(all_jobs) == 2
        search_jobs = mgr.list_jobs(job_type="search")
        assert len(search_jobs) == 1

    def test_singleton(self):
        reset_enterprise_api()
        w1 = get_webhook_manager()
        w2 = get_webhook_manager()
        assert w1 is w2
        b1 = get_batch_manager()
        b2 = get_batch_manager()
        assert b1 is b2
        reset_enterprise_api()
