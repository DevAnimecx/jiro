"""Tests for the self-learning layer."""

from __future__ import annotations

import json
import os
import time

import pytest
import pytest_asyncio

from jiro.scraping.social.self_learning import (
    AsyncLearningStore,
    EngineStats,
    LearningStore,
    ScrapeRecord,
    get_best_engine,
    get_engine_ranking,
    get_learning_summary,
    get_platform_stats,
    prune_old_records,
    record_scrape,
    reset_learning,
)


@pytest.fixture
def tmp_store(tmp_path):
    store = LearningStore(path=str(tmp_path / "learning.json"))
    yield store
    reset_learning()


def test_engine_stats_defaults():
    es = EngineStats(platform="twitter", engine="http")
    assert es.success_rate == 1.0
    assert es.avg_latency_ms == 0.0
    assert es.p95_latency_ms == 0.0
    assert es.recommended_timeout_ms == 15_000


def test_engine_stats_after_records():
    es = EngineStats(platform="twitter", engine="http", method="scrape")
    for i in range(10):
        es.total_attempts += 1
        es.successes += 1
        es.latencies.append(100 + i * 10)
        es.last_success = time.time()
    assert es.success_rate == 1.0
    assert es.avg_latency_ms == pytest.approx(145.0)
    assert es.p95_latency_ms == pytest.approx(190.0)
    # p95=190, recommended = min(max(190*1.5, 5000), 60000) = 5000 (clamped)
    assert es.recommended_timeout_ms == 5000


def test_engine_stats_health_score():
    es = EngineStats(platform="p", engine="e")
    assert es.health_score == 0.5  # neutral for never-tested

    es.total_attempts = 10
    es.successes = 8
    es.last_success = time.time()
    es.latencies = [100.0] * 10
    score = es.health_score
    assert 0.0 < score < 1.0


def test_record_and_rank(tmp_store):
    record_scrape("twitter", "http", "scrape", True, 100.0)
    record_scrape("twitter", "http", "scrape", True, 200.0)
    record_scrape("twitter", "http", "scrape", False, 5000.0, error_type="timeout")

    ranking = get_engine_ranking("twitter", "scrape")
    assert len(ranking) == 1
    assert ranking[0]["engine"] == "http"
    assert ranking[0]["success_rate"] == pytest.approx(2 / 3)


def test_get_best_engine():
    record_scrape("instagram", "graphql", "scrape", True, 200.0)
    record_scrape("instagram", "html", "scrape", True, 800.0)
    best = get_best_engine("instagram", "scrape")
    assert best == "graphql"


def test_get_platform_stats():
    record_scrape("reddit", "http", "scrape", True, 150.0)
    stats = get_platform_stats("reddit")
    assert stats["platform"] == "reddit"
    assert stats["total_records"] >= 1
    assert "by_method" in stats


def test_prune_old_records():
    record_scrape("yt", "http", "scrape", True, 100.0)
    from jiro.scraping.social.self_learning import _store
    _store._records[0].timestamp = time.time() - (100 * 3600)
    pruned = prune_old_records(max_age_hours=24)
    assert pruned >= 1


@pytest.mark.asyncio
async def test_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "learn.json")
    store = LearningStore(path=path)
    store.record(ScrapeRecord("x", "e", "scrape", True, 50.0))
    await store.save()

    store2 = LearningStore(path=path)
    assert len(store2._records) == 1
    assert store2._records[0].platform == "x"


def test_memory_bounded(tmp_path):
    path = str(tmp_path / "bounded.json")
    store = LearningStore(path=path)
    for i in range(15000):
        store.record(ScrapeRecord("p", "e", "scrape", True, float(i)))
    assert len(store._records) <= 10000


def test_get_learning_summary():
    record_scrape("test", "http", "scrape", True, 100.0)
    summary = get_learning_summary()
    assert "total_records" in summary
    assert "platforms_tracked" in summary


@pytest.mark.asyncio
async def test_async_store_debounced_save(tmp_path):
    path = str(tmp_path / "async.json")
    store = AsyncLearningStore(path=path)
    await store.record_async(ScrapeRecord("async", "http", "scrape", True, 100.0))
    # Debounced save is fire-and-forget; force save to verify
    await store.save()


def test_reset_learning():
    record_scrape("reset", "http", "scrape", True, 100.0)
    reset_learning()
    assert get_learning_summary()["total_records"] == 0
