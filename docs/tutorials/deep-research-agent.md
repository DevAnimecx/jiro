# Tutorial: Build a Deep Research Agent with Jiro + Claude (MCP)

This tutorial shows how to give **Claude Desktop** live web research using
**Jiro's MCP server** — no custom glue code required.

## Prerequisites

- Python 3.11+
- `jiro-search` installed (`pip install jiro-search`)
- Claude Desktop installed

## Step 1 — Start Jiro's MCP server

```bash
jiro serve            # optional: keeps the REST API + cache warm
jiro mcp               # starts the MCP server over stdio
```

For remote agents use the HTTP transport:

```bash
jiro mcp --transport http
# → POST /mcp, GET /mcp, DELETE /mcp on http://localhost:8000
```

## Step 2 — Register Jiro with Claude Desktop

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

Restart Claude Desktop. You should see `jiro` listed under available MCP tools
(`search`, `scrape`, `ai_search`).

## Step 3 — Ask a research question

> "Use Jiro to research the best Python web scraping libraries in 2026 and give
> me a cited summary."

Claude will call `jiro.ai_search` (or `search` + `scrape`) and return an answer
with citations sourced from live web pages.

## Step 4 — Verify citations

Each `ai_search` result includes `citations[]` with `title` and `url`. You can
ask Claude to open a specific source with the `scrape` tool to read the full
page.

## Going further

- Enable an LLM key (`llm.api_key`) for higher-quality synthesis.
- Add BYOK proxies (`scraping.proxy.provider`) for reliable Google/DuckDuckGo
  access from datacenter IPs.
- Use `jiro mcp --transport http` behind a reverse proxy for team-wide agents.

→ Reference: [MCP Integration](mcp.md)
