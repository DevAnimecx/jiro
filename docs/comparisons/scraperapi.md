# Jiro vs ScraperAPI

Comparing two approaches to **web data extraction**.

## Feature comparison

| Feature | **Jiro (OSS, self-hosted)** | **ScraperAPI** |
|---------|------------------------------|----------------|
| Web search (9 engines) | ✅ | ❌ |
| Universal scraper | ✅ markdown/text/html/JSON | ✅ |
| Agentic research | ✅ `/ai/search`, `/ai/agent` | ❌ |
| MCP server | ✅ | ❌ |
| Structured extraction (JSON-LD, recipes) | ✅ | Partial |
| AI-agent tooling (function calling) | ✅ | ❌ |
| Legal compliance layer | ✅ | ❌ |
| Self-hosted | ✅ | ❌ (cloud only) |
| Open source | ✅ MIT | ❌ |
| Starting price | **$0** | $299+/mo |

## Pricing

- **ScraperAPI**: from $299/month for ~100k requests; $1,999+/mo at 1M.
- **Jiro**: free (MIT); bring your own proxies or run without them.

## When to choose Jiro

- You need **search + scrape + AI research** in one API.
- You want **MCP** or function-calling for agents.
- You must self-host for data sovereignty.

→ See also: [vs SerpAPI](serpapi.md) · [vs Bright Data](bright-data.md)
