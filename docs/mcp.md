# MCP Integration — Jiro as a Model Context Protocol Server

**Jiro** ships a full **Model Context Protocol (MCP)** server so AI agents
(Claude Desktop, Cursor, Continue.dev, Zed, Cline, VS Code) can call live web
search, page scraping and agentic research directly.

## Transports

| Transport | Command / Endpoint | Use case |
|-----------|-------------------|----------|
| **stdio** | `jiro mcp` | Local agents (Claude Desktop, Cursor) |
| **Streamable HTTP** | `jiro mcp --transport http` → `POST /mcp`, `GET /mcp`, `DELETE /mcp` | Remote agents |
| **HTTP+SSE (legacy)** | `GET /sse`, `POST /messages` | Older MCP clients |

Protocol versions negotiated: `2025-03-26`, `2024-11-05`.

## Tools

| Tool | Description |
|------|-------------|
| `search` | Web search across 9 engines, 6 search types (web/images/news/videos/shopping/places) |
| `scrape` | URL → markdown / text / html / json |
| `ai_search` | Agentic research with citations (plan → search → read → synthesize) |

## Prompts

- `search_and_summarize` — search the web and produce a cited summary.
- `compare_engines` — compare results across google/bing/brave.

## Autocompletion

The server provides argument completion for engine names, search types, time
ranges and scrape formats.

## Claude Desktop

Edit `claude_desktop_config.json`:

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

## Cursor / Continue.dev / Zed / Cline

Point the MCP client at `jiro mcp` as the server command. For remote access use
`jiro mcp --transport http` and connect to `http://host:8000/mcp` with your
`X-API-Key` or `Authorization: Bearer <jwt>`.

## Auth

MCP HTTP endpoints accept the same credentials as the REST API:
`X-API-Key: jsk_...` or `Authorization: Bearer <jwt>`. Sessions are created on
`initialize` and identified by `Mcp-Session-Id`; they support resume via
`Last-Event-ID` and per-key rate limits.
