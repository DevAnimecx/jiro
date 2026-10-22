<div align="center">

<img src="assets/logo.png" alt="JIRO — open-source search API, web scraping and query graph engine" width="120">

# JIRO — Search API, Web Scraping & Query Graph Engine

**9 search engines. 12 social platforms. 22 MCP tools. One self-hosted API.**

[![PyPI version](https://img.shields.io/pypi/v/jirosearch.svg)](https://pypi.org/project/jirosearch/)
[![npm version](https://img.shields.io/npm/v/jiro-sdk.svg)](https://www.npmjs.com/package/jiro-sdk)
[![Python](https://img.shields.io/pypi/pyversions/jirosearch.svg)](https://pypi.org/project/jirosearch/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/docker/pulls/devanimecx/jiro.svg)](https://hub.docker.com/r/devanimecx/jiro)
[![Downloads](https://img.shields.io/pypi/dm/jirosearch.svg)](https://pypi.org/project/jirosearch/)

[Website](https://searchjiro.vercel.app) · [Docs](https://searchjiro.vercel.app/docs) · [Pricing](https://searchjiro.vercel.app/pricing) · [Blog](https://searchjiro.vercel.app/blog) · [Changelog](https://searchjiro.vercel.app/changelog)

</div>

---

## What is JIRO?

JIRO is an **open-source, self-hosted search API and web scraping platform**. It aggregates 9 search
engines and 12 social platforms behind one HTTP API, and adds a **query graph engine** that plans,
executes and cites multi-step research — every answer traces back to the pages that produced it.

It runs entirely on your own infrastructure. No per-query vendor bill, no data leaving your network.

**Answer engine optimization note for the curious:** the *Sourced answers* section below is the part
that replaces a search API. Engines return links; JIRO returns claims plus the source ids behind them.

---

## Quick Start

```bash
pip install jirosearch
jiro serve
```

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"q": "latest AI research", "engine": "google"}'
```

Running: 9 engines, hybrid ranking, caching and structured extraction, all local.

**No server? Use the cloud API:**

```bash
jiro auth login   # device code flow, opens your browser
```

```bash
curl -X POST https://searchjiro.vercel.app/api/proxy/search \
  -H "Authorization: Bearer jsk_live_your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"q": "latest AI research", "engine": "google"}'
```

---

## Sourced answers (0.4.0)

Most search APIs return a list of links and stop. JIRO 0.4.0 runs a **query graph**: it plans the
research shape, executes nodes in parallel, and returns an **evidence packet** where every claim
carries the `source_ids` it rests on.

```bash
curl -X POST http://localhost:8000/v1/graph/search \
  -H "Content-Type: application/json" \
  -d '{"query": "compare vector databases", "fetch_top": 3, "max_tokens": 2000}'
```

```json
{
  "graph_id": "g_8f2c1a",
  "intent": "comparison",
  "status": "ok",
  "duration_ms": 1840,
  "engine_attribution": { "google": 2, "bing": 1 },
  "gaps": [],
  "budget": { "requests_used": 5, "bytes_used": 284113, "nodes_used": 9, "tokens_used": 1780 },
  "packet": {
    "packet_id": "p_41d9",
    "used_tokens": 1780,
    "claims": [
      { "id": "c1", "text": "Qdrant is written in Rust.", "source_ids": ["s2"] }
    ],
    "sources": [{ "id": "s2", "url": "https://qdrant.tech/documentation/" }],
    "contradictions": []
  }
}
```

**Plan without paying.** `/v1/graph/plan` is a dry run — it validates the graph and returns a cost
estimate without touching the network.

### What the graph engine buys you

| Guarantee | How it shows up in the response |
|---|---|
| Every claim is traceable | `packet.claims[].source_ids` resolves against `packet.sources[]` |
| Unknown cost is never invented | `budget.requests_used` is measured; unknown remainders are `null`, not a guess |
| Failures are visible | `gaps[]` records the node, query and reason a branch did not complete |
| Work is bounded | `max_tokens`, `deadline_ms` and `max_nodes` cap the run before it starts |
| You know which engine answered | `engine_attribution` counts per engine |

### Passing work between agents

`session_handoff` is an MCP tool that packages a finished run into a brief another agent can act on —
resolvable evidence ids, open questions taken from gaps, contradictions, remaining budget, and the
next node to run. Roles: `planner`, `retriever`, `verifier`, `writer`, `observer`.

---

## Search engines

| Engine | Type | Notes |
|--------|------|-------|
| Google | Web | Full SERP data, paginated |
| Bing | Web | Rich snippets, paginated |
| Brave | Web | Privacy-focused |
| DuckDuckGo | Web | Instant answers |
| Yandex | Web | Russian market |
| Baidu | Web | Chinese market, paginated |
| YouTube | Video | Transcripts, metadata |
| Amazon | Shopping | Product data |
| eBay | Shopping | Listings, prices |

## Social platforms

Reddit · Twitter/X · YouTube · LinkedIn · TikTok · Instagram · Facebook · Threads · Hacker News · Bluesky · Telegram · Pinterest

---

## Official SDKs

All three expose the 0.4.0 query graph and are versioned in lockstep with the server.

### Python

```bash
pip install jiro-sdk
```

```python
from jiro_sdk import JiroClient

client = JiroClient(api_key="your-key")

# Classic search + scrape
results = client.search("python web scraping")
content = client.scrape("https://example.com")

# Query graph (0.4.0)
plan = client.plan_search("compare vector databases")          # dry run, no network
run  = client.search_graph("compare vector databases", fetch_top=3)
for claim in run["packet"]["claims"]:
    print(claim["text"], claim["source_ids"])
```

### JavaScript / TypeScript

```bash
npm install jiro-sdk
```

```javascript
import { JiroClient } from 'jiro-sdk';

const client = new JiroClient({ apiKey: 'your-key' });

const results = await client.search('python web scraping');
const run = await client.searchGraph('compare vector databases', { fetchTop: 3 });

// WebSocket streaming
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
run, _ := client.GraphSearch("compare vector databases", &jiro.GraphOptions{FetchTop: 3}, false)
```

---

## CLI

```bash
# Search
jiro search web "python web scraping"
jiro search web "AI news" --parallel --engines 3
jiro search web -i                      # interactive

# Scrape
jiro scrape https://example.com
jiro scrape "free SaaS directories"     # search + scrape top result

# AI
jiro ai ask "What is Python?"
jiro ai setup --provider openai -k sk-...

# Auth (cloud)
jiro auth login && jiro auth whoami

# License (self-hosted)
jiro license activate JIRO-PRO-A1B2-C3D4-E5F6

# System
jiro status && jiro doctor && jiro serve --port 8000
```

---

## MCP integration

Works with any MCP-compatible client — Claude Desktop, Cursor, Windsurf, and others.

```json
{
  "mcpServers": {
    "jiro": { "command": "jiro", "args": ["mcp"] }
  }
}
```

### 22 MCP tools

<details>
<summary><strong>Expand the full tool list</strong></summary>

| Tool | Tier | What it does |
|------|:----:|--------------|
| `search` | Free | Search across 9 engines |
| `scrape` | Free | Scrape a URL to markdown |
| `smart_classify` | Free | Classify search intent |
| `compare_engines` | Free | Compare results across engines |
| `list_engines` | Free | List available engines |
| `list_social_platforms` | Free | List social platforms |
| `monitor_status` | Free | Health and metrics |
| `health_check` | Free | Live engine probes |
| `cache_stats` | Free | Cache statistics |
| `task_submit` | Free | Submit a background task |
| `task_status` | Free | Poll a task |
| `task_cancel` | Free | Cancel a task |
| `plan_search` | Free | **0.4.0** — plan a query graph, dry run |
| `ai_search` | Pro | AI research with citations |
| `search_hybrid` | Pro | Hybrid multi-signal search |
| `search_structured` | Pro | Structured data extraction |
| `social_scrape` | Pro | Scrape a social profile or post |
| `social_search` | Pro | Search social platforms |
| `social_batch` | Pro | Batch scrape URLs |
| `smart_search` | Pro | Intent-aware routing |
| `search_graph` | Pro | **0.4.0** — execute a query graph, return the evidence packet |
| `session_handoff` | Pro | **0.4.0** — package a run as a brief for another agent |

</details>

---

## Features

### Sourced answers (0.4.0)
- **Query graph** — plans a research shape, then executes it
- **Evidence packets** — claims paired with resolvable source ids
- **Cost estimation** — dry-run a graph before paying for it
- **Gap reporting** — branches that failed say why
- **Session handoff** — pass a run to another agent as a structured brief
- **Durable runtime** — lease-based node execution that survives restarts

### Search intelligence
- **Hybrid search** — keyword + semantic + freshness signals
- **Multi-query** — parallel query expansion
- **Answer synthesis** — extractive answers from results
- **Pagination** — walks SERP pages until `num` is met or the budget runs out
- **Substitution reporting** — tells you when a fallback engine answered

### Web scraping
- **Stealth engine** — TLS/JA3 fingerprint rotation
- **Anti-bot bypass** — Cloudflare, DataDome, PerimeterX
- **JS rendering** — full browser automation
- **Structured extraction** — JSON-schema-driven
- **Social scraping** — 12 platforms, normalized output

### Enterprise-ready
- **Rate limiting** — sliding window, per-tier quotas
- **Plugin system** — custom engines and scrapers
- **Advanced caching** — LRU/LFU with analytics
- **Batch operations** — concurrent search and scrape
- **Monitoring** — Prometheus metrics, health checks
- **WebSocket streaming** — real-time results

---

## Deploy

```bash
docker-compose up -d                       # Docker
helm install jiro ./helm/jiro              # Kubernetes
pip install jirosearch && jiro serve       # Local
```

---

## Comparisons

### vs SerpAPI

| | JIRO | SerpAPI |
|---|:----:|:-------:|
| Self-hosted | ✅ | ❌ |
| Free tier | 10K RPD | 100/mo |
| Search engines | 9 | 1 (Google) |
| Social scraping | 12 platforms | ❌ |
| Sourced answers with source ids | ✅ | ❌ |
| MCP integration | ✅ | ❌ |
| Official SDKs | Python, JS, Go | Python, JS |

### vs ScraperAPI

| | JIRO | ScraperAPI |
|---|:----:|:----------:|
| Search engines | 9 | ❌ |
| Social platforms | 12 | ❌ |
| Query graph + citations | ✅ | ❌ |
| Self-hosted | ✅ | ❌ |

### vs Bright Data

| | JIRO | Bright Data |
|---|:----:|:-----------:|
| Self-hosted | ✅ | ❌ |
| Query graph + citations | ✅ | ❌ |
| MCP integration | ✅ | ❌ |
| Entry price | ₹999/mo | $500+/mo |

---

## Architecture

```
jiro/
├── server/routers/      FastAPI endpoints (75+ routes)
├── graph/               Query graph (0.4.0)
│   ├── planner.py       Turns a query into a validated graph
│   ├── executor.py      Ready-driven DAG execution
│   ├── operators.py     SEARCH / FETCH / VERIFY / SYNTHESIZE nodes
│   └── service.py       Plan + run entry points
├── parametric/packet.py Evidence packet assembly
├── search/              Hybrid search, reranking, query expansion
├── scraping/            9 engines, stealth client, 12 social platforms
├── ai/                  LLM integration + MCP tool schemas
├── runtime/             Durable execution, leases, config
├── mcp.py               MCP server (22 tools)
├── ratelimit.py         Rate limiting & quotas
├── cache_advanced.py    Cache with analytics
└── dashboard.py         Web UI
```

### Exit codes

For scripting and CI, the CLI distinguishes outcomes rather than collapsing them:

| Code | Meaning |
|:----:|---------|
| `0` | Success |
| `1` | Failure |
| `2` | Usage error |
| `3` | Substituted — a fallback engine answered |

---

## Pricing

### Cloud (managed)

| | Free | Pro | Enterprise |
|---|:---:|:---:|:---:|
| **Price** | ₹0/mo | ₹999/mo | Custom |
| **Credits** | 1,000 | 25,000 | Unlimited |
| **Rate limit** | 5 RPM | 120 RPM | 1,000 RPM |
| **Search engines** | 3 | All 9 | All + custom |
| **Query graph** | — | ✓ | ✓ |
| **MCP & webhooks** | — | ✓ | ✓ |

[Start free →](https://searchjiro.vercel.app/auth) · [Pricing →](https://searchjiro.vercel.app/pricing)

### Self-hosted (one-time)

| | Free | Pro | Enterprise |
|---|:---:|:---:|:---:|
| **Price** | ₹0 | ₹4,999 | ₹14,999 |
| **RPM** | 100 | 500 | 1,000 |
| **RPD** | 10K | 100K | 1M |
| **Query graph** | — | ✓ | ✓ |
| **Commercial use** | — | ✓ | ✓ |
| **White label** | — | — | ✓ |

[Install from GitHub →](https://github.com/DevAnimecx/jiro)

---

## FAQ

**Do I need API keys for the search engines?**
No. All 9 engines are scraped directly. No Google, Bing or SerpAPI keys required.

**Can I run JIRO fully offline?**
The server runs locally, but it reaches out to the engines you query. Point it at an internal
mirror or disable engines you don't want in the config.

**What makes the 0.4.0 answers checkable?**
Every claim in the evidence packet carries `source_ids` that resolve against `packet.sources[]`.
A claim whose sources can't be resolved is flagged, not silently dropped.

**How is this different from a search API like SerpAPI?**
SerpAPI returns links from one engine. JIRO searches 9, fetches and extracts the pages, and returns
claims with citations — plus it's self-hosted, so there's no per-query bill.

**Is it really free?**
The MIT-licensed core is free forever, self-hosted and unlimited. Paid tiers add AI features,
higher rate limits and commercial licensing.

**Can I pass research between AI agents?**
Yes — the `session_handoff` MCP tool packages a run into a brief with evidence ids, open questions
and remaining budget, ready for the next agent to pick up.

---

## Community

- [Website](https://searchjiro.vercel.app) — home
- [GitHub](https://github.com/DevAnimecx/jiro) — source
- [Docs](https://searchjiro.vercel.app/docs) — guides
- [Blog](https://searchjiro.vercel.app/blog) — articles
- [Discord](https://discord.gg/jiro) — chat
- [Twitter](https://twitter.com/jirosearch) — updates

## License

MIT — use it freely, commercially or privately.

---

<div align="center">

**Built by [Blackvault Technology](https://github.com/DevAnimecx)**

[Get started free](https://searchjiro.vercel.app/auth) · [Pricing](https://searchjiro.vercel.app/pricing) · [Docs](https://searchjiro.vercel.app/docs)

</div>
