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

from pydantic import ValidationError as PydanticValidationError

from jiro import __version__
from jiro.ai.agent import Agent
from jiro.ai.llm import LLM
from jiro.ai.tools import mcp_tools
from jiro.auth import AuthManager
from jiro.cache import CacheManager
from jiro.config import Settings
from jiro.db import Database
from jiro.errors import JiroError, LicenseError, ValidationError as JiroValidationError
from jiro.log import get_logger
from jiro.models import SearchRequest
from jiro.scraping.client import ScrapingClient
from jiro.ai.tools import ENGINE_ENUM
from jiro.scraping.engines import (
    ENGINE_DESCRIPTIONS,
    ENGINE_TYPES,
    SearchOrchestrator,
)
from jiro.search.intent import INTENT_TO_PLATFORM, IntentResult, IntentType

log = get_logger("jiro.mcp")

#: Roles a handoff brief can target. Mirrors the fabric session roles in
#: docs/v0.4.0/MASTER_PLAN.md §5.2, restricted to what a graph run can describe.
HANDOFF_ROLES = frozenset({"planner", "retriever", "verifier", "writer", "observer"})

#: Op a receiving agent should consider next, by role. Chosen from the node ops
#: the planner can emit; the brief reports it as a suggestion, never as truth.
_ROLE_NEXT_OP = {
    "planner": "VERIFY",
    "retriever": "FETCH",
    "verifier": "VERIFY",
    "writer": "SYNTHESIZE",
    "observer": "COMPRESS",
}


def build_handoff_brief(run: Dict[str, Any], *, query: str, role: str) -> Dict[str, Any]:
    """Turn a finished graph run into a structured brief for another agent.

    The point is that the receiver gets resolvable ids — evidence and source
    ids that address real entries in the run — rather than prose it would have
    to re-derive by searching again. Fields follow the handoff payload defined
    in docs/v0.4.0/MASTER_PLAN.md §5.2: goal, evidence ids, open questions,
    contradictions, budget left, next suggested node.
    """
    packet = run.get("packet") or {}
    claims = packet.get("claims") or []
    sources = packet.get("sources") or []
    gaps = list(run.get("gaps") or []) + list(packet.get("gaps") or [])

    evidence_ids: List[str] = []
    for claim in claims:
        cid = claim.get("id")
        if cid:
            evidence_ids.append(str(cid))
    source_ids = [str(s["id"]) for s in sources if s.get("id")]
    for sid in source_ids:
        if sid not in evidence_ids:
            evidence_ids.append(sid)

    # Only claims that actually resolved a source can be verified downstream.
    unresolvable = [
        str(c.get("id"))
        for c in claims
        if c.get("id") and not (c.get("source_ids") or [])
    ]

    open_questions = []
    for gap in gaps:
        if isinstance(gap, dict):
            open_questions.append(
                {
                    "node_id": gap.get("node_id"),
                    "query": gap.get("query"),
                    "reason": gap.get("reason") or gap.get("error") or "unresolved",
                }
            )
        else:
            open_questions.append({"reason": str(gap)})

    budget = run.get("budget") or {}
    spec = run.get("budget_spec") or {}
    requests_used = int(budget.get("requests_used") or 0)
    declared = spec.get("max_requests")
    budget_left = {
        "requests": (
            max(0, int(declared) - requests_used) if isinstance(declared, int) else None
        ),
        "requests_used": requests_used,
        "tokens_used": int(budget.get("tokens_used") or 0),
        "tokens_budget": packet.get("budget_tokens"),
        "bytes_used": int(budget.get("bytes_used") or 0),
    }

    return {
        "schema": "jiro.handoff/1",
        "role": role,
        "goal": query,
        "graph_id": run.get("graph_id"),
        "graph_status": run.get("status"),
        "intent": run.get("intent"),
        "packet_id": packet.get("packet_id"),
        "provenance_digest": packet.get("provenance_digest"),
        "evidence_ids": evidence_ids,
        "source_ids": source_ids,
        "claim_ids": [str(c["id"]) for c in claims if c.get("id")],
        "unresolvable_claim_ids": unresolvable,
        "open_questions": open_questions,
        "contradictions": packet.get("contradictions") or [],
        "budget_left": budget_left,
        "next_suggested_node": _ROLE_NEXT_OP.get(role, "SYNTHESIZE"),
        "duration_ms": run.get("duration_ms"),
        "engine_attribution": run.get("engine_attribution") or {},
    }


PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2024-11-05"]
SERVER_VERSION = __version__

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

MAX_QUERY_LENGTH = 500
MIN_QUERY_LENGTH = 1

# Search types any registered engine actually implements. Kept in sync with
# ``jiro.scraping.engines.ENGINE_TYPES`` at import time by ``_all_types()``.
SEARCH_TYPES = ["web", "images", "news", "videos", "shopping", "places"]

_REGISTRY: Any = None


def _all_types() -> List[str]:
    """Union of every search type supported by at least one engine."""
    return _registry_types() or list(SEARCH_TYPES)


def _registry_types() -> List[str]:
    """All search types advertised by the live registry."""
    types: List[str] = []
    try:
        registry = _engine_registry_cached()
        for name in registry.names():
            try:
                for t in registry.get(name).types:
                    if t not in types:
                        types.append(t)
            except Exception:
                continue
    except Exception:
        return []
    return types


def _engine_registry_cached():
    """Engine registry with parsers imported (cached)."""
    global _REGISTRY
    if _REGISTRY is None:
        from jiro.scraping.engines import _build_registry

        _REGISTRY = _build_registry()
    return _REGISTRY


def _engines_supporting(type_: str) -> List[str]:
    """Engines that actually implement ``type_``.

    Read from the engine classes themselves rather than the ENGINE_TYPES
    table, which drifted out of sync with the implementations.
    """
    capable: List[str] = []
    try:
        registry = _engine_registry_cached()
        for name in registry.names():
            try:
                if type_ in registry.get(name).types:
                    capable.append(name)
            except Exception:
                continue
        return sorted(capable)
    except Exception:
        pass
    return sorted(n for n, types in ENGINE_TYPES.items() if type_ in types)


