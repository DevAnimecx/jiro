# Jiro Search

> Local-first, AI-native web search & scraping platform

## What is Jiro?

Jiro is a self-hosted, drop-in alternative to SerpAPI that provides:

- **9 Search Engines**: Google, Bing, Brave, DuckDuckGo, YouTube, Amazon, eBay, Yandex, Baidu
- **12 Social Platforms**: Reddit, HN, YouTube, Bluesky, Twitter, Threads, Instagram, TikTok, LinkedIn, Facebook, Telegram, Pinterest
- **Hybrid Search**: Keyword + semantic + freshness signals
- **Smart Search**: Intent-aware auto-routing
- **MCP Server**: 12 tools for AI agents
- **Pro Tier**: Free, Starter ($29), Pro ($99), Enterprise ($499)

## Quick Start

### Docker

```bash
docker run -p 8000:8000 jiro/jiro:0.2.1
```

### Docker Compose

```yaml
version: '3.8'
services:
  jiro:
    image: jiro/jiro:0.2.1
    ports:
      - "8000:8000"
    volumes:
      - jiro-data:/data
    environment:
      - JIRO_DB_PATH=/data/jiro.db
      - JIRO_SECRET_KEY=your-secret-key
volumes:
  jiro-data:
```

```bash
docker-compose up -d
```

### Kubernetes (Helm)

```bash
helm repo add jiro https://charts.jiro.dev
helm install jiro jiro/jiro --version 0.2.1
```

## API Usage

### Search
```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{"q": "python web scraping", "engine": "google"}'
```

### Hybrid Search
```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{"q": "latest AI research", "hybrid": true, "answer": true}'
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

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JIRO_HOST` | `127.0.0.1` | Server host |
| `JIRO_PORT` | `8000` | Server port |
| `JIRO_DB_PATH` | `~/.jiro/jiro.db` | SQLite database path |
| `JIRO_DB_POSTGRES` | | PostgreSQL connection string |
| `JIRO_CACHE_TYPE` | `sqlite` | Cache type (sqlite/memory/redis) |
| `JIRO_CACHE_TTL` | `3600` | Cache TTL in seconds |
| `JIRO_AUTH_ENABLED` | `true` | Enable authentication |
| `JIRO_JWT_SECRET` | | JWT secret key |
| `JIRO_SECRET_KEY` | | Server secret key |

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
  - duckduckgo

cache:
  type: sqlite
  ttl: 3600

auth:
  enabled: true
  jwt_secret: ${JIRO_JWT_SECRET}
```

## MCP Integration

### Claude Desktop

Add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "jiro": {
      "command": "docker",
      "args": ["exec", "-i", "jiro", "python", "-m", "jiro.mcp"]
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "jiro": {
      "command": "docker",
      "args": ["exec", "-i", "jiro", "python", "-m", "jiro.mcp"]
    }
  }
}
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

## Links

- **GitHub**: https://github.com/DevAnimecx/jiro
- **Documentation**: https://jiro.dev/docs
- **PyPI**: https://pypi.org/project/jirosearch/
- **npm**: https://www.npmjs.com/package/jiro-cli
- **Docker Hub**: https://hub.docker.com/r/jiro/jiro
- **Discord**: https://discord.gg/jiro
- **Twitter**: @jirosearch

## License

MIT License - see [LICENSE](LICENSE) for details.