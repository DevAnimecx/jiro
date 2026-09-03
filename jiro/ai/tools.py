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


def mcp_tools() -> List[Dict[str, Any]]:
    """MCP-style tool descriptors (used by the MCP server)."""
    return [
        {"name": "search",
         "description": "Search the web via Google, Bing, Brave, DuckDuckGo, YouTube, Amazon, eBay, Yandex, or Baidu. "
                        "Returns structured SERP results with titles, links, snippets, and metadata. "
                        "Supports web, images, news, videos, shopping, and places search types.",
         "inputSchema": JIRO_SEARCH_SCHEMA},
        {"name": "scrape",
         "description": "Scrape a URL and extract its readable content as markdown, text, or structured data. "
                        "Returns page title, main content, metadata, links, and images.",
         "inputSchema": JIRO_SCRAPE_SCHEMA},
        {"name": "ai_search",
         "description": "Answer a research question by searching the web, reading multiple pages, "
                        "and synthesizing a cited answer. Returns a comprehensive answer with source citations.",
         "inputSchema": JIRO_AI_SEARCH_SCHEMA},
        {"name": "search_hybrid",
         "description": "Hybrid search combining multiple search strategies (keyword + semantic + freshness). "
                        "Provides enhanced relevance scoring and multi-signal ranking.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query"},
             "type": {"type": "string", "enum": ["web", "images", "news", "videos", "shopping"], "default": "web"},
             "engine": {"type": "string", "description": "Primary engine to use"},
             "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
         }, "required": ["query"]}},
        {"name": "search_structured",
         "description": "Extract structured data from search results using a JSON schema. "
                        "Returns data conforming to the provided schema.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query"},
             "schema": {"type": "object", "description": "JSON Schema for desired output structure"},
         }, "required": ["query", "schema"]}},
        {"name": "social_scrape",
         "description": "Scrape a social media URL (Reddit, YouTube, Twitter/X, TikTok, Instagram, LinkedIn, "
                        "Bluesky, Threads, Facebook, Telegram, Pinterest). Returns normalized post/profile data.",
         "inputSchema": {"type": "object", "properties": {
             "url": {"type": "string", "format": "uri", "description": "Social media URL"},
         }, "required": ["url"]}},
        {"name": "social_search",
         "description": "Search across multiple social platforms simultaneously. Returns normalized results.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query"},
             "platforms": {"type": "array", "items": {"type": "string"}, "default": ["twitter", "reddit", "youtube"]},
         }, "required": ["query"]}},
        {"name": "social_batch",
         "description": "Batch scrape multiple social media URLs in parallel.",
         "inputSchema": {"type": "object", "properties": {
             "urls": {"type": "array", "items": {"type": "string"}, "description": "List of social URLs"},
         }, "required": ["urls"]}},
        {"name": "smart_search",
         "description": "Smart search with automatic intent detection and routing. "
                        "Classifies query intent and routes to appropriate handler.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query"},
             "type": {"type": "string", "enum": ["web", "images", "news", "videos", "shopping"], "default": "web"},
             "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
             "schema": {"type": "object", "description": "JSON Schema for structured extraction"},
         }, "required": ["query"]}},
        {"name": "smart_classify",
         "description": "Classify search intent for a query without executing it.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query to classify"},
         }, "required": ["query"]}},
        {"name": "compare_engines",
         "description": "Compare search results across multiple engines side by side.",
         "inputSchema": {"type": "object", "properties": {
             "query": {"type": "string", "description": "Search query"},
             "engines": {"type": "array", "items": {"type": "string"}, "default": ["google", "bing", "brave"]},
         }, "required": ["query"]}},
        {"name": "monitor_status",
         "description": "Get server status, engine health, and performance metrics.",
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
