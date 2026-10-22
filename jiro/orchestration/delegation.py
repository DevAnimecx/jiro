"""Controlled delegation and task submission for agent orchestration.

Implements bounded, typed subtask delegation with validated inputs/outputs,
tool permissions, and integration with the existing durable runtime.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from jiro.log import get_logger
from jiro.orchestration.agents import AgentRegistry, Capability, get_agent_registry
from jiro.orchestration.graph import (
    GraphNode,
    GraphState,
    GraphStateStore,
    NodeState,
    NodeStatus,
    OrchestrationGraph,
)
from jiro.runtime.handlers import TaskContext
from jiro.runtime.runtime import TaskRuntime
from jiro.runtime.types import TaskLimits, TaskRecord, TaskState
from jiro.runtime.validation import TaskSchema, validate_input
from jiro.errors import ValidationError

log = get_logger("jiro.orchestration.delegation")


class DelegationError(Exception):
    """Raised when delegation fails due to policy or validation."""

    def __init__(self, message: str, *, node_id: str, agent_id: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.node_id = node_id
        self.agent_id = agent_id
        self.details = details or {}


@dataclass(frozen=True)
class DelegationSpec:
    """Specification for a single delegation from orchestrator to agent."""

    node_id: str
    agent_id: str
    task_type: str  # Maps to registered runtime task type
    input: Dict[str, Any]
    output_schema: TaskSchema
    capabilities: Set[Capability]
    budget: Dict[str, Any]  # max_model_calls, max_tokens, max_duration_seconds, etc.
    timeout_seconds: float
    max_retries: int
    requires_approval: bool = False
    approval_scope: Optional[str] = None
    idempotency_key: Optional[str] = None


@dataclass
class DelegationResult:
    """Result of a delegation execution."""

    node_id: str
    agent_id: str
    task_id: Optional[str]
    status: NodeStatus
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    attempts: int = 1
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    evidence_refs: List[str] = field(default_factory=list)


class ToolPermissionChecker:
    """Enforces capability permissions at tool execution boundary.

    This is the actual enforcement point - agents cannot escalate privileges
    through their output because tools check permissions before executing.
    """

    def __init__(self, agent_registry: Optional[AgentRegistry] = None):
        self.registry = agent_registry or get_agent_registry()
        self._capability_handlers: Dict[Capability, Callable] = {}

    def register_handler(self, capability: Capability, handler: Callable) -> None:
        """Register a tool handler for a capability."""
        self._capability_handlers[capability] = handler

    def check_permission(self, agent_id: str, capability: Capability) -> bool:
        """Check if an agent has a specific capability."""
        agent = self.registry.get(agent_id)
        if agent is None:
            return False
        return capability in agent.capabilities

    def require_permission(self, agent_id: str, capability: Capability) -> None:
        """Raise if agent lacks capability."""
        if not self.check_permission(agent_id, capability):
            agent = self.registry.get(agent_id)
            agent_caps = [c.value for c in agent.capabilities] if agent else []
            raise DelegationError(
                f"Agent {agent_id} lacks required capability: {capability.value}",
                node_id="",
                agent_id=agent_id,
                details={"agent_capabilities": agent_caps, "required": capability.value}
            )

    async def execute_with_permission(
        self,
        agent_id: str,
        capability: Capability,
        *args: Any,
        **kwargs: Any
    ) -> Any:
        """Execute a capability handler after permission check."""
        self.require_permission(agent_id, capability)
        handler = self._capability_handlers.get(capability)
        if handler is None:
            raise DelegationError(
                f"No handler registered for capability: {capability.value}",
                node_id="",
                agent_id=agent_id,
            )
        return await handler(*args, **kwargs)


class DelegationEngine:
    """Executes delegated tasks through the durable runtime with full validation."""

    def __init__(
        self,
        runtime: TaskRuntime,
        agent_registry: Optional[AgentRegistry] = None,
        permission_checker: Optional[ToolPermissionChecker] = None,
    ):
        self.runtime = runtime
        self.registry = agent_registry or get_agent_registry()
        self.permissions = permission_checker or ToolPermissionChecker(self.registry)
        self._task_type_map = self._build_task_type_map()

    def _build_task_type_map(self) -> Dict[str, str]:
        """Map agent roles to registered runtime task types."""
        return {
            "builtin.orchestrator": "orchestrate",
            "builtin.researcher": "research",
            "builtin.analyst": "analyze",
            "builtin.verifier": "verify",
            "builtin.synthesizer": "synthesize",
            "builtin.executor": "execute_action",
        }

    async def submit_delegation(
        self,
        delegation: DelegationSpec,
        context: TaskContext,
        parent_task_id: str,
    ) -> DelegationResult:
        """Submit a delegated task to the runtime with full validation."""
        agent = self.registry.get(delegation.agent_id)
        if agent is None:
            raise DelegationError(
                f"Agent not found or disabled: {delegation.agent_id}",
                node_id=delegation.node_id,
                agent_id=delegation.agent_id,
            )

        # Validate input against agent's input schema
        try:
            validated_input = validate_input(agent.input_schema, delegation.input)
        except ValidationError as e:
            raise DelegationError(
                f"Input validation failed: {e}",
                node_id=delegation.node_id,
                agent_id=delegation.agent_id,
                details={"input": delegation.input}
            )

        # Add orchestration metadata to input
        validated_input["_orchestration"] = {
            "parent_task_id": parent_task_id,
            "node_id": delegation.node_id,
            "agent_id": delegation.agent_id,
            "capabilities": [c.value for c in delegation.capabilities],
            "budget": delegation.budget,
        }

        # Generate idempotency key if not provided
        idempotency_key = delegation.idempotency_key or f"{parent_task_id}:{delegation.node_id}"

        # Determine task type
        task_type = self._task_type_map.get(delegation.agent_id, "generic_agent")

        # Create limits from budget
        limits = TaskLimits(
            max_attempts=delegation.max_retries + 1,
            max_attempt_timeout=int(delegation.timeout_seconds),
            max_total_timeout=int(delegation.timeout_seconds * (delegation.max_retries + 1)),
            max_retries=delegation.max_retries,
        )

        # Submit to runtime
        try:
            task_id = await self.runtime.submit(
                task_type=task_type,
                input=validated_input,
                owner_key_id=context.owner_key_id,
                idempotency_key=idempotency_key,
                limits=limits,
                priority=context.limits.max_attempts,  # Inherit priority
            )
        except Exception as e:
            raise DelegationError(
                f"Failed to submit task to runtime: {e}",
                node_id=delegation.node_id,
                agent_id=delegation.agent_id,
            )

        log.info("Delegated node %s to agent %s as task %s", delegation.node_id, delegation.agent_id, task_id)

        # Wait for completion (with timeout)
        return await self._wait_for_completion(
            task_id=task_id,
            node_id=delegation.node_id,
            agent_id=delegation.agent_id,
            output_schema=delegation.output_schema,
            timeout=delegation.timeout_seconds,
            max_retries=delegation.max_retries,
        )

    async def _wait_for_completion(
        self,
        task_id: str,
        node_id: str,
        agent_id: str,
        output_schema: TaskSchema,
        timeout: float,
        max_retries: int,
    ) -> DelegationResult:
        """Wait for task completion with retry handling."""
        attempt = 0

        while attempt <= max_retries:
            attempt += 1
            try:
                # Poll for completion
                record = await self._poll_task(task_id, timeout)
                if record is None:
                    raise DelegationError(
                        "Task not found after submission",
                        node_id=node_id,
                        agent_id=agent_id,
                    )

                if record.state == TaskState.COMPLETED:
                    # Validate output
                    output = record.result or {}
                    try:
                        validated_output = validate_input(output_schema, output)
                    except ValidationError as e:
                        raise DelegationError(
                            f"Output validation failed: {e}",
                            node_id=node_id,
                            agent_id=agent_id,
                            details={"output": output}
                        )

                    return DelegationResult(
                        node_id=node_id,
                        agent_id=agent_id,
                        task_id=task_id,
                        status=NodeStatus.COMPLETED,
                        output=validated_output,
                        attempts=attempt,
                        completed_at=time.time(),
                    )

                elif record.state == TaskState.FAILED:
                    error = record.error or "Task failed without error message"
                    if attempt <= max_retries:
                        log.warning("Task %s failed (attempt %d/%d): %s, retrying",
                                   task_id, attempt, max_retries + 1, error)
                        await asyncio.sleep(min(2 ** attempt, 30))  # Exponential backoff
                        continue
                    return DelegationResult(
                        node_id=node_id,
                        agent_id=agent_id,
                        task_id=task_id,
                        status=NodeStatus.FAILED,
                        error=error,
                        attempts=attempt,
                        completed_at=time.time(),
                    )

                elif record.state == TaskState.CANCELLED:
                    return DelegationResult(
                        node_id=node_id,
                        agent_id=agent_id,
                        task_id=task_id,
                        status=NodeStatus.CANCELLED,
                        error="Task was cancelled",
                        attempts=attempt,
                        completed_at=time.time(),
                    )

            except asyncio.TimeoutError:
                if attempt <= max_retries:
                    log.warning("Task %s timed out (attempt %d/%d), retrying",
                               task_id, attempt, max_retries + 1)
                    continue
                return DelegationResult(
                    node_id=node_id,
                    agent_id=agent_id,
                    task_id=task_id,
                    status=NodeStatus.FAILED,
                    error=f"Task timed out after {timeout}s",
                    attempts=attempt,
                    completed_at=time.time(),
                )

        return DelegationResult(
            node_id=node_id,
            agent_id=agent_id,
            task_id=task_id,
            status=NodeStatus.FAILED,
            error="Max retries exceeded",
            attempts=attempt,
            completed_at=time.time(),
        )

    async def _poll_task(self, task_id: str, timeout: float) -> Optional[TaskRecord]:
        """Poll task until completion or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            record = await self.runtime.get_task(task_id)
            if record is None:
                await asyncio.sleep(0.5)
                continue
            if record.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
                return record
            await asyncio.sleep(0.5)
        raise asyncio.TimeoutError(f"Task {task_id} did not complete within {timeout}s")


