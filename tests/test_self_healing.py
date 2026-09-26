"""Tests for the self-healing layer."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from jiro.scraping.social.self_healing import (
    EngineBlockedError,
    HealingStats,
    SelectorError,
    StaleHashError,
    _is_blocked_error,
    _is_dns_error,
    _is_file_lock_error,
    _is_http_5xx,
    _is_selector_error,
    _is_ssl_error,
    _is_stale_hash_error,
    _is_winerror_32,
    _validate_result,
    heal,
    heal_async,
    heal_pip_install,
    get_stats,
    reset_stats,
    unblock_all,
)


class DummyScraper:
    platform = "test"
    breaker = MagicMock(cooldown=60.0)


class SuccessScraper:
    platform = "ok"
    client = MagicMock()


class FailScraper:
    platform = "fail"


@pytest.mark.asyncio
async def test_healing_stats_success_rate():
    stats = HealingStats()
    assert stats.success_rate("unknown") == 1.0
    await stats.record_attempt("x")
    await stats.record_success("x")
    await stats.record_failure("x", "err")
    assert stats.success_rate("x") == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_healing_stats_blocked_async():
    stats = HealingStats()
    await stats.mark_blocked("x", 0.0)
    assert stats.is_blocked("x")
    stats.unblock("x")
    assert not stats.is_blocked("x")


@pytest.mark.asyncio
async def test_heal_async_success():
    scraper = SuccessScraper()
    result = await heal_async(scraper, lambda: "ok")
    assert result == "ok"


@pytest.mark.asyncio
async def test_heal_async_coroutine():
    scraper = SuccessScraper()

    async def coro():
        return 42

    result = await heal_async(scraper, coro)
    assert result == 42


@pytest.mark.asyncio
async def test_heal_async_stale_hash():
    scraper = DummyScraper()
    scraper._query_hashes = {"a": "1"}

    calls = [0]

    def flaky():
        calls[0] += 1
        if calls[0] == 1:
            raise StaleHashError("bad hash")
        return "recovered"

    result = await heal_async(scraper, flaky)
    assert result == "recovered"


@pytest.mark.asyncio
async def test_heal_async_blocked():
    scraper = DummyScraper()
    scraper.client = MagicMock()
    scraper.client.fingerprint = MagicMock()

    calls = [0]

    def flaky():
        calls[0] += 1
        if calls[0] == 1:
            raise EngineBlockedError("captcha")
        return "unblocked"

    result = await heal_async(scraper, flaky)
    assert result == "unblocked"


@pytest.mark.asyncio
async def test_heal_async_raises_after_max_retries():
    scraper = FailScraper()

    def always_fail():
        raise ValueError("permanent failure")

    with pytest.raises(ValueError):
        await heal_async(scraper, always_fail)


@pytest.mark.asyncio
async def test_heal_async_cancellable():
    scraper = FailScraper()

    async def slow_fail():
        await asyncio.sleep(10)
        raise ValueError("too late")

    with pytest.raises(asyncio.CancelledError):
        task = asyncio.create_task(heal_async(scraper, slow_fail))
        await asyncio.sleep(0.1)
        task.cancel()
        await task


def test_heal_sync_success():
    scraper = SuccessScraper()
    assert heal(scraper, lambda: "ok") == "ok"


def test_heal_sync_rejects_coroutine():
    scraper = SuccessScraper()

    async def coro():
        return "nope"

    with pytest.raises(RuntimeError, match="heal_async"):
        heal(scraper, coro)


def test_validate_result():
    assert _validate_result("hello")
    assert _validate_result([1, 2, 3])
    assert _validate_result({"a": 1})
    assert not _validate_result("")
    assert not _validate_result([])
    assert not _validate_result({})
    assert not _validate_result(None)


def test_error_classifiers():
    assert _is_stale_hash_error(StaleHashError("query_hash invalid"))
    assert _is_selector_error(SelectorError("css selector not found"))
    assert _is_blocked_error(EngineBlockedError("captcha detected"))
    assert _is_file_lock_error(OSError("[WinError 32] file in use"))
    assert _is_dns_error(OSError("[Errno -2] socket.gaierror"))
    assert _is_ssl_error(OSError("ssl certificate verify failed"))
    assert _is_http_5xx(RuntimeError("500 internal server error"))
    assert not _is_http_5xx(RuntimeError("400 bad request"))


def test_is_winerror_32():
    assert _is_winerror_32(OSError("[WinError 32] The process cannot access the file because it is being used by another process: 'jiro.exe'"))
    assert _is_winerror_32(OSError("WinError 32 file in use"))
    assert _is_winerror_32(OSError("winerror 32 permission denied"))
    assert not _is_winerror_32(RuntimeError("some other error"))
    assert not _is_winerror_32(OSError("[Errno 2] No such file"))


@pytest.mark.asyncio
async def test_heal_pip_install_success(monkeypatch):
    import subprocess
    from jiro.scraping.social.self_healing import heal_pip_install

    def mock_run(*args, **kwargs):
        class Result:
            returncode = 0
            stdout = "success"
            stderr = ""
        return Result()

    monkeypatch.setattr(subprocess, "run", mock_run)
    result = heal_pip_install([sys.executable, "-m", "pip", "install", "--upgrade", "jirosearch"])
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_heal_pip_install_retry_on_lock(monkeypatch):
    import subprocess
    from unittest.mock import MagicMock
    from jiro.scraping.social.self_healing import heal_pip_install

    call_count = [0]

    def mock_run(*args, **kwargs):
        call_count[0] += 1
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "ERROR: Could not install packages due to an OSError: [WinError 32] The process cannot access the file because it is being used by another process: 'jiro.exe'"
        return result

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr("jiro.scraping.social.self_healing._kill_jiro_exe", MagicMock(return_value=True))
    monkeypatch.setattr("time.sleep", lambda x: None)

    result = heal_pip_install([sys.executable, "-m", "pip", "install", "--upgrade", "jirosearch"], max_retries=3)
    assert call_count[0] == 3


def test_is_file_lock_error_winerror_32():
    assert _is_file_lock_error(OSError("[WinError 32] The process cannot access the file because it is being used by another process"))


@pytest.mark.asyncio
async def test_get_stats_async():
    stats = get_stats()
    assert "attempts" in stats
    assert "success_rates" in stats


@pytest.mark.asyncio
async def test_reset_and_unblock():
    reset_stats()
    unblock_all()
