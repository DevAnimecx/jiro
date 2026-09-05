"""Pre-built tool schemas for LLM agents (PRD §6.5).

* OpenAI / OpenRouter / Ollama — function-calling ``tools`` payloads.
* Anthropic — ``tools`` payloads.
* Gemini — ``function_declarations`` payloads.
* LangChain-style ``Tool`` and LlamaIndex-style ``ToolSpec`` wrappers
  (duck-typed, no framework dependency).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

ENGINE_ENUM = ["google", "bing", "duckduckgo", "brave", "youtube", "amazon", "ebay",
               "yandex", "baidu"]

JIRO_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "q": {"type": "string", "description": "Search query"},
        "engine": {"type": "string", "enum": ENGINE_ENUM, "default": "google"},
        "type": {"type": "string", "enum": ["web", "images", "news", "videos",
                                             "shopping", "places"], "default": "web"},
        "num": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        "language": {"type": "string", "default": "en"},
        "location": {"type": "string", "default": "us"},
        "time_range": {"type": "string",
                       "enum": ["any", "day", "week", "month", "year"], "default": "any"},
    },
    "required": ["q"],
}

JIRO_SCRAPE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "format": "uri", "description": "URL to scrape"},
        "format": {"type": "string", "enum": ["markdown", "text", "html", "json"],
                   "default": "markdown"},
        "include_metadata": {"type": "boolean", "default": True},
    },
    "required": ["url"],
}

JIRO_AI_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Natural-language research question"},
        "max_sources": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
    },
    "required": ["query"],
}

SOCIAL_SCRAPE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "format": "uri",
                "description": "Social media URL (e.g. https://twitter.com/user/status/123)"},
        "platform": {"type": "string",
                     "enum": ["twitter", "reddit", "youtube", "bluesky", "threads",
                              "instagram", "tiktok", "linkedin", "facebook", "telegram",
                              "pinterest", "hackernews"],
                     "description": "Force a specific platform (auto-detected from URL if omitted)"},
        "format": {"type": "string", "enum": ["json", "markdown", "text"],
                   "default": "json", "description": "Output format"},
    },
    "required": ["url"],
}

SOCIAL_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query"},
        "platforms": {"type": "array", "items": {"type": "string"},
                      "default": ["twitter", "reddit", "youtube"],
                      "description": "Platforms to search (e.g. twitter, reddit, youtube, tiktok, instagram)"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
    },
    "required": ["query"],
}

SOCIAL_BATCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "urls": {"type": "array", "items": {"type": "string", "format": "uri"},
                 "description": "List of social media URLs (max 10)"},
        "parallel": {"type": "boolean", "default": True,
                     "description": "Scrape URLs in parallel (faster) or sequentially (safer)"},
    },
    "required": ["urls"],
}

SMART_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Natural-language search query or research question"},
        "type": {"type": "string", "enum": ["web", "images", "news", "videos", "shopping"], "default": "web"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        "schema": {"type": "object", "description": "Optional JSON Schema for structured extraction"},
    },
    "required": ["query"],
}


def openai_tools(include_ai: bool = True) -> List[Dict[str, Any]]:
    tools = [
        {"type": "function", "function": {"name": "jiro_search",
                                          "description": "Search the web and return "
                                                         "structured SERP results.",
                                          "parameters": JIRO_SEARCH_SCHEMA}},
        {"type": "function", "function": {"name": "jiro_scrape",
                                          "description": "Scrape a URL and extract its "
                                                         "readable content as markdown.",
                                          "parameters": JIRO_SCRAPE_SCHEMA}},
    ]
    if include_ai:
        tools.append({"type": "function",
                      "function": {"name": "jiro_ai_search",
                                   "description": "Answer a research question with "
                                                  "citations by searching and reading pages.",
                                   "parameters": JIRO_AI_SEARCH_SCHEMA}})
    return tools


def anthropic_tools(include_ai: bool = True) -> List[Dict[str, Any]]:
    tools = [
        {"name": "jiro_search", "description": "Search the web and return structured "
                                               "SERP results.",
         "input_schema": JIRO_SEARCH_SCHEMA},
        {"name": "jiro_scrape", "description": "Scrape a URL and extract its readable "
                                               "content as markdown.",
         "input_schema": JIRO_SCRAPE_SCHEMA},
    ]
    if include_ai:
        tools.append({"name": "jiro_ai_search",
                      "description": "Answer a research question with citations by "
                                     "searching and reading pages.",
                      "input_schema": JIRO_AI_SEARCH_SCHEMA})
    return tools


def gemini_tools(include_ai: bool = True) -> List[Dict[str, Any]]:
    tools = [
        {"function_declarations": [
            {"name": "jiro_search", "description": "Search the web and return structured "
                                                   "SERP results.",
             "parameters": JIRO_SEARCH_SCHEMA},
            {"name": "jiro_scrape", "description": "Scrape a URL and extract readable "
                                                   "content as markdown.",
             "parameters": JIRO_SCRAPE_SCHEMA},
        ]}
    ]
    if include_ai:
        tools[0]["function_declarations"].append(
            {"name": "jiro_ai_search",
             "description": "Answer a research question with citations.",
             "parameters": JIRO_AI_SEARCH_SCHEMA}
        )
    return tools


SOCIAL_SCRAPE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "format": "uri",
                "description": "Social media URL (e.g. https://twitter.com/user/status/123)"},
        "platform": {"type": "string",
                     "enum": ["twitter", "reddit", "youtube", "bluesky", "threads",
                              "instagram", "tiktok", "linkedin", "facebook", "telegram",
                              "pinterest", "hackernews"],
                     "description": "Force a specific platform (auto-detected from URL if omitted)"},
        "format": {"type": "string", "enum": ["json", "markdown", "text"],
                   "default": "json", "description": "Output format"},
    },
    "required": ["url"],
}

SOCIAL_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query"},
        "platforms": {"type": "array", "items": {"type": "string"},
                      "default": ["twitter", "reddit", "youtube"],
                      "description": "Platforms to search (e.g. twitter, reddit, youtube, tiktok, instagram)"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
    },
    "required": ["query"],
}

SOCIAL_BATCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "urls": {"type": "array", "items": {"type": "string", "format": "uri"},
                 "description": "List of social media URLs (max 10)"},
        "parallel": {"type": "boolean", "default": True,
                     "description": "Scrape URLs in parallel (faster) or sequentially (safer)"},
    },
    "required": ["urls"],
}

SMART_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Natural-language search query or research question"},
        "type": {"type": "string", "enum": ["web", "images", "news", "videos", "shopping"], "default": "web"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        "schema": {"type": "object", "description": "Optional JSON Schema for structured extraction"},
    },
    "required": ["query"],
}


def mcp_tools() -> List[Dict[str, Any]]:
    """MCP-style tool descriptors with rich schemas for Claude Codex, Hermes, Kimi, OpenCode, Manus."""
    return [
        {"name": "search",
         "description": "Web search across 9 engines (Google, Bing, Brave, DuckDuckGo, YouTube, Amazon, eBay, Yandex, Baidu). "
                        "Returns structured SERP results with titles, URLs, snippets, and metadata. "
                        "Use for general web research, news, images, videos, shopping, or places. "
                        "Example: engine=google, type=web, num=10.",
         "inputSchema": JIRO_SEARCH_SCHEMA},
        {"name": "scrape",
         "description": "Scrape any URL and extract readable content as markdown, text, HTML, or JSON. "
                        "Returns page title, main content, metadata, links, and images. "
                        "Automatically handles JavaScript rendering when needed. "
                        "Example: url=https://example.com/article, format=markdown.",
         "inputSchema": JIRO_SCRAPE_SCHEMA},
        {"name": "ai_search",
         "description": "Answer a research question by searching the web, reading multiple sources, "
                        "and synthesizing a comprehensive answer with citations. "
                        "Use for deep research, fact-checking, or complex questions requiring multiple sources. "
                        "Example: query='latest advances in quantum computing', max_sources=8.",
         "inputSchema": JIRO_AI_SEARCH_SCHEMA},
        {"name": "search_hybrid",
         "description": "Hybrid search combining keyword, semantic, and freshness signals for enhanced relevance. "
                        "Better than plain search for ambiguous or time-sensitive queries. "
                        "Example: query='best laptop 2025', max_results=20.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query"},
             "type": {"type": "string", "enum": ["web", "images", "news", "videos", "shopping"], "default": "web"},
             "engine": {"type": "string", "description": "Primary engine (google, bing, brave, etc.)"},
             "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
         }, "required": ["query"]}},
        {"name": "search_structured",
         "description": "Extract structured data from search results using a JSON Schema. "
                        "Returns data conforming exactly to the provided schema. "
                        "Use for product research, competitor analysis, or any structured extraction task. "
                        "Example: schema={\"name\": \"\", \"price\": {\"type\": \"number\"}, \"rating\": {}}.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query"},
             "schema": {"type": "object", "description": "JSON Schema for desired output structure"},
         }, "required": ["query", "schema"]}},
        {"name": "social_scrape",
         "description": "Scrape a social media post or profile from any supported platform. "
                        "Auto-detects platform from URL. Returns normalized data including author, content, engagement, and media. "
                        "Supports: Twitter/X, Reddit, YouTube, Bluesky, Threads, Instagram, TikTok, LinkedIn, Facebook, Telegram, Pinterest, Hacker News. "
                        "Example: url=https://twitter.com/user/status/123 or url=https://reddit.com/r/python/comments/abc.",
         "inputSchema": SOCIAL_SCRAPE_SCHEMA},
        {"name": "social_search",
         "description": "Search for posts across multiple social platforms simultaneously. "
                        "Returns normalized results ranked by relevance and engagement. "
                        "Use for social listening, trend tracking, or finding discussions about a topic. "
                        "Example: query='AI regulation', platforms=['twitter', 'reddit', 'youtube'], limit=10.",
         "inputSchema": SOCIAL_SEARCH_SCHEMA},
        {"name": "social_batch",
         "description": "Batch scrape multiple social media URLs in parallel (up to 10). "
                        "Returns per-URL results with status, data, or errors. "
                        "Use for bulk data collection, monitoring, or archiving. "
                        "Example: urls=['https://twitter.com/user/status/1', 'https://reddit.com/r/tech/comments/2'].",
         "inputSchema": SOCIAL_BATCH_SCHEMA},
        {"name": "smart_search",
         "description": "Intelligent search with automatic intent detection and routing. "
                        "Classifies the query intent (web, social, structured, images, news, videos) and "
                        "automatically routes to the best handler. "
                        "Use when you're unsure which search type to use — the agent decides. "
                        "Example: query='compare iPhone 15 vs Samsung S24'.",
         "inputSchema": SMART_SEARCH_SCHEMA},
        {"name": "smart_classify",
         "description": "Classify the intent of a search query without executing it. "
                        "Returns intent category, target handler, confidence score, and suggested schema. "
                        "Use to understand what type of search a query needs before executing. "
                        "Example: query='latest AI research papers'.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query to classify"},
         }, "required": ["query"]}},
        {"name": "compare_engines",
         "description": "Compare search results across multiple engines side by side. "
                        "Returns result counts, first results, and engine-specific metadata. "
                        "Use to find which engine is best for a particular query type. "
                        "Example: query='python tutorials', engines=['google', 'bing', 'brave'].",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query"},
             "engines": {"type": "array", "items": {"type": "string"},
                         "default": ["google", "bing", "brave"],
                         "description": "Engines to compare (google, bing, brave, duckduckgo, etc.)"},
         }, "required": ["query"]}},
        {"name": "monitor_status",
         "description": "Get comprehensive server status including engine health, cache stats, social platform status, and MCP tool count. "
                        "Use to diagnose issues or check system health before a long operation. "
                        "Example: no arguments needed.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "health_check",
         "description": "Quick health check for all services and dependencies. "
                        "Returns overall status and per-service health details. "
                        "Use as a fast connectivity test. "
                        "Example: no arguments needed.",
         "inputSchema": {"type": "object", "properties": {
             "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 5},
         }}},
        {"name": "cache_stats",
         "description": "Get cache hit/miss statistics, TTL, and configuration. "
                        "Use to understand caching behavior and debug stale results. "
                        "Example: no arguments needed.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "list_engines",
         "description": "List all available search engines with supported types (web, images, news, videos, shopping, places) and capabilities. "
                        "Use to discover what search types each engine supports. "
                        "Example: no arguments needed.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "list_social_platforms",
         "description": "List all supported social media platforms with their capabilities (scrape, search, profile, timeline, etc.) and base URLs. "
                        "Use to discover what social platforms and actions are available. "
                        "Example: no arguments needed.",
         "inputSchema": {"type": "object", "properties": {}}},
    ]


class JiroTool:
    """Minimal LangChain-compatible Tool (duck-typed: name, description, func/run)."""

    def __init__(self, name: str, description: str, func: Callable[..., Any]) -> None:
        self.name = name
        self.description = description
        self.func = func

    def run(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.run(*args, **kwargs)

    def to_langchain(self) -> "JiroTool":
        return self


def langchain_tools(search_fn: Callable[..., Any], scrape_fn: Callable[..., Any],
                    ai_fn: Optional[Callable[..., Any]] = None) -> List[JiroTool]:
    tools = [
        JiroTool("jiro_search", "Search the web and return structured SERP results.",
                 search_fn),
        JiroTool("jiro_scrape", "Scrape a URL and extract its readable content.", scrape_fn),
    ]
    if ai_fn is not None:
        tools.append(JiroTool("jiro_ai_search",
                              "Answer a research question with citations.", ai_fn))
    return tools


class ToolSpec:
    """LlamaIndex-compatible ToolSpec (duck-typed)."""

    spec_functions: List[str] = []

    def __init__(self, search_fn: Callable[..., Any], scrape_fn: Callable[..., Any],
                 ai_fn: Optional[Callable[..., Any]] = None) -> None:
        self._search_fn = search_fn
        self._scrape_fn = scrape_fn
        self._ai_fn = ai_fn
        self.spec_functions = ["jiro_search", "jiro_scrape"] + (["jiro_ai_search"] if ai_fn else [])

    def jiro_search(self, q: str, engine: str = "google", num: int = 10) -> Any:
        return self._search_fn(q=q, engine=engine, num=num)

    def jiro_scrape(self, url: str, format: str = "markdown") -> Any:
        return self._scrape_fn(url=url, format=format)

    def jiro_ai_search(self, query: str, max_sources: int = 5) -> Any:
        return self._ai_fn(query=query, max_sources=max_sources)  # type: ignore[misc]

    def to_tool_list(self) -> List[JiroTool]:
        tools = [JiroTool("jiro_search", "Search the web.", self.jiro_search),
                 JiroTool("jiro_scrape", "Scrape a URL.", self.jiro_scrape)]
        if self._ai_fn is not None:
            tools.append(JiroTool("jiro_ai_search", "Answer with citations.",
                                  self.jiro_ai_search))
        return tools