def _error_response(msg_id: Any, code: int, message: str,
                    data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _validation_message(exc: Exception) -> str:
    """Render a pydantic/Jiro validation error as an actionable message."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)
    try:
        problems = []
        for err in errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "input"
            problems.append(f"{loc}: {err.get('msg', 'invalid')}")
    except Exception:
        return str(exc)
    if not problems:
        return str(exc)
    return "Invalid parameters — " + "; ".join(problems[:5])


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
        self.graph: Optional[Any] = None
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
        from jiro.graph.service import GraphService

        self.graph = GraphService(self.settings, self.orchestrator, self.client, self.cache)
        llm = LLM(self.settings)
        self.agent = Agent(self.settings, self.orchestrator,
                           self._scrape_for_agent(), llm)

        # Durable task runtime, scoped to a single owner bucket.
        #
        # MCP has no per-client identity of its own — stdio has none at all, and
        # the HTTP transport authenticates the session but does not plumb a key
        # id through to the tool layer. Rather than expose an unscoped view of
        # every task in the database, MCP operates inside the ``mcp`` owner
        # namespace and can only ever see tasks it submitted itself. See
        # docs/adr/0004 for why this is a deliberate Phase 1 boundary.
        from jiro.runtime import HandlerRegistry, TaskRuntime
        from jiro.runtime.builtin import register_builtin_handlers
        from jiro.runtime.metrics import make_metrics_sink

        task_registry = HandlerRegistry()
        register_builtin_handlers(task_registry, services=self)
        self.runtime = await TaskRuntime.create(
            self.settings, self.db, task_registry, metrics=make_metrics_sink())
        await self.runtime.start()
        self.task_owner = "mcp"

        log.info("mcp server ready", extra={"protocol": PROTOCOL_VERSION})

    async def stop(self) -> None:
        # cancel any in-flight tasks
        for task in list(self._inflight.values()):
            if not task.done():
                task.cancel()
        self._inflight.clear()
        if getattr(self, "runtime", None) is not None:
            await self.runtime.stop()
            self.runtime = None
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
        except (JiroValidationError, PydanticValidationError) as exc:
            return _error_response(msg_id, INVALID_PARAMS, _validation_message(exc))
        except LicenseError as exc:
            return _error_response(
                msg_id, INVALID_PARAMS,
                f"{exc.code}: {exc.message}",
                data={"error_code": exc.code, **({"details": exc.details} if exc.details else {})},
            )
        except JiroError as exc:
            # Engine/network/limit failures are client-visible problems, not
            # server bugs — surface the real reason instead of -32603.
            code = INVALID_PARAMS if exc.status_code < 500 else INTERNAL_ERROR
            log.warning("tool error", extra={"method": method, "code": exc.code})
            return _error_response(
                msg_id, code, f"{exc.code}: {exc.message}",
                data={"error_code": exc.code,
                      "status_code": exc.status_code,
                      **({"details": exc.details} if exc.details else {})},
            )
        except Exception:
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
                "- Budgeted query graphs with cited evidence packets\n"
                "- Cache management and statistics\n\n"
                "22 TOOLS: search, scrape, ai_search, search_hybrid, search_structured, "
                "social_scrape, social_search, social_batch, smart_search, smart_classify, "
                "compare_engines, monitor_status, health_check, cache_stats, task_submit, "
                "task_status, task_cancel, list_engines, list_social_platforms, "
                "search_graph, plan_search, session_handoff\n\n"
                "Long calls emit progress notifications when you pass _meta.progressToken.\n"
                "Resources: jiro://engines, jiro://compliance, jiro://social_platforms, jiro://plans, jiro://plugins"
            ),
        }

    # ------------------------------------------------------- input validation
    @staticmethod
    def _require_query(args: Dict[str, Any], *names: str) -> str:
        """Validate a required, non-empty, length-capped query string.

        Accepts the first present alias so tools can take ``q`` or ``query``.
        Raises :class:`MCPError` with ``-32602`` so callers get a real
        diagnostic instead of an opaque internal error.
        """
        label = names[0] if names else "q"
        for name in names:
            if name in args and args[name] is not None:
                label = name
                break
        else:
            raise MCPError(INVALID_PARAMS,
                           f"'{names[0]}' is required",
                           data={"required": list(names)})

        value = args[label]
        if not isinstance(value, str):
            raise MCPError(
                INVALID_PARAMS,
                f"'{label}' must be a string, got {type(value).__name__}",
                data={"field": label},
            )
        stripped = value.strip()
        if len(stripped) < MIN_QUERY_LENGTH:
            raise MCPError(INVALID_PARAMS,
                           f"'{label}' must not be empty",
                           data={"field": label})
        if len(stripped) > MAX_QUERY_LENGTH:
            raise MCPError(
                INVALID_PARAMS,
                f"'{label}' is too long: {len(stripped)} characters "
                f"(maximum {MAX_QUERY_LENGTH})",
                data={"field": label, "length": len(stripped),
                      "max_length": MAX_QUERY_LENGTH},
            )
        return stripped

    @staticmethod
    def _validate_engine(value: Any, field: str = "engine") -> str:
        """Validate an engine name against the registered engines."""
        if value is None:
            return "auto"
        if not isinstance(value, str):
            raise MCPError(INVALID_PARAMS,
                           f"'{field}' must be a string, got {type(value).__name__}",
                           data={"field": field})
        engine = value.strip().lower()
        # "mock" used to be allowed here, but no mock parser exists, so accepting
        # it only deferred the failure to request time with a less clear error.
        valid = sorted(set(ENGINE_ENUM) | {"auto"})
        if engine not in valid:
            raise MCPError(
                INVALID_PARAMS,
                f"unknown engine '{value}'. Available: {', '.join(valid)}",
                data={"field": field, "available": valid},
            )
        return engine

    @staticmethod
    def _validate_type(value: Any, engine: str = "auto") -> str:
        """Validate search type and check it is reachable on some engine."""
        if value is None:
            return "web"
        if not isinstance(value, str):
            raise MCPError(INVALID_PARAMS,
                           f"'type' must be a string, got {type(value).__name__}",
                           data={"field": "type"})
        search_type = value.strip().lower()
        valid = _all_types()
        if search_type not in valid:
            raise MCPError(
                INVALID_PARAMS,
                f"unknown search type '{value}'. Available: {', '.join(valid)}",
                data={"field": "type", "available": valid},
            )
        # Fail fast with a useful message instead of a 502 from the
        # fallback chain when the type is unreachable everywhere.
        capable = _engines_supporting(search_type)
        if not capable:
            raise MCPError(
                INVALID_PARAMS,
                f"no engine supports type '{search_type}'",
                data={"field": "type", "supported_by": capable},
            )
        if engine != "auto" and engine not in capable:
            raise MCPError(
                INVALID_PARAMS,
                f"engine '{engine}' does not support type '{search_type}'. "
                f"Engines that do: {', '.join(capable)}",
                data={"field": "type", "engine": engine, "supported_by": capable},
            )
        return search_type

    @staticmethod
    def _validate_int(args: Dict[str, Any], name: str, default: int,
                      minimum: int, maximum: int) -> int:
        """Validate an integer argument with explicit bounds."""
        value = args.get(name, default)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                value = int(value.strip())
            else:
                raise MCPError(
                    INVALID_PARAMS,
                    f"'{name}' must be an integer, got "
                    f"{type(args.get(name)).__name__}",
                    data={"field": name, "minimum": minimum, "maximum": maximum},
                )
        if value < minimum or value > maximum:
            raise MCPError(
                INVALID_PARAMS,
                f"'{name}' must be between {minimum} and {maximum}, got {value}",
                data={"field": name, "value": value,
                      "minimum": minimum, "maximum": maximum},
            )
        return value

    @staticmethod
    def _validate_str_list(args: Dict[str, Any], name: str, *, required: bool,
                           max_items: int) -> List[str]:
        """Validate a list-of-strings argument."""
        value = args.get(name)
        if value is None:
            if required:
                raise MCPError(INVALID_PARAMS, f"'{name}' is required",
                               data={"field": name})
            return []
        if isinstance(value, str):
            value = [p.strip() for p in value.split(",") if p.strip()]
        if not isinstance(value, list):
            raise MCPError(
                INVALID_PARAMS,
                f"'{name}' must be an array of strings, got {type(value).__name__}",
                data={"field": name},
            )
        if not value:
            if required:
                raise MCPError(INVALID_PARAMS, f"'{name}' must not be empty",
                               data={"field": name})
            return []
        cleaned: List[str] = []
        for item in value:
            if not isinstance(item, str):
                raise MCPError(
                    INVALID_PARAMS,
                    f"'{name}' must contain only strings, got "
                    f"{type(item).__name__}",
                    data={"field": name},
                )
            item = item.strip()
            if item:
                cleaned.append(item)
        if not cleaned:
            raise MCPError(INVALID_PARAMS, f"'{name}' must not be empty",
                           data={"field": name})
        if len(cleaned) > max_items:
            raise MCPError(
                INVALID_PARAMS,
                f"'{name}' accepts at most {max_items} items, got {len(cleaned)}",
                data={"field": name, "max_items": max_items},
            )
        return cleaned

    async def _require_orchestrator(self) -> SearchOrchestrator:
        if self.orchestrator is None:
            raise MCPError(INTERNAL_ERROR, "server not fully started")
        return self.orchestrator

    async def _require_graph(self):
        if self.graph is None:
            raise MCPError(INTERNAL_ERROR, "server not fully started")
        return self.graph

    @staticmethod
    def _search_summary(payload: Dict[str, Any], requested_engine: str) -> str:
        """Build a human-readable summary that never lies about the engine.

        Jiro falls back to other engines when the requested one is blocked or
        fails, so the summary states the engine that *actually* served the
        results alongside the one that was requested. Aggregation modes
        (``auto`` routing, hybrid fusion) are not substitutions and are
        reported as such.
        """
        meta = payload.get("search_metadata") or {}
        used = meta.get("engine") or "unknown"
        query = meta.get("query", "")
        count = len(payload.get("organic_results", []))
        lines = [f"Query: {query}", f"Results: {count}"]

        if used == requested_engine:
            lines.insert(1, f"Engine: {used}")
        elif used == "hybrid":
            # A fusion of several engines is an aggregation, never a
            # substitution — check this before the "auto" case below.
            fused = meta.get("engines_used") or meta.get("engines_queried") or []
            detail = f" fused from {', '.join(fused)}" if fused else ""
            lines.insert(1, f"Engine: hybrid{detail}")
        elif requested_engine == "auto":
            # "auto" delegates routing by design — not a fallback.
            lines.insert(1, f"Engine: {used} (auto-selected)")
        else:
            lines.insert(1,
                         f"Engine: {used} (requested {requested_engine} — "
                         f"fallback used; see skipped_engines)")

        skipped = meta.get("skipped_engines") or []
        if skipped and used != requested_engine and requested_engine != "auto":
            lines.append(
                "Skipped: "
                + "; ".join(
                    f"{e.get('engine')} ({e.get('error_code', 'error')})"
                    for e in skipped
                )
            )
        if meta.get("cached"):
            lines.append("Cached: yes")
        if meta.get("truncated"):
            note = (f"Note: only {count} results available for this query "
                    f"(requested {meta.get('requested_num', '?')})")
            if meta.get("pagination_supported") is False:
                note += " — this engine returns a single fixed result page"
            lines.append(note)
        return "\n".join(lines)

    # --------------------------------------------------------- tools/call
    async def _call_tool(self, params: Dict[str, Any],
                         progress: Optional[ProgressReporter] = None) -> Dict[str, Any]:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise MCPError(INVALID_PARAMS,
                           "'arguments' must be an object",
                           data={"received": type(args).__name__})
        progress = progress or ProgressReporter()

        if name == "search":
            return await self._tool_search(args, progress)
        elif name == "search_graph":
            return await self._tool_search_graph(args, progress)
        elif name == "plan_search":
            return await self._tool_plan_search(args, progress)
        elif name == "session_handoff":
            return await self._tool_session_handoff(args, progress)
        elif name == "scrape":
            return await self._tool_scrape(args, progress)
        elif name == "ai_search":
            # Feature gate (see jiro.feature_flags / jiro.pro). Parity with
            # the REST layer, which gates /ai/search on the same flag.
            from jiro.feature_flags import is_enabled as _feature_enabled
            if not _feature_enabled("ai_search"):
                raise MCPError(
                    INVALID_PARAMS,
                    "ai_search requires a Pro/Enterprise license. "
                    "Upgrade at https://jiro.ai/pricing",
                    data={"feature": "ai_search",
                          "required_tiers": ["pro", "enterprise"]},
                )
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
        elif name == "task_submit":
            return await self._tool_task_submit(args, progress)
        elif name == "task_status":
            return await self._tool_task_status(args, progress)
        elif name == "task_cancel":
            return await self._tool_task_cancel(args, progress)
        elif name == "list_engines":
            return await self._tool_list_engines(args, progress)
        elif name == "list_social_platforms":
            return await self._tool_list_social_platforms(args, progress)
        else:
            raise MCPError(INVALID_PARAMS, f"unknown tool: {name}",
                           data={
                               "available_tools": [
                                   {"name": "search", "description": "Search 9 engines (Google, Bing, Brave, etc.)"},
                                   {"name": "search_graph", "description": "Budgeted query graph with cited packet"},
                                   {"name": "plan_search", "description": "Dry-run graph plan and cost estimate"},
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
                                   {"name": "task_submit", "description": "Submit a durable background task"},
                                   {"name": "task_status", "description": "Check a durable task's state and result"},
                                   {"name": "task_cancel", "description": "Cancel a durable task"},
                                   {"name": "list_engines", "description": "List all search engines"},
                                   {"name": "list_social_platforms", "description": "List social platforms"},
                               ]
                           })

    async def _tool_search(self, args: Dict[str, Any],
                           progress: ProgressReporter) -> Dict[str, Any]:
        # Validate arguments before requiring infrastructure so a bad
        # engine/type/num is reported as -32602 regardless of server state.
        query = self._require_query(args, "q", "query")
        engine = self._validate_engine(args.get("engine"))
        search_type = self._validate_type(args.get("type"), engine)
        num = self._validate_int(args, "num", 10, 1, 100)
        start = self._validate_int(args, "start", 0, 0, 900)
        orchestrator = await self._require_orchestrator()

        request_fields: Dict[str, Any] = {
            "q": query,
            "engine": engine,
            "type": search_type,
            "num": num,
            "start": start,
        }
        # Forward only fields the model actually declares.
        allowed = set(SearchRequest.model_fields)
        for key in ("location", "language", "safe", "time_range", "device",
                    "gl", "hl", "mode", "depth", "include_domains",
                    "exclude_domains", "start_date", "end_date", "highlights",
                    "include_answer", "parallel", "num_engines"):
            if key in args and args[key] is not None:
                request_fields[key] = args[key]
        req = SearchRequest(**{k: v for k, v in request_fields.items()
                               if k in allowed})

        if progress.enabled:
            progress.report(0.1, total=1.0, message=f"searching via {engine}")
        fresh = args.get("fresh", False)
        result = await orchestrator.search(req, fresh=bool(fresh))
        payload = result.model_dump()
        if progress.enabled:
            progress.report(1.0, total=1.0, message="done")
        return self._text_result(
            payload,
            summary=self._search_summary(payload, engine),
        )

    async def _tool_search_graph(self, args: Dict[str, Any],
                                 progress: ProgressReporter) -> Dict[str, Any]:
        service = await self._require_graph()
        query = self._require_query(args, "query", "q")
        engine = self._validate_engine(args.get("engine"))
        num = self._validate_int(args, "num", 5, 1, 10)
        max_tokens = self._validate_int(args, "max_tokens", 1200, 64, 32000)
        deadline_ms = self._validate_int(args, "deadline_ms", 2500, 100, 60000)
        fetch_top = self._validate_int(args, "fetch_top", 0, 0, 10)
        intent = args.get("intent")
        if intent is not None and not isinstance(intent, str):
            raise MCPError(INVALID_PARAMS, "'intent' must be a string")
        if progress.enabled:
            progress.report(0.1, total=1.0, message="planning graph")
        try:
            run = await service.run(
                query,
                engine=engine,
                num=num,
                max_tokens=max_tokens,
                deadline_ms=deadline_ms,
                fetch_top=fetch_top,
                intent=intent,
            )
        except ValueError as exc:
            raise MCPError(INVALID_PARAMS, str(exc)) from exc
        if progress.enabled:
            progress.report(1.0, total=1.0, message="graph complete")
        payload = run.to_dict()
        packet = payload.get("packet") or {}
        summary = (
            f"Graph: {payload['graph_id']} ({payload['intent']})\n"
            f"Status: {payload['status']} in {payload['duration_ms']} ms\n"
            f"Results: {len(payload.get('results') or [])}\n"
            f"Claims: {len(packet.get('claims') or [])} | "
            f"Tokens: {packet.get('used_tokens', 0)}/{packet.get('budget_tokens', 0)}\n"
            f"Gaps: {len(payload.get('gaps') or [])}"
        )
        return self._text_result(payload, summary=summary)

    async def _tool_session_handoff(self, args: Dict[str, Any],
                                    progress: ProgressReporter) -> Dict[str, Any]:
        # Validate arguments before requiring infrastructure, so a bad role or
        # query is reported as -32602 regardless of server state.
        query = self._require_query(args, "query", "q")
        role = args.get("role") or "writer"
        if role not in HANDOFF_ROLES:
            raise MCPError(
                INVALID_PARAMS,
                f"'role' must be one of {', '.join(sorted(HANDOFF_ROLES))}",
                data={"received": role},
            )
        engine = self._validate_engine(args.get("engine"))
        num = self._validate_int(args, "num", 5, 1, 10)
        max_tokens = self._validate_int(args, "max_tokens", 1200, 64, 32000)
        # 0 is meaningful here: it asks the planner to derive the deadline from
        # measured latency rather than use a constant.
        deadline_ms = self._validate_int(args, "deadline_ms", 0, 0, 60000)
        fetch_top = self._validate_int(args, "fetch_top", 0, 0, 10)
        intent = args.get("intent")
        service = await self._require_graph()
        if progress.enabled:
            progress.report(0.1, total=1.0, message="building handoff brief")
        try:
            run = await service.run(
                query,
                engine=engine,
                num=num,
                max_tokens=max_tokens,
                deadline_ms=deadline_ms or None,
                fetch_top=fetch_top,
                intent=intent,
            )
        except ValueError as exc:
            raise MCPError(INVALID_PARAMS, str(exc)) from exc
        if progress.enabled:
            progress.report(1.0, total=1.0, message="handoff ready")
        brief = build_handoff_brief(run.to_dict(), query=query, role=role)
        summary = (
            f"Handoff to {role}: {brief['goal']}\n"
            f"Evidence: {len(brief['evidence_ids'])} ids | "
            f"Open: {len(brief['open_questions'])} | "
            f"Contradictions: {len(brief['contradictions'])}\n"
            f"Budget left: {brief['budget_left']['requests']} requests\n"
            f"Next: {brief['next_suggested_node'] or 'none'}"
        )
        return self._text_result(brief, summary=summary)

    async def _tool_plan_search(self, args: Dict[str, Any],
                                progress: ProgressReporter) -> Dict[str, Any]:
        service = await self._require_graph()
        query = self._require_query(args, "query", "q")
        num = self._validate_int(args, "num", 5, 1, 10)
        max_tokens = self._validate_int(args, "max_tokens", 1200, 64, 32000)
        deadline_ms = self._validate_int(args, "deadline_ms", 2500, 100, 60000)
        fetch_top = self._validate_int(args, "fetch_top", 0, 0, 10)
        intent = args.get("intent")
        try:
            plan = service.plan(
                query,
                num=num,
                max_tokens=max_tokens,
                deadline_ms=deadline_ms,
                fetch_top=fetch_top,
                intent=intent if isinstance(intent, str) else None,
            )
        except ValueError as exc:
            raise MCPError(INVALID_PARAMS, str(exc)) from exc
        payload = {
            "graph": plan.graph.model_dump(mode="json", by_alias=True),
            "estimate": plan.estimate.model_dump(),
            "rationale": plan.rationale,
            "alternatives": [a.model_dump() for a in plan.alternatives],
        }
        summary = (
            f"Intent: {plan.graph.intent}\n"
            f"Nodes: {plan.estimate.nodes} in {plan.estimate.waves} waves\n"
            f"Estimated latency: {plan.estimate.estimated_latency_ms} ms\n"
            f"Estimated requests: {plan.estimate.estimated_requests}\n"
            f"Rationale: {plan.rationale}"
        )
        return self._text_result(payload, summary=summary)

    async def _tool_scrape(self, args: Dict[str, Any],
                           progress: ProgressReporter) -> Dict[str, Any]:
        from jiro.extract import scrape_url
        from jiro.security import async_validate_target_url

        url = args.get("url", "")
        if not isinstance(url, str) or not url.strip():
            raise MCPError(INVALID_PARAMS, "'url' is required",
                           data={"field": "url"})
        url = url.strip()
        if len(url) > 2048:
            raise MCPError(INVALID_PARAMS,
                           f"'url' is too long: {len(url)} characters "
                           f"(maximum 2048)",
                           data={"field": "url", "max_length": 2048})
        fmt = args.get("format", "markdown")
        if fmt not in ("markdown", "text", "html", "json"):
            raise MCPError(
                INVALID_PARAMS,
                f"unknown format '{fmt}'. Available: markdown, text, html, json",
                data={"field": "format",
                      "available": ["markdown", "text", "html", "json"]},
            )
        # SECURITY: Validate URL against SSRF before scraping
        try:
            await async_validate_target_url(url)
        except Exception as e:
            raise MCPError(INVALID_PARAMS, f"URL validation failed: {e}")
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
        query = self._require_query(args, "query", "q")
        max_sources = self._validate_int(args, "max_sources", 5, 1, 20)

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
            if not answer_payload:
                raise MCPError(
                    INTERNAL_ERROR,
                    "ai_search produced no answer; the agent stream ended "
                    "without a result",
                    data={"reason": "empty_agent_stream"},
                )
            return self._text_result(answer_payload)
        result = await self.agent.research(query, max_sources=max_sources)
        return self._text_result(result)

    async def _tool_search_hybrid(self, args: Dict[str, Any],
                                  progress: ProgressReporter) -> Dict[str, Any]:
        """Hybrid search combining multiple signals."""
        # Validate arguments before requiring infrastructure so callers get a
        # useful -32602 for bad input regardless of server state.
        query = self._require_query(args, "query", "q")
        # An omitted engine must become a real value — passing None straight
        # into SearchRequest raised a pydantic ValidationError, which the MCP
        # layer then reported as an opaque internal error.
        engine = self._validate_engine(args.get("engine"))
        search_type = self._validate_type(args.get("type"), engine)
        # ``max_results`` is the tool's parameter name; SearchRequest calls it
        # ``num``. It used to be dropped on the floor.
        num = self._validate_int(args, "max_results", 20, 1, 100)
        if "num" in args and args["num"] is not None:
            num = self._validate_int(args, "num", num, 1, 100)
        depth = args.get("depth") or "advanced"
        if depth not in ("instant", "fast", "basic", "advanced", "deep"):
            raise MCPError(
                INVALID_PARAMS,
                f"unknown depth '{depth}'. Available: instant, fast, basic, "
                "advanced, deep",
                data={"field": "depth",
                      "available": ["instant", "fast", "basic", "advanced",
                                    "deep"]},
            )
        orchestrator = await self._require_orchestrator()

        if progress.enabled:
            progress.report(0.1, message="initializing hybrid search")

        req = SearchRequest(
            q=query,
            type=search_type,
            engine=engine,
            num=num,
            # This is what actually engages the hybrid searcher; without it
            # the tool silently performed a plain single-engine search.
            mode="hybrid",
            depth=depth,
        )
        result = await orchestrator.search(req)
        payload = result.model_dump()
        if progress.enabled:
            progress.report(1.0, message="done")
        # _search_summary already names the fusion and the engines involved.
        return self._text_result(
            payload,
            summary=self._search_summary(payload, engine),
        )

    async def _tool_search_structured(self, args: Dict[str, Any],
                                      progress: ProgressReporter) -> Dict[str, Any]:
        """Search with structured JSON schema output."""
        # Validate before touching the orchestrator.
        query = self._require_query(args, "query", "q")

        schema = args.get("schema")
        if schema is None:
            raise MCPError(INVALID_PARAMS, "'schema' is required",
                           data={"field": "schema"})
        if isinstance(schema, str):
            import json as _json
            try:
                schema = _json.loads(schema)
            except ValueError as exc:
                raise MCPError(
                    INVALID_PARAMS,
                    f"'schema' must be a JSON object: {exc}",
                    data={"field": "schema"},
                ) from None
        if not isinstance(schema, dict) or not schema:
            raise MCPError(
                INVALID_PARAMS,
                "'schema' must be a non-empty JSON Schema object",
                data={"field": "schema",
                      "example": {"type": "object",
                                  "properties": {"name": {"type": "string"}}}},
            )

        from jiro.search.structured import StructuredExtractor

        engine = self._validate_engine(args.get("engine"))
        num = self._validate_int(args, "max_results", 5, 1, 20)
        orchestrator = await self._require_orchestrator()

        if progress.enabled:
            progress.report(0.1, message="collecting search sources")

        # StructuredExtractor.extract(query, sources, schema) needs actual
        # sources to extract from — searching first is what makes the tool work.
        search_req = SearchRequest(q=query, engine=engine, num=num, type="web")
        search_result = await orchestrator.search(search_req)
        sources = [
            {
                "url": r.link,
                "title": r.title,
                "snippet": r.snippet,
                "content": " ".join(r.highlights or []) if r.highlights else "",
            }
            for r in search_result.organic_results if r.link
        ]
        if not sources:
            raise MCPError(
                INTERNAL_ERROR,
                f"no search results available to extract from for '{query}'",
                data={"query": query,
                      "engine": search_result.search_metadata.get("engine")},
            )

        if progress.enabled:
            progress.report(0.5, message=f"extracting from {len(sources)} sources")

        extractor = StructuredExtractor(self.settings)
        try:
            result = await extractor.extract(query, sources, schema)
        except ValueError as exc:
            # Invalid JSON Schema — that is a client input problem.
            raise MCPError(INVALID_PARAMS, str(exc),
                           data={"field": "schema"}) from None

        payload = {
            "query": query,
            "data": result.data,
            "provider": result.provider,
            "model": result.model,
            "confidence": result.confidence,
            "mode": result.mode,
            "sources_used": len(sources),
            "engine": search_result.search_metadata.get("engine"),
        }
        if progress.enabled:
            progress.report(1.0, message="done")
        return self._text_result(
            payload,
            summary=f"Structured extraction for: {query} "
                    f"({len(sources)} sources, {result.mode} mode)",
        )

    # ------------------------------------------------------------- social
    def _social_scraper(self, platform: str):
        """Resolve an instantiated social scraper for ``platform``.

        ``SocialRouter.get_scraper(url)`` only returns a *class* and takes a
        URL — calling it as ``get_scraper(platform, client)`` was the bug that
        made every social tool fail. Instantiation needs client + settings, so
        it goes through the social registry.
        """
        from jiro.scraping.social import registry as social_registry

        try:
            return social_registry.get_instance(platform, self.client, self.settings)
        except ValueError:
            available = sorted(
                p["platform"] for p in social_registry.list_platforms()
            )
            raise MCPError(
                INVALID_PARAMS,
                f"unsupported social platform: {platform}. "
                f"Available: {', '.join(available)}",
                data={"field": "platform", "available": available},
            ) from None

    @staticmethod
    def _social_normalize(result: Any) -> Any:
        """Normalize SocialPost/SocialProfile dataclasses to plain dicts."""
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return [JiroMCPServer._social_normalize(item) for item in result]
        to_dict = getattr(result, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        if hasattr(result, "platform") and hasattr(result, "data"):
            return {
                "platform": getattr(result, "platform", ""),
                "type": getattr(result, "type", "post"),
                "url": getattr(result, "url", ""),
                "data": getattr(result, "data", {}),
                "scraped_at": getattr(result, "scraped_at", None),
                "credits_charged": getattr(result, "credits_charged", None),
            }
        return result

    async def _social_scrape_action(self, scraper, platform: str, action: Optional[str],
                                    url: str, username: Optional[str] = None):
        """Run the correct scraper method for a detected ``action``."""
        if action in ("post", "video", "pin", "message", "reel", "story",
                      "channel", "subreddit", "shorts", "playlist"):
            return await scraper.scrape_post(url)
        if action in ("profile", "user", "company", "page", "board"):
            identifier = username or url.rstrip("/").rsplit("/", 1)[-1]
            return await scraper.scrape_profile(identifier)
        if action == "timeline":
            identifier = username or url.rstrip("/").rsplit("/", 1)[-1]
            return {"platform": platform, "type": "timeline", "url": url,
                    "data": self._social_normalize(
                        await scraper.scrape_timeline(identifier))}
        # Unknown/undetected action: a bare post URL is the common case.
        return await scraper.scrape_post(url)

    async def _tool_social_scrape(self, args: Dict[str, Any],
                                   progress: ProgressReporter) -> Dict[str, Any]:
        """Scrape a social media URL."""
        from jiro.scraping.social import SocialRouter, registry as social_registry
        from jiro.security import async_validate_target_url

        url = args.get("url", "")
        if not isinstance(url, str) or not url.strip():
            raise MCPError(INVALID_PARAMS, "'url' is required",
                           data={"field": "url"})
        url = url.strip()

        # SECURITY: Validate URL against SSRF before scraping
        try:
            await async_validate_target_url(url)
        except Exception as e:
            raise MCPError(INVALID_PARAMS, f"URL validation failed: {e}")

        router = SocialRouter()
        if progress.enabled:
            progress.report(0.1, message=f"detecting platform: {url}")

        platform = args.get("platform") or router.detect_platform(url)
        if not platform:
            raise MCPError(
                INVALID_PARAMS,
                f"unsupported platform for URL: {url}",
                data={"field": "url", "url": url,
                      "supported": sorted(
                          p["platform"] for p in social_registry.list_platforms()
                      )},
            )
        platform = str(platform).strip().lower()

        action = args.get("action") or router.detect_action(platform, url)
        identifier = router.extract_identifier(platform, url)
        scraper = self._social_scraper(platform)

        if progress.enabled:
            progress.report(0.3, message=f"scraping {platform} ({action or 'post'})")

        try:
            result = await self._social_scrape_action(scraper, platform, action,
                                                      url, identifier)
        except NotImplementedError as exc:
            raise MCPError(INVALID_PARAMS, str(exc),
                           data={"platform": platform, "action": action}) from None
        except Exception as exc:
            code = getattr(exc, "code", "")
            status = getattr(exc, "platform", "")
            raise MCPError(
                INTERNAL_ERROR,
                f"{platform} scrape failed: {exc}",
                data={"platform": platform, "action": action,
                      "error_code": code, "failed_platform": status},
            ) from exc

        if progress.enabled:
            progress.report(1.0, message="done")
        payload = self._social_normalize(result)
        return self._text_result(
            payload,
            summary=f"Platform: {platform}\nAction: {action or 'post'}\nURL: {url}",
        )

    async def _tool_social_search(self, args: Dict[str, Any],
                                   progress: ProgressReporter) -> Dict[str, Any]:
        """Search across social platforms."""
        query = self._require_query(args, "query", "q")
        from jiro.scraping.social import registry as social_registry

        available = sorted(p["platform"] for p in social_registry.list_platforms())
        platforms = self._validate_str_list(args, "platforms", required=False,
                                            max_items=12) or ["twitter", "reddit",
                                                              "youtube"]
        limit = self._validate_int(args, "limit", 10, 1, 50)

        unknown = [p for p in platforms if p not in available]
        if unknown:
            raise MCPError(
                INVALID_PARAMS,
                f"unknown social platform(s): {', '.join(unknown)}. "
                f"Available: {', '.join(available)}",
                data={"field": "platforms", "available": available,
                      "unknown": unknown},
            )

        all_results: List[Any] = []
        errors: List[Dict[str, Any]] = []
        searched: List[str] = []

        if progress.enabled:
            progress.report(0.0, message=f"searching {len(platforms)} platforms")

        for i, platform in enumerate(platforms):
            try:
                scraper = self._social_scraper(platform)
                found = await scraper.search(query, limit=limit)
                all_results.extend(self._social_normalize(found))
                searched.append(platform)
            except NotImplementedError as exc:
                errors.append({"platform": platform, "error": str(exc),
                               "error_code": "not_supported"})
            except Exception as exc:
                log.warning("social_search failed for %s: %s", platform, exc)
                errors.append({"platform": platform, "error": str(exc),
                               "error_code": getattr(exc, "code", "search_failed")})
            if progress.enabled:
                progress.report((i + 1) / len(platforms), message=f"searched {platform}")

        if not all_results and errors and not searched:
            raise MCPError(
                INTERNAL_ERROR,
                f"social search returned no results for '{query}' on any platform",
                data={"query": query, "errors": errors},
            )

        payload = {
            "query": query,
            "platforms_searched": searched,
            "platforms_failed": [e["platform"] for e in errors],
            "errors": errors,
            "results": all_results,
            "total": len(all_results),
        }
        summary = f"Social search: {query} — {len(all_results)} results"
        if errors:
            summary += f" ({len(errors)} platform(s) failed)"
        return self._text_result(payload, summary=summary)

    async def _tool_social_batch(self, args: Dict[str, Any],
                                  progress: ProgressReporter) -> Dict[str, Any]:
        """Batch scrape multiple social URLs."""
        import asyncio as _asyncio

        from jiro.scraping.social import SocialRouter
        from jiro.security import async_validate_target_url

        urls = self._validate_str_list(args, "urls", required=True, max_items=10)
        router = SocialRouter()

        results: List[Dict[str, Any]] = []
        if progress.enabled:
            progress.report(0.0, message=f"batch scraping {len(urls)} URLs")

        async def _one(i: int, url: str) -> Dict[str, Any]:
            try:
                await async_validate_target_url(url)
            except Exception:
                return {"url": url, "error": "URL validation failed",
                        "status": "failed"}
            platform = router.detect_platform(url)
            if not platform:
                return {"url": url, "error": "unsupported platform",
                        "status": "failed"}
            action = router.detect_action(platform, url)
            try:
                scraper = self._social_scraper(platform)
                result = await self._social_scrape_action(
                    scraper, platform, action, url,
                    router.extract_identifier(platform, url))
                return {"url": url, "platform": platform,
                        "action": action or "post",
                        "data": self._social_normalize(result),
                        "status": "success"}
            except NotImplementedError as exc:
                return {"url": url, "platform": platform, "error": str(exc),
                        "status": "failed"}
            except Exception as exc:
                log.warning("social_batch failed for %s: %s", url, exc)
                return {"url": url, "platform": platform,
                        "error": str(exc),
                        "error_code": getattr(exc, "code", "scrape_failed"),
                        "status": "failed"}
            finally:
                if progress.enabled:
                    progress.report((i + 1) / len(urls),
                                    message=f"scraped {i + 1}/{len(urls)}")

        if args.get("parallel", True):
            results = list(await _asyncio.gather(
                *[_one(i, u) for i, u in enumerate(urls)]))
        else:
            for i, url in enumerate(urls):
                results.append(await _one(i, url))

        succeeded = sum(1 for r in results if r.get("status") == "success")
        payload = {
            "results": results,
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
        }
        return self._text_result(
            payload,
            summary=f"Batch: {succeeded}/{len(results)} URLs scraped",
        )

    # --------------------------------------------------------- intent tools
    @staticmethod
    def _intent_target(intent: IntentResult) -> str:
        """Derive a routing target from a real :class:`IntentResult`.

        ``IntentResult`` exposes ``intent``/``confidence``/``platform``/
        ``action``/``extracted`` — it has no ``target``, ``label``,
        ``category``, ``reasoning`` or ``social_platform`` attributes, so the
        routing decision is computed here from the fields that do exist.
        """
        if intent.intent in INTENT_TO_PLATFORM or \
                intent.intent == IntentType.SOCIAL_PROFILE:
            return "social"
        if intent.intent == IntentType.SCRAPE:
            return "scrape"
        if intent.intent == IntentType.NEWS_SEARCH:
            return "news"
        if intent.intent == IntentType.SOCIAL_YOUTUBE:
            return "videos"
        return "web"

    @classmethod
    def _intent_payload(cls, query: str, intent: IntentResult) -> Dict[str, Any]:
        return {
            "query": query,
            "intent": intent.intent.value,
            "confidence": round(float(intent.confidence), 3),
            "target": cls._intent_target(intent),
            "platform": intent.platform,
            "action": intent.action,
            "extracted": intent.extracted or {},
        }

    async def _tool_smart_search(self, args: Dict[str, Any],
                                  progress: ProgressReporter) -> Dict[str, Any]:
        """Smart search with intent detection and auto-routing."""
        from jiro.search.intent import IntentClassifier

        # Validate before requiring infrastructure.
        query = self._require_query(args, "query", "q")
        if isinstance(args.get("schema"), str):
            # Let search_structured own the schema coercion/validation.
            return await self._tool_search_structured(
                {**args, "query": query}, progress)
        if isinstance(args.get("schema"), dict) and args["schema"]:
            return await self._tool_search_structured(
                {**args, "query": query}, progress)
        orchestrator = await self._require_orchestrator()

        classifier = IntentClassifier(self.settings)
        if progress.enabled:
            progress.report(0.1, message="classifying intent")

        intent = classifier.classify(query)
        target = self._intent_target(intent)
        intent_name = intent.intent.value

        if progress.enabled:
            progress.report(0.3, message=f"intent: {intent_name} -> {target}")

        # A schema was handled above, so route purely on detected intent.
        # URL in the query -> scrape it.
        if target == "scrape" and (intent.extracted or {}).get("url"):
            return await self._tool_scrape(
                {"url": intent.extracted["url"], "format": "markdown"}, progress)

        # Social intent without a URL -> platform search, not a bogus scrape.
        if target == "social" and intent.platform:
            return await self._tool_social_search(
                {"query": query, "platforms": [intent.platform],
                 "limit": self._validate_int(args, "limit", 10, 1, 50)},
                progress)

        search_type = args.get("type") or (
            "news" if target == "news" else
            "videos" if target == "videos" else "web"
        )
        engine = self._validate_engine(args.get("engine"))
        search_type = self._validate_type(search_type, engine)
        num = self._validate_int(args, "max_results", 10, 1, 100)

        req = SearchRequest(q=query, type=search_type, engine=engine, num=num)
        result = await orchestrator.search(req)
        payload = result.model_dump()
        payload["intent"] = self._intent_payload(query, intent)
        if progress.enabled:
            progress.report(1.0, message="done")
        return self._text_result(
            payload,
            summary=f"Intent: {intent_name} (target: {target})\n"
                    + self._search_summary(payload, engine),
        )

    async def _tool_smart_classify(self, args: Dict[str, Any],
                                    progress: ProgressReporter) -> Dict[str, Any]:
        """Classify search intent for a query."""
        from jiro.search.intent import IntentClassifier

        query = self._require_query(args, "query", "q")
        classifier = IntentClassifier(self.settings)
        intent = classifier.classify(query)
        return self._text_result(
            self._intent_payload(query, intent),
            summary=f"Intent: {intent.intent.value} "
                    f"({self._intent_target(intent)})",
        )

    async def _tool_compare_engines(self, args: Dict[str, Any],
                                     progress: ProgressReporter) -> Dict[str, Any]:
        """Compare search results across multiple engines."""
        # Validate before requiring infrastructure.
        query = self._require_query(args, "query", "q")
        engines = self._validate_str_list(args, "engines", required=False,
                                          max_items=9) or ["google", "bing", "brave"]
        for engine in engines:
            self._validate_engine(engine)
        search_type = self._validate_type(args.get("type"))
        num = self._validate_int(args, "max_results", 5, 1, 50)
        orchestrator = await self._require_orchestrator()

        comparison: Dict[str, Any] = {"query": query, "type": search_type,
                                      "results": {}}
        if progress.enabled:
            progress.report(0.0, message=f"comparing {len(engines)} engines")

        for i, engine in enumerate(engines):
            req = SearchRequest(q=query, engine=engine, type=search_type, num=num)
            try:
                result = await orchestrator.search(req)
                used = result.search_metadata.get("engine", engine)
                comparison["results"][engine] = {
                    "total": len(result.organic_results),
                    "first_result": result.organic_results[0].title
                    if result.organic_results else None,
                    # Report the engine that actually served the results so a
                    # blocked engine is never mistaken for a working one.
                    "engine_used": used,
                    "substituted": used != engine,
                    "latency_ms": result.search_metadata.get("total_time_taken"),
                    "skipped_engines": result.search_metadata.get("skipped_engines", []),
                }
            except Exception as e:
                comparison["results"][engine] = {
                    "error": str(e),
                    "error_code": getattr(e, "code", "engine_error"),
                }
            if progress.enabled:
                progress.report((i + 1) / len(engines), message=f"compared {engine}")

        healthy = [e for e, d in comparison["results"].items() if d.get("total")]
        summary = f"Compared {len(engines)} engines for: {query}"
        summary += f" — {len(healthy)} returned results"
        blocked = [e for e, d in comparison["results"].items()
                   if d.get("substituted") or d.get("error")]
        if blocked:
            summary += f" (unavailable/fell back: {', '.join(blocked)})"
        return self._text_result(comparison, summary=summary)

    async def _tool_monitor_status(self, args: Dict[str, Any],
                                    progress: ProgressReporter) -> Dict[str, Any]:
        """Get server/scraper status and metrics."""
        orchestrator = await self._require_orchestrator()

        engines_info = await orchestrator.engines_info()
        # Instantiation/health only — this tool is the cheap status view.
        # Use health_check when you need live reachability.
        health = await orchestrator.health_check(live=False)

        status = {
            "version": SERVER_VERSION,
            "engines": engines_info,
            "engines_registered": len(engines_info),
            "health": health,
            "health_note": "instantiation check only — call health_check for "
                           "live reachability",
            "cache_enabled": self.settings.cache_type != "memory",
            "cache_type": self.settings.cache_type,
            "cache_ttl": self.settings.cache_ttl,
            "plugins_enabled": True,
            "default_engine": self.settings.default_engine,
            "fallback_order": list(self.settings.fallback_order),
            "mcp_tools": len(mcp_tools()),
        }
        return self._text_result(status)

    async def _tool_health_check(self, args: Dict[str, Any],
                                  progress: ProgressReporter) -> Dict[str, Any]:
        """Health check that actually probes engine reachability.

        The previous implementation reported ``ok: true`` for every engine
        because it never performed a request — it only checked that the engine
        module imported. It now issues a real, minimal search per engine (in
        parallel, with a timeout) unless ``live=false``.
        """
        orchestrator = await self._require_orchestrator()
        live = args.get("live", True)
        if not isinstance(live, bool):
            raise MCPError(INVALID_PARAMS, "'live' must be a boolean",
                           data={"field": "live"})
        timeout = self._validate_int(args, "timeout", 15, 1, 120)

        if progress.enabled:
            progress.report(0.1, message="checking engine reachability")

        health = await orchestrator.health_check(live=live, timeout=float(timeout))
        if progress.enabled:
            progress.report(1.0, message="done")

        reachable = sorted(n for n, h in health.items() if h.get("ok"))
        unreachable = sorted(n for n, h in health.items() if not h.get("ok"))
        status = "healthy" if not unreachable else (
            "degraded" if reachable else "unhealthy"
        )
        payload = {
            "status": status,
            "mode": "live" if live else "instantiation",
            "reachable_engines": reachable,
            "unreachable_engines": unreachable,
            "services": health,
        }
        summary = f"Status: {status} — {len(reachable)}/{len(health)} engines reachable"
        if unreachable:
            summary += f"\nUnreachable: {', '.join(unreachable)}"
        if not live:
            summary += "\n(live=False: reachability not tested)"
        return self._text_result(payload, summary=summary)

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

    # ------------------------------------------------------- durable tasks

    def _require_runtime(self):
        """The task runtime, or a clear error when MCP is not started."""
        runtime = getattr(self, "runtime", None)
        if runtime is None:
            raise MCPError(INTERNAL_ERROR,
                           "durable task runtime is not initialised",
                           data={"hint": "call initialize/start first"})
        return runtime

    async def _tool_task_submit(self, args: Dict[str, Any],
                                progress: ProgressReporter) -> Dict[str, Any]:
        """Submit a durable task into the MCP owner namespace."""
        runtime = self._require_runtime()
        task_type = self._require_query(args, "type")
        payload = args.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise MCPError(INVALID_PARAMS, "'payload' must be an object",
                           data={"received": type(payload).__name__})

        limits = {}
        if args.get("max_attempts") is not None:
            limits["max_attempts"] = self._validate_int(
                args, "max_attempts", 3, 1, 10)
        if args.get("timeout") is not None:
            limits["attempt_timeout_seconds"] = self._validate_int(
                args, "timeout", 60, 1, 3600)

        idempotency_key = args.get("idempotency_key")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip()
        ):
            raise MCPError(INVALID_PARAMS,
                           "'idempotency_key' must be a non-empty string")

        record = await runtime.submit(
            task_type,
            payload,
            owner_key_id=self.task_owner,
            idempotency_key=(idempotency_key.strip() if idempotency_key else None),
            limits=limits or None,
        )
        status = record.to_status()
        status["hint"] = ("task is persisted; poll task_status for the result. "
                          "State 'running' with cancel_requested means "
                          "cancellation was asked for but the work has not "
                          "stopped yet.")
        return self._text_result(status)

    async def _tool_task_status(self, args: Dict[str, Any],
                                progress: ProgressReporter) -> Dict[str, Any]:
        """Read a task this MCP session submitted."""
        runtime = self._require_runtime()
        task_id = self._require_query(args, "task_id")
        include_result = args.get("include_result", True) is not False
        record = await runtime.get(task_id, owner_key_id=self.task_owner)
        status = record.to_result() if include_result else record.to_status()
        status.pop("result", None) if not include_result else None
        return self._text_result(status)

    async def _tool_task_cancel(self, args: Dict[str, Any],
                                progress: ProgressReporter) -> Dict[str, Any]:
        """Cancel a task this MCP session submitted."""
        runtime = self._require_runtime()
        task_id = self._require_query(args, "task_id")
        record = await runtime.cancel(task_id, owner_key_id=self.task_owner)
        status = record.to_status()
        if not record.is_terminal:
            status["hint"] = ("cancellation requested; the task stops once its "
                              "handler unwinds")
        return self._text_result(status)

    def _engine_registry(self):
        """Return the engine registry, importing parsers if needed.

        Discovery tools must work before ``start()`` has run, so this does not
        depend on a live orchestrator.
        """
        from jiro.scraping.engines import _build_registry

        return _build_registry()

    async def _tool_list_engines(self, args: Dict[str, Any],
                                 progress: ProgressReporter) -> Dict[str, Any]:
        """List engines with capabilities read from the live registry.

        The capability list used to be a hardcoded table that disagreed with
        the engines (it advertised ``videos``/``places`` for engines that do
        not implement them). It is now derived from each engine's own
        ``types`` attribute.
        """
        registry = self._engine_registry()
        engines: List[Dict[str, Any]] = []

        for name in sorted(registry.names()):
            try:
                engine_cls = registry.get(name)
                types = sorted(set(engine_cls.types))
                description = getattr(engine_cls, "description", "") or \
                    ENGINE_DESCRIPTIONS.get(name, "")
            except Exception:
                types = sorted(ENGINE_TYPES.get(name, ["web"]))
                description = ENGINE_DESCRIPTIONS.get(name, "")
            engines.append({
                "name": name,
                "types": types,
                "description": description,
                "default": name == self.settings.default_engine,
                "enabled": name in set(self.settings.engines),
                "supports_freshness": name in {
                    "google", "bing", "brave", "youtube", "yandex", "baidu",
                },
            })

        type_matrix = {t: _engines_supporting(t) for t in _all_types()}
        return self._text_result({
            "engines": engines,
            "total": len(engines),
            "type_matrix": type_matrix,
            "default_engine": self.settings.default_engine,
            "fallback_order": list(self.settings.fallback_order),
            "notes": (
                "Engine fallback is automatic: if the requested engine is "
                "blocked (rate limit, CAPTCHA) or fails, Jiro substitutes "
                "another engine and records the reason in "
                "search_metadata.skipped_engines. Check "
                "search_metadata.engine vs search_metadata.engine_requested, "
                "or call health_check for live reachability."
            ),
        })

    async def _tool_list_social_platforms(self, args: Dict[str, Any],
                                          progress: ProgressReporter) -> Dict[str, Any]:
        """List supported social platforms from the live scraper registry."""
        from jiro.scraping.social import registry as social_registry

        platforms = [
            {
                "name": info["platform"],
                "capabilities": sorted(info.get("supported_actions", [])),
                "requires_auth": info.get("requires_auth", False),
                "rate_limit_rpm": info.get("rate_limit_rpm"),
                "search": "search" in info.get("supported_actions", []),
            }
            for info in sorted(social_registry.list_platforms(),
                               key=lambda i: i["platform"])
        ]
        return self._text_result({
            "platforms": platforms,
            "total": len(platforms),
            "searchable": [p["name"] for p in platforms if p["search"]],
        })

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
            from jiro.pro import PLAN_LIMITS
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
