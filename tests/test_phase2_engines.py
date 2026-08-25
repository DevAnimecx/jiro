"""Tests for Phase 2: YouTube, Amazon, eBay, Yandex, Baidu engines."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from jiro.config import Settings
from jiro.server import create_app
from jiro.models import SearchRequest


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


# --------------------------------------------------------------------------
# YouTube engine
# --------------------------------------------------------------------------
YOUTUBE_HTML = """<!DOCTYPE html><html><body>
<script>
var ytInitialData = {"contents":{"twoColumnSearchResultsRenderer":{"primaryContents":{"sectionListRenderer":{"contents":[{"itemSectionRenderer":{"contents":[{"videoRenderer":{"videoId":"dQw4w9WgXcQ","title":{"runs":[{"text":"Never Gonna Give You Up"}]},"ownerText":{"runs":[{"text":"Rick Astley"}]},"viewCountText":{"simpleText":"1,234,567 views"},"publishedTimeText":{"simpleText":"3 years ago"},"lengthText":{"simpleText":"3:33"},"thumbnail":{"thumbnails":[{"url":"https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"}]}}},{"videoRenderer":{"videoId":"abc123","title":{"runs":[{"text":"Best Python Tutorial 2026"}]},"ownerText":{"runs":[{"text":"Code Academy"}]},"viewCountText":{"simpleText":"456,789 views"},"publishedTimeText":{"simpleText":"1 month ago"},"lengthText":{"simpleText":"15:42"},"thumbnail":{"thumbnails":[{"url":"https://i.ytimg.com/vi/abc123/hqdefault.jpg"}]}}}]}}]}}}}};
</script>
</body></html>"""


@pytest.mark.asyncio
async def test_youtube_engine(settings):
    from jiro.scraping.parsers.youtube import YouTubeEngine
    from tests.helpers import FakeClient

    client = FakeClient({"youtube.com": YOUTUBE_HTML})
    engine = YouTubeEngine(client, settings)
    result = await engine.search(SearchRequest(q="python tutorial", engine="youtube",
                                               type="videos", num=5))
    assert len(result.organic_results) == 2
    assert result.organic_results[0].title == "Never Gonna Give You Up"
    assert result.organic_results[0].channel == "Rick Astley"
    assert result.organic_results[0].duration == "3:33"
    assert result.organic_results[0].views == "1,234,567 views"
    assert "youtube.com" in result.organic_results[0].link


def test_youtube_in_registry(settings):
    from jiro.scraping.engines import _build_registry
    reg = _build_registry()
    assert "youtube" in reg.names()


def test_youtube_types():
    from jiro.scraping.parsers.youtube import YouTubeEngine
    assert YouTubeEngine.types == ["videos"]


# --------------------------------------------------------------------------
# Amazon engine
# --------------------------------------------------------------------------
AMAZON_HTML = """<!DOCTYPE html><html><body>
<div data-component-type="s-search-result" data-asin="B09V3KXJPB">
  <h2><a href="https://www.amazon.com/dp/B09V3KXJPB"><span>Wireless Bluetooth Headphones</span></a></h2>
  <span class="a-price"><span class="a-price-symbol">$</span><span class="a-price-whole">29</span><span class="a-price-fraction">99</span></span>
  <span class="a-icon-alt">4.5 out of 5 stars</span>
  <span class="a-size-base s-underline-text">1,234 ratings</span>
  <img class="s-image" src="https://m.media-amazon.com/images/I/headphones.jpg"/>
  <span class="a-icon-prime"></span>
  <div class="a-section a-spacing-small a-spacing-top-micro">Premium sound quality with noise cancellation</div>
</div>
<div data-component-type="s-search-result" data-asin="B08N5WRWNW">
  <h2><a href="https://www.amazon.com/dp/B08N5WRWNW"><span>USB-C Hub Adapter 7-in-1</span></a></h2>
  <span class="a-price"><span class="a-price-symbol">$</span><span class="a-price-whole">19</span><span class="a-price-fraction">99</span></span>
  <span class="a-icon-alt">4.3 out of 5 stars</span>
  <span class="a-size-base s-underline-text">567 ratings</span>
  <img class="s-image" src="https://m.media-amazon.com/images/I/usb-hub.jpg"/>
