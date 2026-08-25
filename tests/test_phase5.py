"""Phase 5: API & Developer Experience — tests for export formats,
batch search, SSE streaming, and webhook improvements.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiro.export import (
    export,
    to_csv,
    to_json,
    to_rss,
    to_xml,
)
from jiro.jobs import JobManager


# ── Fixtures ────────────────────────────────────────────────────────────

SAMPLE_SEARCH_DATA = {
    "search_metadata": {"query": "python tutorial", "engine": "google"},
    "search_information": {"total_results": "1,000,000"},
    "organic_results": [
        {"position": 1, "title": "Python.org", "link": "https://python.org",
         "snippet": "The official Python website", "source": "python.org",
         "date": "2026-01-15"},
        {"position": 2, "title": "Learn Python", "link": "https://learnpython.org",
         "snippet": "Free Python tutorials", "source": "learnpython.org",
         "price": "$0.00", "rating": "4.5"},
        {"position": 3, "title": "Python Tutorial - W3Schools",
         "link": "https://w3schools.com/python",
         "snippet": "Python intro for beginners", "source": "w3schools.com"},
    ],
    "related_searches": [
        {"query": "python tutorial for beginners"},
        {"query": "python 3 tutorial"},
    ],
    "knowledge_graph": {"title": "Python", "type": "Programming language"},
}


# ── JSON Export ──────────────────────────────────────────────────────────

class TestJsonExport:
    def test_to_json_returns_string(self):
        result = to_json(SAMPLE_SEARCH_DATA)
        assert isinstance(result, str)

    def test_to_json_is_valid(self):
        result = to_json(SAMPLE_SEARCH_DATA)
        parsed = json.loads(result)
        assert "organic_results" in parsed

    def test_to_json_pretty(self):
        result = to_json(SAMPLE_SEARCH_DATA, pretty=True)
        assert "\n" in result
        assert "  " in result

    def test_to_json_compact(self):
        result = to_json(SAMPLE_SEARCH_DATA)
        # Compact should not have excessive whitespace
        assert "\n  " not in result


# ── CSV Export ───────────────────────────────────────────────────────────

class TestCsvExport:
    def test_to_csv_returns_string(self):
        results = SAMPLE_SEARCH_DATA["organic_results"]
        result = to_csv(results)
        assert isinstance(result, str)

    def test_to_csv_has_header(self):
        results = SAMPLE_SEARCH_DATA["organic_results"]
        result = to_csv(results)
        lines = result.strip().split("\n")
        assert len(lines) >= 2  # header + at least 1 row

    def test_to_csv_contains_data(self):
        results = SAMPLE_SEARCH_DATA["organic_results"]
        result = to_csv(results)
        assert "Python.org" in result
        assert "https://python.org" in result

    def test_to_csv_custom_fields(self):
        results = SAMPLE_SEARCH_DATA["organic_results"]
        result = to_csv(results, fields=["title", "link"])
        lines = result.strip().split("\n")
        assert "title" in lines[0]
        assert "link" in lines[0]

    def test_to_csv_empty(self):
        result = to_csv([])
        assert result == ""

    def test_to_csv_escapes_commas(self):
        results = [{"title": "Hello, World", "link": "https://example.com"}]
        result = to_csv(results, fields=["title", "link"])
        assert '"Hello, World"' in result


# ── XML Export ───────────────────────────────────────────────────────────

class TestXmlExport:
    def test_to_xml_returns_string(self):
        result = to_xml(SAMPLE_SEARCH_DATA)
        assert isinstance(result, str)

    def test_to_xml_has_declaration(self):
        result = to_xml(SAMPLE_SEARCH_DATA)
        assert result.startswith("<?xml")

    def test_to_xml_contains_results(self):
        result = to_xml(SAMPLE_SEARCH_DATA)
        assert "Python.org" in result
        assert "python tutorial" in result.lower() or "Python" in result

    def test_to_xml_custom_root(self):
        result = to_xml(SAMPLE_SEARCH_DATA, root_tag="my_results")
        assert "my_results" in result

    def test_to_xml_escapes_special_chars(self):
        data = {"organic_results": [{"title": "A < B & C > D", "link": ""}]}
        result = to_xml(data)
        assert "&lt;" in result
        assert "&amp;" in result

    def test_to_xml_empty_results(self):
        data = {"organic_results": []}
        result = to_xml(data)
        assert 'count="0"' in result


# ── RSS Export ───────────────────────────────────────────────────────────

class TestRssExport:
    def test_to_rss_returns_string(self):
        result = to_rss(SAMPLE_SEARCH_DATA)
        assert isinstance(result, str)

    def test_to_rss_has_rss_tag(self):
        result = to_rss(SAMPLE_SEARCH_DATA)
        assert "<rss" in result
        assert 'version="2.0"' in result

    def test_to_rss_has_channel(self):
        result = to_rss(SAMPLE_SEARCH_DATA)
        assert "<channel>" in result
        assert "<title>" in result

    def test_to_rss_has_items(self):
        result = to_rss(SAMPLE_SEARCH_DATA)
        assert "<item>" in result
        assert "Python.org" in result

    def test_to_rss_has_guid(self):
        result = to_rss(SAMPLE_SEARCH_DATA)
        assert "<guid" in result

    def test_to_rss_custom_title(self):
        result = to_rss(SAMPLE_SEARCH_DATA, feed_title="My Feed")
        assert "My Feed" in result

    def test_to_rss_empty_results(self):
        data = {"organic_results": [], "related_searches": []}
        result = to_rss(data)
        assert "<channel>" in result


# ── Format Router ────────────────────────────────────────────────────────

class TestFormatRouter:
    def test_export_json(self):
        result = export(SAMPLE_SEARCH_DATA, "json")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "organic_results" in parsed

    def test_export_csv(self):
        result = export(SAMPLE_SEARCH_DATA, "csv")
        assert isinstance(result, str)
        assert "title" in result

    def test_export_xml(self):
        result = export(SAMPLE_SEARCH_DATA, "xml")
        assert isinstance(result, str)
        assert "<" in result

    def test_export_rss(self):
        result = export(SAMPLE_SEARCH_DATA, "rss")
        assert isinstance(result, str)
        assert "rss" in result.lower()

    def test_export_unknown_format(self):
        with pytest.raises(ValueError, match="unsupported"):
            export(SAMPLE_SEARCH_DATA, "yaml")


# ── Webhook Improvements ────────────────────────────────────────────────

class TestWebhookRetry:
    def test_job_manager_init(self):
        jm = JobManager()
        assert jm.db is None
        assert jm._max_concurrent == 8

    def test_job_types(self):
        from jiro.jobs import JOB_TYPES
        assert "ai_search" in JOB_TYPES
        assert "ai_agent" in JOB_TYPES
        assert "batch_scrape" in JOB_TYPES

    @pytest.mark.asyncio
    async def test_submit_requires_runner(self):
        jm = JobManager()
        with pytest.raises(ValueError, match="runner required"):
            await jm.submit("ai_search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_submit_rejects_unknown_type(self):
        jm = JobManager()
        with pytest.raises(ValueError, match="unknown job type"):
            await jm.submit("unknown_type", {}, runner=AsyncMock())

    @pytest.mark.asyncio
    async def test_submit_and_get(self):
        jm = JobManager()
        async def runner(job_type, payload):
            return {"result": "ok"}
        record = await jm.submit("ai_search", {"q": "test"}, runner=runner)
        assert record["status"] in ("queued", "running")
        # Wait for completion
        await asyncio.sleep(0.5)
        job = await jm.get(record["id"])
        assert job is not None
        assert job["status"] == "completed"

    @pytest.mark.asyncio
    async def test_list_jobs(self):
        jm = JobManager()
        async def runner(job_type, payload):
            return {"result": "ok"}
        await jm.submit("ai_search", {"q": "test1"}, runner=runner)
        await jm.submit("ai_search", {"q": "test2"}, runner=runner)
        await asyncio.sleep(0.5)
        jobs = await jm.list_jobs(limit=10)
        assert len(jobs) >= 2

    @pytest.mark.asyncio
    async def test_job_failure(self):
        jm = JobManager()
        async def runner(job_type, payload):
            raise RuntimeError("test error")
        record = await jm.submit("ai_search", {"q": "test"}, runner=runner)
        await asyncio.sleep(0.5)
        job = await jm.get(record["id"])
        assert job["status"] == "failed"
        assert "test error" in (job.get("error") or "")


# ── SSE Headers ──────────────────────────────────────────────────────────

class TestSSEHeaders:
    def test_sse_headers_defined(self):
        from jiro.server.routers.search import SSE_HEADERS
        assert "Cache-Control" in SSE_HEADERS
        assert SSE_HEADERS["Cache-Control"] == "no-cache"

    def test_sse_format(self):
        from jiro.server.routers.search import _sse
        result = _sse("test_event", {"key": "value"})
        assert result.startswith("event: test_event")
        assert "data: " in result
        assert result.endswith("\n\n")
