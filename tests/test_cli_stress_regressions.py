"""Regressions for bugs found by the 2026-10-21 sandbox stress test.

Each test here corresponds to a documented failure. They are written to fail
loudly if the behaviour regresses, especially the ones that used to hang: a
hanging test is a broken test suite, so the subprocess calls are bounded.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

# Exit codes from jiro.cli
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_SUBSTITUTED = 3

# Anything slower than this in the "must not hang" tests is a failure. The
# original bugs blocked forever, so the bar is deliberately generous.
HANG_BUDGET_S = 45


def _run(args, timeout=HANG_BUDGET_S):
    """Run the CLI as a subprocess, returning (rc, stdout, stderr, seconds)."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "jiro", *args],
            capture_output=True, text=True, timeout=timeout, input="",
            # The CLI reconfigures its stdio to UTF-8, so decode as UTF-8 here
            # rather than inheriting the host's ANSI code page (cp1252), which
            # cannot represent non-Latin output.
            encoding="utf-8", errors="replace",
        )
        elapsed = time.monotonic() - start
        return proc.returncode, proc.stdout, proc.stderr, elapsed
    except subprocess.TimeoutExpired:
        pytest.fail(f"`jiro {' '.join(args)}` hung for more than {timeout}s")


# --------------------------------------------------------------------------
# Critical: commands that used to block forever with no admin key
# --------------------------------------------------------------------------
@pytest.mark.parametrize("args", [["keys", "list"], ["usage", "--days", "90"]])
def test_admin_commands_fail_fast_without_key(args, monkeypatch):
    """No admin key must be an immediate usage error, never an infinite prompt.

    `_admin_key_required` used to call typer.prompt() when stdin looked like a
    tty, which blocked forever (stdin.isatty() is unreliable on Windows).
    """
    monkeypatch.delenv("JIRO_ADMIN_KEY", raising=False)
    rc, out, err, elapsed = _run(args)
    assert elapsed < HANG_BUDGET_S, "command blocked instead of failing fast"
    assert rc == EXIT_USAGE, f"expected usage error, got rc={rc}"
    assert "admin" in (out + err).lower()


# --------------------------------------------------------------------------
# High: --json must be machine-parseable
# --------------------------------------------------------------------------
def test_json_output_is_valid_and_clean_stdout():
    """Output on stdout must parse as JSON, even at a narrow console width.

    The payload used to be printed through Rich, which word-wraps long lines and
    inserts real newlines inside JSON string values, so json.loads failed.
    """
    rc, out, err, _ = _run(["search", "web", "test query", "--num", "5", "--json"])
    # Any exit code is acceptable here (3 when substituted); stdout must be JSON.
    if not out.strip():
        pytest.skip("no search output available (no network/engines)")
    data = json.loads(out)  # raises if stdout was corrupted
    assert "search_metadata" in data
    assert data["search_metadata"].get("status") in ("success", "partial")


def test_json_status_agrees_with_substitution_flag():
    """status must be 'partial' exactly when engine_substituted is true."""
    rc, out, err, _ = _run(["search", "web", "test query", "--num", "5", "--json"])
    if not out.strip():
        pytest.skip("no search output available")
    meta = json.loads(out)["search_metadata"]
    if meta.get("engine_substituted"):
        assert meta["status"] == "partial"
        assert rc == EXIT_SUBSTITUTED
    else:
        assert meta["status"] == "success"
        assert rc == EXIT_OK


# --------------------------------------------------------------------------
# High: validation errors must fail fast with a usage exit code
# --------------------------------------------------------------------------
def test_unknown_engine_is_rejected():
    rc, out, err, _ = _run(["search", "web", "test", "--engine", "nonexistent_engine"])
    assert rc == EXIT_USAGE
    combined = (out + err).lower()
    assert "unknown engine" in combined
    # The message must list real engines so the user can correct the typo.
    assert "google" in combined


def test_empty_scrape_url_is_rejected():
    rc, out, err, _ = _run(["scrape", ""])
    assert rc == EXIT_USAGE
    assert "url" in (out + err).lower()


def test_non_numeric_num_is_rejected():
    rc, out, err, _ = _run(["search", "web", "test", "--num", "abc"])
    assert rc == EXIT_USAGE


# --------------------------------------------------------------------------
# Critical: non-ASCII must not crash the console renderer
# --------------------------------------------------------------------------
def test_non_ascii_query_does_not_crash():
    """CJK/Cyrillic used to die with UnicodeEncodeError (cp1252 console)."""
    rc, out, err, _ = _run(["search", "web", "日本語テスト 中文搜索 한국어", "--num", "3"])
    assert "UnicodeEncodeError" not in err
    assert "UnicodeEncodeError" not in out
    assert "charmap" not in err


# --------------------------------------------------------------------------
# Low: the starlette deprecation must not pollute stderr
# --------------------------------------------------------------------------
def test_starlette_warning_is_not_emitted():
    rc, out, err, _ = _run(["keys", "list"])
    assert "StarletteDeprecationWarning" not in err
    assert "StarletteDeprecationWarning" not in out


# --------------------------------------------------------------------------
# Config: engines named in config but not registered must be dropped
# --------------------------------------------------------------------------
def test_unregistered_engines_are_filtered_from_config():
    from jiro.config import Settings

    settings = Settings.load(None)
    settings.raw["scraping"]["engines"] = ["google", "mock", "bing"]
    # "mock" has no parser; it must not be advertised as usable.
    assert "mock" not in settings.engines
    assert "google" in settings.engines
    assert "bing" in settings.engines


def test_config_engines_fall_back_to_default_when_all_invalid():
    from jiro.config import Settings

    settings = Settings.load(None)
    settings.raw["scraping"]["engines"] = ["mock", "definitely_not_real"]
    # Never return an empty engine list; that would break every search.
    assert settings.engines
