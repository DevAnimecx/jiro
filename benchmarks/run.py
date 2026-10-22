"""Reproducible benchmark harness for the v0.4.0 claims.

This script does not invent numbers. It runs a live JIRO server, records raw
runs, and writes the artifacts referenced by ``benchmarks/claims.yaml``.

Example:
    python benchmarks/run.py --base-url http://127.0.0.1:8000 --runs 30
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jiro.parametric.compress import estimate_tokens  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _guarded(fn):
    """Run ``fn`` and return the exception instead of raising, so one failed
    call in a batch does not discard the results of its siblings."""

    def _wrapped(arg):
        try:
            return fn(arg)
        except Exception as exc:
            return exc

    return _wrapped


def _request(url: str, *, method: str = "GET", payload: Dict[str, Any] | None = None,
             timeout: float = 60.0) -> Dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        return json.loads(body)


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


def _stats(values: List[float]) -> Dict[str, Any]:
    return {
        "p50": round(statistics.median(values), 3) if values else None,
        "p95": round(_percentile(values, 95), 3) if values else None,
        "unit": "s",
        "runs": len(values),
    }


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        return "unknown"


def _version() -> str:
    try:
        from jiro import __version__

        return __version__
    except Exception:
        return "unknown"


def run_baseline(base_url: str, workload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    started = time.perf_counter()
    searches = 0
    pages = 0
    raw_tokens = 0
    errors = []
    config = workload.get("config", {})
    from concurrent.futures import ThreadPoolExecutor
    from urllib.request import Request as _Req, urlopen as _open

    def _search(query: str) -> None:
        params = urllib.parse.urlencode(
            {"q": query, "engine": "auto", "num": config.get("num", 5)}
        )
        _request(f"{base_url}/search?{params}", timeout=timeout)

    def _scrape(url: str) -> int:
        data = _request(
            f"{base_url}/scrape",
            method="POST",
            payload={"url": url, "format": "markdown"},
            timeout=timeout,
        )
        return estimate_tokens(str(data.get("content") or ""))

    # Issue the baseline's independent calls concurrently, matching the
    # client-side parallelism a real caller uses. Run serially the searches
    # contend with one another and the baseline collapses to a concurrency
    # artifact rather than a 0.3.3-vs-0.4.0 comparison.
    workers = max(1, int(config.get("baseline_concurrency", 5)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for query, exc in zip(
            workload["subqueries"], pool.map(_guarded(_search), workload["subqueries"])
        ):
            if exc is not None:
                errors.append(f"search {query!r}: {exc}")
            else:
                searches += 1
        for url, result in zip(
            workload["sources"], pool.map(_guarded(_scrape), workload["sources"])
        ):
            if isinstance(result, Exception):
                errors.append(f"scrape {url}: {result}")
            else:
                raw_tokens += result
                pages += 1
    elapsed = time.perf_counter() - started
    # Every fetched page counts as a citable source. The previous formula
    # capped this at `required_citations` (which defaults to 1), so a run that
    # pulled 49,718 tokens of raw markdown was credited with a single citation
    # and the ratio measured download volume rather than extraction.
    citations = max(1, pages)
    return {
        "duration_s": round(elapsed, 3),
        "searches": searches,
        "pages": pages,
        "raw_tokens": raw_tokens,
        "tokens_per_citation": round(raw_tokens / citations, 3) if raw_tokens else None,
        "citations": citations,
        "errors": errors,
    }


def run_candidate(base_url: str, workload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    config = workload.get("config", {})
    payload = {
        "query": workload["query"],
        "num": config.get("num", 5),
        "max_tokens": config.get("max_tokens", 1200),
        "deadline_ms": config.get("deadline_ms", 0),
        "fetch_top": config.get("fetch_top", 10),
    }
    started = time.perf_counter()
    try:
        data = _request(f"{base_url}/v1/graph/search", method="POST", payload=payload,
                        timeout=timeout)
    except Exception as exc:
        return {
            "duration_s": round(time.perf_counter() - started, 3),
            "error": str(exc),
            "packet": None,
        }
    elapsed = time.perf_counter() - started
    packet = data.get("packet") or {}
    claims = packet.get("claims") or []
    return {
        "duration_s": round(elapsed, 3),
        "status": data.get("status"),
        "graph_id": data.get("graph_id"),
        "used_tokens": packet.get("used_tokens"),
        "claims": len(claims),
        "citations": len([c for c in claims if c.get("source_ids")]),
        "tokens_per_citation": (
            round(float(packet.get("used_tokens") or 0) / max(1, len(claims)), 3)
            if packet
            else None
        ),
        "gaps": len(data.get("gaps") or []),
        "packet": packet,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--workload",
        default=str(ROOT / "benchmarks" / "workloads" / "research_5q_10src.json"),
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--out", default=str(ROOT / "docs" / "v0.4.0" / "benchmarks"))
    parser.add_argument(
        "--cache-state",
        default="unknown",
        choices=["cold", "warm", "unknown"],
        help="Declare the server's cache state for this run, e.g. 'cold' after "
        "clearing the `cache` table in the Jiro database.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    workload = json.loads(Path(args.workload).read_text(encoding="utf-8"))
    if args.dry_run:
        print(json.dumps({
            "workload": workload["id"],
            "base_url": args.base_url,
            "candidate": {"method": "POST", "path": "/v1/graph/search"},
            "baseline": [
                {"method": "GET", "path": "/search"},
                {"method": "POST", "path": "/scrape"},
            ],
            "runs": args.runs,
        }, indent=2))
        return 0

    if args.runs < 1:
        print("error: --runs must be >= 1", file=sys.stderr)
        return 2

    baseline_runs: List[Dict[str, Any]] = []
    candidate_runs: List[Dict[str, Any]] = []
    for _ in range(args.runs):
        baseline_runs.append(run_baseline(args.base_url, workload, args.timeout))
        candidate_runs.append(run_candidate(args.base_url, workload, args.timeout))

    # Time every run that returned a response on both sides. An earlier version
    # dropped any baseline run carrying *any* per-call error while keeping every
    # candidate run, so a single blocked third-party scrape emptied the
    # baseline sample and left the ratio null. A run that completed is a
    # measurement of the path even if one of its sources refused to serve.
    baseline_ok = [r["duration_s"] for r in baseline_runs if r.get("duration_s")]
    candidate_ok = [
        r["duration_s"] for r in candidate_runs if r.get("duration_s")
    ]
    base_stats = _stats(baseline_ok)
    cand_stats = _stats(candidate_ok)
    speed_ratio = (
        round(base_stats["p50"] / cand_stats["p50"], 3)
        if base_stats["p50"] and cand_stats["p50"]
        else None
    )

    def _err_count(run: Dict[str, Any]) -> int:
        return len(run.get("errors") or [])

    baseline_calls = sum(r.get("searches", 0) + r.get("pages", 0) for r in baseline_runs)
    baseline_errors = sum(_err_count(r) for r in baseline_runs)
    coverage = {
        "baseline_calls_ok": baseline_calls,
        "baseline_calls_failed": baseline_errors,
        "baseline_coverage": (
            round(baseline_calls / (baseline_calls + baseline_errors), 3)
            if (baseline_calls + baseline_errors)
            else None
        ),
        "note": (
            "Per-call failures are third-party transport errors (blocked or "
            "rate-limited source sites), not Jiro errors. They are retained in "
            "raw and excluded from no timing sample."
        ),
    }

    baseline_tpc = [
        r["tokens_per_citation"] for r in baseline_runs if r.get("tokens_per_citation")
    ]
    candidate_tpc = [
        r["tokens_per_citation"] for r in candidate_runs if r.get("tokens_per_citation")
    ]
    base_tpc = round(statistics.median(baseline_tpc), 3) if baseline_tpc else None
    cand_tpc = round(statistics.median(candidate_tpc), 3) if candidate_tpc else None
    efficiency_ratio = (
        round(base_tpc / cand_tpc, 3) if base_tpc and cand_tpc else None
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    created = _now()
    hardware = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
    }
    common = {
        "created_at": created,
        "commit": _commit(),
        "version": _version(),
        "hardware": hardware,
        "network": {"class": "live", "base_url": args.base_url},
        "workload": workload["id"],
        "cache_state": args.cache_state,
        "reproduction": (
            f"python benchmarks/run.py --base-url {args.base_url} --runs {args.runs}"
        ),
        "raw": {"baseline": baseline_runs, "candidate": [
            {k: v for k, v in run.items() if k != "packet"} for run in candidate_runs
        ]},
    }

    speed = {
        "claim_id": "speed-2x",
        **common,
        "runs": len(baseline_ok),
        "coverage": coverage,
        "metrics": {
            "baseline": base_stats,
            "candidate": cand_stats,
            "ratio": speed_ratio,
            "target": 2.0,
            "met": bool(speed_ratio and speed_ratio >= 2.0),
        },
        "notes": "Every run that returned a response is timed on both sides; per-call transport failures are recorded in coverage.",
    }
    (out / "speed.json").write_text(json.dumps(speed, indent=2), encoding="utf-8")

    efficiency = {
        "claim_id": "efficiency-3x",
        **common,
        "runs": len(candidate_ok),
        "metrics": {
            "baseline": {"tokens_per_citation": base_tpc},
            "candidate": {"tokens_per_citation": cand_tpc},
            "ratio": efficiency_ratio,
            "target": 3.0,
            "met": bool(efficiency_ratio and efficiency_ratio >= 3.0),
        },
        "quality": {
            "status": "not_measured",
            "note": "This artifact cannot support the 3x claim until the rubric/human quality gate runs.",
        },
    }
    (out / "efficiency.json").write_text(json.dumps(efficiency, indent=2), encoding="utf-8")

    last_packet = next(
        (run["packet"] for run in reversed(candidate_runs) if run.get("packet")), None
    )
    if last_packet:
        claims = last_packet.get("claims") or []
        citations = {
            "claim_id": "citation-complete",
            **common,
            "metrics": {
                "claims": len(claims),
                "claims_with_source": len([c for c in claims if c.get("source_ids")]),
                "ratio": round(
                    len([c for c in claims if c.get("source_ids")]) / max(1, len(claims)), 6
                ),
                "target": 1.0,
                "met": all(c.get("source_ids") for c in claims),
            },
        }
        (out / "citations.json").write_text(json.dumps(citations, indent=2), encoding="utf-8")

    print(json.dumps({
        "speed_ratio": speed_ratio,
        "efficiency_ratio": efficiency_ratio,
        "speed_met": speed["metrics"]["met"],
        "efficiency_met": efficiency["metrics"]["met"],
        "artifacts": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
