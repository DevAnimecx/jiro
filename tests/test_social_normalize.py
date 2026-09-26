"""0.3.1 guards: social normalizer dict handling + curl adapter httpx compat."""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

from jiro.scraping.client import _CurlResponseAdapter
from jiro.scraping.social.base import SocialPost, SocialProfile
from jiro.server.routers.social import normalize_post, normalize_profile


# ---------------------------------------------------------------------------
# normalize_post
# ---------------------------------------------------------------------------
def test_normalize_post_passes_dict_through():
    payload = {"platform": "hackernews", "type": "post", "url": "x", "data": {}}
    assert normalize_post(payload) is payload


def test_normalize_post_converts_object():
    post = SocialPost(platform="reddit", type="post", url="https://reddit.com/1")
    out = normalize_post(post)
    assert out["platform"] == "reddit"
    assert out["type"] == "post"
    assert out["url"] == "https://reddit.com/1"
    assert out["credits_charged"] == 2
    assert "scraped_at" in out


# ---------------------------------------------------------------------------
# normalize_profile
# ---------------------------------------------------------------------------
def test_normalize_profile_passes_dict_through():
    payload = {"platform": "twitter", "type": "profile", "url": "x", "data": {}}
    assert normalize_profile(payload) is payload


def test_normalize_profile_converts_object_without_type_attr():
    # SocialProfile has no `type` field — must not raise AttributeError.
    profile = SocialProfile(
        platform="twitter", username="jack", url="https://x.com/jack"
    )
    out = normalize_profile(profile)
    assert out["platform"] == "twitter"
    assert out["type"] == "profile"
    assert out["url"] == "https://x.com/jack"
    assert out["credits_charged"] == 3
    assert "scraped_at" in out


# ---------------------------------------------------------------------------
# _CurlResponseAdapter
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status_code: int, text: str = '{"ok": true}', url: str = ""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode() if isinstance(text, str) else text
        self.headers = {"content-type": "application/json"}
        self.url = url


def test_adapter_passes_2xx():
    adapter = _CurlResponseAdapter(_FakeResp(200))
    assert adapter.status_code == 200
    assert adapter.raise_for_status() is None


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
def test_adapter_raises_on_error_statuses(status):
    adapter = _CurlResponseAdapter(_FakeResp(status, url="http://example.com/x"))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        adapter.raise_for_status()
    assert exc.value.response.status_code == status


def test_adapter_text_decodes_bytes():
    adapter = _CurlResponseAdapter(_FakeResp(200, text=b"binary-ish \xff"))
    assert isinstance(adapter.text, str)
    assert "binary-ish" in adapter.text


def test_adapter_json_parses_body():
    adapter = _CurlResponseAdapter(_FakeResp(200, text='{"items": [1, 2]}'))
    assert adapter.json() == {"items": [1, 2]}


def test_adapter_json_invalid_body_raises():
    adapter = _CurlResponseAdapter(_FakeResp(200, text="not json"))
    with pytest.raises(ValueError):
        adapter.json()


def test_adapter_headers_and_content_passthrough():
    adapter = _CurlResponseAdapter(_FakeResp(200))
    assert adapter.headers["content-type"] == "application/json"
    assert adapter.content == b'{"ok": true}'
