"""Tests for the Terms-of-Service compliance layer (legal/security surface)."""

from __future__ import annotations

import pytest

from jiro.compliance import ComplianceManager, EngineTOS
from jiro.config import Settings
from tests.integration_utils import TEST_CONFIG


@pytest.fixture
def compliance_manager() -> ComplianceManager:
    settings = Settings(raw=TEST_CONFIG.copy())
    return ComplianceManager(settings)


class TestComplianceRegistry:
    def test_all_major_engines_registered(self, compliance_manager):
        tos = compliance_manager.get_all_tos()
        for engine in ("google", "bing", "duckduckgo", "brave", "youtube",
                       "amazon", "ebay", "yandex", "baidu"):
            assert engine in tos
            assert isinstance(tos[engine], EngineTOS)
            assert tos[engine].tos_url.startswith("http")

    def test_get_tos_unknown_returns_none(self, compliance_manager):
        assert compliance_manager.get_tos("nonexistent") is None


class TestComplianceChecks:
    def test_prohibited_use_flagged(self, compliance_manager):
        res = compliance_manager.check_compliance("google", "automated scraping at scale")
        assert res["compliant"] is False
        assert any("prohibited" in w.lower() for w in res["warnings"])

    def test_commercial_use_blocked(self, compliance_manager):
        res = compliance_manager.check_compliance("google", "commercial research")
        assert res["compliant"] is False
        assert any("commercial" in w.lower() for w in res["warnings"])

    def test_requires_approval_flagged(self, compliance_manager):
        # DuckDuckGo allows automated use without approval; google requires it.
        ddg = compliance_manager.check_compliance("duckduckgo", "personal use")
        assert ddg["compliant"] is True
        g = compliance_manager.check_compliance("google", "personal research")
        assert g["compliant"] is False
        assert any("approval" in w.lower() for w in g["warnings"])

    def test_unknown_engine_is_compliant(self, compliance_manager):
        res = compliance_manager.check_compliance("mystery", "anything")
        assert res["compliant"] is True
        assert res["warnings"] == []


class TestComplianceSummary:
    def test_summary_shape(self, compliance_manager):
        summary = compliance_manager.get_compliance_summary(["google", "duckduckgo"])
        assert "google" in summary and "duckduckgo" in summary
        assert summary["google"]["commercial_allowed"] is False
        assert summary["duckduckgo"]["attribution_required"] is True


class TestTOAcknowledgment:
    @pytest.mark.asyncio
    async def test_acknowledge_and_check_in_memory(self, compliance_manager):
        assert compliance_manager.has_acknowledged("user1", "google") is False
        ack = await compliance_manager.acknowledge_tos("user1", "google", ip="1.2.3.4")
        assert ack.user_id == "user1"
        assert ack.engine == "google"
        assert ack.tos_version
        assert compliance_manager.has_acknowledged("user1", "google") is True
        # Different user still needs ack
        assert compliance_manager.has_acknowledged("user2", "google") is False

    @pytest.mark.asyncio
    async def test_acknowledge_unknown_engine_raises(self, compliance_manager):
        with pytest.raises(ValueError):
            await compliance_manager.acknowledge_tos("u", "nope")

    @pytest.mark.asyncio
    async def test_acknowledge_persists_to_db(self, test_db):
        settings = Settings(raw=TEST_CONFIG.copy())
        manager = ComplianceManager(settings, test_db)
        await manager.acknowledge_tos("u1", "bing", ip="9.9.9.9")
        # A fresh manager over the same DB should see the ack (via DB lookup).
        manager2 = ComplianceManager(settings, test_db)
        assert await manager2.has_acknowledged_async("u1", "bing") is True
