# Jiro v0.2.1 - Complete Release Notes

> Local-first, AI-native web search & scraping platform with 12 social platforms, hybrid search, structured extraction, and Pro tier.

## What's New

### Phase 1: Search Intelligence
- **Hybrid Search** - Combines keyword, semantic, and freshness signals for enhanced relevance
- **Cross-Encoder Reranking** - ML-powered result reranking with configurable models
- **Semantic Embeddings** - Vector similarity search using sentence-transformers
- **Relevance Scoring** - Multi-signal ranking with keyword match, source authority, and freshness
- **Search Filters** - Domain include/exclude, time range, category filters
- **Highlight Extraction** - Query-aware snippet highlighting
- **Answer Synthesis** - Extractive answers from search results
- **Multi-Query Expansion** - Parallel query generation for complex topics

### Phase 2: Social Scraping (12 Platforms)
| Platform | Features |
|----------|----------|
| **Reddit** | Posts, comments, subreddits, user profiles |
| **Hacker News** | Stories, comments, user profiles |
| **YouTube** | Videos, channels, playlists, comments |
| **Bluesky** | Posts, profiles, feeds |
| **Twitter/X** | Tweets, threads, profiles (3-backend fallback) |
| **Threads** | Posts, profiles |
| **Instagram** | Posts, stories, reels, profiles |
| **TikTok** | Videos, profiles, trending |
| **LinkedIn** | Posts, profiles, companies |
| **Facebook** | Posts, profiles, groups |
| **Telegram** | Messages, channels, groups |
| **Pinterest** | Pins, boards, profiles |

### Phase 3: Advanced Features
- **Structured Extraction** - JSON Schema-based data extraction from search results
- **Intent Classification** - Rule-based classifier with 16 intent types:
  - Informational, Navigational, Transactional, Research
  - Social, Shopping, News, Video, Image, Code
  - Academic, Local, Comparison, Definition, Troubleshooting, Creative
- **Smart Search** - Auto-routing based on detected intent
- **Plugin System** - 5 plugin types:
  - **Engine Plugins** (6): Google Scholar, arXiv, GitHub, Wikipedia, HN, Reddit
  - **Search Plugins** (6): Reranker, Deduplicator, Domain Filter, Freshness Boost, Source Authority, Snippet Enricher
  - **Datasource Plugins** (3): SEC Filings, Clinical Trials, Patents
  - **Extractor Plugins**: Custom content extraction
  - **Social Plugins**: Custom platform scrapers

### Phase 4: Pro Tier
| Plan | Price | RPM | RPD | Features |
|------|-------|-----|-----|----------|
| **Free** | $0 | 10 | 100 | Basic search, social scraping |
| **Starter** | $29/mo | 60 | 5,000 | Hybrid search, structured extraction |
| **Pro** | $99/mo | 300 | 50,000 | All features, webhooks |
| **Enterprise** | $499/mo | 1,000 | 500,000 | Custom models, priority support |

- **API Key Authentication** - Secure key-based access with tiered permissions
- **Rate Limiting** - Token bucket per API key
- **Quota Management** - Daily request limits per tier
- **Usage Analytics** - Detailed per-key, per-endpoint tracking

### Phase 5: Production Ready
- **Docker** - One-command deployment with docker-compose
- **Kubernetes** - Production-ready Helm chart
- **OpenAPI 3.1** - Complete API specification
- **SDK Generation** - Python, TypeScript, Go clients
- **Web Dashboard** - Alpine.js + Tailwind UI with 5 tabs

### Security Hardening
- Auth enabled by default
- Header-only API key authentication
- Sanitized error messages
- CORS protection
- SSRF validation
- JWT secret validation

## API Endpoints

### Search
- `POST /v1/search` - Web search with hybrid mode
- `POST /v1/search/multi` - Multi-query parallel search
- `GET /v1/search/stream` - SSE streaming results

### Social
- `POST /v1/social` - Scrape social media URL
- `POST /v1/social/batch` - Batch scrape multiple URLs
- `GET /v1/social/platforms` - List supported platforms
- `POST /v1/social/search` - Search across platforms
- `POST /v1/social/search/everywhere` - Search all platforms

### Smart
- `POST /v1/smart` - Intent-aware smart search
- `POST /v1/smart/classify` - Classify search intent

### Structured
- `POST /v1/structured/extract` - Extract structured data

### Pro
- `POST /v1/pro/keys` - Create API key
- `GET /v1/pro/keys` - List API keys
- `DELETE /v1/pro/keys/{id}` - Revoke API key
- `PUT /v1/pro/keys/{id}/upgrade` - Upgrade tier
- `GET /v1/pro/usage` - Get usage stats
- `GET /v1/pro/plans` - List available plans

### Monitor
- `GET /v1/monitor/status` - Server status
- `GET /v1/monitor/health` - Health check

## MCP Tools (12)
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

## Installation

### pip
```bash
pip install jirosearch==0.2.1
```

### npm (CLI wrapper)
```bash
npm install -g jiro-cli
```

### Docker
```bash
docker pull jiro/jiro:0.2.1
docker run -p 8000:8000 jiro/jiro:0.2.1
```

### Docker Compose
```bash
git clone https://github.com/DevAnimecx/jiro.git
cd jiro-search
docker-compose up -d
```

### Kubernetes (Helm)
```bash
helm repo add jiro https://charts.jiro.dev
helm install jiro jiro/jiro --version 0.2.1
```

### From Source
```bash
git clone https://github.com/DevAnimecx/jiro.git
cd jiro-search
pip install -e .
```

## Quick Start

### Start Server
```bash
jiro serve
```

### Start Dashboard
```bash
jiro dashboard  # Opens on port 3000
```

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
  -d '{"url": "https://reddit.com/r/programming/comments/abc123"}'
```

### Smart Search
```bash
curl -X POST http://localhost:8000/v1/smart \
  -H "Content-Type: application/json" \
  -d '{"query": "github.com/fastapi"}'
```

## Configuration

### Environment Variables
```bash
# Server
JIRO_HOST=0.0.0.0
JIRO_PORT=8000

# Database
JIRO_DB_PATH=~/.jiro/jiro.db
JIRO_DB_POSTGRES=postgresql://user:pass@localhost/jiro

# Cache
JIRO_CACHE_TYPE=sqlite  # sqlite | memory | redis
JIRO_CACHE_TTL=3600

# Auth
JIRO_AUTH_ENABLED=true
JIRO_JWT_SECRET=your-secret-key-here

# Engines
JIRO_ENGINES=google,bing,brave,duckduckgo
```

### Config File (~/.jiro/config.yaml)
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

### Cursor
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

## Testing
```bash
# Run all tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_phase1_search_intelligence.py -v
pytest tests/test_social_scraping.py -v
pytest tests/test_pro_tier.py -v
```

## Security
- Auth enabled by default
- Header-only API key authentication
- Sanitized error messages
- CORS protection
- SSRF validation
- JWT secret validation

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## Changelog

### v0.2.1 (2026-09-03)
- Security patch with auth hardening
- Fixed CORS wildcard default
- Removed hardcoded DB credentials
- Sanitized error messages
- Header-only API authentication

### v0.2.0 (2026-09-03)
- Initial v0.2 release with all features above

## License

MIT License - see [LICENSE](LICENSE) for details.

## Support

- **Documentation**: https://jiro.dev/docs
- **GitHub Issues**: https://github.com/DevAnimecx/jiro/issues
- **Discord**: https://discord.gg/jiro
- **Twitter**: @jirosearch

## Credits

Built with ❤️ by the Jiro team.