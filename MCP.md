# Jiro MCP Server

> Model Context Protocol server for AI assistants

## Overview

Jiro implements the full MCP JSON-RPC protocol, allowing AI assistants like Claude Desktop, Cursor, Continue.dev, and VS Code to call Jiro tools directly.

**Version**: 0.2.1  
**Protocol**: 2025-03-26 (with 2024-11-05 fallback)  
**Transport**: stdio (primary), HTTP/SSE (secondary)

## Quick Start

### Claude Desktop

Add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "jiro": {
      "command": "python",
      "args": ["-m", "jiro.mcp"],
      "env": {
        "JIRO_DB_PATH": "~/.jiro/jiro.db"
      }
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
      "command": "python",
      "args": ["-m", "jiro.mcp"]
    }
  }
}
```

### Docker

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

## Tools (16)

### Core Search

| Tool | Description | Required Args |
|------|-------------|---------------|
| `search` | Search 9 engines (Google, Bing, Brave, etc.) | `q` |
| `scrape` | Scrape URL to markdown/text/html/json | `url` |
| `ai_search` | AI research with citations | `query` |

### Advanced Search

| Tool | Description | Required Args |
|------|-------------|---------------|
| `search_hybrid` | Hybrid multi-signal search | `query` |
| `search_structured` | Structured data extraction | `query`, `schema` |
| `compare_engines` | Compare engine results | `query` |

### Social Media

| Tool | Description | Required Args |
|------|-------------|---------------|
| `social_scrape` | Scrape social media URLs | `url` |
| `social_search` | Search social platforms | `query` |
| `social_batch` | Batch scrape multiple URLs | `urls` |

### Smart Features

| Tool | Description | Required Args |
|------|-------------|---------------|
| `smart_search` | Intent-aware smart routing | `query` |
| `smart_classify` | Classify search intent | `query` |

### System

| Tool | Description | Required Args |
|------|-------------|---------------|
| `monitor_status` | Server health metrics | - |
| `health_check` | Quick health check | - |
| `cache_stats` | Cache statistics | - |
| `list_engines` | List all search engines | - |
| `list_social_platforms` | List social platforms | - |

## Resources (5)

| URI | Description |
|-----|-------------|
| `jiro://engines` | Supported search engines |
| `jiro://compliance` | Engine ToS compliance |
| `jiro://social_platforms` | Social platform capabilities |
| `jiro://plans` | Pricing plans |
| `jiro://plugins` | Installed plugins |

## Prompts (6)

| Prompt | Description | Arguments |
|--------|-------------|-----------|
| `search_and_summarize` | Search and summarize with citations | `topic` |
| `compare_engines` | Compare across engines | `query` |
| `social_research` | Research on social media | `topic`, `platforms` |
| `deep_research` | Deep AI research | `question`, `max_sources` |
| `extract_structured` | Extract structured data | `query`, `fields` |
| `competitor_analysis` | Analyze competitors | `competitor` |

## Tool Details

### search

Search the web via multiple engines.

```json
{
  "name": "search",
  "arguments": {
    "q": "python web scraping",
    "engine": "google",
    "type": "web",
    "num": 10,
    "time_range": "month"
  }
}
```

**Engines**: google, bing, brave, duckduckgo, youtube, amazon, ebay, yandex, baidu  
**Types**: web, images, news, videos, shopping, places  
**Time Range**: any, day, week, month, year

### scrape

Scrape a URL and extract readable content.

```json
{
  "name": "scrape",
  "arguments": {
    "url": "https://example.com",
    "format": "markdown",
    "include_metadata": true
  }
}
```

**Formats**: markdown, text, html, json

### ai_search

AI-powered research with citations.

```json
{
  "name": "ai_search",
  "arguments": {
    "query": "What are the best Python web frameworks in 2026?",
    "max_sources": 8
  }
}
```

### search_hybrid

Hybrid search combining keyword + semantic + freshness signals.

```json
{
  "name": "search_hybrid",
  "arguments": {
    "query": "latest AI research papers",
    "type": "web",
    "engine": "google",
    "max_results": 20
  }
}
```

### search_structured

Extract structured data with JSON Schema.

```json
{
  "name": "search_structured",
  "arguments": {
    "query": "python web frameworks",
    "schema": {
      "name": {},
      "description": {},
      "stars": {},
      "url": {}
    }
  }
}
```

### social_scrape

Scrape social media URLs.

```json
{
  "name": "social_scrape",
  "arguments": {
    "url": "https://reddit.com/r/programming/comments/abc123"
  }
}
```

**Supported**: Reddit, HN, YouTube, Bluesky, Twitter, Threads, Instagram, TikTok, LinkedIn, Facebook, Telegram, Pinterest

### social_search

Search across social platforms.

```json
{
  "name": "social_search",
  "arguments": {
    "query": "python tips",
    "platforms": ["reddit", "twitter", "youtube"]
  }
}
```

### social_batch

Batch scrape multiple URLs.