</div>
</body></html>"""


@pytest.mark.asyncio
async def test_amazon_engine(settings):
    from jiro.scraping.parsers.amazon import AmazonEngine
    from tests.helpers import FakeClient

    client = FakeClient({"amazon.com": AMAZON_HTML})
    engine = AmazonEngine(client, settings)
    result = await engine.search(SearchRequest(q="headphones", engine="amazon",
                                               type="shopping", num=5))
    assert len(result.organic_results) == 2
    assert result.organic_results[0].title == "Wireless Bluetooth Headphones"
    assert result.organic_results[0].price == "$29.99"
    assert result.organic_results[0].asin == "B09V3KXJPB"
    assert result.organic_results[0].rating == "4.5 out of 5 stars"
    assert result.organic_results[0].prime is True
    assert result.organic_results[1].prime is None


def test_amazon_in_registry(settings):
    from jiro.scraping.engines import _build_registry
    reg = _build_registry()
    assert "amazon" in reg.names()


def test_amazon_types():
    from jiro.scraping.parsers.amazon import AmazonEngine
    assert "web" in AmazonEngine.types
    assert "shopping" in AmazonEngine.types


# --------------------------------------------------------------------------
# eBay engine
# --------------------------------------------------------------------------
EBAY_HTML = """<!DOCTYPE html><html><body>
<li class="s-item">
  <div class="s-item__title"><span>Apple MacBook Pro 14 inch M3</span></div>
  <a class="s-item__link" href="https://www.ebay.com/itm/123456789"><span></span></a>
  <span class="s-item__price">$1,299.00</span>
  <span class="s-item__shipping">Free shipping</span>
  <span class="SECONDARY_INFO">Certified Refurbished</span>
  <span class="s-item__seller-info-text">Seller: techdeals99 (1234)</span>
  <img class="s-item__image-img" src="https://i.ebayimg.com/macbook.jpg"/>
</li>
<li class="s-item">
  <div class="s-item__title"><span>Samsung Galaxy S24 Ultra 256GB</span></div>
  <a class="s-item__link" href="https://www.ebay.com/itm/987654321"><span></span></a>
  <span class="s-item__price">$899.99</span>
  <span class="s-item__shipping">$12.50 shipping</span>
  <span class="SECONDARY_INFO">New</span>
</li>
<li class="s-item">
  <div class="s-item__title"><span>Shop on eBay</span></div>
</li>
</body></html>"""


@pytest.mark.asyncio
async def test_ebay_engine(settings):
    from jiro.scraping.parsers.ebay import EbayEngine
    from tests.helpers import FakeClient

    client = FakeClient({"ebay.com": EBAY_HTML})
    engine = EbayEngine(client, settings)
    result = await engine.search(SearchRequest(q="macbook", engine="ebay",
                                               type="shopping", num=5))
    assert len(result.organic_results) == 2
    assert result.organic_results[0].title == "Apple MacBook Pro 14 inch M3"
    assert result.organic_results[0].price == "$1,299.00"
    assert result.organic_results[0].condition == "Certified Refurbished"
    assert result.organic_results[0].shipping == "Free shipping"
    assert result.organic_results[0].seller is not None
    # "Shop on eBay" should be filtered out
    assert result.organic_results[1].title == "Samsung Galaxy S24 Ultra 256GB"


def test_ebay_in_registry(settings):
    from jiro.scraping.engines import _build_registry
    reg = _build_registry()
    assert "ebay" in reg.names()


def test_ebay_types():
    from jiro.scraping.parsers.ebay import EbayEngine
    assert "web" in EbayEngine.types
    assert "shopping" in EbayEngine.types


# --------------------------------------------------------------------------
# Yandex engine
# --------------------------------------------------------------------------
YANDEX_HTML = """<!DOCTYPE html><html><body>
<li class="serp-item">
  <h2><a class="organic__url-text" href="https://example.com/article">Python Guide 2026</a></h2>
  <div class="organic__content-wrapper">A comprehensive guide to Python programming in 2026.</div>
  <div class="organic__url-text">example.com</div>
</li>
<li class="serp-item">
  <h2><a class="organic__url-text" href="https://docs.python.org">Python Documentation</a></h2>
  <div class="organic__content-wrapper">Official Python documentation and tutorials.</div>
  <div class="organic__url-text">docs.python.org</div>
