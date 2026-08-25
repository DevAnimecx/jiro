# Jiro Search API 🔍

> **Local-first, AI-native web search & scraping API** — a drop-in, **self-hosted SerpAPI alternative** with **MCP server**, **agentic research**, and **built-in legal compliance**.

[![GitHub stars](https://img.shields.io/github/stars/DevAnimecx/jiro?style=social)](https://github.com/DevAnimecx/jiro/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/DevAnimecx/jiro?style=social)](https://github.com/DevAnimecx/jiro/network/members)
[![PyPI version](https://img.shields.io/pypi/v/jiro-search?color=blue)](https://pypi.org/project/jiro-search/)
[![Docker](https://img.shields.io/badge/docker-ghcr.io%2FDevAnimecx%2Fjiro-blue)](https://github.com/DevAnimecx/jiro/pkgs/container/jiro)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-380%20passing-brightgreen)](https://github.com/DevAnimecx/jiro/actions)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

**Jiro** scrapes Google, Bing, DuckDuckGo, Brave, YouTube, Amazon, eBay, Yandex and Baidu directly — no third-party search API, no per-query billing, no cloud lock-in. Results are cached locally in SQLite (sub-50 ms cached responses), exposed through a **SerpAPI-compatible REST API**, and built to be called by **AI agents**: function-calling schemas for OpenAI/Anthropic/Gemini, a **Model Context Protocol (MCP) server**, LangChain/LlamaIndex wrappers, and an agentic `/ai/search` loop that plans → searches → reads pages → synthesizes a cited answer. Bring your own keys (BYOK) for proxies, CAPTCHA solvers and LLM providers.

> **Status:** Production-ready MVP (open-source, MIT). Self-host it for $0 or subscribe to **Jiro Cloud** for a managed proxy fleet, SLA and compliance dashboard.
> **Responsible use:** search engines actively fight bots. From residential IPs (and with BYOK proxies) Google/DuckDuckGo work; on datacenter IPs Jiro automatically falls back across engines (`google → bing → brave → duckduckgo`). Respect each engine's ToS and `robots.txt`.

---

## Why Jiro? (SerpAPI Alternative, Self-Hosted)

| Problem with closed search APIs | Jiro's open-source solution |
|---|---|
| 💸 SerpAPI costs **$200+/mo** for 100k requests | **Free forever** — run on your own infrastructure (MIT) |
| ☁️ Cloud lock-in, your queries leave your network | **100% local-first** — your queries, your data, your compliance |
| 🤖 No native AI-agent integration | **MCP + Function Calling + LangChain/LlamaIndex** native |
| ⚖️ Legal gray area (robots.txt, ToS) | **Built-in compliance**: robots.txt parser, ToS tracking, immutable audit logs |
| 🔧 Fragile parsers break on UI changes | **Self-healing selectors** + 9-engine automatic fallback chain |

---

## One-Command Start

```bash
pip install jiro-search          # or: uv tool install jiro-search

jiro serve                       # API on http://localhost:8000  (docs: /docs)
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

## Jiro vs SerpAPI, ScraperAPI & Bright Data

Jiro is the only **open-source, self-hostable** project that combines **search + scrape + agentic AI research + MCP** in one binary, with **legal compliance built in**. Closed competitors charge $200–$3,000/month for subsets of this and never let you self-host.

→ Full comparisons: [vs SerpAPI](docs/comparisons/serpapi.md) · [vs ScraperAPI](docs/comparisons/scraperapi.md) · [vs Bright Data](docs/comparisons/bright-data.md)

---

## AI Agent Integration

### Model Context Protocol (MCP)

Jiro ships a full **MCP server** (stdio, Streamable HTTP, legacy SSE) — giving AI agents live web search, page scraping and research.

```bash
jiro mcp                           # MCP server over stdio
jiro mcp --transport http         # Streamable HTTP + SSE on :8000/mcp
```

**Tools:** `search` (9 engines, 6 search types) · `scrape` (markdown/text/html/json) · `ai_search` (agentic research with citations).
**Prompts:** `search_and_summarize`, `compare_engines`. **Autocompletion:** engine names, search types, time ranges, formats.

#### Claude Desktop

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

#### Cursor / Continue.dev / Zed / Cline

Point the MCP client at `jiro mcp` as the server command (see [docs/mcp](docs/mcp.md)).

### Function Calling (OpenAI / Anthropic / Gemini)

```python
from jiro.ai.tools import openai_tools, anthropic_tools, gemini_tools
tools = openai_tools()           # OpenAI / OpenRouter / Ollama
```

### LangChain / LlamaIndex

```python
from jiro.ai.tools import langchain_tools, ToolSpec
tools = langchain_tools(search_fn=my_search, scrape_fn=my_scrape, ai_fn=my_ai_search)
```

→ Tutorial: [Build a Deep Research Agent with Jiro + Claude (MCP)](docs/tutorials/deep-research-agent.md)

---

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

## CLI

```bash
jiro serve                        # start the API server
jiro search web "python scraping" --engine bing --num 5 --json
jiro scrape "https://example.com" --format markdown
jiro ask "best python scraping library?" --max-sources 5
jiro mcp                          # MCP server over stdio
jiro config init                  # write ~/.jiro/config.yaml
jiro config show
jiro keys create --name "ci" --role user        # prompts for admin key
jiro keys list
jiro keys revoke key_abc123
jiro usage --days 7
jiro plugins create myengine --author "Your Name"   # scaffold a new engine
```

---

## Configuration & BYOK

Config at `~/.jiro/config.yaml` (or `$JIRO_CONFIG`). Override anything with env: `JIRO_SERVER__PORT=9000`, `JIRO_AUTH__ENABLED=true`. Secrets interpolate from env: `api_key: ${OPENAI_API_KEY}`.

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

## 💡 Open Core & Monetization

Jiro is **open-source (MIT)** and will always be free to self-host. The sustainable model:

| Edition | What | License | For |
|---------|------|---------|-----|
| **Jiro OSS** | Full search/scrape/AI/MCP, all engines, plugins, compliance | MIT | Everyone — $0 |
| **Jiro Cloud** *(roadmap)* | Managed hosting, auto-scaling, global residential proxy pool, SLA, SSO, compliance dashboard | SaaS | Teams & agents |
| **Jiro Enterprise** *(roadmap)* | Air-gapped license (BSL-1.0), SOC 2 path, dedicated support, private engine plugins | Source-available | Fintech/Legal/Gov/AI labs |

We monetize **convenience, compliance and support** — never the code. Community contributions stay MIT.

→ Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md)

---

## Documentation

- 📖 [Docs hub](docs/README.md)
- 🔌 [MCP integration](docs/mcp.md)
- ⚖️ [Compliance & responsible use](docs/compliance.md)
- 🆚 [vs SerpAPI](docs/comparisons/serpapi.md) · [vs ScraperAPI](docs/comparisons/scraperapi.md) · [vs Bright Data](docs/comparisons/bright-data.md)
- 🎓 [Deep Research Agent tutorial](docs/tutorials/deep-research-agent.md)

---

## Performance

| Metric | Value |
|---|---|
| Startup | < 1 s |
| Cached search (SQLite) | ~1–3 ms round-trip in-process |
| Live Bing search | ~0.3–0.8 s from a datacenter IP |
| `/scrape` of a small page | ~0.3–0.9 s first hit, then cached |
| Test suite | 380 tests passing |

---

## Project Layout

```
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
├── models.py      Pydantic contracts
├── proxy.py       BYOK proxy manager (provider presets, rotation, cooldown)
├── recipes.py     CSS / XPath / JSONPath extraction recipes
├── redis_cache.py Redis cache backend
├── semantic.py    embedding-based semantic cache
└── cli.py         Typer CLI
tests/             parser fixtures + unit/API/integration/chaos/property tests
```

---

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

## License & Responsible Use

**MIT**. Jiro is a scraping tool: respect each search engine's Terms of Service and `robots.txt`, keep request rates respectful, and use proxies/CAPTCHA services at your own discretion. All traffic originates from *your* network; queries are only visible to the engines you query.

---

## Community & Support

- 💬 [GitHub Discussions](https://github.com/DevAnimecx/jiro/discussions) — questions & ideas
- 🐛 [Issues](https://github.com/DevAnimecx/jiro/issues) — bugs & feature requests
- 📜 [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Code of Conduct](CODE_OF_CONDUCT.md)
- 🐦 Follow the launch: [@DevAnimecx](https://github.com/DevAnimecx)

---

**Developed by [Adarsh Kushwah](https://github.com/DevAnimecx) · Blackvault Technology**
*Local-first, AI-native search & scraping — free, open, and yours to self-host.*
