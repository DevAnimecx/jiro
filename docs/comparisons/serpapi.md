# Jiro vs SerpAPI

A practical comparison for developers choosing a **search API**.

## Feature comparison

| Feature | **Jiro (OSS, self-hosted)** | **SerpAPI** |
|---------|------------------------------|-------------|
| Web search | ✅ 9 engines | ✅ Google-focused |
| Web scraping | ✅ universal (markdown/text/html/JSON) | ❌ |
| Agentic research (`/ai/search`) | ✅ | ❌ |
| MCP server | ✅ | ❌ |
| Function-calling schemas | ✅ OpenAI/Anthropic/Gemini | ❌ |
| LangChain / LlamaIndex | ✅ | ❌ |
| Legal compliance layer | ✅ robots.txt, ToS, audit | ❌ |
| Self-hosted / air-gapped | ✅ | ❌ |
| Data leaves your network | ❌ (stays local) | ✅ (cloud) |
| Open source | ✅ MIT | ❌ |
| Starting price | **$0** | $200+/mo (100k req) |

## Pricing

- **SerpAPI**: ~$200/month for 100k searches; scales to $1,500+/mo at 1M.
- **Jiro**: free (MIT). You pay only for your own infrastructure/proxies.

## When to choose Jiro

- You want **no per-query billing**.
- You need **scraping + search + AI agents** in one tool.
- You must keep data **on-prem / air-gapped** for compliance.
- You want **MCP** so Claude/Cursor can search the web natively.

## When SerpAPI may fit

- You only need Google SERP JSON and don't want to manage infrastructure.
- You accept a managed cloud dependency.

→ See also: [vs ScraperAPI](scraperapi.md) · [vs Bright Data](bright-data.md)