</li>
</body></html>"""


@pytest.mark.asyncio
async def test_yandex_engine(settings):
    from jiro.scraping.parsers.yandex import YandexEngine
    from tests.helpers import FakeClient

    client = FakeClient({"yandex.com": YANDEX_HTML})
    engine = YandexEngine(client, settings)
    result = await engine.search(SearchRequest(q="python", engine="yandex",
                                               type="web", num=5))
    assert len(result.organic_results) == 2
    assert result.organic_results[0].title == "Python Guide 2026"
    assert "example.com" in result.organic_results[0].link


def test_yandex_in_registry(settings):
    from jiro.scraping.engines import _build_registry
    reg = _build_registry()
    assert "yandex" in reg.names()


def test_yandex_types():
    from jiro.scraping.parsers.yandex import YandexEngine
    assert YandexEngine.types == ["web"]


# --------------------------------------------------------------------------
# Baidu engine
# --------------------------------------------------------------------------
BAIDU_HTML = """<!DOCTYPE html><html><body>
<div class="result">
  <h3><a href="https://www.baidu.com/link?url=abc123">Python 教程 - 百度文库</a></h3>
  <span class="content-right_8Zs40">Python编程入门教程，适合初学者。</span>
  <span class="c-color-gray">wenku.baidu.com</span>
</div>
<div class="result">
  <h3><a href="https://www.baidu.com/link?url=def456">Python 官方文档</a></h3>
  <span class="content-right_8Zs40">Python官方文档和API参考。</span>
  <span class="c-color-gray">docs.python.org</span>
</div>
</body></html>"""


@pytest.mark.asyncio
async def test_baidu_engine(settings):
    from jiro.scraping.parsers.baidu import BaiduEngine
    from tests.helpers import FakeClient

    client = FakeClient({"baidu.com": BAIDU_HTML})
    engine = BaiduEngine(client, settings)
    result = await engine.search(SearchRequest(q="python", engine="baidu",
                                               type="web", num=5))
    assert len(result.organic_results) == 2
    assert "Python" in result.organic_results[0].title
    assert result.organic_results[0].source is not None


def test_baidu_in_registry(settings):
    from jiro.scraping.engines import _build_registry
    reg = _build_registry()
    assert "baidu" in reg.names()


def test_baidu_types():
    from jiro.scraping.parsers.baidu import BaiduEngine
    assert BaiduEngine.types == ["web"]


# --------------------------------------------------------------------------
# Integration tests — all engines in registry
# --------------------------------------------------------------------------
def test_all_new_engines_registered():
    from jiro.scraping.engines import _build_registry, ENGINE_TYPES, ENGINE_DESCRIPTIONS
    reg = _build_registry()
    for name in ["youtube", "amazon", "ebay", "yandex", "baidu"]:
        assert name in reg.names(), f"{name} not registered"
        assert name in ENGINE_TYPES, f"{name} not in ENGINE_TYPES"
        assert name in ENGINE_DESCRIPTIONS, f"{name} not in ENGINE_DESCRIPTIONS"


def test_engine_types_complete():
    from jiro.scraping.engines import ENGINE_TYPES
    assert set(ENGINE_TYPES["youtube"]) == {"videos"}
    assert set(ENGINE_TYPES["amazon"]) == {"web", "shopping"}
    assert set(ENGINE_TYPES["ebay"]) == {"web", "shopping"}
    assert set(ENGINE_TYPES["yandex"]) == {"web"}
    assert set(ENGINE_TYPES["baidu"]) == {"web"}


# --------------------------------------------------------------------------
# Config defaults include new engines
# --------------------------------------------------------------------------
def test_config_includes_new_engines():
    s = Settings()  # pure defaults, no file influence
    for engine in ["youtube", "amazon", "ebay", "yandex", "baidu"]:
        assert engine in s.engines, f"{engine} not in default engines"
        assert engine in s.fallback_order, f"{engine} not in default fallback_order"


# --------------------------------------------------------------------------
# Real engine integration tests
# --------------------------------------------------------------------------
@pytest.mark.network
def test_search_google_real(client):
    r = client.get("/search.json", params={"q": "python web scraping", "engine": "google",
                                           "num": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["search_metadata"]["engine"] in ("google", "bing", "brave", "duckduckgo")
    assert len(data["organic_results"]) > 0


@pytest.mark.network
def test_search_bing_real(client):
    r = client.get("/search.json", params={"q": "python web scraping", "engine": "bing",
                                           "num": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["search_metadata"]["engine"] == "bing"
    assert len(data["organic_results"]) > 0


def test_engines_endpoint_includes_new(client):
    r = client.get("/engines")
    names = [e["name"] for e in r.json()["engines"]]
    for engine in ["youtube", "amazon", "ebay", "yandex", "baidu"]:
        assert engine in names, f"{engine} not in /engines response"
