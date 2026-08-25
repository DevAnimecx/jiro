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

## [Unreleased]

- Jiro Cloud (managed SaaS) — roadmap.
- Jiro Enterprise (air-gapped BSL-1.0 license, SOC 2 path) — roadmap.
- Plugin marketplace — roadmap.
- PostgreSQL backend for horizontal scaling — roadmap.
