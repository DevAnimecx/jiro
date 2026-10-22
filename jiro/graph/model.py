"""Validated contracts for the query graph.

The graph is a DAG of retrieval operators under one shared budget. Validation
happens before any network call: cycles, depth, fanout, node count, unknown
operators, and malformed roots are rejected at the model boundary.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Dict, Literal, Mapping, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

OperatorName = Literal[
    "SEARCH",
    "FETCH",
    "EXTRACT",
    "MERGE",
    "VERIFY",
    "COMPRESS",
    "SYNTHESIZE",
]

EvidenceLevel = Literal["NONE", "SNIPPET", "PAGE", "MULTI_SOURCE"]

GRAPH_SCHEMA = "jiro.graph/v1"


class Budget(BaseModel):
    """Hard limits for one graph run.

    The budget is enforced by the executor, not by prompts. A node that would
    exceed a limit fails visibly instead of silently degrading the result.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deadline_ms: int = Field(2500, ge=100, le=60_000)
    max_nodes: int = Field(12, ge=1, le=32)
    max_depth: int = Field(3, ge=1, le=6)
    max_fanout: int = Field(6, ge=1, le=12)
    max_requests: int = Field(24, ge=1, le=200)
    max_bytes: int = Field(8_000_000, ge=1_000, le=100_000_000)
    max_tokens: int = Field(1200, ge=64, le=32_000)


class QueryNode(BaseModel):
    """One operator invocation.

    ``inputs`` refers to other node ids. A root node has no inputs and must be
    a ``SEARCH`` or ``FETCH`` node.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    op: OperatorName
    inputs: Tuple[str, ...] = ()
    engine_policy: Tuple[str, ...] = ("auto",)
    query_template: str = Field("", max_length=1000)
    schema_ref: str | None = Field(None, max_length=128)
    required: bool = True
    evidence: EvidenceLevel = "SNIPPET"
    timeout_ms: int = Field(1200, ge=50, le=30_000)
    hedge_after_ms: int = Field(0, ge=0, le=30_000)

    @model_validator(mode="after")
    def _check_shape(self) -> "QueryNode":
        if self.op == "SEARCH":
            if self.inputs:
                raise ValueError("SEARCH nodes must be roots and cannot have inputs")
            if not self.query_template.strip():
                raise ValueError("SEARCH nodes require a query_template")
        elif self.op == "FETCH":
            if not self.inputs and not self.query_template.strip():
                raise ValueError("FETCH nodes require inputs or a URL template")
        else:
            if not self.inputs:
                raise ValueError(f"{self.op} nodes require at least one input")
        if self.id in self.inputs:
            raise ValueError(f"node {self.id!r} cannot depend on itself")
        return self


class QueryGraph(BaseModel):
    """A validated, deterministic query plan."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    graph_schema: str = Field(GRAPH_SCHEMA, alias="schema")
    graph_id: str = Field(..., min_length=1, max_length=64)
    intent: str = Field(..., min_length=1, max_length=64)
    nodes: Tuple[QueryNode, ...]
    budget: Budget = Field(default_factory=Budget)
    policy: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_graph(self) -> "QueryGraph":
        if self.graph_schema != GRAPH_SCHEMA:
            raise ValueError(f"unsupported graph schema: {self.graph_schema!r}")

        ids = [n.id for n in self.nodes]
        if not ids:
            raise ValueError("graph must contain at least one node")
        if len(ids) > self.budget.max_nodes:
            raise ValueError(
                f"graph has {len(ids)} nodes; budget allows {self.budget.max_nodes}"
            )
        duplicates = [node_id for node_id, count in Counter(ids).items() if count > 1]
        if duplicates:
            raise ValueError(f"duplicate node ids: {sorted(duplicates)}")

        node_map = {n.id: n for n in self.nodes}
        for node in self.nodes:
            for parent in node.inputs:
                if parent not in node_map:
                    raise ValueError(f"node {node.id!r} references unknown input {parent!r}")

        levels = self._levels(node_map)
        depth = max(levels.values())
        if depth > self.budget.max_depth:
            raise ValueError(
                f"graph depth {depth} exceeds budget {self.budget.max_depth}"
            )
        per_level = Counter(levels.values())
        widest = max(per_level.values())
        if widest > self.budget.max_fanout:
            raise ValueError(
                f"graph has {widest} concurrent nodes in one wave; "
                f"budget allows {self.budget.max_fanout}"
            )

        for node in self.nodes:
            if not node.inputs and node.op not in ("SEARCH", "FETCH"):
                raise ValueError(f"root node {node.id!r} must be SEARCH or FETCH")
        return self

    @staticmethod
    def _levels(node_map: Mapping[str, QueryNode]) -> Dict[str, int]:
        """Kahn topological levels; raises ValueError on a cycle."""
        indegree = {node_id: len(node.inputs) for node_id, node in node_map.items()}
        children: Dict[str, list[str]] = {node_id: [] for node_id in node_map}
        for node in node_map.values():
            for parent in node.inputs:
                children[parent].append(node.id)

        levels: Dict[str, int] = {}
        ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
        visited = 0
        while ready:
            current = ready.pop(0)
            visited += 1
            levels[current] = max(
                (levels[parent] + 1 for parent in node_map[current].inputs),
                default=0,
            )
            for child in sorted(children[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
            ready.sort()

        if visited != len(node_map):
            remaining = sorted(set(node_map) - set(levels))
            raise ValueError(f"graph contains a cycle involving: {remaining}")
        return levels

    def levels(self) -> Dict[str, int]:
        return self._levels({n.id: n for n in self.nodes})

    def node(self, node_id: str) -> QueryNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def canonical_payload(self) -> Dict[str, Any]:
        """Stable payload used for hashing and replay.

        ``graph_id`` is excluded so the id can be derived from the plan itself.
        """
        data = self.model_dump(mode="json", by_alias=True)
        data.pop("graph_id", None)
        return data

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def graph_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def with_derived_id(self) -> "QueryGraph":
        digest = self.graph_hash()[:16]
        return self.model_copy(update={"graph_id": f"g_{digest}"})


def canonical_digest(payload: Mapping[str, Any] | Any) -> str:
    """SHA-256 of a canonical JSON payload."""
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
