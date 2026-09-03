# Jiro v0.2 - Search Intelligence Platform

[![Version](https://img.shields.io/badge/version-0.2.0-blue.svg)](https://github.com/DevAnimecx/jiro)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.10+-yellow.svg)](https://python.org)

> Local-first web search, scraping, and social media intelligence platform.

## Features

### Phase 1: Search Intelligence
- **Hybrid Search** - Combines keyword, semantic, and freshness signals
- **Relevance Scoring** - Multi-signal ranking with configurable weights
- **Search Filters** - Domain include/exclude, time range, category filters
- **Highlights** - Query-aware snippet extraction
- **Answer Synthesis** - Extractive answers from search results
- **Multi-Query** - Parallel query expansion for complex topics

### Phase 2: Social Scraping (12 Platforms)
- Reddit, Hacker News, YouTube, Bluesky, Twitter/X
- Threads, Instagram, TikTok, LinkedIn, Facebook
- Telegram, Pinterest

### Phase 3: Advanced Features
- **Structured Extraction** - JSON Schema-based data extraction
- **Intent Classification** - Rule-based intent detection (16 types)
- **Smart Search** - Auto-routing based on intent
- **Plugin System** - 5 plugin types: engine, search, datasource, extractor, social

### Phase 4: Pro Tier
- **API Key Authentication** - Secure key-based access
- **Rate Limiting** - Token bucket per API key
- **Quota Management** - Daily request limits
- **Usage Analytics** - Detailed usage tracking
- **Tiered Plans** - Free, Starter ($29), Pro ($99), Enterprise ($499)

### Phase 5: Production Ready
- **Docker Support** - One-command deployment
- **Kubernetes Helm Chart** - Production-ready orchestration
- **OpenAPI 3.1 Spec** - Complete API documentation
- **SDK Generation** - Python, TypeScript, Go clients

## Quick Start

### Installation

```bash
# Install from PyPI
pip install jirosearch

# Or clone and install from source
git clone https://github.com/DevAnimecx/jiro.git
cd jiro-search
pip install -e .
```

### Update

```bash
# Check for updates
jiro check-update

# Update to latest version with health checks
jiro update

# Update without running tests
jiro update --no-tests

# Force reinstall current version
jiro update --force
```

### Run Server

```bash
# Start the API server
jiro serve

# Or with specific host/port
jiro serve --host 0.0.0.0 --port 8000
```

### Run Dashboard

```bash
# Start the web dashboard on port 3000
jiro dashboard
```

### Docker Deployment

```bash
# Build and run with Docker
docker-compose up -d

# Or build image manually
docker build -t jiro .
docker run -p 8000:8000 jiro
```

## API Usage

### Basic Search

```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "q": "python web scraping",
    "engine": "google",
    "max_results": 10
  }'
```

### Hybrid Search

```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "q": "latest AI research",
    "hybrid": true,
    "answer": true,
    "highlights": true
  }'
```

### Social Media Scraping

```bash
# Scrape a Reddit post
curl -X POST http://localhost:8000/v1/social \
  -H "Content-Type: application/json" \
  -d '{"url": "https://reddit.com/r/programming/comments/abc123"}'

# Scrape a YouTube video
curl -X POST http://localhost:8000/v1/social \
  -H "Content-Type: application/json" \
  -d '{"url": "https://youtube.com/watch?v=dQw4w9WgXcQ"}'
```

### Smart Search (Intent Routing)

```bash
# Auto-detect intent and route
curl -X POST http://localhost:8000/v1/smart \
  -H "Content-Type: application/json" \
  -d '{"query": "github.com/fastapi"}'

# Classify intent without executing
curl -X POST http://localhost:8000/v1/smart/classify \
  -H "Content-Type: application/json" \
  -d '{"query": "buy iphone 15 pro"}'
```

### Structured Extraction

```bash
curl -X POST http://localhost:8000/v1/structured/extract \
  -H "Content-Type: application/json" \
  -d '{
    "query": "python web frameworks",
    "schema": {
      "type": "object",
      "properties": {
        "name": {"type": "string"},
        "stars": {"type": "integer"},
        "description": {"type": "string"}
      }
    }
  }'
```

## Pro Tier & Pricing

### Plans

| Plan | Price | RPM | RPD | Features |
|------|-------|-----|-----|----------|
| **Free** | $0 | 10 | 100 | Basic search, social scraping |
| **Starter** | $29/mo | 60 | 5,000 | Hybrid search, structured extraction |
| **Pro** | $99/mo | 300 | 50,000 | All features, webhooks |
| **Enterprise** | $499/mo | 1,000 | 500,000 | Custom models, priority support |

### API Key Management

```bash
# Create a new API key
curl -X POST http://localhost:8000/v1/pro/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "My App", "tier": "starter"}'

# List API keys
curl http://localhost:8000/v1/pro/keys

# Get usage stats
curl "http://localhost:8000/v1/pro/usage?key_id=YOUR_KEY_ID&days=30"

# Upgrade tier
curl -X PUT http://localhost:8000/v1/pro/keys/KEY_ID/upgrade \
  -H "Content-Type: application/json" \
  -d '{"tier": "pro"}'
```

## MCP Integration

Jiro works with MCP-compatible clients (Claude Desktop, Cursor, Continue.dev):

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

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `search` | Search the web via multiple engines |
| `scrape` | Scrape URL content as markdown |
| `ai_search` | Research with citations |
| `search_hybrid` | Hybrid search with multi-signal ranking |
| `search_structured` | Extract structured data with JSON schema |
| `social_scrape` | Scrape social media URLs |
| `social_search` | Search across social platforms |
| `social_batch` | Batch scrape multiple URLs |
| `smart_search` | Intent-aware smart routing |
| `smart_classify` | Classify search intent |
| `compare_engines` | Compare results across engines |
| `monitor_status` | Get server health and metrics |

## Configuration

### Environment Variables

```bash
# Database
JIRO_DB_PATH=/path/to/jiro.db  # SQLite path
JIRO_DB_POSTGRES=postgresql://user:pass@localhost/jiro  # PostgreSQL URL

# Cache
JIRO_CACHE_TYPE=sqlite  # sqlite, memory, redis
JIRO_CACHE_TTL=3600

# API
JIRO_API_KEY=your_api_key
JIRO_SECRET_KEY=your_secret_key

# Engines
JIRO_ENGINES=google,bing,brave,duckduckgo

# Proxy
JIRO_PROXY_URL=http://proxy:8080
```

### Config File

```yaml
# jiro.yaml
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

pro:
  enabled: true
  default_tier: free
```

## Architecture

```
jiro/
├── server/           # FastAPI application
│   ├── routers/      # API endpoints
│   └── deps.py       # Dependencies
├── search/           # Search intelligence
│   ├── hybrid.py     # Hybrid search
│   ├── reranker.py   # Result reranking
│   ├── embeddings.py # Semantic search
│   ├── relevance.py  # Relevance scoring
│   ├── filters.py    # Search filters
│   ├── highlights.py # Snippet highlights
│   ├── answer.py     # Answer synthesis
│   ├── multiquery.py # Query expansion
│   ├── structured.py # Structured extraction
│   └── intent.py     # Intent classification
├── scraping/         # Web scraping
│   ├── engines.py    # Search engines
│   ├── client.py     # HTTP client
│   └── social/       # Social platform scrapers
├── plugins/          # Plugin system
│   ├── engine/       # Engine plugins
│   ├── search_plugin/# Search plugins
│   └── datasource/   # Datasource plugins
├── ai/               # AI/LLM integration
├── mcp.py           # MCP server
├── pro.py           # Pro tier system
├── db.py            # SQLite database
├── db_postgres.py   # PostgreSQL database
└── dashboard.py     # Web UI dashboard
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run type checker
mypy jiro/

# Run linter
ruff check jiro/
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Support

- Documentation: https://jiro.dev/docs
- Issues: https://github.com/DevAnimecx/jiro/issues
- Discord: https://discord.gg/jiro