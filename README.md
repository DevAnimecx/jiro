<!--
SEO meta description (used by search engines indexing this README/repo):
Jiro Search — a local-first, AI-native, self-hosted web search and scraping API.
Free open-source SerpAPI alternative with a built-in MCP server, agentic research,
9 search engines, legal compliance, and first-class AI-agent integrations
(Claude, Codex, Cursor, Manus, Hermes). pip install jirosearch.
-->

<p align="center">
  <a href="https://github.com/DevAnimecx/jiro">
    <img src="https://github.com/DevAnimecx/jiro/blob/main/assests/Search%20with%20ai.png?raw=true alt="Jiro Search" width="240"
         onerror="this.src='https://github.com/DevAnimecx.png?size=240';this.alt='Jiro';" />
  </a>
</p>

<h1 align="center">Jiro Search — Local-First, AI-Native Web Search & Scraping API</h1>

<p align="center">
  <b>A free, open-source <a href="https://serpapi.com">SerpAPI</a> alternative.</b>
  Self-hosted search + scrape + agentic research + <b>MCP server</b>, built for AI agents.
</p>

<p align="center">
  <a href="https://pypi.org/project/jirosearch/"><img src="https://img.shields.io/pypi/v/jirosearch?color=blue&label=pypi%20jirosearch" alt="PyPI jirosearch"></a>
  <a href="https://pypi.org/project/jirosearch/"><img src="https://img.shields.io/pypi/pyversions/jirosearch.svg" alt="Python 3.11+"></a>
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
  <a href="https://github.com/DevAnimecx/jiro/actions"><img src="https://img.shields.io/badge/tests-390%20passing-brightgreen" alt="Tests"></a>
  <a href="https://github.com/DevAnimecx/jiro/stargazers"><img src="https://img.shields.io/github/stars/DevAnimecx/jiro?style=social" alt="GitHub stars"></a>
  <a href="https://github.com/DevAnimecx/jiro/network/members"><img src="https://img.shields.io/github/forks/DevAnimecx/jiro?style=social" alt="GitHub forks"></a>
  <a href="https://github.com/DevAnimecx/jiro/pkgs/container/jiro"><img src="https://img.shields.io/badge/docker-ghcr.io%2FDevAnimecx%2Fjiro-blue" alt="Docker"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python">
</p>

<p align="center">
  <a href="https://github.com/DevAnimecx/jiro/discussions"><img src="https://cdn.simpleicons.org/github/EAC54F" width="22" alt="GitHub Discussions"></a>
  &nbsp;
  <a href="https://www.producthunt.com/search?q=jiro%20search"><img src="https://cdn.simpleicons.org/producthunt/FB6831" width="22" alt="Product Hunt"></a>
  &nbsp;
  <a href="https://www.reddit.com/search/?q=jiro%20search"><img src="https://cdn.simpleicons.org/reddit/FF4500" width="22" alt="Reddit"></a>
  &nbsp;
  <a href="https://dev.to/devanimecx"><img src="https://cdn.simpleicons.org/devdotto/0A0A0A" width="22" alt="dev.to"></a>
  &nbsp;
  <a href="https://www.instagram.com/explore/tags/jirosearch/"><img src="https://cdn.simpleicons.org/instagram/E4405F" width="22" alt="Instagram"></a>
  &nbsp;
  <a href="https://www.threads.net/"><img src="https://cdn.simpleicons.org/threads/000000" width="22" alt="Threads"></a>
  &nbsp;
  <a href="https://x.com/DevAnimecx"><img src="https://cdn.simpleicons.org/x/000000" width="22" alt="X.com"></a>
</p>

---

> **Jiro** scrapes Google, Bing, DuckDuckGo, Brave, YouTube, Amazon, eBay, Yandex and Baidu
> directly — no third-party search API, no per-query billing, no cloud lock-in. Results are
> cached locally in SQLite (sub-50 ms cached responses) and exposed through a **SerpAPI-compatible
> REST API**, an **MCP server**, and **agentic research** with citations. Bring your own keys (BYOK)
> for proxies, CAPTCHA solvers and LLM providers.