class OrchestrationExecutor:
    """Executes a full orchestration graph with bounded parallelism and recovery."""

    def __init__(
        self,
        runtime: TaskRuntime,
        graph_store: GraphStateStore,
        agent_registry: Optional[AgentRegistry] = None,
        permission_checker: Optional[ToolPermissionChecker] = None,
    ):
        self.runtime = runtime
        self.graph_store = graph_store
        self.registry = agent_registry or get_agent_registry()
        self.permissions = permission_checker or ToolPermissionChecker(self.registry)
        self.delegation = DelegationEngine(runtime, self.registry, self.permissions)
        self._running_executions: Dict[str, asyncio.Task] = {}
        self._max_global_parallel = 5
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def execute_graph(
        self,
        graph: OrchestrationGraph,
        root_input: Dict[str, Any],
        owner_key_id: Optional[str],
        execution_id: Optional[str] = None,
    ) -> GraphState:
        """Execute an orchestration graph to completion."""
        execution_id = execution_id or str(uuid.uuid4())
        self._semaphore = asyncio.Semaphore(graph.max_parallel)

        # Create initial state
        state = GraphState(
            graph_id=graph.graph_id,
            execution_id=execution_id,
            root_input=root_input,
        )

        # Initialize node states
        for node in graph.nodes:
            state.nodes[node.node_id] = NodeState(
                node_id=node.node_id,
                retries_remaining=node.max_retries,
            )

        await self.graph_store.save(state)

        # Execute
        try:
            await self._execute_loop(graph, state, owner_key_id, set(), set(), set())
            state.status = "completed"
            state.completed_at = time.time()
            # Aggregate final output from sink nodes
            state.final_output = self._aggregate_output(graph, state)
        except Exception as e:
            log.error("Graph execution failed: %s", e)
            state.status = "failed"
            state.error = str(e)
            state.completed_at = time.time()
        finally:
            state.updated_at = time.time()
            await self.graph_store.save(state)

        return state

    async def _execute_loop(
        self,
        graph: OrchestrationGraph,
        state: GraphState,
        owner_key_id: Optional[str],
        completed: Optional[Set[str]] = None,
        failed: Optional[Set[str]] = None,
        running: Optional[Set[str]] = None,
    ) -> None:
        """Main execution loop with dependency-aware scheduling."""
        completed = completed or set()
        failed = failed or set()
        running = running or set()

        # Create a mock TaskContext for delegation
        context = TaskContext(
            task_id=state.execution_id,
            task_type="orchestrate",
            input=state.root_input or {},
            attempt=1,
            idempotency_key=state.execution_id,
            deadline_at=time.time() + graph.global_timeout_seconds,
            limits=TaskLimits(max_attempts=1),
            owner_key_id=owner_key_id,
        )

        while True:
            # Check for ready nodes
            ready = graph.get_ready_nodes(completed, running, failed)

            # Filter by parallelism limit
            available_slots = self._max_global_parallel - len(running)
            if available_slots <= 0:
                await self._wait_for_any(running, state)
                continue

            ready = ready[:available_slots]

            if not ready:
                # No ready nodes - check if done
                if len(completed) + len(failed) == len(graph.nodes):
                    break  # All nodes finished
                # Wait for running nodes
                await self._wait_for_any(running, state)
                continue

            # Launch ready nodes
            for node_id in ready:
                node = graph.node_map[node_id]
                state.nodes[node_id].status = NodeStatus.QUEUED
                await self.graph_store.save(state)

                # Submit as background task
                task = asyncio.create_task(
                    self._execute_node(graph, node, state, context, completed, failed, running)
                )
                running.add(node_id)
                self._running_executions[f"{state.execution_id}:{node_id}"] = task

            # Brief pause to let tasks start
            await asyncio.sleep(0.1)

    async def _execute_node(
        self,
        graph: OrchestrationGraph,
        node: GraphNode,
        state: GraphState,
        context: TaskContext,
        completed: Set[str],
        failed: Set[str],
        running: Set[str],
    ) -> None:
        """Execute a single node with semaphore for parallelism control."""
        async with self._semaphore:
            node_state = state.nodes[node.node_id]
            node_state.status = NodeStatus.RUNNING
            node_state.started_at = time.time()
            node_state.attempt = 1
            await self.graph_store.save(state)

            try:
                # Build delegation spec
                agent = self.registry.get(node.agent_id)
                if agent is None:
                    raise DelegationError(
                        f"Agent not found: {node.agent_id}",
                        node_id=node.node_id,
                        agent_id=node.agent_id,
                    )

                # Resolve input from dependencies
                dep_results = {dep: state.nodes[dep].output for dep in node.depends_on}
                resolved_input = node.resolve_input(dep_results)

                delegation = DelegationSpec(
                    node_id=node.node_id,
                    agent_id=node.agent_id,
                    task_type=self.delegation._task_type_map.get(node.agent_id, "generic_agent"),
                    input=resolved_input,
                    output_schema=node.output_schema,
                    capabilities=agent.capabilities,
                    budget={
                        "max_model_calls": agent.budget.max_model_calls,
                        "max_tokens": agent.budget.max_tokens,
                        "max_duration_seconds": agent.budget.max_duration_seconds,
                    },
                    timeout_seconds=node.timeout_seconds,
                    max_retries=node.max_retries,
                )

                # Execute delegation
                result = await self.delegation.submit_delegation(delegation, context, state.execution_id)

                # Update state
                node_state.status = result.status
                node_state.output = result.output
                node_state.error = result.error
                node_state.attempt = result.attempts
                node_state.completed_at = result.completed_at

                if result.status == NodeStatus.COMPLETED:
                    completed.add(node.node_id)
                    # Extract evidence refs if present
                    if result.output and "evidence_refs" in result.output:
                        node_state.output["evidence_refs"] = result.output["evidence_refs"]
                else:
                    failed.add(node.node_id)

            except Exception as e:
                log.error("Node %s failed: %s", node.node_id, e)
                node_state.status = NodeStatus.FAILED
                node_state.error = str(e)
                node_state.completed_at = time.time()
                failed.add(node.node_id)
            finally:
                running.discard(node.node_id)
                key = f"{state.execution_id}:{node.node_id}"
                self._running_executions.pop(key, None)
                state.updated_at = time.time()
                await self.graph_store.save(state)

    async def _wait_for_any(self, running: Set[str], state: GraphState) -> None:
        """Wait for any running node to complete."""
        if not running:
            return

        tasks = [self._running_executions[f"{state.execution_id}:{nid}"] for nid in running
                 if f"{state.execution_id}:{nid}" in self._running_executions]

        if tasks:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            # Results are handled in _execute_node callbacks

    def _aggregate_output(self, graph: OrchestrationGraph, state: GraphState) -> Dict[str, Any]:
        """Aggregate final output from sink nodes (nodes with no dependents)."""
        sink_nodes = [
            n.node_id for n in graph.nodes
            if n.node_id not in graph.adjacency
        ]

        output = {
            "graph_id": graph.graph_id,
            "execution_id": state.execution_id,
            "status": state.status,
            "nodes_completed": len([n for n in state.nodes.values() if n.status == NodeStatus.COMPLETED]),
            "nodes_failed": len([n for n in state.nodes.values() if n.status == NodeStatus.FAILED]),
        }

        if sink_nodes:
            # Use first sink node's output as primary result
            primary = state.nodes[sink_nodes[0]].output
            if primary:
                output["result"] = primary

        # Include all node outputs for debugging
        output["node_outputs"] = {
            nid: ns.output for nid, ns in state.nodes.items() if ns.output
        }

        return output

    async def cancel_execution(self, graph_id: str, execution_id: str) -> bool:
        """Cancel a running execution."""
        key_prefix = f"{execution_id}:"
        cancelled = False
        for key, task in list(self._running_executions.items()):
            if key.startswith(key_prefix):
                task.cancel()
                cancelled = True

        # Also cancel runtime tasks
        for node_id in self._running_executions:
            if node_id.startswith(key_prefix):
                # Would need to track task_ids to cancel them
                pass

        return cancelled

    async def resume_execution(
        self,
        graph: OrchestrationGraph,
        state: GraphState,
        owner_key_id: Optional[str],
    ) -> GraphState:
        """Resume a previously interrupted execution."""
        # Reconstruct completed/failed/running sets from state
        completed = {nid for nid, ns in state.nodes.items() if ns.status == NodeStatus.COMPLETED}
        failed = {nid for nid, ns in state.nodes.items() if ns.status == NodeStatus.FAILED}
        running = {nid for nid, ns in state.nodes.items() if ns.status == NodeStatus.RUNNING}

        # Reset running nodes to pending for retry
        for nid in running:
            state.nodes[nid].status = NodeStatus.PENDING
            state.nodes[nid].retries_remaining = max(0, state.nodes[nid].retries_remaining - 1)

        state.status = "running"
        state.error = None
        state.updated_at = time.time()
        await self.graph_store.save(state)

        return await self._execute_loop(graph, state, owner_key_id, completed, failed, running)


__all__ = [
    "DelegationEngine",
    "DelegationError",
    "DelegationResult",
    "DelegationSpec",
    "OrchestrationExecutor",
    "ToolPermissionChecker",
]