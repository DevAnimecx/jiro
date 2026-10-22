"""Durable orchestration graph for agent workflows.

Represents an agent workflow as a validated directed acyclic graph (DAG) with
explicit dependency ordering, durable state, and recovery semantics.

Each node has a stable identity and clearly defined input/output contract.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from jiro.log import get_logger
from jiro.orchestration.agents import AgentRegistry, Capability, get_agent_registry
from jiro.runtime.validation import TaskSchema, validate_input
from jiro.errors import ValidationError

log = get_logger("jiro.orchestration.graph")


class NodeStatus(str, Enum):
    """Node execution status."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class CompletionPolicy(str, Enum):
    """When a downstream node may execute."""

    ALL_SUCCESS = "all_success"          # All dependencies must succeed
    ANY_SUCCESS = "any_success"          # At least one dependency succeeds
    ALL_COMPLETED = "all_completed"      # All dependencies finished (any status)
    MANUAL = "manual"                    # Explicit trigger required


@dataclass(frozen=True)
class GraphNode:
    """A single node in the orchestration graph."""

    node_id: str
    agent_id: str
    name: str
    input_template: Dict[str, Any]  # Template with {{ref}} placeholders
    output_schema: TaskSchema
    depends_on: Tuple[str, ...] = ()
    completion_policy: CompletionPolicy = CompletionPolicy.ALL_SUCCESS
    timeout_seconds: float = 120.0
    max_retries: int = 2
    budget_overrides: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def resolve_input(self, dep_results: Mapping[str, Any]) -> Dict[str, Any]:
        """Resolve template placeholders from dependency results.

        Placeholders: {{node_id.field.subfield}} or {{node_id}}
        """
        import re

        def replace_placeholder(match: re.Match) -> str:
            path = match.group(1).strip()
            parts = path.split(".")
            node_id = parts[0]
            if node_id not in dep_results:
                return match.group(0)  # Keep as-is if dependency not found
            value = dep_results[node_id]
            for part in parts[1:]:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return match.group(0)
                if value is None:
                    return match.group(0)
            if isinstance(value, (dict, list)):
                return json.dumps(value)
            return str(value)

        template_str = json.dumps(self.input_template)
        resolved = re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_placeholder, template_str)
        return json.loads(resolved)


@dataclass(frozen=True)
class OrchestrationGraph:
    """Validated directed acyclic graph of agent nodes."""

    graph_id: str
    name: str
    nodes: Tuple[GraphNode, ...]
    global_timeout_seconds: float = 600.0
    max_parallel: int = 3
    max_depth: int = 5
    max_total_nodes: int = 50
    created_at: float = field(default_factory=time.time)
    version: str = "1.0"

    @property
    def node_map(self) -> Dict[str, GraphNode]:
        return {n.node_id: n for n in self.nodes}

    @property
    def adjacency(self) -> Dict[str, List[str]]:
        """Forward edges: node -> list of dependents."""
        adj = defaultdict(list)
        for node in self.nodes:
            for dep in node.depends_on:
                adj[dep].append(node.node_id)
        return dict(adj)

    @property
    def reverse_adjacency(self) -> Dict[str, List[str]]:
        """Reverse edges: node -> list of dependencies."""
        return {n.node_id: list(n.depends_on) for n in self.nodes}

    def topological_order(self) -> List[str]:
        """Return nodes in topological order (dependencies first)."""
        indegree = {n.node_id: len(n.depends_on) for n in self.nodes}
        queue = deque([nid for nid, deg in indegree.items() if deg == 0])
        order = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for dependent in self.adjacency.get(nid, []):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)

        if len(order) != len(self.nodes):
            raise ValidationError("Graph contains a cycle")
        return order

    def get_ready_nodes(self, completed: Set[str], running: Set[str], failed: Set[str]) -> List[str]:
        """Get nodes ready to execute given current state."""
        ready = []
        for node in self.nodes:
            if node.node_id in completed or node.node_id in running:
                continue

            deps = set(node.depends_on)
            if not deps:
                ready.append(node.node_id)
                continue

            if node.completion_policy == CompletionPolicy.ALL_SUCCESS:
                if deps.issubset(completed):
                    ready.append(node.node_id)
            elif node.completion_policy == CompletionPolicy.ANY_SUCCESS:
                if deps & completed:
                    ready.append(node.node_id)
            elif node.completion_policy == CompletionPolicy.ALL_COMPLETED:
                finished = completed | failed
                if deps.issubset(finished):
                    ready.append(node.node_id)

        return ready


