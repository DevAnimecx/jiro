"""Tests for Phase 1: anti-detection client, CORS, proxy health check."""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from jiro.config import Settings
from jiro.server import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


# --------------------------------------------------------------------------
# Browser fingerprint rotation
# --------------------------------------------------------------------------
def test_browser_fingerprint_rotates_profiles():
    from jiro.scraping.client import BrowserFingerprint

    fp = BrowserFingerprint()
    profiles = set()
    for _ in range(30):
        profiles.add(fp.next_profile())
    # Should cycle through multiple profiles
    assert len(profiles) > 5


def test_browser_fingerprint_rotates_geo_headers():
    from jiro.scraping.client import BrowserFingerprint

    fp = BrowserFingerprint()
    seen = set()
    for _ in range(20):
        h = fp.next_geo_headers()
        seen.add(h.get("Accept-Language", ""))
    # Should have at least 3 different Accept-Language values
    assert len(seen) >= 3


# --------------------------------------------------------------------------
# Cookie jar
# --------------------------------------------------------------------------
def test_cookie_jar_update_and_get():
    from jiro.scraping.client import EngineCookieJar

    jar = EngineCookieJar()
    jar.update("bing", ["sessionid=abc123; Path=/; Max-Age=3600", "csrf=xyz; Path=/"])
    cookies = jar.get_dict("bing")
    assert cookies["sessionid"] == "abc123"
    assert cookies["csrf"] == "xyz"
    # Different engine has no cookies
    assert jar.get_dict("google") == {}


def test_cookie_jar_clear():
    from jiro.scraping.client import EngineCookieJar

    jar = EngineCookieJar()
    jar.update("bing", ["session=abc"])
    jar.clear("bing")
    assert jar.get_dict("bing") == {}


def test_cookie_jar_clear_all():
    from jiro.scraping.client import EngineCookieJar

    jar = EngineCookieJar()
    jar.update("bing", ["session=abc"])
    jar.update("google", ["session=def"])
    jar.clear()
    assert jar.get_dict("bing") == {}
    assert jar.get_dict("google") == {}


# --------------------------------------------------------------------------
# Circuit breaker
# --------------------------------------------------------------------------
def test_circuit_breaker_basic():
    from jiro.scraping.client import CircuitBreaker

    cb = CircuitBreaker(threshold=2, cooldown=60.0)
    assert cb.is_open("bing") is False
    cb.record_failure("bing")
    assert cb.is_open("bing") is False
    cb.record_failure("bing")
    assert cb.is_open("bing") is True
    cb.record_success("bing")
    assert cb.is_open("bing") is False


# --------------------------------------------------------------------------
# curl_cffi availability
# --------------------------------------------------------------------------
def test_curl_cffi_available():
    from jiro.scraping.client import _CURL_CFFI_AVAILABLE
    assert _CURL_CFFI_AVAILABLE is True


def test_browser_profiles_list():
    from jiro.scraping.client import _BROWSER_PROFILES
    assert len(_BROWSER_PROFILES) > 10
    assert "chrome131" in _BROWSER_PROFILES


def test_geo_headers_list():
    from jiro.scraping.client import _GEO_HEADERS
    assert len(_GEO_HEADERS) >= 4
    for h in _GEO_HEADERS:
        assert "Accept-Language" in h


def test_referer_chains():
    from jiro.scraping.client import _REFERER_CHAINS
    assert "bing" in _REFERER_CHAINS
    assert "google" in _REFERER_CHAINS
    assert len(_REFERER_CHAINS["bing"]) == 2


# --------------------------------------------------------------------------
# Response adapter
# --------------------------------------------------------------------------
def test_curl_response_adapter():
    from jiro.scraping.client import _CurlResponseAdapter

    class FakeCurlResp:
        status_code = 200
        text = "<html>test</html>"
        content = b"<html>test</html>"
        headers = {}

    adapter = _CurlResponseAdapter(FakeCurlResp())
    assert adapter.status_code == 200
    assert adapter.text == "<html>test</html>"
    assert adapter.content == b"<html>test</html>"


# --------------------------------------------------------------------------
# CORS config
# --------------------------------------------------------------------------
def test_cors_config_defaults():
    s = Settings.load()
    cors = s.cors
    assert cors["enabled"] is False
    assert cors["origins"] == ["*"]
    assert cors["allow_credentials"] is False


def test_cors_enabled_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JIRO_SERVER__CORS__ENABLED", "true")
    s = Settings.load()
    assert s.cors_enabled is True
    # Origins come from defaults when not overridden via env
    assert s.cors_origins == ["*"]


# --------------------------------------------------------------------------
# CORS endpoint test
# --------------------------------------------------------------------------
def test_cors_headers_not_set_by_default(client):
    """Without CORS enabled, no CORS headers should be sent."""
    r = client.options("/search.json", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    })
    # Default: no CORS headers
    assert "access-control-allow-origin" not in r.headers


def test_cors_headers_when_enabled(settings):
    """With CORS enabled, CORS headers should be sent."""
    settings.raw["server"]["cors"]["enabled"] = True
    settings.raw["server"]["cors"]["origins"] = ["http://localhost:3000"]
    with TestClient(create_app(settings)) as c:
        r = c.options("/search.json", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        })
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


# --------------------------------------------------------------------------
# Proxy config
# --------------------------------------------------------------------------
def test_proxy_config_defaults():
    s = Settings.load()
    proxy = s.proxy
    assert proxy["enabled"] is False
    assert proxy.get("health_check", False) is False
    assert proxy.get("rotation_strategy", "round_robin") == "round_robin"


def test_proxy_rotation_strategies(settings):
    from jiro.proxy import ProxyManager

    settings.raw["scraping"]["proxy"] = {
        "enabled": True,
        "url": "http://a:1,http://b:2,http://c:3",
        "rotation_strategy": "round_robin",
    }
    pm = ProxyManager(settings)
    # Round-robin should return proxies in order
    results = [pm.next() for _ in range(6)]
    assert results[0] != results[1]  # different proxies

    settings.raw["scraping"]["proxy"]["rotation_strategy"] = "random"
    pm2 = ProxyManager(settings)
    results2 = [pm2.next() for _ in range(6)]
    # Random should still return valid proxies
    for r in results2:
        assert r is not None


def test_proxy_info_includes_strategy(settings):
    from jiro.proxy import ProxyManager

    settings.raw["scraping"]["proxy"] = {
        "enabled": True, "url": "http://a:1",
        "rotation_strategy": "least_failures",
    }
    pm = ProxyManager(settings)
    info = pm.info()
    assert info["rotation_strategy"] == "least_failures"
    assert info["health_check"] is False


# --------------------------------------------------------------------------
# Proxy health check endpoint
# --------------------------------------------------------------------------
def test_proxy_health_no_proxies(client):
    r = client.post("/proxy/health")
    assert r.status_code == 200
    assert r.json()["endpoints"] == []
    assert "no proxies configured" in r.json()["message"]


# --------------------------------------------------------------------------
# Engine headers
# --------------------------------------------------------------------------
def test_engine_headers():
    from jiro.scraping.client import ENGINE_HEADERS

    assert "bing" in ENGINE_HEADERS
    assert "google" in ENGINE_HEADERS
    assert ENGINE_HEADERS["bing"]["Referer"] == "https://www.bing.com/"


def test_block_markers():
    from jiro.scraping.client import BLOCK_MARKERS

    assert "google" in BLOCK_MARKERS
    assert "bing" in BLOCK_MARKERS
    assert "duckduckgo" in BLOCK_MARKERS
    assert "captcha" in BLOCK_MARKERS["bing"]