**Status:** Production-ready MVP (open-source, MIT). Self-host it for $0 or subscribe to **Jiro Cloud** for a managed proxy fleet, SLA and compliance dashboard.
**Responsible use:** search engines actively fight bots. From residential IPs (and with BYOK proxies) Google/DuckDuckGo work; on datacenter IPs Jiro automatically falls back across engines (`google → bing → brave → duckduckgo`). Respect each engine's ToS and `robots.txt`.

---

## Table of Contents
- [What's New](#whats-new)
- [Why Jiro? (SerpAPI Alternative, Self-Hosted)](#why-jiro)
- [One-Command Start](#one-command-start)
- [AI Agent Integrations](#ai-agent-integrations) — Claude Desktop · Claude Code · Codex · Hermes · Manus · Cursor · Continue · Zed · Cline
- [Install Jiro in Your Agent (copy-paste prompt)](#install-jiro-in-your-agent)
- [Function Calling (OpenAI / Anthropic / Gemini)](#function-calling)
- [LangChain / LlamaIndex](#langchain--llamaIndex)
- [Feature Matrix](#feature-matrix)
- [What You Get](#what-you-get)
- [API Reference](#api-reference)
- [CLI](#cli)
- [Configuration & BYOK](#configuration--byok)
- [Deployment (Docker · Helm · Team)](#deployment)
- [Security & Compliance](#security--compliance)
- [Performance](#performance)
- [Project Layout](#project-layout)
- [Development](#development)
- [Roadmap](#roadmap)
- [License & Responsible Use](#license--responsible-use)
- [Community & Support](#community--support)
- [Brand Assets](#brand-assets)

---

<a id="whats-new"></a>
## What's New

**v0.1.2 — packaging & release**
- Distribution published to PyPI as **`jirosearch`** → `pip install jirosearch` works world-wide.
- `jiro --version` now reports the real installed release (via `importlib.metadata`).
- README, badges and docs updated to the `jirosearch` name.

**v0.1.1 — security & reliability hardening**
- **SSRF protection** for scrape targets (blocks loopback, RFC1918, link-local, cloud metadata `169.254.169.254`).
- Auth startup guard refuses non-loopback binds without `--insecure`.
- JWT upgraded to **RS256** + `kid` with a startup secret-strength check.
- Redis-backed rate limiting with in-memory fallback; SQLite connection pool + schema migrations.
- Agent execution **deadline** + **content budget**; robots.txt enforcement; `/health/engines`; structured request logging; graceful shutdown.

---

<a id="why-jiro"></a>
## Why Jiro? (SerpAPI Alternative, Self-Hosted)

| Problem with closed search APIs | Jiro's open-source solution |
|---|---|
| 💸 SerpAPI costs **$200+/mo** for 100k requests | **Free forever** — run on your own infrastructure (MIT) |
| ☁️ Cloud lock-in, your queries leave your network | **100% local-first** — your queries, your data, your compliance |
| 🤖 No native AI-agent integration | **MCP + Function Calling + LangChain/LlamaIndex** native |
| ⚖️ Legal gray area (robots.txt, ToS) | **Built-in compliance**: robots.txt parser, ToS tracking, immutable audit logs |
| 🔧 Fragile parsers break on UI changes | **Self-healing selectors** + 9-engine automatic fallback chain |

---

<a id="one-command-start"></a>
## One-Command Start

```bash
pip install jirosearch          # or: uv tool install jirosearch

jiro serve                      # API on http://localhost:8000  (docs: /docs)
```

That's it — a working **self-hosted search API** in one command.

```bash
# Search (SerpAPI-compatible endpoint)
curl "http://localhost:8000/search.json?engine=google&q=python+web+scraping&num=5"

# Scrape a page into clean markdown
curl -X POST http://localhost:8000/scrape \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","format":"markdown"}'

# Agentic research with citations
curl -X POST http://localhost:8000/ai/search \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the best Python web scraping library in 2026?","max_sources":5}'
```

---

<a id="ai-agent-integrations"></a>
## AI Agent Integrations

Jiro is an **MCP server** at its core, so any MCP-capable agent can use live web search,
scraping and agentic research. It also ships **function-calling schemas** for OpenAI /
Anthropic / Gemini and **LangChain / LlamaIndex** wrappers.

**Transports**

| Transport | Command / Endpoint | Best for |
|-----------|-------------------|----------|
| **stdio** | `jiro mcp` | Local agents (Claude Desktop, Claude Code, Codex, Cursor, Hermes) |
| **Streamable HTTP** | `jiro mcp --transport http` → `http://host:8000/mcp` | Remote / cloud agents (Manus, hosted Claude) |
| **HTTP+SSE (legacy)** | `http://host:8000/sse` | Older MCP clients |

Protocol versions negotiated: `2025-03-26`, `2024-11-05`.

**MCP Tools**
| Tool | Description |
|------|-------------|
| `search` | Web search across 9 engines, 6 search types (web/images/news/videos/shopping/places) |
| `scrape` | URL → markdown / text / html / json |
| `ai_search` | Agentic research with citations (plan → search → read → synthesize) |

**MCP Prompts:** `search_and_summarize`, `compare_engines`. Argument autocompletion for engine names, search types, time ranges and formats.

### Claude Desktop
Edit `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "jiro": {
      "command": "jiro",
      "args": ["mcp"],
      "env": { "JIRO_CONFIG": "~/.jiro/config.yaml" }
    }
  }
}
```

### Claude Code
```bash
# Quick add (recommended)
claude mcp add jiro -- jiro mcp

# …or add to .claude/settings.json / project .mcp.json:
```
```json
{
  "mcpServers": {
    "jiro": { "command": "jiro", "args": ["mcp"] }
  }
}
```

### OpenAI Codex (Codex CLI)
Codex reads MCP servers from `~/.codex/config.toml`:
```toml
[mcp_servers.jiro]
command = "jiro"
args = ["mcp"]
```
Or one-shot:
```bash
codex mcp add jiro --command jiro --args mcp
```

### Hermes Agent
Point Hermes at the Jiro **stdio** server:
```json
{
  "mcpServers": {
    "jiro": { "command": "jiro", "args": ["mcp"] }
  }
}
```
For a remote Hermes deployment, run `jiro mcp --transport http` and connect to
`http://host:8000/mcp` with header `X-API-Key: jsk_...` (or `Authorization: Bearer <jwt>`).

### Manus AI
Manus connects to remote tools over **Streamable HTTP MCP**. Start Jiro in HTTP mode:
```bash
jiro mcp --transport http --host 0.0.0.0 --port 8000
```
Then register the tool endpoint in Manus:
```
MCP endpoint:  http://<your-host>:8000/mcp
Auth:         X-API-Key: jsk_...   (or Authorization: Bearer <jwt>)
```
Manus can now call `search`, `scrape` and `ai_search` as native tools.

### Cursor / Continue.dev / Zed / Cline
Point the MCP client at `jiro mcp` as the server command. For remote access use
`jiro mcp --transport http` and connect to `http://host:8000/mcp` with your
`X-API-Key` or `Authorization: Bearer <jwt>`.

> **Auth note:** MCP HTTP endpoints accept the same credentials as the REST API:
> `X-API-Key: jsk_...` or `Authorization: Bearer <jwt>`. Sessions are created on
> `initialize` and identified by `Mcp-Session-Id`; they support resume via
> `Last-Event-ID` and per-key rate limits.

---

<a id="install-jiro-in-your-agent"></a>
## Install Jiro in Your Agent (copy-paste prompt)

Copy the block below and paste it into **any** chat-based agent (Claude, Codex, ChatGPT,
Hermes, Manus, etc.) to have it wire Jiro up for you:

```
Install the Jiro Search MCP server so I can search the web, scrape pages into
markdown, and run agentic research with citations.

1) Install the package:   pip install jirosearch
2) Start the MCP server:  jiro mcp            (stdio, local)
   or for remote/cloud:   jiro mcp --transport http --host 0.0.0.0 --port 8000
3) Register this MCP server in my agent config:

{
  "mcpServers": {
    "jiro": {
      "command": "jiro",
      "args": ["mcp"]
    }
  }
}

Then verify with: jiro --version  (expect jiro 0.1.2)
```

Prefer a one-liner? For Claude Code / Codex:
```bash
# Claude Code
claude mcp add jiro -- jiro mcp
# Codex
codex mcp add jiro --command jiro --args mcp
```

---

<a id="function-calling"></a>
## Function Calling (OpenAI / Anthropic / Gemini)

```python
from jiro.ai.tools import openai_tools, anthropic_tools, gemini_tools
tools = openai_tools()           # OpenAI / OpenRouter / Ollama
```

Drop the returned schema into your LLM request and call `jiro`'s `search` / `scrape` /
`ai_search` when the model invokes the function.

---

<a id="langchain--llamaIndex"></a>
## LangChain / LlamaIndex

```python
from jiro.ai.tools import langchain_tools, ToolSpec
tools = langchain_tools(search_fn=my_search, scrape_fn=my_scrape, ai_fn=my_ai_search)
```
→ Tutorial: [Build a Deep Research Agent with Jiro + Claude (MCP)](docs/tutorials/deep-research-agent.md)

---

<a id="feature-matrix"></a>
## Feature Matrix

| Capability | Jiro (OSS) | SerpAPI | ScraperAPI | Bright Data |
|-----------|:----------:|:-------:|:----------:|:-----------:|
| **Web Search — 9 engines** | ✅ | ✅ | ❌ | ❌ |
| **Universal Web Scraper** (markdown/text/html/JSON) | ✅ | ❌ | ✅ | ✅ |
| **Agentic Research** (`/ai/search`) | ✅ | ❌ | ❌ | ❌ |
| **MCP Server** (stdio + Streamable HTTP + SSE) | ✅ | ❌ | ❌ | ❌ |
| **Function-Calling Schemas** (OpenAI/Anthropic/Gemini) | ✅ | ❌ | ❌ | ❌ |
| **Legal Compliance Layer** (robots.txt, ToS, audit) | ✅ | ❌ | ❌ | ❌ |
| **Self-Hosted / Air-Gapped** | ✅ | ❌ | ❌ | ❌ |
| **BYOK Proxies + CAPTCHA** | ✅ | ❌ | Partial | ✅ |
| **Open Source (MIT)** | ✅ | ❌ | ❌ | ❌ |
| **Pricing** | **Free** | $200+/mo | $299+/mo | $500+/mo |

---

<a id="what-you-get"></a>
## What You Get

| Area | Features |
|------|----------|
| **Engines** | Google (web/images/news/videos/shopping/places), Bing (web/images/news/videos), Brave (web/videos), DuckDuckGo (web/images), YouTube, Amazon, eBay, Yandex, Baidu |
| **Resilience** | Automatic engine fallback chain, UA rotation, retries + exponential backoff, per-engine circuit breaker, bot-wall detection, optional Playwright browser fallback for JS-heavy pages |
| **Cache** | SQLite (WAL) or Redis with TTL, `fresh=true` to bypass; memory mode; **semantic cache** (embedding-based fuzzy reuse); sub-50 ms cached p95 |
| **Scraper** | URL → markdown/text/html/JSON, readability extraction, OpenGraph/Twitter/JSON-LD metadata, links & images, LLM schema extraction, **custom CSS/XPath/JSONPath recipes** |
| **AI-native** | OpenAI/Anthropic/Gemini tool schemas, **MCP server** (`jiro mcp`), LangChain & LlamaIndex wrappers, `/ai/search` agent loop, `/ai/agent` multi-step research, **SSE streaming**, extractive fallback when no LLM key |
| **BYOK** | Proxies (HTTP/SOCKS5, single list or presets: BrightData/Oxylabs/ScraperAPI/ZenRows/Smartproxy), CAPTCHA solvers (2Captcha/CapSolver), LLM keys (OpenAI, Anthropic, Gemini, OpenRouter, Ollama) — all via config/env |
| **Async jobs** | `POST /jobs` for long-running research/scrape batches, `GET /jobs/{id}` status, webhook delivery with HMAC signature |
| **Team** | Hashed API keys, admin/user roles + scopes, per-key rate limits, JWT, usage tracking (`/usage`, `/metrics`) |
| **Ops** | Prometheus `/metrics`, `/proxy/status`, `/captcha/status`, structured JSON logs, Helm chart |
| **Privacy** | No telemetry, queries not logged by default, all data stays local |
| **Lightweight** | Async httpx + selectolax (C parser), ~15 core deps, starts in < 1 s |

---

<a id="api-reference"></a>
## API Reference

Interactive docs at **`http://localhost:8000/docs`** (Swagger) and `http://localhost:8000/openapi.json`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/search.json` | SerpAPI-compatible — `engine`, `q`, `num`, `start`, `hl`, `gl`, `api_key`, … |
| `GET`/`POST` | `/search` | Alias / JSON body |
| `POST` | `/search/batch` | Up to 10 queries in parallel |
| `GET` | `/search/stream` | SSE stream (single or multi-engine) |
| `POST` | `/scrape` | `{url, format, include_metadata, extract_schema, recipe}` |
| `POST` | `/scrape/batch` | Up to 50 URLs |
| `POST` | `/ai/search` | Plan → search → scrape top N → synthesize cited answer |
| `GET` | `/ai/search/stream` | SSE stream (`plan\|search\|source\|synthesize\|answer`) |
| `POST` | `/ai/agent` | Multi-step autonomous research |
| `POST` | `/ai/extract` | LLM extraction from URL/text with a custom schema |
| `POST` | `/jobs` | `ai_search` / `ai_agent` / `batch_scrape` with webhook |
| `GET` | `/health`, `/engines`, `/metrics` | Status, engines, Prometheus counters |
| `POST`/`GET`/`DELETE` | `/api-keys` | Hashed key mgmt (admin) |
| `POST` | `/auth/token` | Exchange API key for JWT |

Auth: `X-API-Key: jsk_...` header, `?api_key=...` param, or `Authorization: Bearer <jwt>`. When `auth.enabled: false` (default) the API is open for local use.

---

<a id="cli"></a>
## CLI

```bash
jiro serve                        # start the API server
jiro search web "python scraping" --engine bing --num 5 --json
jiro scrape "https://example.com" --format markdown
jiro ask "best python scraping library?" --max-sources 5
jiro mcp                          # MCP server over stdio
jiro mcp --transport http         # Streamable HTTP + SSE on :8000/mcp
jiro config init                  # write ~/.jiro/config.yaml
jiro config show
jiro keys create --name "ci" --role user        # prompts for admin key
jiro keys list
jiro keys revoke key_abc123
jiro usage --days 7
jiro plugins create myengine --author "Your Name"   # scaffold a new engine
```

---

<a id="configuration--byok"></a>
## Configuration & BYOK

Config at `~/.jiro/config.yaml` (or `$JIRO_CONFIG`). Override anything with env:
`JIRO_SERVER__PORT=9000`, `JIRO_AUTH__ENABLED=true`. Secrets interpolate from env:
`api_key: ${OPENAI_API_KEY}`.

| Service | Config | Env example |
|---|---|---|
| Proxy (custom) | `scraping.proxy.url` (comma-separated rotates) | `http://user:pass@proxy.example:22225` |
| Proxy (BrightData) | `scraping.proxy.provider: brightdata` + `api_key` | `${BRIGHTDATA_API_KEY}` |
| Proxy (Oxylabs/ScraperAPI/ZenRows/Smartproxy) | `scraping.proxy.provider` + `api_key` | `${OXYLABS_API_KEY}` |
| CAPTCHA (2Captcha / CapSolver) | `scraping.captcha.provider` + `api_key` | `${CAPSOLVER_API_KEY}` |
| LLM (OpenAI/Anthropic/Gemini/OpenRouter) | `llm.provider/api_key/model` | `${OPENAI_API_KEY}` |
| LLM (Ollama, local) | `llm.provider: ollama`, `base_url: http://localhost:11434/v1` | — |
| Redis cache | `cache.type: redis`, `cache.url` | `JIRO_CACHE__TYPE=redis` |

---

<a id="deployment"></a>
## Deployment

### Docker
```bash
docker compose up -d            # http://localhost:8000
```

### Helm (Kubernetes)
```bash
helm install jiro ./helm \
  --set config.env.JIRO_AUTH__ENABLED=true \
  --set config.envFromSecret=jiro-secrets
```
Ships Deployment, Service, PVC (SQLite data), optional Ingress and optional Redis cache (`--set redis.enabled=true`).

### Team setup (auth on)
```bash
export JIRO_AUTH__ENABLED=true JIRO_JWT_SECRET=$(openssl rand -hex 32)
jiro keys create --name admin --role admin --admin-key "$ADMIN"
jiro keys create --name "alice" --role user --rate-limit 30
```

---

<a id="security--compliance"></a>
## Security & Compliance

Jiro is built for **responsible, compliant** use:
- **SSRF protection** — scrape targets cannot hit loopback, RFC1918, link-local or cloud metadata IPs (fail-open for air-gapped use).
- **Auth guard** — refuses to bind a public interface without `--insecure`.
- **JWT RS256** + `kid`, with a startup check that rejects weak secrets.
- **robots.txt** enforcement, ToS tracking, and **immutable audit logs**.

→ Full details: [docs/compliance.md](docs/compliance.md) · [SECURITY.md](SECURITY.md)

---

<a id="performance"></a>
## Performance

| Metric | Value |
|---|---|
| Startup | < 1 s |
| Cached search (SQLite) | ~1–3 ms round-trip in-process |
| Live Bing search | ~0.3–0.8 s from a datacenter IP |
| `/scrape` of a small page | ~0.3–0.9 s first hit, then cached |
| Test suite | 390 tests passing |

---

<a id="project-layout"></a>
## Project Layout

```text
jiro/
├── ai/            LLM providers, tool schemas, agentic loop (research + multi-step agent + SSE)
├── scraping/      HTTP client (UA rotation, retries, circuit breaker, proxy manager, browser fallback),
│                  engines: google/bing/brave/duckduckgo/youtube/amazon/ebay/yandex/baidu
├── server/        FastAPI app: routers (search, scrape, ai, stream, jobs, admin, ops, system)
├── auth.py        API keys (SHA-256 hashed), JWT, rate limiting
├── browser.py     Playwright browser fallback (lazy, graceful degradation)
├── cache.py       SQLite / memory cache manager
├── captcha.py     BYOK CAPTCHA solvers (2Captcha, CapSolver)
├── config.py      YAML + env config with ${VAR} interpolation
├── db.py          SQLite (WAL): cache, api_keys, usage, jobs, semantic_cache, tos_acknowledgments
├── extract.py     readability + metadata + HTML→Markdown
├── jobs.py        async job queue + webhooks (HMAC-signed)
├── mcp.py         MCP server (stdio): tools, prompts, resources, autocompletion
├── mcp_http.py    MCP server (Streamable HTTP + SSE) for remote agents
├── models.py      Pydantic contracts
├── proxy.py       BYOK proxy manager (provider presets, rotation, cooldown)
├── recipes.py     CSS / XPath / JSONPath extraction recipes
├── redis_cache.py Redis cache backend
├── semantic.py    embedding-based semantic cache
└── cli.py         Typer CLI
tests/             parser fixtures + unit/API/integration/chaos/property tests
```

---

<a id="development"></a>
## Development

```bash
git clone https://github.com/DevAnimecx/jiro.git && cd jiro
pip install -e ".[dev,browser,redis,recipes]"
pytest -m "not network"            # skip network-dependent tests
jiro serve --reload
```

### Roadmap status
- **Phase 1 (MVP)** — ✅ CLI, config, FastAPI server, google/bing/ddg web engines, SerpAPI-compatible JSON, SQLite cache, API-key auth, OpenAI tool schema, `/ai/search`, Dockerfile.
- **Phase 2** — ✅ Brave + Bing videos, images/news types, team keys, MCP server, LangChain/LlamaIndex wrappers, batch scrape, `/metrics`, Playwright fallback, BYOK proxy + CAPTCHA, SSE, Redis.
- **Phase 3** — ✅ `/ai/agent` multi-step research, CSS/XPath/JSONPath recipes, LLM extraction, plugin registry, async jobs + webhooks.
- **Phase 4** — partial: semantic cache in; RAG pipelines, horizontal scaling and the community parser marketplace remain future work. Helm chart provided.

---

<a id="roadmap"></a>
## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full plan (Jiro Cloud, Enterprise/BSL edition,
RAG pipelines, parser marketplace, horizontal scaling).

---

<a id="license--responsible-use"></a>
## License & Responsible Use

**MIT**. Jiro is a scraping tool: respect each search engine's Terms of Service and `robots.txt`,
keep request rates respectful, and use proxies/CAPTCHA services at your own discretion. All traffic
originates from *your* network; queries are only visible to the engines you query.

---

<a id="community--support"></a>
## Community & Support

<p>
  <a href="https://github.com/DevAnimecx/jiro/discussions"><img src="https://cdn.simpleicons.org/github/EAC54F" width="20" alt="GitHub Discussions"></a>
  <a href="https://www.producthunt.com/search?q=jiro%20search"><img src="https://cdn.simpleicons.org/producthunt/FB6831" width="20" alt="Product Hunt"></a>
  <a href="https://www.reddit.com/search/?q=jiro%20search"><img src="https://cdn.simpleicons.org/reddit/FF4500" width="20" alt="Reddit"></a>
  <a href="https://dev.to/devanimecx"><img src="https://cdn.simpleicons.org/devdotto/0A0A0A" width="20" alt="dev.to"></a>
  <a href="https://www.instagram.com/explore/tags/jirosearch/"><img src="https://cdn.simpleicons.org/instagram/E4405F" width="20" alt="Instagram"></a>
  <a href="https://www.threads.net/"><img src="https://cdn.simpleicons.org/threads/000000" width="20" alt="Threads"></a>
  <a href="https://x.com/DevAnimecx"><img src="https://cdn.simpleicons.org/x/000000" width="20" alt="X.com"></a>
</p>

- 💬 [GitHub Discussions](https://github.com/DevAnimecx/jiro/discussions) — questions & ideas
- 🐛 [Issues](https://github.com/DevAnimecx/jiro/issues) — bugs & feature requests
- 📜 [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Code of Conduct](CODE_OF_CONDUCT.md)
- 🐦 Follow the launch: [@DevAnimecx](https://github.com/DevAnimecx)

---

<a id="brand-assets"></a>
## Brand Assets

- **Logo (hero):** served via the hunter.io logo API (Clearbit-backed) from
  `https://logo.clearbit.com/jiro.dev`. If you own a different domain, update that URL,
  or drop your **transparent logo** at `assets/logo.png` and change the hero `src` to
  `assets/logo.png`.
- **Social strip:** community logos use [Simple Icons](https://simpleicons.org) CDN
  (`cdn.simpleicons.org`) for GitHub, Product Hunt, Reddit, dev.to, Instagram, Threads and X.
- **Badges:** [shields.io](https://shields.io) for PyPI version, Python versions, license, tests and Docker.

---

**Developed by [Adarsh Kushwah](https://github.com/DevAnimecx) · Blackvault Technology**
*Local-first, AI-native search & scraping — free, open, and yours to self-host.*
