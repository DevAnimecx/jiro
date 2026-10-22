"""Shared context and memory integration for agent orchestration.

Provides controlled context sharing through explicit references and scoped data
transfer. Each agent receives only its assigned input, relevant task context,
necessary evidence references, and relevant memory results.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from jiro.log import get_logger
from jiro.orchestration.agents import AgentSpec, Capability
from jiro.orchestration.graph import GraphNode, GraphState, OrchestrationGraph

log = get_logger("jiro.orchestration.context")


@dataclass
class ContextPackage:
    """Controlled context package for an agent invocation."""

    agent_id: str
    node_id: str
    task_input: Dict[str, Any]
    relevant_memory: List[Dict[str, Any]]
    evidence_refs: List[str]
    parent_results: Dict[str, Any]
    tool_capabilities: List[str]
    budget_remaining: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "node_id": self.node_id,
            "task_input": self.task_input,
            "relevant_memory": self.relevant_memory,
            "evidence_refs": self.evidence_refs,
            "parent_results": self.parent_results,
            "tool_capabilities": self.tool_capabilities,
            "budget_remaining": self.budget_remaining,
            "metadata": self.metadata,
        }


class ContextManager:
    """Manages context assembly and distribution to agents.

    Ensures each agent receives only what it needs, preserves provenance,
    and prevents unauthorized context access.
    """

    def __init__(
        self,
        memory_service=None,  # Injected at runtime
        research_repository=None,  # Injected at runtime
        max_context_chars: int = 10000,
        max_memory_results: int = 20,
    ):
        self.memory_service = memory_service
        self.research_repository = research_repository
        self.max_context_chars = max_context_chars
        self.max_memory_results = max_memory_results

    async def assemble_context(
        self,
        agent: AgentSpec,
        node: GraphNode,
        graph: "OrchestrationGraph",
        state: GraphState,
        owner_key_id: Optional[str],
    ) -> ContextPackage:
        """Assemble a context package for an agent invocation."""
        # Get dependency results
        parent_results = {}
        evidence_refs = []

        for dep_id in node.depends_on:
            dep_state = state.nodes.get(dep_id)
            if dep_state and dep_state.output:
                parent_results[dep_id] = dep_state.output
                # Collect evidence refs from parent outputs
                if "evidence_refs" in dep_state.output:
                    evidence_refs.extend(dep_state.output["evidence_refs"])

        # Deduplicate evidence refs
        evidence_refs = list(dict.fromkeys(evidence_refs))

        # Retrieve relevant memory if agent has read capability
        relevant_memory = []
        if Capability.READ_MEMORY in agent.capabilities and self.memory_service:
            relevant_memory = await self._retrieve_relevant_memory(
                agent, node, state, owner_key_id
            )

        # Calculate budget remaining
        budget_remaining = self._calculate_budget_remaining(agent, state, node)

        return ContextPackage(
            agent_id=agent.agent_id,
            node_id=node.node_id,
            task_input=node.resolve_input({dep: state.nodes[dep].output for dep in node.depends_on}),
            relevant_memory=relevant_memory,
            evidence_refs=evidence_refs,
            parent_results=parent_results,
            tool_capabilities=[c.value for c in agent.capabilities],
            budget_remaining=budget_remaining,
            metadata={
                "assembled_at": time.time(),
                "graph_id": graph.graph_id,
                "execution_id": state.execution_id,
            },
        )

    async def _retrieve_relevant_memory(
        self,
        agent: AgentSpec,
        node: GraphNode,
        state: GraphState,
        owner_key_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant memories for the agent's task."""
        if not self.memory_service:
            return []

        try:
            # Build query from task input and node metadata
            query_parts = []
            task_input = node.resolve_input({
                dep: state.nodes[dep].output for dep in node.depends_on
            })

            if isinstance(task_input, dict):
                for key in ("question", "objective", "query", "claim"):
                    if key in task_input:
                        query_parts.append(str(task_input[key]))

            query = " ".join(query_parts)[:500]  # Limit query length

            if not query:
                return []

            # Use memory service to retrieve
            # This is a simplified interface - actual implementation would use
            # the memory service's retrieve method
            return []

        except Exception as e:
            log.warning("Failed to retrieve memory for agent %s: %s", agent.agent_id, e)
            return []

    def _calculate_budget_remaining(
        self,
        agent: AgentSpec,
        state: GraphState,
        node: GraphNode,
    ) -> Dict[str, Any]:
        """Calculate remaining budget for this node."""
        return {
            "max_model_calls": agent.budget.max_model_calls,
            "max_tokens": agent.budget.max_tokens,
            "max_duration_seconds": agent.budget.max_duration_seconds,
            "max_child_tasks": agent.budget.max_child_tasks,
            "max_depth": agent.budget.max_depth,
        }


