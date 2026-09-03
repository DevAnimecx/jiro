# jiro-cli

> Jiro Search CLI - Local-first, AI-native web search & scraping platform

[![npm](https://img.shields.io/npm/v/jiro-cli.svg)](https://www.npmjs.com/package/jiro-cli)
[![License](https://img.shields.io/npm/l/jiro-cli.svg)](https://opensource.org/licenses/MIT)

A Node.js CLI wrapper for [Jiro Search](https://github.com/DevAnimecx/jiro), a drop-in, self-hosted SerpAPI alternative with MCP server, agentic research, and built-in legal compliance.

## Features

- **9 Search Engines**: Google, Bing, Brave, DuckDuckGo, YouTube, Amazon, eBay, Yandex, Baidu
- **12 Social Platforms**: Reddit, HN, YouTube, Bluesky, Twitter, Threads, Instagram, TikTok, LinkedIn, Facebook, Telegram, Pinterest
- **Hybrid Search**: Keyword + semantic + freshness signals
- **Smart Search**: Intent-aware auto-routing
- **MCP Server**: 12 tools for AI agents (Claude Desktop, Cursor, Continue.dev)
- **Pro Tier**: Free, Starter ($29), Pro ($99), Enterprise ($499)

## Installation

```bash
npm install -g jiro-cli
```

This will automatically install Python and the `jirosearch` package if not already present.

## Quick Start

### Start Server
```bash
jiro serve
```

### Start Dashboard
```bash
jiro dashboard
```

### Search
```bash
jiro search "python web scraping"
jiro search "AI news" --engine bing --num 20
```

### Scrape
```bash
jiro scrape https://example.com
jiro scrape https://example.com --format markdown
```

### Check Status
```bash
jiro status
```

### MCP Server (for AI agents)
```bash
jiro mcp
```

## Configuration

### Claude Desktop / Cursor

Add to your MCP config:

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

### Config File

Create `~/.jiro/config.yaml`:

```yaml
server:
  host: 0.0.0.0
  port: 8000

engines:
  - google
  - bing
  - brave

cache:
  type: sqlite
  ttl: 3600

auth:
  enabled: true
  jwt_secret: ${JIRO_JWT_SECRET}
```

## API Usage

### Start Server
```bash
jiro serve --host 0.0.0.0 --port 8000
```

### Search API
```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{"q": "python web scraping", "engine": "google"}'
```

### Social Scraping
```bash
curl -X POST http://localhost:8000/v1/social \
  -H "Content-Type: application/json" \
  -d '{"url": "https://reddit.com/r/programming"}'
```

### Smart Search
```bash
curl -X POST http://localhost:8000/v1/smart \
  -H "Content-Type: application/json" \
  -d '{"query": "github.com/fastapi"}'
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search` | Search via 9 engines |
| `scrape` | Extract URL content |
| `ai_search` | Research with citations |
| `search_hybrid` | Hybrid multi-signal search |
| `search_structured` | Extract with JSON schema |
| `social_scrape` | Scrape social URLs |
| `social_search` | Search social platforms |
| `social_batch` | Batch scrape URLs |
| `smart_search` | Intent-aware routing |
| `smart_classify` | Classify intent |
| `compare_engines` | Compare engine results |
| `monitor_status` | Server health metrics |

## Pricing

| Plan | Price | RPM | RPD | Features |
|------|-------|-----|-----|----------|
| Free | $0 | 10 | 100 | Basic search, social scraping |
| Starter | $29/mo | 60 | 5,000 | Hybrid search, structured extraction |
| Pro | $99/mo | 300 | 50,000 | All features, webhooks |
| Enterprise | $499/mo | 1,000 | 500,000 | Custom models, priority support |

## Requirements

- **Node.js**: >= 18.0.0
- **Python**: >= 3.11 (auto-installed if missing)

## Links

- **GitHub**: https://github.com/DevAnimecx/jiro
- **Documentation**: https://jiro.dev/docs
- **Discord**: https://discord.gg/jiro
- **Twitter**: @jirosearch

## License

MIT License - see [LICENSE](LICENSE) for details.