```json
{
  "name": "social_batch",
  "arguments": {
    "urls": [
      "https://reddit.com/r/python",
      "https://news.ycombinator.com"
    ]
  }
}
```

### smart_search

Intent-aware smart routing.

```json
{
  "name": "smart_search",
  "arguments": {
    "query": "github.com/fastapi",
    "type": "web"
  }
}
```

### smart_classify

Classify search intent.

```json
{
  "name": "smart_classify",
  "arguments": {
    "query": "buy iphone 16 pro max"
  }
}
```

### compare_engines

Compare results across engines.

```json
{
  "name": "compare_engines",
  "arguments": {
    "query": "best python IDE",
    "engines": ["google", "bing", "brave"]
  }
}
```

### monitor_status

Get server status and metrics.

```json
{
  "name": "monitor_status",
  "arguments": {}
}
```

### health_check

Quick health check.

```json
{
  "name": "health_check",
  "arguments": {
    "timeout": 5
  }
}
```

### cache_stats

Get cache statistics.

```json
{
  "name": "cache_stats",
  "arguments": {}
}
```

### list_engines

List all search engines.

```json
{
  "name": "list_engines",
  "arguments": {}
}
```

### list_social_platforms

List social platforms.

```json
{
  "name": "list_social_platforms",
  "arguments": {}
}
```

## Resources

### jiro://engines

List of configured search engines and supported types.

### jiro://compliance

Terms-of-service notes per engine.

### jiro://social_platforms

List of supported social media platforms and their capabilities.

### jiro://plans

Available pricing tiers and feature comparison.

### jiro://plugins

Installed search, engine, and datasource plugins.

## Prompts

### search_and_summarize

Search the web and provide a summary with citations.

```json
{
  "name": "search_and_summarize",
  "arguments": {
    "topic": "latest advances in AI"
  }
}
```

### compare_engines

Compare search results across multiple engines.

```json
{
  "name": "compare_engines",
  "arguments": {
    "query": "python web scraping"
  }
}
```

### social_research

Research a topic across social media platforms.

```json
{
  "name": "social_research",
  "arguments": {
    "topic": "artificial intelligence",
    "platforms": "reddit,twitter,youtube"
  }
}
```

### deep_research

Deep AI research with multiple sources and citations.

```json
{
  "name": "deep_research",
  "arguments": {
    "question": "What are the pros and cons of microservices?",
    "max_sources": "10"
  }
}
```

### extract_structured

Extract structured data from search results.

```json
{
  "name": "extract_structured",
  "arguments": {
    "query": "python web frameworks",
    "fields": "{\"name\": {}, \"description\": {}, \"stars\": {}}"
  }
}
```

### competitor_analysis

Analyze competitors across search engines and social media.

```json
{
  "name": "competitor_analysis",
  "arguments": {
    "competitor": "fastapi"
  }
}
```

## Progress Notifications

Long-running tool calls emit progress notifications when you pass `_meta.progressToken`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "ai_search",
    "arguments": {"query": "complex research question"},
    "_meta": {"progressToken": "unique-token-123"}
  }
}
```

Server will emit:

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/progress",
  "params": {
    "progressToken": "unique-token-123",
    "progress": 0.3,
    "total": 1.0,
    "message": "searching"
  }
}
```

## Cancellation

Clients can cancel in-flight requests:

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/cancelled",
  "params": {
    "requestId": 1,
    "reason": "user cancelled"
  }
}
```

## Error Handling

MCP errors follow JSON-RPC spec:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "url is required",
    "data": {
      "available_tools": [...]
    }
  }
}
```

**Error Codes**:
- `-32700`: Parse error
- `-32600`: Invalid request
- `-32601`: Method not found
- `-32602`: Invalid params
- `-32603`: Internal error

## Completion

The server supports argument auto-completion for:

- `search.engine`: google, bing, brave, etc.
- `search.type`: web, images, news, videos, shopping, places
- `search.time_range`: any, day, week, month, year
- `scrape.format`: markdown, text, html, json
- `social_search.platforms`: reddit, twitter, youtube, etc.
- `compare_engines.engines`: google, bing, brave, etc.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JIRO_DB_PATH` | `~/.jiro/jiro.db` | SQLite database path |
| `JIRO_CACHE_TYPE` | `sqlite` | Cache type (sqlite/memory/redis) |
| `JIRO_AUTH_ENABLED` | `false` | Enable authentication |

## Troubleshooting

### Tool not found

Ensure you're using the correct tool name. Run `tools/list` to see all available tools.

### Connection refused

1. Ensure Jiro is installed: `pip install jirosearch`
2. Test the MCP server: `python -m jiro.mcp`
3. Check logs for errors

### Slow responses

1. Use `monitor_status` to check engine health
2. Enable caching: `JIRO_CACHE_TYPE=sqlite`
3. Use `compare_engines` to find fastest engine

## Links

- **GitHub**: https://github.com/DevAnimecx/jiro
- **PyPI**: https://pypi.org/project/jirosearch/
- **Documentation**: https://jiro.dev/docs
- **Discord**: https://discord.gg/jiro