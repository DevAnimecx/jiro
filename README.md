<div align="center">

<img src="assets/logo.png" alt="JIRO Search API - AI-Powered Web Search & Scraping Platform" width="120">

# JIRO — Search API & Web Scraping Platform

**9 search engines. 12 social platforms. AI-powered. Self-hosted. Free forever.**

[![PyPI version](https://img.shields.io/pypi/v/jirosearch.svg)](https://pypi.org/project/jirosearch/)
[![npm version](https://img.shields.io/npm/v/jiro-sdk.svg)](https://www.npmjs.com/package/jiro-sdk)
[![Python](https://img.shields.io/pypi/pyversions/jirosearch.svg)](https://pypi.org/project/jirosearch/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/docker/pulls/devanimecx/jiro.svg)](https://hub.docker.com/r/devanimecx/jiro)
[![Downloads](https://img.shields.io/pypi/dm/jirosearch.svg)](https://pypi.org/project/jirosearch/)

[Website](https://searchjiro.vercel.app) · [Documentation](https://searchjiro.vercel.app/docs) · [Pricing](https://searchjiro.vercel.app/pricing) · [Blog](https://searchjiro.vercel.app/blog) · [Changelog](https://searchjiro.vercel.app/changelog)

</div>

---

## What is JIRO?

JIRO is an **open-source, self-hosted search API and web scraping platform** that aggregates results from 9 search engines and 12 social platforms. It provides AI-powered search intelligence, hybrid ranking, and stealth web scraping — all running locally on your infrastructure.

**Use cases:**
- Search API for AI agents and LLMs
- Web scraping at scale with anti-bot bypass
- Social media monitoring across 12 platforms
- SERP data collection for SEO tools
- Real-time web intelligence feeds
- MCP server for Claude Desktop, Cursor, and other AI tools

---

## Quick Start

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

You're now searching across 9 engines with hybrid ranking, caching, and structured extraction — all running locally on your machine.

**Cloud API (no server needed):**
```bash
# Authenticate via browser
jiro auth login

# Search with cloud API
curl -X POST https://searchjiro.vercel.app/api/proxy/search \
  -H "Authorization: Bearer jsk_live_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"q": "latest AI research", "engine": "google"}'
```

---

## Search Engines

| Engine | Type | Notes |
|--------|------|-------|
| Google | Web | Full SERP data |
| Bing | Web | Rich snippets |
| Brave | Web | Privacy-focused |
| DuckDuckGo | Web | Instant answers |
| YouTube | Video | Transcripts, metadata |
| Amazon | Shopping | Product data |
| eBay | Shopping | Listings, prices |
| Yandex | Web | Russian market |
| Baidu | Web | Chinese market |

## Social Platforms

Reddit · Twitter/X · YouTube · LinkedIn · TikTok · Instagram · Facebook · Threads · Hacker News · Bluesky · Telegram · Pinterest

---

## Official SDKs

### Python

```bash
pip install jiro-sdk
```

```python
from jiro_sdk import JiroClient

client = JiroClient(api_key="your-key")

results = client.search("python web scraping")
content = client.scrape("https://example.com")
answer = client.ai_ask("What is Python?")
results = client.search_parallel("AI news", num_engines=3)
job = client.batch_search(["python", "javascript", "go"])
```

### JavaScript / TypeScript

```bash
npm install jiro-sdk
```

```javascript
import { JiroClient } from 'jiro-sdk';

const client = new JiroClient({ apiKey: 'your-key' });

const results = await client.search('python web scraping');
const content = await client.scrape('https://example.com');
const answer = await client.aiAsk('What is Python?');

// WebSocket Streaming
const ws = client.createSearchStream('AI news');
ws.onmessage = (event) => console.log(JSON.parse(event.data));
```

### Go

```bash
go get github.com/DevAnimecx/jiro/sdk/go
```

```go
import "github.com/DevAnimecx/jiro/sdk/go"

client := jiro.NewClient(jiro.WithAPIKey("your-key"))

results, _ := client.Search("python web scraping", nil)
content, _ := client.Scrape("https://example.com", nil)
answer, _ := client.AiAsk("What is Python?", nil)
```

---

## CLI Commands

```bash
# Search
jiro search web "python web scraping"
jiro search web "AI news" --parallel --engines 3
jiro search web -i  # Interactive mode

# Scrape
jiro scrape https://example.com
jiro scrape "free SaaS directories"  # Search + scrape top result

# AI
jiro ai ask "What is Python?"
jiro ai setup --provider openai -k sk-...

# Auth (Cloud)
jiro auth login       # Device code flow
jiro auth whoami      # Show account + credits
jiro auth status      # Test API key

# License (Self-Hosted)
jiro license activate JIRO-PRO-A1B2-C3D4-E5F6
jiro license info
jiro license deactivate

# System
jiro status
jiro doctor
jiro serve --port 8000
```

---

## Pricing

### Cloud (Managed)

| | Free | Pro | Enterprise |
|---|:---:|:---:|:---:|
| **Price** | ₹0/mo | ₹999/mo | Custom |
| **Credits** | 1,000 | 25,000 | Unlimited |
| **Rate Limit** | 5 RPM | 120 RPM | 1,000 RPM |
| **Search Engines** | 3 | All 9 | All + Custom |
| **AI Search** | — | ✓ | ✓ |
| **MCP & Webhooks** | — | ✓ | ✓ |
| **Support** | Community | Priority | Dedicated |

[Start Free →](https://searchjiro.vercel.app/auth) · [View Pricing →](https://searchjiro.vercel.app/pricing)

### Self-Hosted (One-Time)

| | Free | Pro | Enterprise |
|---|:---:|:---:|:---:|
| **Price** | ₹0 | ₹4,999 | ₹14,999 |
| **RPM** | 100 | 500 | 1,000 |
| **RPD** | 10K | 100K | 1M |
| **AI Search** | — | ✓ | ✓ |
| **Commercial Use** | — | ✓ | ✓ |
| **Custom Models** | — | — | ✓ |
| **White Label** | — | — | ✓ |

[Install from GitHub →](https://github.com/DevAnimecx/jiro)

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
| `ai_search` | Pro | AI research with citations |
| `search_hybrid` | Pro | Hybrid multi-signal search |
| `search_structured` | Pro | Structured data extraction |
| `social_scrape` | Pro | Scrape social media |
| `social_search` | Pro | Search social platforms |
| `social_batch` | Pro | Batch scrape URLs |
| `smart_search` | Pro | Intent-aware routing |

---

## Features

### Search Intelligence
- **Hybrid Search** — keyword + semantic + freshness signals
- **Multi-Query** — parallel query expansion
- **Answer Synthesis** — extractive answers from results
- **Search Filters** — domain, time range, category
- **Highlights** — query-aware snippet extraction
- **Parallel Search** — multi-engine concurrent search

### Web Scraping
- **Stealth Engine** — TLS/JA3 fingerprint rotation
- **Anti-Bot Bypass** — Cloudflare, DataDome, PerimeterX
- **JavaScript Rendering** — full browser automation
- **Structured Extraction** — JSON schema-based data extraction
- **Social Scraping** — 12 platforms with normalized output

### AI-Powered
- **Smart Search** — intent-aware auto-routing
- **AI Research** — agentic search with citations
- **Intent Classification** — 16 intent types
- **Self-Learning** — adapts to search patterns
- **Advanced Healing** — automatic retry with fallback

### Enterprise Ready
- **Rate Limiting** — sliding window with tiers
- **Usage Quotas** — monthly limits per tier
- **Plugin System** — custom engines & scrapers
- **Advanced Caching** — LRU/LFU with analytics
- **Batch Operations** — concurrent search & scrape
- **Monitoring** — Prometheus metrics, health checks
- **WebSocket Streaming** — real-time results
- **MCP Integration** — 16 tools for AI clients

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

### Cloud

```bash
# No server needed — use cloud API
jiro auth login
```

---

## Comparisons

### vs SerpAPI

| Feature | JIRO | SerpAPI |
|---------|:----:|:-------:|
| Self-hosted | ✅ | ❌ |
| Free tier | 10K RPD | 100/mo |
| Social scraping | 12 platforms | ❌ |
| Hybrid search | ✅ | ❌ |
| WebSocket streaming | ✅ | ❌ |
| Official SDKs | Python, JS, Go | Python, JS |
| MCP integration | ✅ | ❌ |
| Price (paid) | ₹999/mo | $50/mo |

### vs ScraperAPI

| Feature | JIRO | ScraperAPI |
|---------|:----:|:----------:|
| Search engines | 9 | ❌ |
| Social platforms | 12 | ❌ |
| AI research | ✅ | ❌ |
| Self-hosted | ✅ | ❌ |
| Free tier | 10K RPD | 5K/mo |

### vs Bright Data

| Feature | JIRO | Bright Data |
|---------|:----:|:-----------:|
| Price | ₹999/mo | $500+/mo |
| Self-hosted | ✅ | ❌ |
| Hybrid search | ✅ | ❌ |
| WebSocket streaming | ✅ | ❌ |
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
│   ├── client.py     Stealth engine (TLS/JA3)
│   └── social/       12 social platforms
├── ai/               AI/LLM integration
├── plugins/          Plugin system
├── monitoring.py     Metrics & health checks
├── batch.py          Batch operations
├── scheduler.py      Scheduled searches
├── history.py        Search history
├── export_import.py  Data export/import
├── ratelimit.py      Rate limiting & quotas
├── cache_advanced.py Advanced caching
├── stealth.py        Anti-bot bypass
├── mcp.py            MCP server (16 tools)
├── pro.py            Tier system
├── licensing.py      HMAC license tokens
├── secure_store.py   Encrypted credentials
├── device_auth.py    RFC 8628 device code flow
├── cloud_auth.py     Cloud authentication
├── db.py             SQLite/PostgreSQL
└── dashboard.py      Web UI
```

---

## Community

- [Website](https://searchjiro.vercel.app) — Home
- [GitHub](https://github.com/DevAnimecx/jiro) — Source code
- [Documentation](https://searchjiro.vercel.app/docs) — Guides & tutorials
- [Pricing](https://searchjiro.vercel.app/pricing) — Plans & tiers
- [Blog](https://searchjiro.vercel.app/blog) — Articles & tutorials
- [Changelog](https://searchjiro.vercel.app/changelog) — Release notes
- [Discord](https://discord.gg/jiro) — Community chat
- [Twitter](https://twitter.com/jirosearch) — Updates

---

## License

MIT License — use freely, commercially, or privately.

---

<div align="center">

**Built with ❤️ by [Blackvault Technology](https://github.com/DevAnimecx)**

[Get Started Free](https://searchjiro.vercel.app/auth) · [View Pricing](https://searchjiro.vercel.app/pricing) · [Read Docs](https://searchjiro.vercel.app/docs)

</div>
