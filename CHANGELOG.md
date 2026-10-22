# Changelog

All notable changes to Jiro will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-10-22

Query graph, evidence packets, and a durable runtime, plus the performance work
that makes them viable, plus the fixes from a full 8-phase sandbox stress test.

### Added
- **Query graph** (`POST /v1/graph/search`, `jiro graph plan|run`): a planner turns a query into a DAG of SEARCH / FETCH / MERGE / EXTRACT / VERIFY / COMPRESS / SYNTHESIZE nodes and the executor runs it. Dispatch is ready-driven — a node starts when its own parents finish, not when its topological level drains. Timeouts are derived from a rolling per-engine p50/p95 latency window rather than a constant, so `--deadline-ms 0` (the new default) means "derive it".
- **Evidence packets**: results are compressed into claims, each carrying the source spans that support it, so a caller gets citations rather than raw markdown.
- **Engine racing** (`search.race_engines`): a slow request can be duplicated on an alternate engine and the first success wins, with preference order deciding among successes so a faster substitute cannot silently replace the requested engine.
- **Hedging** (`hedge_after_ms`): a SEARCH node whose primary attempt is slow starts a second attempt and takes the first success.
- **MCP handoff protocol**: a task can be handed to another client as a structured envelope rather than a free-text handoff.
- Bounded retries (`scraping.retry_budget_seconds`) so a retry tail cannot extend a request past its caller's patience, and a pooled per-proxy HTTP client.
- Batched research-source writes via `ResearchRepository.add_sources`, replacing one awaited insert per result.
- `benchmarks/setup_artifact.py`, `benchmarks/quality_gate.py`, and the four claim artefacts under `docs/v0.4.0/benchmarks/`.
- `search_metadata.status` now reports fidelity, not just transport. A search that falls back to a substitute engine is `"partial"` instead of `"success"`, so a caller can tell an exact-engine result from a substituted one without inspecting `engine_substituted`. Cached and semantic-cache responses derive it the same way.
- CLI exit code `3` (substituted engine) alongside the existing `0` success / `1` failure / `2` usage error. `jiro search web` prints which engine was requested, which served, and why the requested one was skipped.
- `tests/test_cli_stress_regressions.py` covering the hangs, JSON validity, validation exits, and non-ASCII output.

### Performance

All four 0.4.0 claims are backed by committed artifacts under
`docs/v0.4.0/benchmarks/`. Reproduce with `python benchmarks/report.py`.

- **2x faster on multi-source research** — measured **81.6x** on workload
  `research_5q_10src`, cold cache (p50 2.9s baseline vs 0.04s). The ratio varies
  by run with live-search latency; it was as low as 2.0x across runs, so treat
  81.6x as this machine's measurement, not a floor.
- **3x fewer tokens per verified citation** — measured **59.2x** (7,103 tokens
  per citation raw markdown vs 120 in the evidence packet).
  > **Caveat — quality gate pending.** The non-inferiority gate from
  > `docs/v0.4.0/CLAIMS.md` §3 has not run: it requires 30 LLM-judge runs and 50
  > blind human spot checks. This is a tokens-per-citation measurement, *not* a
  > claim of equivalent answer quality. If the gate fails, the number is
  > withdrawn.
- **Every claim cites a source** — 1.0 (20/20 non-gap claims carry a source).
- **15+ agents, one command** — 15/15 tier-A MCP clients configured and
  verified, each driven through `mcp_setup.setup_client` in a temporary HOME
  with its config read back.

### Changed
- **Exit code `3` is a new, breaking signal**: scripts that treat any non-zero exit as failure will now see substituted searches as failures. That is the intent — the alternative was a silent wrong-index result.
- `POST /search/batch` returns HTTP 502 with `error_code: "batch_failed"` when *every* item fails, instead of HTTP 200 with per-item errors. Partial success still returns 200.
- Pagination fetches its pages concurrently and merges them in page order.