class GraphValidator:
    """Validates orchestration graphs before execution."""

    def __init__(self, agent_registry: Optional[AgentRegistry] = None):
        self.registry = agent_registry or get_agent_registry()

    def validate(self, graph: OrchestrationGraph) -> List[str]:
        """Validate graph structure and agent references.

        Returns list of warnings (non-blocking issues).
        Raises ValidationError for blocking issues.
        """
        warnings = []

        # Check node count
        if len(graph.nodes) > graph.max_total_nodes:
            raise ValidationError(f"Graph exceeds max_total_nodes ({graph.max_total_nodes})")

        # Check for duplicate node IDs
        seen_ids = set()
        for node in graph.nodes:
            if node.node_id in seen_ids:
                raise ValidationError(f"Duplicate node_id: {node.node_id}")
            seen_ids.add(node.node_id)

        # Check all dependencies exist
        for node in graph.nodes:
            for dep in node.depends_on:
                if dep not in seen_ids:
                    raise ValidationError(f"Node {node.node_id} depends on unknown node: {dep}")

        # Check for cycles
        try:
            graph.topological_order()
        except ValidationError as e:
            raise ValidationError(f"Graph contains cycle: {e}")

        # Check depth
        depth = self._calculate_depth(graph)
        if depth > graph.max_depth:
            raise ValidationError(f"Graph depth {depth} exceeds max_depth {graph.max_depth}")

        # Validate agent references and capabilities
        for node in graph.nodes:
            agent = self.registry.get(node.agent_id)
            if agent is None:
                raise ValidationError(f"Node {node.node_id} references unknown or disabled agent: {node.agent_id}")

            # Check agent can be delegated to (if not root)
            if node.depends_on:
                # Find a parent agent that would delegate to this one
                parent_agents = [self.registry.get(self.nodes_by_id[dep].agent_id)
                                for dep in node.depends_on if dep in self.nodes_by_id]
                can_delegate = any(p and p.can_delegate_to(agent.role) for p in parent_agents if p)
                if not can_delegate and parent_agents:
                    warnings.append(f"Node {node.node_id}: parent may not delegate to {agent.role.value}")

            # Validate input template against agent's input schema
            # (We validate structure, not values since they depend on runtime results)
            try:
                # Create a dummy input with string placeholders for validation
                dummy_input = self._make_dummy_input(node.input_template, agent.input_schema)
                validate_input(agent.input_schema, dummy_input)
            except Exception as e:
                warnings.append(f"Node {node.node_id} input template may not match agent schema: {e}")

        # Check parallelism
        max_parallel_possible = self._max_parallel_width(graph)
        if max_parallel_possible > graph.max_parallel:
            warnings.append(f"Graph width {max_parallel_possible} exceeds max_parallel {graph.max_parallel}")

        return warnings

    def _calculate_depth(self, graph: OrchestrationGraph) -> int:
        """Calculate longest path length in the graph."""
        topo = graph.topological_order()
        depth = {nid: 0 for nid in topo}
        for nid in topo:
            for dep in graph.nodes_by_id[nid].depends_on:
                depth[nid] = max(depth[nid], depth[dep] + 1)
        return max(depth.values()) if depth else 0

    def _max_parallel_width(self, graph: OrchestrationGraph) -> int:
        """Calculate maximum number of nodes that could run in parallel."""
        topo = graph.topological_order()
        level = {nid: 0 for nid in topo}
        for nid in topo:
            node = graph.nodes_by_id[nid]
            if node.depends_on:
                level[nid] = max(level[dep] for dep in node.depends_on) + 1
        # Count nodes per level
        level_counts = defaultdict(int)
        for nid, lvl in level.items():
            level_counts[lvl] += 1
        return max(level_counts.values()) if level_counts else 0

    def _make_dummy_input(self, template: Dict[str, Any], schema: TaskSchema) -> Dict[str, Any]:
        """Create dummy input matching schema structure for validation."""
        result = {}
        for field_name, field_def in schema.fields.items():
            if field_name in template:
                result[field_name] = template[field_name]
            elif field_def.required:
                # Provide dummy values for required fields
                if field_def.type == "string":
                    result[field_name] = "placeholder"
                elif field_def.type == "integer":
                    result[field_name] = 0
                elif field_def.type == "boolean":
                    result[field_name] = False
                elif field_def.type == "object":
                    result[field_name] = {}
                elif field_def.type == "array":
                    result[field_name] = []
                else:
                    result[field_name] = None
        return result

    @property
    def nodes_by_id(self) -> Dict[str, GraphNode]:
        return {n.node_id: n for n in self.nodes}


