# Jiro vs Bright Data

Comparing a **self-hosted search/scrape stack** with a **proxy + unblocking
platform**.

## Feature comparison

| Feature | **Jiro (OSS, self-hosted)** | **Bright Data** |
|---------|------------------------------|-----------------|
| Web search (9 engines) | ✅ | ❌ (proxy only) |
| Universal scraper | ✅ | ✅ (via unblocking) |
| Agentic research | ✅ | ❌ |
| MCP server | ✅ | ❌ |
| AI-agent tooling | ✅ function calling, LangChain | ❌ |
| Legal compliance layer | ✅ | Partial |
| Self-hosted / air-gapped | ✅ | ❌ (cloud only) |
| Open source | ✅ MIT | ❌ |
| Starting price | **$0** | $500+/mo |

## Pricing

- **Bright Data**: ~$500+/month for proxy traffic; $3,000+/mo at scale.
- **Jiro**: free (MIT). Bring your own Bright Data / Oxylabs / ScraperAPI /
  ZenRows / Smartproxy credentials via `scraping.proxy.provider`.

## Complementary, not competing

Jiro **integrates with** Bright Data and other proxy providers through BYOK. Use
Bright Data's residential fleet as Jiro's `proxy.provider: brightdata` and get
both worlds: a free, open-source search/scrape/AI stack on top of a paid proxy
network.

→ See also: [vs SerpAPI](serpapi.md) · [vs ScraperAPI](scraperapi.md)
