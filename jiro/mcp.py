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
SERVER_VERSION = "0.1.0"

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
            log.exception("dispatch failed", extra={"method": method})
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": INTERNAL_ERROR, "message": str(exc)}}
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
                "Jiro Search: local-first web search & scraping. "
                "Tools: search (9 engines), scrape (URL → markdown/text/html/json), "
                "ai_search (research with citations). Long calls emit progress "
                "notifications when you pass _meta.progressToken."
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
        else:
            raise MCPError(INVALID_PARAMS, f"unknown tool: {name}",
                           data={"available_tools": ["search", "scrape", "ai_search"]})

    async def _tool_search(self, args: Dict[str, Any],
                           progress: ProgressReporter) -> Dict[str, Any]:
        assert self.orchestrator is not None
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
        url = args.get("url", "")
        if not url:
            raise MCPError(INVALID_PARAMS, "url is required")
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
        assert self.agent is not None
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

        return {
            "completion": {
                "values": values,
                "hasMore": False,
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