### Fixed
- Scraping retries referenced `_time`, which was never imported, so every search through the HTTP client raised `NameError` and `/search` returned HTTP 500.
- Pagination returned fewer results than requested. `page_size = max(have, 10)` skipped a page whenever the first page came back short, so `num=9` returned 6.
- `jiro keys list` and `jiro usage` no longer hang forever. `_admin_key_required()` prompted on stdin, and the `stdin.isatty()` guard is unreliable on Windows (it reports a tty even with stdin redirected). Both now exit `2` with an actionable message.
- Non-ASCII queries no longer crash with `UnicodeEncodeError`. `stdout`/`stderr` are reconfigured to UTF-8 at startup, and Rich is constructed with `legacy_windows=False` so it stops writing through the ANSI code page (cp1252). Affected CJK, Cyrillic, Arabic, and emoji.
- `jiro doctor` no longer reports "No internet connectivity" on a working connection. The check probed only `8.8.8.8:80`; networks that block that one endpoint but nothing else failed the check. It now probes several endpoints and downgrades to a warning, naming what failed.
- `jiro doctor`'s encoding check tests what `stdout` can actually encode rather than trusting `PYTHONIOENCODING`, which said "OK" while the console crashed on non-ASCII.
- `--json` output is valid JSON again. It was printed through Rich, which word-wraps long lines and inserted real newlines inside string values, so `json.loads` failed on any long result. Payloads now go straight to `stdout`, and warnings go to `stderr` so stdout stays parseable.
- `--engine <unknown>` exits `2` with the list of valid engines instead of returning an empty result set and exiting `0`.
- `jiro scrape ""` exits `2` instead of silently searching for the empty string.
- Scrape requests pass an explicit per-request timeout. On the `curl_cffi` path the timeout came only from the session, so an origin that never responded hung the caller indefinitely (observed: `httpbin.org/delay/15` past 30s).
- Engines named in config that have no registered parser are dropped with a warning. A stale name such as `mock` was previously advertised as available at startup and then failed every request with a 422. Also removed the `mock` special-case from the MCP engine allowlist, which disagreed with the registry.
- The `StarletteDeprecationWarning` about `httpx` no longer pollutes stderr on every command that spins up the in-process app.

## [0.3.3] - 2026-10-02

### Added
- **Real result pagination**: `SearchOrchestrator._paginate()` walks successive SERP pages on the same engine, so `num` above a single page (~10 rows) now returns the requested count instead of silently serving 3 results. Bounded by `search.pagination.max_pages` (default 5) and deduped by link.
- `BaseEngine.supports_pagination` flag, opt-in per engine. Enabled for Google (`start`), Bing (`first`), and Baidu (`pn`) — the engines whose upstream request actually accepts a page offset.
- **Live engine health probes**: `SearchOrchestrator.probe_engine()` issues a real search request per engine, so rate limits, bot walls, and parse regressions surface as failures instead of reporting every engine healthy because its module imported. `health_check(live=True)` is now the default; `live=False` keeps the cheap instantiation-only mode.
- Liveness and readiness probes (`GET /health/live`, `GET /health/ready`) for orchestrators and for the jiro-web status page.
- Expanded MCP tool surface with structured input validation (see `tests/test_mcp_tool_fixes.py`).

### Changed
- **Honest engine attribution**: `search_metadata` now always carries `engine_requested` and `engine_substituted`, so a caller can never mistake a fallback engine for the one it asked for. Applied to fresh results, exact cache hits, and semantic cache hits.
- Short pages are disclosed instead of hidden: `requested_num`, `truncated`, `pagination_supported`, and `pages_fetched` are reported when fewer results than requested are available.
- Unified `0.3.3` version across the CLI banner, dashboard, OpenAPI spec, npm package, and the Python/JavaScript/Go SDKs.

### Fixed
- Server startup fails fast with an actionable error when `auth.jwt_secret` is empty but auth is enabled (set `JIRO_AUTH__JWT_SECRET` to 32+ random bytes, or `JIRO_JWT_SECRET` via config interpolation).
- `jiro ai` no longer passes the removed `timeout` kwarg to Starlette's `TestClient`; per-request timeout now uses `backend_options={"timeout": ...}`.
- `_heal_file_lock()` no longer shadows the module-level `os` import.

## [0.3.1] - 2026-09-26

### Added
- Official package alignment across the CLI, JavaScript SDK, Python SDK, and Cloud API at `0.3.1`.
- **Auto-heal update system** (`heal_pip_install()`, `_kill_jiro_exe()`, `_is_winerror_32()`) in `self_healing.py` for automatic WinError 32 recovery during pip upgrades.
- Built-in engine plugin auto-registration for arXiv, GitHub, Google Scholar, Hacker News, Reddit, and Wikipedia.
- Cloud pricing endpoint and synchronized credit actions for search, scrape, AI, agent, MCP, and AEO feed operations.
- CI workflow: pytest (non-network tests) + pip-audit + gitleaks on every push.
- Release guard tests for social normalizers and the curl response adapter (15 tests).

### Changed
- Hardened internal authentication, API-key ownership checks, WAF query normalization, and circuit-breaker failure handling.
- Improved async session validation and Firebase realtime token refresh across web authentication flows.
- Updated CLI auth, dashboard navigation, favicon branding, and public documentation for the completed release.
