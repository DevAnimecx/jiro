<div align="center">

<img src="assets/logo.png" alt="Jiro" width="120">

# Jiro — The Search Intelligence Platform

**One API. 9 search engines. 12 social platforms. AI-powered. Free forever.**

[![PyPI version](https://img.shields.io/pypi/v/jirosearch.svg)](https://pypi.org/project/jirosearch/)
[![Python](https://img.shields.io/pypi/pyversions/jirosearch.svg)](https://pypi.org/project/jirosearch/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/docker/pulls/devanimecx/jiro.svg)](https://hub.docker.com/r/devanimecx/jiro)
[![Downloads](https://img.shields.io/pypi/dm/jirosearch.svg)](https://pypi.org/project/jirosearch/)

[Get Started Free](#quick-start) · [API Docs](http://localhost:8000/docs) · [Enterprise](#pricing) · [Discord](https://discord.gg/jiro)

</div>

---

## Why Jiro?

Jiro is a **local-first, AI-native search & scraping API** — a self-hosted alternative to SerpAPI, ScraperAPI, and Bright Data. It gives you:

- **9 search engines** — Google, Bing, Brave, DuckDuckGo, YouTube, Amazon, eBay, Yandex, Baidu
- **12 social platforms** — Reddit, Twitter/X, YouTube, LinkedIn, TikTok, Instagram, and more
- **Hybrid search** — keyword + semantic + freshness signals combined
- **AI-powered research** — agentic search with citations (Enterprise)
- **MCP integration** — works with Claude Desktop, Cursor, Continue.dev
- **Free forever** — generous free tier, no credit card required

---

## Quick Start (30 seconds)

```bash
# Install
pip install jirosearch

# Start server
jiro serve

# Search the web
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"q": "latest AI research", "engine": "google"}'
```

That's it. You're searching across 9 engines with hybrid ranking, caching, and structured extraction — all running locally on your machine.

---

## Features

<table>
<tr>
<td width="50%">

### 🔍 Search Intelligence
- **Hybrid Search** — keyword + semantic + freshness
- **Multi-Query** — parallel query expansion
- **Answer Synthesis** — extractive answers from results
- **Search Filters** — domain, time range, category
- **Highlights** — query-aware snippet extraction

### 🌐 Social Scraping (12 Platforms)
- Reddit, Twitter/X, YouTube, LinkedIn
- TikTok, Instagram, Facebook, Threads
- Hacker News, Bluesky, Telegram, Pinterest

</td>
<td width="50%">

### 🧠 AI-Powered
- **Smart Search** — intent-aware auto-routing
- **Structured Extraction** — JSON schema-based data extraction
- **AI Research** — agentic search with citations *(Enterprise)*
- **Intent Classification** — 16 intent types

### 🏢 Enterprise Ready
- **Tenant Management** — multi-tenant isolation
- **SOC2 Compliance** — audit logging, data residency
- **SLA Monitoring** — p50/p95/p99 latency tracking
- **Webhooks & Batch Jobs** — event-driven automation

</td>
</tr>
</table>

---

## Free vs Enterprise

| Feature | Free | Enterprise |
|---------|:----:|:----------:|
| **Rate Limits** | 100 RPM / 10K RPD | 1,000 RPM / 1M RPD |
| **Search Engines** | 9 engines | 9 engines |
| **Social Platforms** | 12 platforms | 12 platforms |
| **Hybrid Search** | ✅ | ✅ |
| **Smart Search** | ✅ | ✅ |
| **Structured Extraction** | ✅ | ✅ |
| **Social Batch** | ✅ (5/batch) | ✅ (500/batch) |
| **Self-Learning** | ✅ (basic) | ✅ (advanced) |
| **AI Research** | ❌ | ✅ |
| **Advanced Healing** | ❌ | ✅ |
| **Custom Models** | ❌ | ✅ |
| **Commercial Use** | ❌ | ✅ |
| **White Label** | ❌ | ✅ |
| **Premium Support** | ❌ | ✅ |
| **Price** | **$0 forever** | **$499/mo** |

---

## API Examples

### Search the Web

```bash
# Basic search
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"q": "python web scraping", "engine": "google", "num": 10}'

# Hybrid search with answer synthesis
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"q": "latest AI research", "hybrid": true, "answer": true}'
```

### Scrape Any URL

```bash
# Scrape to markdown
curl -X POST http://localhost:8000/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://docs.python.org", "format": "markdown"}'

# Batch scrape
curl -X POST http://localhost:8000/scrape/batch \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com", "https://docs.python.org"]}'
```

### Social Media

```bash
# Scrape a Reddit post
curl -X POST http://localhost:8000/social \
  -H "Content-Type: application/json" \
  -d '{"url": "https://reddit.com/r/programming/comments/abc123"}'

# Search across platforms
curl -X POST http://localhost:8000/social/search \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning", "platform": "reddit", "limit": 10}'
```

### Smart Search (Auto-Routing)

```bash
# Auto-detect intent and route to best engine
curl -X POST http://localhost:8000/v1/smart \
  -H "Content-Type: application/json" \
  -d '{"query": "github.com/fastapi"}'
```

---

## MCP Integration

Works with any MCP-compatible client:

```json
{
  "mcpServers": {
    "jiro": {
      "command": "jiro",
      "args": ["mcp"]
    }
  }
}
```

### 16 MCP Tools

| Tool | Tier | Description |
|------|:----:|-------------|
| `search` | Free | Search 9 engines |
| `scrape` | Free | Scrape URL to markdown |
| `smart_classify` | Free | Classify search intent |
| `compare_engines` | Free | Compare across engines |
| `list_engines` | Free | List all engines |
| `list_social_platforms` | Free | List social platforms |
| `monitor_status` | Free | Health metrics |
| `health_check` | Free | Quick health check |
| `cache_stats` | Free | Cache statistics |
| `ai_search` | Enterprise | AI research with citations |
| `search_hybrid` | Enterprise | Hybrid multi-signal search |
| `search_structured` | Enterprise | Structured data extraction |
| `social_scrape` | Enterprise | Scrape social media |
| `social_search` | Enterprise | Search social platforms |
| `social_batch` | Enterprise | Batch scrape URLs |
| `smart_search` | Enterprise | Intent-aware routing |

---

## Pricing

### Free — $0/forever

The most generous free tier in search APIs. No credit card required.

- 100 requests/minute
- 10,000 requests/day
- 9 search engines
- 12 social platforms
- Hybrid search & smart routing
- MCP integration
- Community support

### Enterprise — $499/mo

Everything in Free, plus unlimited power.

- 1,000 requests/minute
- 1,000,000 requests/day
- AI-powered agentic research
- Custom LLM models
- White-label customization
- SOC2 compliance
- Premium support
- Commercial use license

[Get Enterprise →](https://jiro.ai/pricing)

---

## Deploy

### Docker

```bash
docker-compose up -d
```

### Kubernetes

```bash
helm install jiro ./helm/jiro
```

### Local

```bash
pip install jirosearch
jiro serve --host 0.0.0.0 --port 8000
```

---

## Comparisons

### vs SerpAPI

| Feature | Jiro | SerpAPI |
|---------|:----:|:-------:|
| Self-hosted | ✅ | ❌ |
| Free tier | 10K RPD | 100/mo |
| Social scraping | 12 platforms | ❌ |
| Hybrid search | ✅ | ❌ |
| MCP integration | ✅ | ❌ |
| Price (paid) | $499/mo | $50/mo |

### vs ScraperAPI

| Feature | Jiro | ScraperAPI |
|---------|:----:|:----------:|
| Search engines | 9 | ❌ |
| Social platforms | 12 | ❌ |
| AI research | ✅ | ❌ |
| Self-hosted | ✅ | ❌ |
| Free tier | 10K RPD | 5K/mo |

### vs Bright Data

| Feature | Jiro | Bright Data |
|---------|:----:|:-----------:|
| Price | $499/mo | $500+/mo |
| Self-hosted | ✅ | ❌ |
| Hybrid search | ✅ | ❌ |
| MCP integration | ✅ | ❌ |
| AI research | ✅ | ❌ |

---

## Architecture

```
jiro/
├── server/           FastAPI application
│   └── routers/      API endpoints (75+ routes)
├── search/           Search intelligence
│   ├── hybrid.py     Hybrid search
│   ├── reranker.py   Result reranking
│   └── multiquery.py Query expansion
├── scraping/         Web scraping
│   ├── engines.py    9 search engines
│   └── social/       12 social platforms
├── ai/               AI/LLM integration
├── mcp.py           MCP server (16 tools)
├── pro.py           Tier system
├── licensing.py     HMAC license tokens
├── db.py            SQLite/PostgreSQL
└── dashboard.py     Web UI
```

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run linter
ruff check jiro/
```

---

## Community

- [GitHub](https://github.com/DevAnimecx/jiro) — Source code
- [Discord](https://discord.gg/jiro) — Community chat
- [Twitter](https://twitter.com/jirosearch) — Updates
- [Documentation](https://jiro.dev/docs) — Guides & tutorials

---

## License

MIT License — use freely, commercially, or privately.

---

<div align="center">

**Built with ❤️ by [Blackvault Technology](https://github.com/DevAnimecx)**

[Get Started Free](#quick-start) · [Enterprise](#pricing)

</div>
