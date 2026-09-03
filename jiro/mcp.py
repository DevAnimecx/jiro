"""MCP (Model Context Protocol) server — core protocol engine.

Implements the full MCP JSON-RPC protocol so MCP clients (Claude Desktop,
Cursor, Continue.dev, Zed, VS Code, ...) can call Jiro tools directly.

Transports:
* stdio   — ``jiro mcp`` (this module, ``run_stdio``)
* HTTP    — Streamable HTTP + legacy SSE via :mod:`jiro.mcp_http`

Protocol versions negotiated: 2024-11-05, 2025-03-26.
Implements: initialize, ping, tools/list, tools/call (with progress +
cancellation), resources/list, resources/read, prompts/list, prompts/get,
completion/complete.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Callable, Dict, List, Optional

from jiro.ai.agent import Agent
from jiro.ai.llm import LLM
from jiro.ai.tools import mcp_tools
from jiro.auth import AuthManager
from jiro.cache import CacheManager
from jiro.config import Settings
from jiro.db import Database
from jiro.log import get_logger
from jiro.models import SearchRequest
from jiro.scraping.client import ScrapingClient
from jiro.ai.tools import ENGINE_ENUM
from jiro.scraping.engines import SearchOrchestrator

log = get_logger("jiro.mcp")

PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2024-11-05"]
SERVER_VERSION = "0.2.1"

CAPABILITIES = {
    "tools": {"listChanged": True},
    "resources": {"subscribe": False, "listChanged": False},
    "prompts": {"listChanged": False},
    "logging": {},
    # Streamable-HTTP-only capability; harmless over stdio.
    "streaming": {"progressNotifications": True, "cancellation": True},
}

# JSON-RPC error codes used by MCP
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class MCPError(Exception):
    def __init__(self, code: int, message: str, data: Optional[Any] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class ProgressReporter:
    """Emit ``notifications/progress`` messages to a sink (stdio callback or SSE queue)."""

    def __init__(self, progress_token: Optional[Any] = None,
                 sink: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self.progress_token = progress_token
        self._sink = sink or (lambda _msg: None)

    def report(self, progress: float, total: Optional[float] = None,
               message: Optional[str] = None) -> None:
        if self.progress_token is None:
            return
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {
                "progressToken": self.progress_token,
                "progress": progress,
            },
        }
        if total is not None:
            payload["params"]["total"] = total
        if message:
            payload["params"]["message"] = message
        try:
            self._sink(payload)
        except Exception:  # never break the tool call over a notification failure
            pass

    @property
    def enabled(self) -> bool:
        return self.progress_token is not None


class JiroMCPServer:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or Settings.load()
        self.db: Optional[Database] = None
        self.cache: Optional[CacheManager] = None
        self.client: Optional[ScrapingClient] = None
        self.orchestrator: Optional[SearchOrchestrator] = None
        self.agent: Optional[Agent] = None
        self._negotiated_version: str = PROTOCOL_VERSION
        # request_id -> task handle for in-flight cancellation
        self._inflight: Dict[Any, asyncio.Task] = {}
        # subscribers for server-initiated notifications (HTTP/SSE transports)
        self._notify_sinks: List[Callable[[Dict[str, Any]], None]] = []
        self._client_initialized = False

    async def start(self) -> None:
        self.db = Database(self.settings.db_path)
        await self.db.connect()
        self.cache = CacheManager(
            self.db if self.settings.cache_type == "sqlite" else None,
            memory=self.settings.cache_type == "memory",
            ttl=self.settings.cache_ttl,
        )
        self.client = ScrapingClient(self.settings)
        AuthManager(self.settings, self.db)
        self.orchestrator = SearchOrchestrator(self.settings, self.client, self.cache)
        llm = LLM(self.settings)
        self.agent = Agent(self.settings, self.orchestrator,
                           self._scrape_for_agent(), llm)
        log.info("mcp server ready", extra={"protocol": PROTOCOL_VERSION})

    async def stop(self) -> None:
        # cancel any in-flight tasks
        for task in list(self._inflight.values()):
            if not task.done():
                task.cancel()
        self._inflight.clear()
        if self.client:
            await self.client.close()
        if self.db:
            await self.db.close()

    def _scrape_for_agent(self):
        from jiro.extract import scrape_url

        async def _scrape(url: str) -> dict:
            return await scrape_url(url, self.client, fmt="markdown",
                                    include_metadata=False)
        return _scrape

    def add_notify_sink(self, sink: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        """Register a sink for server-initiated notifications; returns unsubscribe."""
        self._notify_sinks.append(sink)

        def _remove() -> None:
            try:
                self._notify_sinks.remove(sink)
            except ValueError:
                pass
        return _remove

    def _broadcast(self, notification: Dict[str, Any]) -> None:
        for sink in list(self._notify_sinks):
            try:
                sink(notification)
            except Exception:
                pass

    # -------------------------------------------------------------- dispatch
    async def dispatch(self, msg: Dict[str, Any],
                       progress_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
                       ) -> Optional[Dict[str, Any]]:
        """Handle one JSON-RPC message.

        ``progress_sink`` (optional) receives every server-initiated
        notification dict as it is generated — used by the HTTP transport to
        stream progress to SSE clients.
        """
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if msg_id is None:  # notification — no response expected
            if method == "notifications/initialized":
                self._client_initialized = True
                log.info("client initialized")
            elif method == "notifications/cancelled":
                request_id = params.get("requestId")
                task = self._inflight.pop(request_id, None)
                if task and not task.done():
                    task.cancel()
                    log.info("request cancelled", extra={"request_id": request_id})
            elif method == "logging/setLevel":
                log.info("client set log level", extra={"level": params.get("level")})
            elif method == "notifications/roots/list_changed":
                pass  # no roots support yet
            return None

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "ping":
                result = {}
            elif method in ("tools/list", "tools/listChanged"):
                result = {"tools": mcp_tools()}
            elif method == "tools/call":
                # Run the tool call as a cancellable task so
                # notifications/cancelled can abort it mid-flight.
                progress_token = params.get("_meta", {}).get("progressToken")
                reporter = ProgressReporter(progress_token, progress_sink or self._broadcast)
                task = asyncio.current_task()
                if task is not None:
                    self._inflight[msg_id] = task
                try:
                    result = await self._call_tool(params, progress=reporter)
                finally:
                    self._inflight.pop(msg_id, None)
            elif method == "resources/list":
                result = self._handle_resources_list()
            elif method == "resources/read":
                result = await self._handle_resources_read(params)
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "prompts/list":
                result = self._handle_prompts_list()
            elif method == "prompts/get":
                result = self._handle_prompts_get(params)
            elif method == "completion/complete":
                result = self._handle_completion(params)
            elif method == "logging/setLevel":
                result = {}
            else:
                raise MCPError(METHOD_NOT_FOUND, f"method not found: {method}")
        except MCPError as exc:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": exc.code, "message": exc.message,
                              "data": exc.data}}
        except asyncio.CancelledError:
            raise  # propagate so cancellation works
        except Exception as exc:
            # SECURITY: Log full exception server-side only, return generic message to client
            log.exception("dispatch failed", extra={"method": method})
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": INTERNAL_ERROR, "message": "internal server error"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    # --------------------------------------------------------- initialize
    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        client_info = params.get("clientInfo", {})
        requested = params.get("protocolVersion", "")
        if requested in SUPPORTED_PROTOCOL_VERSIONS:
            self._negotiated_version = requested
        else:
            # fall back to our newest supported version
            self._negotiated_version = PROTOCOL_VERSION
        log.info("client connected",
                 extra={"name": client_info.get("name", "unknown"),
                        "version": client_info.get("version", "unknown"),
                        "protocol": self._negotiated_version})
        return {
            "protocolVersion": self._negotiated_version,
            "capabilities": CAPABILITIES,
            "serverInfo": {"name": "jiro", "version": SERVER_VERSION},
            "instructions": (
                "Jiro Search: local-first AI-native web search & scraping platform.\n\n"
                "CAPABILITIES:\n"
                "- Search 9 engines: Google, Bing, Brave, DuckDuckGo, YouTube, Amazon, eBay, Yandex, Baidu\n"
                "- Scrape any URL to markdown/text/html/json with metadata\n"
                "- AI-powered research with citations (ai_search)\n"
                "- Hybrid search combining keyword + semantic + freshness signals\n"
                "- Structured data extraction with JSON Schema\n"
                "- Social media scraping: Reddit, HN, YouTube, Bluesky, Twitter, Threads, Instagram, TikTok, LinkedIn, Facebook, Telegram, Pinterest\n"
                "- Smart search with intent detection and auto-routing\n"
                "- Compare results across multiple engines\n"
                "- Server health monitoring\n"
                "- Cache management and statistics\n\n"
                "16 TOOLS: search, scrape, ai_search, search_hybrid, search_structured, "
                "social_scrape, social_search, social_batch, smart_search, smart_classify, "
                "compare_engines, monitor_status, health_check, cache_stats, list_engines, "
                "list_social_platforms\n\n"
                "Long calls emit progress notifications when you pass _meta.progressToken.\n"
                "Resources: jiro://engines, jiro://compliance, jiro://social_platforms, jiro://plans, jiro://plugins"
            ),
        }

    # --------------------------------------------------------- tools/call
    async def _call_tool(self, params: Dict[str, Any],
                         progress: Optional[ProgressReporter] = None) -> Dict[str, Any]:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        progress = progress or ProgressReporter()

        if name == "search":
            return await self._tool_search(args, progress)
        elif name == "scrape":
            return await self._tool_scrape(args, progress)
        elif name == "ai_search":
            return await self._tool_ai_search(args, progress)
        elif name == "search_hybrid":
            return await self._tool_search_hybrid(args, progress)
        elif name == "search_structured":
            return await self._tool_search_structured(args, progress)
        elif name == "social_scrape":
            return await self._tool_social_scrape(args, progress)
        elif name == "social_search":
            return await self._tool_social_search(args, progress)
        elif name == "social_batch":
            return await self._tool_social_batch(args, progress)
        elif name == "smart_search":
            return await self._tool_smart_search(args, progress)
        elif name == "smart_classify":
            return await self._tool_smart_classify(args, progress)
        elif name == "compare_engines":
            return await self._tool_compare_engines(args, progress)
        elif name == "monitor_status":
            return await self._tool_monitor_status(args, progress)
        elif name == "health_check":
            return await self._tool_health_check(args, progress)
        elif name == "cache_stats":
            return await self._tool_cache_stats(args, progress)
        elif name == "list_engines":
            return await self._tool_list_engines(args, progress)
        elif name == "list_social_platforms":
            return await self._tool_list_social_platforms(args, progress)
        else:
            raise MCPError(INVALID_PARAMS, f"unknown tool: {name}",
                           data={
                               "available_tools": [
                                   {"name": "search", "description": "Search 9 engines (Google, Bing, Brave, etc.)"},
                                   {"name": "scrape", "description": "Scrape URL to markdown/text/html/json"},
                                   {"name": "ai_search", "description": "AI research with citations"},
                                   {"name": "search_hybrid", "description": "Hybrid multi-signal search"},
                                   {"name": "search_structured", "description": "Structured data extraction"},
                                   {"name": "social_scrape", "description": "Scrape social media URLs"},
                                   {"name": "social_search", "description": "Search social platforms"},
                                   {"name": "social_batch", "description": "Batch scrape multiple URLs"},
                                   {"name": "smart_search", "description": "Intent-aware smart routing"},
                                   {"name": "smart_classify", "description": "Classify search intent"},
                                   {"name": "compare_engines", "description": "Compare engine results"},
                                   {"name": "monitor_status", "description": "Server health metrics"},
                                   {"name": "health_check", "description": "Quick health check"},
                                   {"name": "cache_stats", "description": "Cache statistics"},
                                   {"name": "list_engines", "description": "List all search engines"},
                                   {"name": "list_social_platforms", "description": "List social platforms"},
                               ]
                           })

    async def _tool_search(self, args: Dict[str, Any],
                           progress: ProgressReporter) -> Dict[str, Any]:
        if self.orchestrator is None:
            raise MCPError(INTERNAL_ERROR, "server not fully started")
        req = SearchRequest(**{k: v for k, v in args.items()
                                if k in SearchRequest.model_fields})
        if progress.enabled:
            progress.report(0.1, total=1.0, message="searching")
        result = await self.orchestrator.search(req, fresh=bool(args.get("fresh", False)))
        payload = result.model_dump()
        if progress.enabled:
            progress.report(1.0, total=1.0, message="done")
        # Add a human-readable summary for MCP clients
        summary_lines = [
            f"Query: {payload.get('search_metadata', {}).get('query', '')}",
            f"Engine: {payload.get('search_metadata', {}).get('engine', '')}",
            f"Results: {len(payload.get('organic_results', []))}",
        ]
        summary = "\n".join(summary_lines)
        return self._text_result(payload, summary=summary)

    async def _tool_scrape(self, args: Dict[str, Any],
                           progress: ProgressReporter) -> Dict[str, Any]:
        from jiro.extract import scrape_url
        from jiro.security import async_validate_target_url
        url = args.get("url", "")
        if not url:
            raise MCPError(INVALID_PARAMS, "url is required")
        # SECURITY: Validate URL against SSRF before scraping
        try:
            await async_validate_target_url(url)
        except Exception as e:
            raise MCPError(INVALID_PARAMS, f"URL validation failed: {e}")
        fmt = args.get("format", "markdown")
        if progress.enabled:
            progress.report(0.2, total=1.0, message=f"fetching {url}")
        payload = await scrape_url(url, self.client, fmt=fmt,
                                    include_metadata=args.get("include_metadata", True))
        if progress.enabled:
            progress.report(1.0, total=1.0, message="done")
        # For markdown/text, return content directly for better LLM consumption
        if isinstance(payload, dict) and "content" in payload:
            content = payload["content"]
            if fmt in ("markdown", "text") and len(content) < 100_000:
                summary = f"Title: {payload.get('title', 'N/A')}\nURL: {url}"
                return self._text_result(payload, summary=summary)
        return self._text_result(payload)

    async def _tool_ai_search(self, args: Dict[str, Any],
                               progress: ProgressReporter) -> Dict[str, Any]:
        if self.agent is None:
            raise MCPError(INTERNAL_ERROR, "AI agent not initialized")
        query = args.get("query", "")
        if not query:
            raise MCPError(INVALID_PARAMS, "query is required")
        max_sources = int(args.get("max_sources", 5))

        # Wrap agent research with progress events by driving the streaming
        # variant and collecting the final result.
        if progress.enabled:
            answer_payload: Dict[str, Any] = {}
            step_total = float(max_sources + 3)
            step = 0.0
            async for event in self.agent.research_stream(query, max_sources=max_sources):
                step += 1.0
                etype = event.get("type", "")
                if etype == "answer":
                    answer_payload = event
                elif etype == "plan":
                    progress.report(step / step_total, total=step_total,
                                    message=f"plan: {', '.join(event.get('queries', []))}")
                elif etype == "source":
                    title = event.get("title") or event.get("url", "")
                    ok = "" if event.get("status") != "failed" else " (failed)"
                    progress.report(step / step_total, total=step_total,
                                    message=f"read source{ok}: {title}")
                elif etype == "synthesize":
                    progress.report(step / step_total, total=step_total,
                                    message="synthesizing answer")
            return self._text_result(answer_payload)
        result = await self.agent.research(query, max_sources=max_sources)
        return self._text_result(result)

    async def _tool_search_hybrid(self, args: Dict[str, Any],
                                   progress: ProgressReporter) -> Dict[str, Any]:
        """Hybrid search combining multiple signals."""
        query = args.get("query", "")
        if not query:
            raise MCPError(INVALID_PARAMS, "query is required")
        if self.orchestrator is None:
            raise MCPError(INTERNAL_ERROR, "server not fully started")

        if progress.enabled:
            progress.report(0.1, message="initializing hybrid search")

        req = SearchRequest(
            q=query,
            type=args.get("type", "web"),
            engine=args.get("engine"),
            max_results=args.get("max_results", 20),
        )
        result = await self.orchestrator.search(req)
        payload = result.model_dump()
        if progress.enabled:
            progress.report(1.0, message="done")
        return self._text_result(payload, summary=f"Hybrid search for: {query}")

    async def _tool_search_structured(self, args: Dict[str, Any],
                                      progress: ProgressReporter) -> Dict[str, Any]:
        """Search with structured JSON schema output."""
        query = args.get("query", "")
        schema = args.get("schema", {})
        if not query:
            raise MCPError(INVALID_PARAMS, "query is required")
        if not schema:
            raise MCPError(INVALID_PARAMS, "schema is required")

        from jiro.search.structured import StructuredExtractor
        from jiro.config import Settings as ConfigSettings

        if progress.enabled:
            progress.report(0.1, message="extracting structured data")

        extractor = StructuredExtractor(self.settings)
        result = await extractor.extract(query, schema)
        if progress.enabled:
            progress.report(1.0, message="done")
        return self._text_result(result)

    async def _tool_social_scrape(self, args: Dict[str, Any],
                                   progress: ProgressReporter) -> Dict[str, Any]:
        """Scrape a social media URL."""
        from jiro.scraping.social import SocialRouter
        from jiro.security import async_validate_target_url

        url = args.get("url", "")
        if not url:
            raise MCPError(INVALID_PARAMS, "url is required")

        # SECURITY: Validate URL against SSRF before scraping
        try:
            await async_validate_target_url(url)
        except Exception as e:
            raise MCPError(INVALID_PARAMS, f"URL validation failed: {e}")

        if progress.enabled:
            progress.report(0.1, message=f"detecting platform: {url}")

        router = SocialRouter()
        platform = router.detect_platform(url)
        if not platform:
            raise MCPError(INVALID_PARAMS, f"unsupported platform for URL: {url}")

        scraper = router.get_scraper(platform, self.client)
        if not scraper:
            raise MCPError(INTERNAL_ERROR, f"failed to create scraper for: {platform}")

        action = router.detect_action(url)
        if progress.enabled:
            progress.report(0.3, message=f"scraping {platform}")

        if action == "profile":
            result = await scraper.get_profile(url)
        else:
            result = await scraper.scrape(url)
        if progress.enabled:
            progress.report(1.0, message="done")
        return self._text_result(result)

    async def _tool_social_search(self, args: Dict[str, Any],
                                   progress: ProgressReporter) -> Dict[str, Any]:
        """Search across social platforms."""
        from jiro.scraping.social import SocialRouter

        query = args.get("query", "")
        platforms = args.get("platforms", ["twitter", "reddit", "youtube"])
        if not query:
            raise MCPError(INVALID_PARAMS, "query is required")

        router = SocialRouter()
        all_results = []

        if progress.enabled:
            progress.report(0.0, message=f"searching {len(platforms)} platforms")

        for i, platform in enumerate(platforms):
            scraper = router.get_scraper(platform, self.client)
            if scraper:
                try:
                    results = await scraper.search(query)
                    all_results.extend(results)
                except Exception:
                    pass  # continue on failure
            if progress.enabled:
                progress.report((i + 1) / len(platforms), message=f"searched {platform}")

        return self._text_result({"results": all_results, "total": len(all_results)})

    async def _tool_social_batch(self, args: Dict[str, Any],
                                  progress: ProgressReporter) -> Dict[str, Any]:
        """Batch scrape multiple social URLs."""
        from jiro.scraping.social import SocialRouter

        urls = args.get("urls", [])
        if not urls:
            raise MCPError(INVALID_PARAMS, "urls is required")

        router = SocialRouter()
        results = []

        if progress.enabled:
            progress.report(0.0, message=f"batch scraping {len(urls)} URLs")

        for i, url in enumerate(urls):
            platform = router.detect_platform(url)
            if platform:
                scraper = router.get_scraper(platform, self.client)
                if scraper:
                    try:
                        result = await scraper.scrape(url)
                        results.append({"url": url, "result": result, "status": "success"})
                    except Exception as e:
                        results.append({"url": url, "error": str(e), "status": "failed"})
            else:
                results.append({"url": url, "error": "unsupported platform", "status": "failed"})
            if progress.enabled:
                progress.report((i + 1) / len(urls), message=f"scraped {i+1}/{len(urls)}")

        return self._text_result({"results": results, "total": len(results)})

    async def _tool_smart_search(self, args: Dict[str, Any],
                                  progress: ProgressReporter) -> Dict[str, Any]:
        """Smart search with intent detection and auto-routing."""
        from jiro.search.intent import IntentClassifier
        from jiro.search.structured import StructuredExtractor

        query = args.get("query", "")
        if not query:
            raise MCPError(INVALID_PARAMS, "query is required")

        classifier = IntentClassifier(self.settings)

        if progress.enabled:
            progress.report(0.1, message="classifying intent")

        intent = classifier.classify(query)
        target = intent.target
        intent_name = intent.intent

        if progress.enabled:
            progress.report(0.3, message=f"intent: {intent_name} -> {target}")

        # Route based on intent
        if target == "social":
            from jiro.scraping.social import SocialRouter
            router = SocialRouter()
            platform = classifier.social_platform
            if platform:
                scraper = router.get_scraper(platform, self.client)
                if scraper:
                    result = await scraper.scrape(query)
                    return self._text_result(result)
        elif target == "structured":
            extractor = StructuredExtractor(self.settings)
            schema = args.get("schema", classifier.suggested_schema)
            if schema:
                result = await extractor.extract(query, schema)
                return self._text_result(result)

        # Default: regular search
        if self.orchestrator is None:
            raise MCPError(INTERNAL_ERROR, "server not fully started")
        req = SearchRequest(
            q=query,
            type=args.get("type", "web"),
            max_results=args.get("max_results", 10),
        )
        result = await self.orchestrator.search(req)
        payload = result.model_dump()
        if progress.enabled:
            progress.report(1.0, message="done")
        return self._text_result(payload, summary=f"Intent: {intent_name}")

    async def _tool_smart_classify(self, args: Dict[str, Any],
                                    progress: ProgressReporter) -> Dict[str, Any]:
        """Classify search intent for a query."""
        from jiro.search.intent import IntentClassifier

        query = args.get("query", "")
        if not query:
            raise MCPError(INVALID_PARAMS, "query is required")

        classifier = IntentClassifier(self.settings)
        intent = classifier.classify(query)

        result = {
            "query": query,
            "intent": intent.intent,
            "intent_label": intent.label,
            "confidence": intent.confidence,
            "target": intent.target,
            "social_platform": intent.social_platform,
            "category": intent.category,
            "reasoning": intent.reasoning,
        }
        return self._text_result(result)

    async def _tool_compare_engines(self, args: Dict[str, Any],
                                     progress: ProgressReporter) -> Dict[str, Any]:
        """Compare search results across multiple engines."""
        query = args.get("query", "")
        engines = args.get("engines", ["google", "bing", "brave"])
        if not query:
            raise MCPError(INVALID_PARAMS, "query is required")

        if self.orchestrator is None:
            raise MCPError(INTERNAL_ERROR, "server not fully started")
        comparison = {"query": query, "results": {}}

        if progress.enabled:
            progress.report(0.0, message=f"comparing {len(engines)} engines")

        for i, engine in enumerate(engines):
            req = SearchRequest(q=query, engine=engine, max_results=5)
            try:
                result = await self.orchestrator.search(req)
                comparison["results"][engine] = {
                    "total": len(result.organic_results),
                    "first_result": result.organic_results[0].title if result.organic_results else None,
                    "source": engine,
                }
            except Exception as e:
                comparison["results"][engine] = {"error": str(e)}
            if progress.enabled:
                progress.report((i + 1) / len(engines), message=f"compared {engine}")

        return self._text_result(comparison, summary=f"Compared {len(engines)} engines for: {query}")

    async def _tool_monitor_status(self, args: Dict[str, Any],
                                    progress: ProgressReporter) -> Dict[str, Any]:
        """Get server/scraper status and metrics."""
        if self.orchestrator is None:
            raise MCPError(INTERNAL_ERROR, "server not fully started")

        engines_info = await self.orchestrator.engines_info()
        health = await self.orchestrator.health_check()

        status = {
            "version": SERVER_VERSION,
            "engines": engines_info,
            "health": health,
            "cache_enabled": self.settings.cache_type != "memory",
            "plugins_enabled": True,
            "social_platforms": 12,
            "search_engines": 9,
            "mcp_tools": len(mcp_tools()),
        }
        return self._text_result(status)

    async def _tool_health_check(self, args: Dict[str, Any],
                                  progress: ProgressReporter) -> Dict[str, Any]:
        """Quick health check for all services."""
        if self.orchestrator is None:
            raise MCPError(INTERNAL_ERROR, "server not fully started")

        health = await self.orchestrator.health_check()
        return self._text_result({
            "status": "healthy" if all(h.get("ok") for h in health.values()) else "degraded",
            "services": health,
        })

    async def _tool_cache_stats(self, args: Dict[str, Any],
                                 progress: ProgressReporter) -> Dict[str, Any]:
        """Get cache statistics."""
        if self.cache is None:
            raise MCPError(INTERNAL_ERROR, "cache not initialized")

        stats = {
            "enabled": self.settings.cache_type != "memory",
            "type": self.settings.cache_type,
            "ttl": self.settings.cache_ttl,
            "hit_count": getattr(self.cache, '_hits', 0),
            "miss_count": getattr(self.cache, '_misses', 0),
        }
        return self._text_result(stats)

    async def _tool_list_engines(self, args: Dict[str, Any],
                                 progress: ProgressReporter) -> Dict[str, Any]:
        """List all available search engines with their capabilities."""
        engines = [
            {"name": "google", "types": ["web", "images", "news", "videos", "shopping", "places"], "supports_freshness": True},
            {"name": "bing", "types": ["web", "images", "news", "videos"], "supports_freshness": True},
            {"name": "brave", "types": ["web", "images", "news", "videos"], "supports_freshness": True},
            {"name": "duckduckgo", "types": ["web", "images", "news", "videos"], "supports_freshness": False},
            {"name": "youtube", "types": ["web", "videos"], "supports_freshness": True},
            {"name": "amazon", "types": ["web", "shopping"], "supports_freshness": False},
            {"name": "ebay", "types": ["web", "shopping"], "supports_freshness": False},
            {"name": "yandex", "types": ["web", "images", "news"], "supports_freshness": True},
            {"name": "baidu", "types": ["web", "images", "news"], "supports_freshness": True},
        ]
        return self._text_result({"engines": engines, "total": len(engines)})

    async def _tool_list_social_platforms(self, args: Dict[str, Any],
                                          progress: ProgressReporter) -> Dict[str, Any]:
        """List all supported social platforms with capabilities."""
        platforms = [
            {"name": "reddit", "capabilities": ["scrape", "search", "profile", "comments", "subreddit"], "base_url": "https://reddit.com"},
            {"name": "hackernews", "capabilities": ["scrape", "search", "profile", "comments", "top"], "base_url": "https://news.ycombinator.com"},
            {"name": "youtube", "capabilities": ["scrape", "search", "channel", "video", "comments"], "base_url": "https://youtube.com"},
            {"name": "bluesky", "capabilities": ["scrape", "search", "profile", "post", "feed"], "base_url": "https://bsky.app"},
            {"name": "twitter", "capabilities": ["scrape", "search", "profile", "post", "thread"], "base_url": "https://twitter.com"},
            {"name": "threads", "capabilities": ["scrape", "search", "profile", "post"], "base_url": "https://threads.net"},
            {"name": "instagram", "capabilities": ["scrape", "profile", "post", "stories"], "base_url": "https://instagram.com"},
            {"name": "tiktok", "capabilities": ["scrape", "profile", "video", "trending"], "base_url": "https://tiktok.com"},
            {"name": "linkedin", "capabilities": ["scrape", "profile", "company", "post"], "base_url": "https://linkedin.com"},
            {"name": "facebook", "capabilities": ["scrape", "profile", "group", "page", "post"], "base_url": "https://facebook.com"},
            {"name": "telegram", "capabilities": ["scrape", "channel", "message", "group"], "base_url": "https://t.me"},
            {"name": "pinterest", "capabilities": ["scrape", "search", "pin", "board"], "base_url": "https://pinterest.com"},
        ]
        return self._text_result({"platforms": platforms, "total": len(platforms)})

    # --------------------------------------------------------- resources
    def _handle_resources_list(self) -> Dict[str, Any]:
        return {"resources": [
            {
                "uri": "jiro://engines",
                "name": "Supported engines",
                "description": "List of configured search engines and supported types",
                "mimeType": "application/json",
            },
            {
                "uri": "jiro://compliance",
                "name": "Engine ToS compliance",
                "description": "Terms-of-service notes per engine",
                "mimeType": "application/json",
            },
            {
                "uri": "jiro://social_platforms",
                "name": "Social platforms",
                "description": "List of supported social media platforms and their capabilities",
                "mimeType": "application/json",
            },
            {
                "uri": "jiro://plans",
                "name": "Pricing plans",
                "description": "Available pricing tiers and feature comparison",
                "mimeType": "application/json",
            },
            {
                "uri": "jiro://plugins",
                "name": "Plugins",
                "description": "Installed search, engine, and datasource plugins",
                "mimeType": "application/json",
            },
        ]}

    async def _handle_resources_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        uri = params.get("uri", "")
        if uri == "jiro://engines":
            data = (await self.orchestrator.engines_info()) if self.orchestrator else []
            return {"contents": [{"uri": uri, "mimeType": "application/json",
                                  "text": json.dumps(data, indent=2)}]}
        if uri == "jiro://compliance":
            from jiro.compliance import ComplianceManager
            cm = ComplianceManager(self.settings, self.db)
            return {"contents": [{"uri": uri, "mimeType": "application/json",
                                  "text": json.dumps(cm.generate_compliance_report(),
                                                     indent=2)}]}
        if uri == "jiro://social_platforms":
            platforms = [
                {"name": "Reddit", "capabilities": ["scrape", "search", "profile"]},
                {"name": "Hacker News", "capabilities": ["scrape", "search", "profile"]},
                {"name": "YouTube", "capabilities": ["scrape", "search", "channel", "video"]},
                {"name": "Bluesky", "capabilities": ["scrape", "search", "profile", "post"]},
                {"name": "Twitter/X", "capabilities": ["scrape", "search", "profile", "post"]},
                {"name": "Threads", "capabilities": ["scrape", "search", "profile"]},
                {"name": "Instagram", "capabilities": ["scrape", "profile", "post"]},
                {"name": "TikTok", "capabilities": ["scrape", "profile", "video"]},
                {"name": "LinkedIn", "capabilities": ["scrape", "profile", "company"]},
                {"name": "Facebook", "capabilities": ["scrape", "profile", "group", "page"]},
                {"name": "Telegram", "capabilities": ["scrape", "channel", "message"]},
                {"name": "Pinterest", "capabilities": ["scrape", "search", "pin"]},
            ]
            return {"contents": [{"uri": uri, "mimeType": "application/json",
                                  "text": json.dumps({"platforms": platforms}, indent=2)}]}
        if uri == "jiro://plans":
            from jiro.pro import PlanTier, PLAN_LIMITS
            plans = []
            for tier, limits in PLAN_LIMITS.items():
                plans.append({
                    "name": tier.value,
                    "requests_per_minute": limits.rpm,
                    "requests_per_day": limits.rpd,
                    "max_results": limits.max_results,
                })
            return {"contents": [{"uri": uri, "mimeType": "application/json",
                                  "text": json.dumps({"plans": plans}, indent=2)}]}
        if uri == "jiro://plugins":
            from jiro.plugins import engine_registry, search_plugin_registry, datasource_registry
            plugins = {
                "engines": engine_registry.names(),
                "search": search_plugin_registry.names(),
                "datasources": datasource_registry.names(),
            }
            return {"contents": [{"uri": uri, "mimeType": "application/json",
                                  "text": json.dumps({"plugins": plugins}, indent=2)}]}
        raise MCPError(INVALID_PARAMS, f"resource not found: {uri}")

    # --------------------------------------------------------- prompts
    def _handle_prompts_list(self) -> Dict[str, Any]:
        return {
            "prompts": [
                {
                    "name": "search_and_summarize",
                    "description": "Search the web and provide a summary with citations",
                    "arguments": [
                        {
                            "name": "topic",
                            "description": "The topic to search and summarize",
                            "required": True,
                        },
                    ],
                },
                {
                    "name": "compare_engines",
                    "description": "Compare search results across multiple engines",
                    "arguments": [
                        {
                            "name": "query",
                            "description": "The search query to compare",
                            "required": True,
                        },
                    ],
                },
                {
                    "name": "social_research",
                    "description": "Research a topic across social media platforms",
                    "arguments": [
                        {
                            "name": "topic",
                            "description": "The topic to research on social media",
                            "required": True,
                        },
                        {
                            "name": "platforms",
                            "description": "Comma-separated list of platforms (e.g., reddit,twitter,youtube)",
                            "required": False,
                        },
                    ],
                },
                {
                    "name": "deep_research",
                    "description": "Deep AI research with multiple sources and citations",
                    "arguments": [
                        {
                            "name": "question",
                            "description": "The research question to answer",
                            "required": True,
                        },
                        {
                            "name": "max_sources",
                            "description": "Maximum number of sources to consult",
                            "required": False,
                        },
                    ],
                },
                {
                    "name": "extract_structured",
                    "description": "Extract structured data from search results",
                    "arguments": [
                        {
                            "name": "query",
                            "description": "What to search for",
                            "required": True,
                        },
                        {
                            "name": "fields",
                            "description": "JSON fields to extract (e.g., {\"name\": {}, \"price\": {}, \"rating\": {}})",
                            "required": True,
                        },
                    ],
                },
                {
                    "name": "competitor_analysis",
                    "description": "Analyze competitors across search engines and social media",
                    "arguments": [
                        {
                            "name": "competitor",
                            "description": "The competitor company or product to analyze",
                            "required": True,
                        },
                    ],
                },
            ]
        }

    def _handle_prompts_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name", "")
        args = params.get("arguments") or {}

        if name == "search_and_summarize":
            topic = args.get("topic", "latest technology trends")
            return {
                "description": f"Search and summarize: {topic}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Search the web for '{topic}' and provide a comprehensive "
                                f"summary with citations. Use the search tool to find relevant "
                                f"results, then scrape the top 3 pages for detailed information, "
                                f"and synthesize a well-sourced answer."
                            ),
                        },
                    }
                ],
            }
        elif name == "compare_engines":
            query = args.get("query", "python web scraping")
            return {
                "description": f"Compare results for: {query}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Search for '{query}' on google, bing, and brave engines "
                                f"using the search tool. Compare the top 3 results from each "
                                f"engine and identify which engine provides the most relevant "
                                f"results for this query."
                            ),
                        },
                    }
                ],
            }
        elif name == "social_research":
            topic = args.get("topic", "artificial intelligence")
            platforms = args.get("platforms", "reddit,twitter,youtube")
            return {
                "description": f"Research '{topic}' on social media",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Research '{topic}' across social media platforms: {platforms}. "
                                f"Use the social_search tool to find discussions, then use "
                                f"social_scrape to get detailed content from top posts. "
                                f"Summarize the key insights, opinions, and trends you find."
                            ),
                        },
                    }
                ],
            }
        elif name == "deep_research":
            question = args.get("question", "latest advances in AI")
            max_sources = args.get("max_sources", "8")
            return {
                "description": f"Deep research: {question}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Perform deep research on: '{question}'. "
                                f"Use the ai_search tool with max_sources={max_sources} to "
                                f"find and analyze multiple sources. Provide a comprehensive "
                                f"answer with proper citations."
                            ),
                        },
                    }
                ],
            }
        elif name == "extract_structured":
            query = args.get("query", "python libraries")
            fields = args.get("fields", '{"name": {}, "description": {}, "stars": {}}')
            return {
                "description": f"Extract structured data for: {query}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Search for '{query}' and extract structured data with "
                                f"the following schema: {fields}. "
                                f"Use the search_structured tool to extract and organize "
                                f"the information in a consistent format."
                            ),
                        },
                    }
                ],
            }
        elif name == "competitor_analysis":
            competitor = args.get("competitor", "fastapi")
            return {
                "description": f"Analyze competitor: {competitor}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Perform a comprehensive competitor analysis of '{competitor}'. "
                                f"1. Search for '{competitor}' on Google and Bing using search tool.\n"
                                f"2. Scrape the top 3 competitor pages for detailed features.\n"
                                f"3. Search social media (reddit, twitter, youtube) for "
                                f"discussions about '{competitor}'.\n"
                                f"4. Compare results across engines using compare_engines.\n"
                                f"5. Provide a summary of their positioning, strengths, "
                                f"weaknesses, and community sentiment."
                            ),
                        },
                    }
                ],
            }
        else:
            raise MCPError(-32602, f"unknown prompt: {name}")

    # --------------------------------------------------------- completion
    def _handle_completion(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ref = params.get("ref", {})
        ref_type = ref.get("type", "")
        argument = params.get("argument", {})
        arg_name = argument.get("name", "")
        arg_value = argument.get("value", "")

        values: List[str] = []
        if ref_type == "ref/tool":
            tool_name = ref.get("name", "")
            if tool_name == "search" and arg_name == "engine":
                values = [e for e in ENGINE_ENUM if e.startswith(arg_value)]
            elif tool_name == "search" and arg_name == "type":
                all_types = ["web", "images", "news", "videos", "shopping", "places"]
                values = [t for t in all_types if t.startswith(arg_value)]
            elif tool_name == "search" and arg_name == "time_range":
                all_ranges = ["any", "day", "week", "month", "year"]
                values = [r for r in all_ranges if r.startswith(arg_value)]
            elif tool_name == "scrape" and arg_name == "format":
                all_fmts = ["markdown", "text", "html", "json"]
                values = [f for f in all_fmts if f.startswith(arg_value)]
            elif tool_name == "social_search" and arg_name == "platforms":
                all_platforms = ["reddit", "twitter", "youtube", "bluesky", "hackernews",
                                 "threads", "instagram", "tiktok", "linkedin", "facebook",
                                 "telegram", "pinterest"]
                values = [p for p in all_platforms if p.startswith(arg_value)]
            elif tool_name == "social_scrape" and arg_name == "format":
                all_formats = ["markdown", "text", "json"]
                values = [f for f in all_formats if f.startswith(arg_value)]
            elif tool_name == "smart_search" and arg_name == "type":
                all_types = ["web", "social", "structured"]
                values = [t for t in all_types if t.startswith(arg_value)]
            elif tool_name == "compare_engines" and arg_name == "engines":
                values = [e for e in ENGINE_ENUM if e.startswith(arg_value)]
            elif tool_name == "health_check" and arg_name == "timeout":
                values = ["1", "5", "10", "30", "60"]
            elif tool_name == "search_structured" and arg_name == "schema":
                schemas = [
                    '{"name": {}, "description": {}, "url": {}}',
                    '{"title": {}, "author": {}, "date": {}, "content": {}}',
                    '{"product": {}, "price": {}, "rating": {}, "reviews": {}}',
                    '{"name": {}, "email": {}, "phone": {}, "address": {}}',
                ]
                values = [s for s in schemas if s.startswith(arg_value)]

        return {
            "completion": {
                "values": values[:25],
                "hasMore": len(values) > 25,
            }
        }

    # --------------------------------------------------------- helpers
    @staticmethod
    def _text_result(payload: Any, summary: Optional[str] = None) -> Dict[str, Any]:
        text = json.dumps(payload, default=str, indent=2)
        # MCP has a 1MB limit on tool results; truncate if needed
        if len(text) > 900_000:
            text = text[:900_000] + "\n... (truncated)"
        if summary:
            text = f"{summary}\n\n---\n\n{text}"
        return {"content": [{"type": "text", "text": text}]}

    # ------------------------------------------------------------------ run
    async def run_stdio(self) -> None:
        await self.start()
        loop = asyncio.get_running_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    log.warning("invalid JSON on stdin", extra={"error": str(exc)})
                    continue
                response = await self.dispatch(msg)
                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()
        finally:
            await self.stop()


def run_mcp_stdio(settings: Optional[Settings] = None) -> None:
    server = JiroMCPServer(settings)
    asyncio.run(server.run_stdio())


if __name__ == "__main__":  # allows `python -m jiro.mcp`
    run_mcp_stdio()