@dataclass
class NodeState:
    """Runtime state of a graph node."""

    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    input: Optional[Dict[str, Any]] = None
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    attempt: int = 0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    retries_remaining: int = 0
    checkpoint: Optional[Dict[str, Any]] = None


@dataclass
class GraphState:
    """Complete durable state of a workflow execution."""

    graph_id: str
    execution_id: str  # Unique per execution attempt
    status: str = "running"  # running, completed, failed, cancelled
    nodes: Dict[str, NodeState] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    root_input: Optional[Dict[str, Any]] = None
    final_output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "execution_id": self.execution_id,
            "status": self.status,
            "nodes": {
                nid: {
                    "status": ns.status.value,
                    "input": ns.input,
                    "output": ns.output,
                    "error": ns.error,
                    "attempt": ns.attempt,
                    "started_at": ns.started_at,
                    "completed_at": ns.completed_at,
                    "retries_remaining": ns.retries_remaining,
                    "checkpoint": ns.checkpoint,
                }
                for nid, ns in self.nodes.items()
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "root_input": self.root_input,
            "final_output": self.final_output,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphState":
        nodes = {}
        for nid, nd in data.get("nodes", {}).items():
            nodes[nid] = NodeState(
                node_id=nid,
                status=NodeStatus(nd["status"]),
                input=nd.get("input"),
                output=nd.get("output"),
                error=nd.get("error"),
                attempt=nd.get("attempt", 0),
                started_at=nd.get("started_at"),
                completed_at=nd.get("completed_at"),
                retries_remaining=nd.get("retries_remaining", 0),
                checkpoint=nd.get("checkpoint"),
            )
        return cls(
            graph_id=data["graph_id"],
            execution_id=data["execution_id"],
            status=data["status"],
            nodes=nodes,
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            completed_at=data.get("completed_at"),
            root_input=data.get("root_input"),
            final_output=data.get("final_output"),
            error=data.get("error"),
            metadata=data.get("metadata", {}),
        )


class GraphStateStore:
    """Abstract interface for persisting graph state."""

    async def save(self, state: GraphState) -> None:
        raise NotImplementedError

    async def load(self, graph_id: str, execution_id: str) -> Optional[GraphState]:
        raise NotImplementedError

    async def list_executions(self, graph_id: str) -> List[str]:
        raise NotImplementedError

    async def claim_execution(self, graph_id: str, execution_id: str, owner: str, ttl_seconds: int) -> bool:
        """Try to claim an execution for recovery. Returns True if claimed."""
        raise NotImplementedError

    async def release_execution(self, graph_id: str, execution_id: str, owner: str) -> None:
        raise NotImplementedError


__all__ = [
    "Capability",
    "CompletionPolicy",
    "GraphNode",
    "GraphState",
    "GraphStateStore",
    "GraphValidator",
    "NodeState",
    "NodeStatus",
    "OrchestrationGraph",
]