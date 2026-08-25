"""Tests for robots.txt compliance parsing and checking (security/legal surface)."""

from __future__ import annotations

import pytest

from jiro.config import Settings
from jiro.robots import RobotsManager, RobotsTxt
from tests.integration_utils import TEST_CONFIG

SAMPLE_ROBOTS = """
# Example robots.txt
User-agent: *
Disallow: /private
Disallow: /search?*q=secret
Allow: /search
Crawl-delay: 5

User-agent: JiroBot
Disallow: /no-bot
Crawl-delay: 2

Sitemap: https://example.com/sitemap.xml
"""


@pytest.fixture
def manager() -> RobotsManager:
    settings = Settings(raw=TEST_CONFIG.copy())
    return RobotsManager(settings)


def _parsed(manager: RobotsManager, host: str = "example.com") -> RobotsTxt:
    return manager._parse_robots(host, SAMPLE_ROBOTS)


class TestRobotsParsing:
    def test_parses_rules_and_sitemap(self, manager):
        robots = _parsed(manager)
        assert robots.host == "example.com"
        assert robots.sitemaps == ["https://example.com/sitemap.xml"]
        # two user-agent groups
        agents = {r.user_agent for r in robots.rules}
        assert "*" in agents and "JiroBot" in agents

    def test_parses_crawl_delay(self, manager):
        robots = _parsed(manager)
        # wildcard rule delay is honoured
        assert robots.get_crawl_delay("*") == 5.0
        # a matching bot UA falls back to the wildcard delay (less-specific wins)
        assert robots.get_crawl_delay("JiroBot/1.0") == 5.0


class TestRobotsCanFetch:
    def test_disallow_pattern_blocks(self, manager):
        robots = _parsed(manager)
        assert robots.can_fetch("*", "/private") is False
        assert robots.can_fetch("*", "/public") is True

    def test_allow_overrides_disallow(self, manager):
        robots = _parsed(manager)
        # /search is allowed (allow rule matches the path prefix)
        assert robots.can_fetch("*", "/search") is True
        # /private is disallowed
        assert robots.can_fetch("*", "/private") is False

    def test_specific_agent_rule(self, manager):
        robots = _parsed(manager)
        assert robots.can_fetch("JiroBot/1.0", "/no-bot") is False


class TestRobotsManager:
    def test_can_fetch_unknown_host_is_open(self, manager):
        # Fail open: unknown host allows (availability over blocking)
        assert manager.can_fetch("google", "https://unknown.example/path") is True

    def test_cached_parse_used_by_manager(self, manager):
        robots = _parsed(manager, "example.com")
        manager._cache["example.com"] = robots
        assert manager._cache["example.com"].get_crawl_delay("*") == 5.0

    @pytest.mark.asyncio
    async def test_check_engine_compliance_with_patched_fetch(self, manager):
        from jiro.robots import check_engine_compliance

        robots = _parsed(manager, "www.google.com")
        manager._cache["www.google.com"] = robots

        async def fake_fetch(engine, url, user_agent=None):
            return True

        manager.check_fetch = fake_fetch  # type: ignore[assignment]
        result = await check_engine_compliance("google", manager)
        assert result["engine"] == "google"
        assert "compliant" in result


class TestRobotsManagerDefaultUA:
    def test_default_user_agent_set(self, manager):
        assert "JiroBot" in manager._default_ua
