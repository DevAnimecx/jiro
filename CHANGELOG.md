# Changelog

All notable changes to Jiro Search are documented here. This project adheres to
[Semantic Versioning](https://semver.org/) and
[Keep a Changelog](https://keepachangelog.com/).

## [0.1.0] — 2026-08-26

### Added
- **Search** across 9 engines (Google, Bing, DuckDuckGo, Brave, YouTube, Amazon,
  eBay, Yandex, Baidu) with automatic fallback, UA rotation, retries and
  per-engine circuit breaker.
- **Universal scraper**: URL → markdown/text/html/JSON, readability extraction,
  OpenGraph/Twitter/JSON-LD metadata, custom CSS/XPath/JSONPath recipes.
- **Agentic research**: `/ai/search` (plan → search → scrape → synthesize with
  citations) and `/ai/agent` multi-step autonomous research, both with SSE
  streaming and extractive fallback when no LLM key is present.
- **MCP server** (`jiro mcp`) over stdio, Streamable HTTP and legacy SSE, with
  `search` / `scrape` / `ai_search` tools, prompts and autocompletion.
- **AI-agent integration**: OpenAI/Anthropic/Gemini function-calling schemas,
  LangChain & LlamaIndex wrappers.
- **Cache**: SQLite (WAL) or Redis, `fresh=true` bypass, semantic (embedding)
  cache for fuzzy reuse.
- **Team features**: hashed API keys, admin/user roles + scopes, per-key rate
  limits, JWT, usage tracking (`/usage`, `/metrics`).
- **Compliance layer**: robots.txt parsing, ToS acknowledgments persisted to
  SQLite, audit logging, immutable-ish acknowledgment records.
- **BYOK**: proxies (custom + BrightData/Oxylabs/ScraperAPI/ZenRows/Smartproxy
  presets), CAPTCHA solvers (2Captcha/CapSolver), LLM keys (OpenAI/Anthropic/
  Gemini/OpenRouter/Ollama).
- **Async jobs** (`/jobs`) with webhook delivery (HMAC-signed).
- **Ops**: Prometheus `/metrics`, Docker + Helm, structured JSON logs.
- **Plugin architecture**: `jiro plugins create/validate/list` to scaffold and
  register custom engines.

### Fixed
- `jiro/cli_plugins.py` scaffold template f-string bug (`{href}`/`{self.name}`
  raised `NameError` on `create_plugin`).
- `jiro/db.py` missing `tos_acknowledgments` table — `acknowledge_tos` now
  persists and `has_acknowledged_async` checks the DB.
- `jiro/scraping/client.py normalize_url` base64 padding + raw fallback for
  Bing `u=a1...` redirect links.
- `jiro/models.py SearchRequest.q` max_length=500 request-validation guard.

### Tests
- 380 passing, 10 skipped, 8 network-deselected (0 failures).

---

## [0.1.1] — 2026-08-26 (security & reliability hardening)

### Added
- **SSRF protection** (`jiro/security.py`): blocks loopback, RFC1918 private,
  link-local (incl. cloud metadata `169.254.169.254`), ULA and IPv6 link-local
  scrape targets; refuses to scrape the server's own host; DNS-resolution
  failures fail **open** so air-gapped/offline use still works.
- `SSRFError` raised (HTTP 400) when a user-controlled scrape target is blocked.
- **Startup security guard** (`jiro/server`): refuses to bind a non-loopback host
  with auth disabled unless `--insecure`; warns when auth is disabled on loopback.
- **JWT RS256 + `kid`** support and `validate_security_config()` startup check
  that refuses to start when auth is enabled with a missing/short (`<32` char)
  `jwt_secret`.
- **Redis-backed rate limiting** (`check_rate_limit_async`) with in-memory
  fallback; wired into REST and MCP HTTP transports.
- **SQLite connection pool** (concurrent reads, serialized writes) + automatic
  `schema_version` migrations.
- **Agent hardening**: `deadline_seconds`, `content_budget_chars` truncation and
  LLM-call timeouts via `asyncio.wait_for`.
- **robots.txt enforcement** for scrape targets (best-effort, fail-open).
- **`/health/engines`** status endpoint; concurrent batch scrape with a
  `Semaphore(10)`; enriched structured request logging (`key_id`/`engine`).
- `--insecure` flag on `jiro serve`; graceful-shutdown timeout on uvicorn runs.
- Regression tests for the SSRF validator (`tests/test_security.py`).

### Changed
- `PermissionError` builtin shadow renamed to `JiroPermissionError`.
- Cookie jar logs warnings instead of silently swallowing.

### Tests
- 390 passing, 10 skipped, 8 network-deselected (0 failures).

### Maintenance
- Enabled `pydantic.mypy` plugin; fixed all `ruff` (144) and `mypy` (87)
  lint/type errors: unused imports & variables, ambiguous names, SSRF annotation
  import, duplicate `MCPError`, genuine `None`/type mismatches in parsers, db
  pool, auth/jobs/robots typing, and unreachable `await _authenticate` call in the
  MCP stream endpoint.

---

## [Unreleased]

- Jiro Cloud (managed SaaS) — roadmap.
- Jiro Enterprise (air-gapped BSL-1.0 license, SOC 2 path) — roadmap.
- Plugin marketplace — roadmap.
- PostgreSQL backend for horizontal scaling — roadmap.