class EvidenceTracker:
    """Tracks evidence provenance across agent boundaries.

    Ensures evidence references are preserved and citations remain valid
    as information flows between agents.
    """

    def __init__(self, research_repository=None):
        self.research_repository = research_repository
        self._evidence_cache: Dict[str, Dict[str, Any]] = {}

    def register_evidence(
        self,
        evidence_id: str,
        source_id: str,
        passage: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Register a piece of evidence with its provenance."""
        self._evidence_cache[evidence_id] = {
            "evidence_id": evidence_id,
            "source_id": source_id,
            "passage": passage,
            "metadata": metadata,
            "registered_at": time.time(),
        }

    def get_evidence(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve evidence by ID."""
        return self._evidence_cache.get(evidence_id)

    def validate_citations(
        self,
        citations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Validate that all citations resolve to registered evidence."""
        valid = []
        invalid = []

        for citation in citations:
            evidence_id = citation.get("evidence_id") or citation.get("id")
            if evidence_id and evidence_id in self._evidence_cache:
                valid.append(citation)
            else:
                invalid.append(citation)

        return {
            "valid": valid,
            "invalid": invalid,
            "all_valid": len(invalid) == 0,
        }

    def trace_provenance(self, evidence_id: str) -> List[Dict[str, Any]]:
        """Trace the provenance chain for a piece of evidence."""
        chain = []
        current = self._evidence_cache.get(evidence_id)

        while current:
            chain.append({
                "evidence_id": current["evidence_id"],
                "source_id": current["source_id"],
                "metadata": current["metadata"],
            })
            # In a full implementation, we'd follow source -> parent evidence links
            break

        return chain


class DisagreementDetector:
    """Detects and tracks disagreements between agent outputs."""

    def __init__(self):
        self._disagreements: List[Dict[str, Any]] = []

    def check_disagreement(
        self,
        node_id: str,
        agent_id: str,
        output: Dict[str, Any],
        previous_outputs: Mapping[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Check if this output disagrees with previous agent outputs.

        Returns list of detected disagreements.
        """
        disagreements = []

        # Compare claims
        claims = output.get("claims", [])
        if not isinstance(claims, list):
            claims = []

        for prev_node_id, prev_output in previous_outputs.items():
            prev_claims = prev_output.get("claims", [])
            if not isinstance(prev_claims, list):
                continue

            for claim in claims:
                for prev_claim in prev_claims:
                    disagreement = self._compare_claims(claim, prev_claim)
                    if disagreement:
                        disagreements.append({
                            "type": "claim_conflict",
                            "node_id": node_id,
                            "agent_id": agent_id,
                            "conflicts_with": prev_node_id,
                            "claim": claim,
                            "conflicting_claim": prev_claim,
                            "details": disagreement,
                        })

        # Store for tracking
        for d in disagreements:
            d["detected_at"] = time.time()
            self._disagreements.append(d)

        return disagreements

    def _compare_claims(
        self,
        claim1: Dict[str, Any],
        claim2: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Compare two claims for contradiction."""
        text1 = claim1.get("claim_text", "").lower().strip()
        text2 = claim2.get("claim_text", "").lower().strip()

        if not text1 or not text2:
            return None

        # Simple contradiction detection - would be enhanced with semantic comparison
        # Check for direct negation patterns
        negation_words = ["not", "no", "never", "false", "incorrect", "wrong", "untrue"]
        if any(text1.startswith(n + " ") for n in negation_words) and text2 in text1:
            return {"type": "direct_negation"}

        # Check for mutually exclusive assessments
        assessment1 = claim1.get("assessment", "")
        assessment2 = claim2.get("assessment", "")
        if assessment1 == "supported" and assessment2 == "contradicted":
            return {"type": "assessment_conflict", "assessment1": assessment1, "assessment2": assessment2}
        if assessment1 == "contradicted" and assessment2 == "supported":
            return {"type": "assessment_conflict", "assessment1": assessment1, "assessment2": assessment2}

        return None

    def get_all_disagreements(self) -> List[Dict[str, Any]]:
        return self._disagreements.copy()

    def clear(self) -> None:
        self._disagreements.clear()


__all__ = [
    "ContextManager",
    "ContextPackage",
    "DisagreementDetector",
    "EvidenceTracker",
]