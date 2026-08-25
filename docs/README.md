# Jiro Search — Documentation Hub

Welcome to the **Jiro Search** documentation. Jiro is a **local-first, AI-native
web search & scraping API** — a **self-hosted SerpAPI alternative** with an
**MCP server**, agentic research, and a built-in legal compliance layer.

## Quick Navigation

| Guide | What it covers |
|-------|----------------|
| [MCP Integration](mcp.md) | Connect Jiro to Claude Desktop, Cursor, Continue.dev, Zed, Cline |
| [Compliance & Responsible Use](compliance.md) | robots.txt, ToS tracking, audit logs, GDPR notes |
| [Roadmap](ROADMAP.md) | Open-core model, Jiro Cloud & Enterprise plans |
| [vs SerpAPI](comparisons/serpapi.md) | Feature & pricing comparison |
| [vs ScraperAPI](comparisons/scraperapi.md) | Feature & pricing comparison |
| [vs Bright Data](comparisons/bright-data.md) | Feature & pricing comparison |
| [Deep Research Agent Tutorial](tutorials/deep-research-agent.md) | Build an agent with Jiro + Claude (MCP) |

## What is Jiro?

Jiro scrapes Google, Bing, DuckDuckGo, Brave, YouTube, Amazon, eBay, Yandex and
Baidu directly, caches results locally in SQLite (sub-50 ms cached responses),
and exposes them via a **SerpAPI-compatible REST API** plus an **MCP server** for
AI agents. It is **MIT-licensed** and designed to be self-hosted.

## Why self-host a search API?

- **No per-query billing** — SerpAPI charges $200+/month for 100k requests.
- **Data sovereignty** — your queries never leave your infrastructure.
- **AI-agent native** — first-class MCP, function calling and LangChain support.
- **Compliance-ready** — robots.txt, ToS acknowledgments and audit logging.

## Next steps

```bash
pip install jiro-search
jiro serve
```

Then open `http://localhost:8000/docs` for the interactive API reference.
