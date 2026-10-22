"""Tool permissions and execution boundaries for orchestration.

Provides concrete tool handlers for each capability, with enforcement at the
execution boundary. Agents cannot escalate privileges through their output.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Callable

from jiro.log import get_logger
from jiro.orchestration.agents import Capability
from jiro.runtime.handlers import TaskContext

log = get_logger("jiro.orchestration.tools")


@dataclass
class ToolResult:
    """Result of a tool execution."""

    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ToolDispatcher:
    """Dispatches tool calls with permission enforcement.

    This is the actual enforcement boundary - agents cannot escalate
    privileges because tools check permissions before executing.
    """

    def __init__(self, permission_checker):
        self.permission_checker = permission_checker
        self._handlers: Dict[Capability, Callable] = {}
        self._register_builtin_handlers()

    def _register_builtin_handlers(self) -> None:
        """Register built-in tool handlers for each capability."""
        self._handlers[Capability.SEARCH] = self._handle_search
        self._handlers[Capability.RETRIEVE] = self._handle_retrieve
        self._handlers[Capability.SCRAPE] = self._handle_scrape
        self._handlers[Capability.READ_MEMORY] = self._handle_read_memory
        self._handlers[Capability.WRITE_MEMORY] = self._handle_write_memory
        self._handlers[Capability.CREATE_RESEARCH] = self._handle_create_research
        self._handlers[Capability.GENERATE_REPORT] = self._handle_generate_report
        self._handlers[Capability.INTERNAL_API] = self._handle_internal_api
        self._handlers[Capability.EXTERNAL_ACTION] = self._handle_external_action
        self._handlers[Capability.VALIDATE_EVIDENCE] = self._handle_validate_evidence
        self._handlers[Capability.EXTRACT_CLAIMS] = self._handle_extract_claims

    def register_handler(self, capability: Capability, handler: Callable) -> None:
        """Register a custom handler for a capability."""
        self._handlers[capability] = handler

    async def execute(
        self,
        agent_id: str,
        capability: Capability,
        params: Dict[str, Any],
        context: TaskContext,
    ) -> ToolResult:
        """Execute a tool with permission check."""
        # Check permission
        if not self.permission_checker.check_permission(agent_id, capability):
            return ToolResult(
                success=False,
                error=f"Agent {agent_id} lacks capability: {capability.value}",
            )

        handler = self._handlers.get(capability)
        if handler is None:
            return ToolResult(
                success=False,
                error=f"No handler for capability: {capability.value}",
            )

        start = time.time()
        try:
            result = await handler(params, context, agent_id)
            result.metadata["latency_ms"] = (time.time() - start) * 1000
            return result
        except Exception as e:
            log.error("Tool %s failed for agent %s: %s", capability.value, agent_id, e)
            return ToolResult(
                success=False,
                error=str(e),
                metadata={"latency_ms": (time.time() - start) * 1000},
            )

    # --- Built-in handlers ---

    async def _handle_search(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Execute a search query."""
        query = params.get("query", "")
        engine = params.get("engine", "auto")
        _max_results = params.get("max_results", 10)

        return ToolResult(
            success=True,
            data={"query": query, "engine": engine, "results": []},
            metadata={"tool": "search", "agent": agent_id},
        )

    async def _handle_retrieve(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Retrieve content from a URL."""
        url = params.get("url", "")
        # Placeholder - would call actual scraper
        return ToolResult(
            success=True,
            data={"url": url, "content": ""},
            metadata={"tool": "retrieve", "agent": agent_id},
        )

    async def _handle_scrape(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Scrape a URL with full extraction."""
        url = params.get("url", "")
        # Placeholder - would call actual scraper
        return ToolResult(
            success=True,
            data={"url": url, "extracted": {}},
            metadata={"tool": "scrape", "agent": agent_id},
        )

    async def _handle_read_memory(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Read from memory."""
        scope = params.get("scope", "task")
        query = params.get("query", "")
        # Placeholder - would call memory service
        return ToolResult(
            success=True,
            data={"scope": scope, "query": query, "memories": []},
            metadata={"tool": "read_memory", "agent": agent_id},
        )

    async def _handle_write_memory(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Write to memory."""
        memory_type = params.get("type", "episodic")
        _content = params.get("content", {})
        # Placeholder - would call memory service
        return ToolResult(
            success=True,
            data={"type": memory_type, "id": "mem_123"},
            metadata={"tool": "write_memory", "agent": agent_id},
        )

    async def _handle_create_research(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Create a research task."""
        question = params.get("question", "")
        _budget = params.get("budget", {})
        # Placeholder - would call research engine
        return ToolResult(
            success=True,
            data={"question": question, "research_id": "res_123", "status": "created"},
            metadata={"tool": "create_research", "agent": agent_id},
        )

    async def _handle_generate_report(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Generate a report from evidence."""
        _evidence_refs = params.get("evidence_refs", [])
        format = params.get("format", "markdown")
        # Placeholder - would call report generator
        return ToolResult(
            success=True,
            data={"format": format, "report": "# Report\n\nContent here", "citations": []},
            metadata={"tool": "generate_report", "agent": agent_id},
        )

    async def _handle_internal_api(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Call an internal API endpoint."""
        endpoint = params.get("endpoint", "")
        method = params.get("method", "GET")
        # Placeholder - would call actual internal API
        return ToolResult(
            success=True,
            data={"endpoint": endpoint, "method": method, "response": {}},
            metadata={"tool": "internal_api", "agent": agent_id},
        )

    async def _handle_external_action(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Execute an external action (requires approval)."""
        action = params.get("action", "")
        requires_approval = params.get("requires_approval", True)

        if requires_approval:
            return ToolResult(
                success=False,
                error="External action requires approval",
                data={"action": action, "status": "awaiting_approval"},
            )

        # Placeholder - would execute actual external action
        return ToolResult(
            success=True,
            data={"action": action, "result": "completed"},
            metadata={"tool": "external_action", "agent": agent_id},
        )

    async def _handle_validate_evidence(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Validate evidence against claims."""
        claim = params.get("claim", "")
        _evidence_refs = params.get("evidence_refs", [])
        # Placeholder - would call verification pipeline
        return ToolResult(
            success=True,
            data={"claim": claim, "verified": True, "confidence": "high"},
            metadata={"tool": "validate_evidence", "agent": agent_id},
        )

    async def _handle_extract_claims(self, params: Dict, context: TaskContext, agent_id: str) -> ToolResult:
        """Extract claims from evidence."""
        evidence_refs = params.get("evidence_refs", [])
        # Placeholder - would call claim extractor
        return ToolResult(
            success=True,
            data={"evidence_refs": evidence_refs, "claims": []},
            metadata={"tool": "extract_claims", "agent": agent_id},
        )


class ApprovalManager:
    """Manages approval workflows for sensitive actions."""

    def __init__(self):
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._approved: Dict[str, Dict[str, Any]] = {}
        self._rejected: Dict[str, Dict[str, Any]] = {}

    async def request_approval(
        self,
        agent_id: str,
        action: str,
        params: Dict[str, Any],
        requester: str,
        scope: str = "default",
    ) -> str:
        """Request approval for an action. Returns approval ID."""
        import uuid
        approval_id = str(uuid.uuid4())
        self._pending[approval_id] = {
            "approval_id": approval_id,
            "agent_id": agent_id,
            "action": action,
            "params": params,
            "requester": requester,
            "scope": scope,
            "requested_at": time.time(),
            "status": "pending",
        }
        log.info("Approval requested: %s for agent %s action %s", approval_id, agent_id, action)
        return approval_id

    async def approve(self, approval_id: str, approver: str, notes: str = "") -> bool:
        """Approve a pending request."""
        if approval_id not in self._pending:
            return False
        request = self._pending.pop(approval_id)
        request["status"] = "approved"
        request["approved_at"] = time.time()
        request["approver"] = approver
        request["notes"] = notes
        self._approved[approval_id] = request
        log.info("Approval %s granted by %s", approval_id, approver)
        return True

    async def reject(self, approval_id: str, rejecter: str, reason: str = "") -> bool:
        """Reject a pending request."""
        if approval_id not in self._pending:
            return False
        request = self._pending.pop(approval_id)
        request["status"] = "rejected"
        request["rejected_at"] = time.time()
        request["rejecter"] = rejecter
        request["reason"] = reason
        self._rejected[approval_id] = request
        log.info("Approval %s rejected by %s: %s", approval_id, rejecter, reason)
        return True

    def get_status(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Get approval status."""
        if approval_id in self._pending:
            return self._pending[approval_id]
        if approval_id in self._approved:
            return self._approved[approval_id]
        if approval_id in self._rejected:
            return self._rejected[approval_id]
        return None

    def is_approved(self, approval_id: str) -> bool:
        return approval_id in self._approved


__all__ = [
    "ApprovalManager",
    "ToolDispatcher",
    "ToolResult",
]