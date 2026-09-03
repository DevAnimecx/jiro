"""Regression tests for SSRF protection in :mod:`jiro.security`."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from jiro import security
from jiro.errors import SSRFError


def test_is_blocked_ip_detects_private_ranges():
    blocked = [
        "127.0.0.1",
        "127.255.255.254",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
    ]
    for ip in blocked:
        assert security.is_blocked_ip(ip), f"{ip} should be blocked"


def test_is_blocked_ip_allows_public_ranges():
    allowed = [
        "8.8.8.8",
        "93.184.216.34",  # example.com
        "1.1.1.1",
        "2606:4700:4700::1111",
    ]
    for ip in allowed:
        assert not security.is_blocked_ip(ip), f"{ip} should be allowed"


def test_validate_target_url_rejects_localhost():
    with pytest.raises(SSRFError):
        security.validate_target_url("http://localhost:8000/admin")


def test_validate_target_url_rejects_blocked_ip_literal():
    with pytest.raises(SSRFError):
        security.validate_target_url("http://169.254.169.254/latest/meta-data/")


def test_validate_target_url_allows_public_ip_literal():
    # Public IP literals must not be refused.
    assert security.validate_target_url("http://8.8.8.8/") == "http://8.8.8.8/"


def test_validate_target_url_rejects_resolved_private():
    with patch.object(security, "_resolve", return_value=["10.0.0.5"]):
        with pytest.raises(SSRFError):
            security.validate_target_url("http://internal.example/")


def test_validate_target_url_rejects_own_host():
    with patch.object(security, "_resolve", return_value=["93.184.216.34"]):
        with pytest.raises(SSRFError):
            security.validate_target_url(
                "http://jiro.local/", own_hosts=["jiro.local"]
            )


def test_validate_target_url_fails_closed_without_dns():
    # When DNS resolution fails we MUST reject (fail-closed security).
    with patch.object(security, "_resolve", side_effect=OSError("no dns")):
        with pytest.raises(SSRFError, match="DNS resolution failed"):
            security.validate_target_url("http://example.com/")


def test_async_validate_target_url_rejects_resolved_private():
    with patch.object(
        security, "resolve_hostname", new=AsyncMock(return_value=["192.168.0.9"])
    ):
        with pytest.raises(SSRFError):
            asyncio.run(
                security.async_validate_target_url("http://internal.example/")
            )


def test_async_validate_target_url_fails_closed_without_dns():
    # When DNS resolution fails we MUST reject (fail-closed security).
    with patch.object(
        security, "resolve_hostname", new=AsyncMock(side_effect=OSError("no dns"))
    ):
        with pytest.raises(SSRFError, match="DNS resolution failed"):
            asyncio.run(
                security.async_validate_target_url("http://example.com/")
            )
