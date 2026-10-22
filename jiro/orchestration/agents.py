"""Agent contract and registry for bounded orchestration.

An agent definition is configuration, not an authorization boundary. Capabilities
are enforced at the actual tool-execution boundary by the tool dispatcher.

Agent definitions are validated at registration time and cannot grant themselves
additional permissions through their own output.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from jiro.log import get_logger
from jiro.runtime.validation import TaskSchema
from jiro.errors import ValidationError

log = get_logger("jiro.orchestration.agents")


class AgentRole(str, Enum):
    """Logical agent responsibilities. Roles may be combined in a single agent."""

    ORCHESTRATOR = "orchestrator"
    RESEARCHER = "researcher"
    ANALYST = "analyst"
    VERIFIER = "verifier"
    SYNTHESIZER = "synthesizer"
    EXECUTOR = "executor"


class AgentStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


class Capability(str, Enum):
    """Explicit tool/capability permissions. Enforced by the tool dispatcher."""

    SEARCH = "search"
    RETRIEVE = "retrieve"
    SCRAPE = "scrape"
    READ_MEMORY = "read_memory"
    WRITE_MEMORY = "write_memory"
    CREATE_RESEARCH = "create_research"
    GENERATE_REPORT = "generate_report"
    INTERNAL_API = "internal_api"
    EXTERNAL_ACTION = "external_action"
    VALIDATE_EVIDENCE = "validate_evidence"
    EXTRACT_CLAIMS = "extract_claims"


# Capability requirements per role (minimum baseline)
ROLE_BASE_CAPABILITIES: Dict[AgentRole, Set[Capability]] = {
    AgentRole.ORCHESTRATOR: {Capability.SEARCH, Capability.READ_MEMORY, Capability.INTERNAL_API},
    AgentRole.RESEARCHER: {Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE, Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
    AgentRole.ANALYST: {Capability.READ_MEMORY, Capability.WRITE_MEMORY, Capability.VALIDATE_EVIDENCE, Capability.EXTRACT_CLAIMS},
    AgentRole.VERIFIER: {Capability.VALIDATE_EVIDENCE, Capability.EXTRACT_CLAIMS, Capability.READ_MEMORY},
    AgentRole.SYNTHESIZER: {Capability.GENERATE_REPORT, Capability.READ_MEMORY, Capability.WRITE_MEMORY},
    AgentRole.EXECUTOR: {Capability.INTERNAL_API, Capability.EXTERNAL_ACTION, Capability.WRITE_MEMORY},
}


@dataclass(frozen=True)
class AgentBudget:
    """Per-invocation budget limits."""

    max_model_calls: int = 10
    max_tokens: int = 8000
    max_duration_seconds: float = 60.0
    max_child_tasks: int = 5
    max_depth: int = 3

    def with_overrides(self, **kwargs: Any) -> "AgentBudget":
        return replace(self, **kwargs)


@dataclass(frozen=True)
class AgentSpec:
    """Validated agent specification.

    This is the single source of truth for what an agent may do. The registry
    validates all fields and rejects unknown, disabled, or unauthorized agents.
    """

    agent_id: str
    name: str
    role: AgentRole
    description: str
    input_schema: TaskSchema
    output_schema: TaskSchema
    capabilities: Set[Capability]
    model_config: Dict[str, Any]  # Provider, model, temperature, etc.
    budget: AgentBudget
    timeout_seconds: float = 120.0
    max_retries: int = 2
    concurrency_limit: int = 3
    required_evidence: bool = False
    validation_criteria: Dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"
    status: AgentStatus = AgentStatus.ENABLED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        """Validate the agent spec after initialization."""
        if not self.agent_id or not self.agent_id.strip():
            raise ValidationError("agent_id is required")
        if not self.name or not self.name.strip():
            raise ValidationError("name is required")
        if not self.description or not self.description.strip():
            raise ValidationError("description is required")
        if not isinstance(self.input_schema, TaskSchema):
            raise ValidationError("input_schema must be a TaskSchema")
        if not isinstance(self.output_schema, TaskSchema):
            raise ValidationError("output_schema must be a TaskSchema")
        if not self.capabilities:
            raise ValidationError("at least one capability is required")
        if self.budget.max_model_calls <= 0:
            raise ValidationError("max_model_calls must be positive")
        if self.budget.max_duration_seconds <= 0:
            raise ValidationError("max_duration_seconds must be positive")
        if self.timeout_seconds <= 0:
            raise ValidationError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValidationError("max_retries cannot be negative")
        if self.concurrency_limit <= 0:
            raise ValidationError("concurrency_limit must be positive")
        if self.budget.max_child_tasks < 0:
            raise ValidationError("max_child_tasks cannot be negative")
        if self.budget.max_depth <= 0:
            raise ValidationError("max_depth must be positive")
        if "provider" not in self.model_config:
            raise ValidationError("model_config must include 'provider'")
        if "model" not in self.model_config:
            raise ValidationError("model_config must include 'model'")

        # Verify role capabilities are satisfied
        base_caps = ROLE_BASE_CAPABILITIES.get(self.role, set())
        missing = base_caps - self.capabilities
        if missing:
            raise ValidationError(
                f"Agent {self.agent_id} with role {self.role.value} missing required capabilities: {missing}"
            )

    @property
    def capability_hash(self) -> str:
        """Stable hash of capabilities for quick comparison."""
        caps = sorted(c.value for c in self.capabilities)
        return hashlib.sha256(json.dumps(caps, sort_keys=True).encode()).hexdigest()[:16]

    def can_delegate_to(self, target_role: AgentRole) -> bool:
        """Whether this agent role may delegate to another role.

        Orchestrator can delegate to any role. Other roles have restricted delegation.
        """
        if self.role == AgentRole.ORCHESTRATOR:
            return True
        # Researchers can delegate to analysts for evidence analysis
        if self.role == AgentRole.RESEARCHER and target_role in (AgentRole.ANALYST, AgentRole.VERIFIER):
            return True
        # Analysts can delegate to verifiers
        if self.role == AgentRole.ANALYST and target_role == AgentRole.VERIFIER:
            return True
        return False


class AgentRegistry:
    """Validates and stores agent definitions.

    Rejects unknown, disabled, or unauthorized agents. Agent definitions are
    immutable once registered; updates require a new version with explicit
    re-registration.
    """

    def __init__(self) -> None:
        self._agents: Dict[str, AgentSpec] = {}
        self._by_role: Dict[AgentRole, List[str]] = {r: [] for r in AgentRole}

    def register(self, spec: AgentSpec) -> None:
        """Register an agent after full validation.

        Raises:
            ValidationError: if the spec is invalid or conflicts with an existing agent.
        """
        self._validate_spec(spec)

        if spec.agent_id in self._agents:
            existing = self._agents[spec.agent_id]
            if existing.version == spec.version and existing.capability_hash == spec.capability_hash:
                log.debug("Agent %s already registered with same version and capabilities", spec.agent_id)
                return
            raise ValidationError(
                f"Agent {spec.agent_id} already registered with different definition",
                details={"existing_version": existing.version, "new_version": spec.version}
            )

        # Verify role capabilities are satisfied
        base_caps = ROLE_BASE_CAPABILITIES.get(spec.role, set())
        missing = base_caps - spec.capabilities
        if missing:
            raise ValidationError(
                f"Agent {spec.agent_id} with role {spec.role.value} missing required capabilities: {missing}"
            )

        self._agents[spec.agent_id] = spec
        self._by_role[spec.role].append(spec.agent_id)
        log.info("Registered agent %s (%s) with capabilities: %s",
                 spec.agent_id, spec.role.value, [c.value for c in spec.capabilities])

    def get(self, agent_id: str) -> Optional[AgentSpec]:
        """Get an agent by ID, only if enabled."""
        spec = self._agents.get(agent_id)
        if spec is None or spec.status != AgentStatus.ENABLED:
            return None
        return spec

    def get_by_role(self, role: AgentRole) -> List[AgentSpec]:
        """Get all enabled agents for a role."""
        return [self._agents[aid] for aid in self._by_role.get(role, []) if self._agents[aid].status == AgentStatus.ENABLED]

    def list_enabled(self) -> List[AgentSpec]:
        return [s for s in self._agents.values() if s.status == AgentStatus.ENABLED]

    def _validate_spec(self, spec: AgentSpec) -> None:
        # Validation is now done in AgentSpec.__post_init__
        # This method is kept for any additional registry-level validation
        pass

    def unregister(self, agent_id: str) -> bool:
        """Unregister an agent. Returns True if it existed."""
        spec = self._agents.pop(agent_id, None)
        if spec:
            self._by_role[spec.role] = [aid for aid in self._by_role[spec.role] if aid != agent_id]
            log.info("Unregistered agent %s", agent_id)
            return True
        return False


# Global registry instance
_agent_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    global _agent_registry
    if _agent_registry is None:
        _agent_registry = AgentRegistry()
        _register_builtin_agents(_agent_registry)
    return _agent_registry


def _register_builtin_agents(registry: AgentRegistry) -> None:
    """Register the built-in agent definitions."""
    from jiro.runtime.validation import Field, TaskSchema

    # Orchestrator
    registry.register(AgentSpec(
        agent_id="builtin.orchestrator",
        name="Orchestrator",
        role=AgentRole.ORCHESTRATOR,
        description="Manages workflow execution and delegates approved tasks to specialized agents",
        input_schema=TaskSchema("orchestrator_input", [
            Field("objective", "str", required=True, max_length=2000),
            Field("context", "dict", required=False),
            Field("constraints", "dict", required=False),
        ]),
        output_schema=TaskSchema("orchestrator_output", [
            Field("status", "str", required=True),
            Field("result", "dict", required=False),
            Field("delegations", "list", required=False),
            Field("evidence_refs", "list", required=False),
        ]),
        capabilities={
            Capability.SEARCH, Capability.RETRIEVE, Capability.READ_MEMORY,
            Capability.INTERNAL_API, Capability.CREATE_RESEARCH,
        },
        model_config={"provider": "default", "model": "default", "temperature": 0.2},
        budget=AgentBudget(max_model_calls=20, max_tokens=12000, max_duration_seconds=180.0,
                          max_child_tasks=10, max_depth=5),
        timeout_seconds=300.0,
        max_retries=1,
        concurrency_limit=2,
        required_evidence=False,
        validation_criteria={"must_produce_result": True},
    ))

    # Researcher
    registry.register(AgentSpec(
        agent_id="builtin.researcher",
        name="Researcher",
        role=AgentRole.RESEARCHER,
        description="Gathers sources and evidence through Jiro research capabilities",
        input_schema=TaskSchema("researcher_input", [
            Field("question", "str", required=True, max_length=1000),
            Field("subquestions", "list", required=False, item_type="str"),
            Field("budget", "dict", required=False),
            Field("exclusions", "list", required=False, item_type="str"),
        ]),
        output_schema=TaskSchema("researcher_output", [
            Field("research_id", "str", required=True),
            Field("sources_found", "int", required=False),
            Field("evidence_count", "int", required=False),
            Field("claims", "list", required=False),
            Field("gaps", "list", required=False),
        ]),
        capabilities={
            Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
            Capability.CREATE_RESEARCH, Capability.READ_MEMORY,
        },
        model_config={"provider": "default", "model": "default", "temperature": 0.3},
        budget=AgentBudget(max_model_calls=15, max_tokens=10000, max_duration_seconds=300.0,
                          max_child_tasks=3, max_depth=2),
        timeout_seconds=600.0,
        max_retries=2,
        concurrency_limit=3,
        required_evidence=True,
        validation_criteria={"min_sources": 1, "min_evidence": 1},
    ))

    # Analyst
    registry.register(AgentSpec(
        agent_id="builtin.analyst",
        name="Analyst",
        role=AgentRole.ANALYST,
        description="Evaluates structured evidence and identifies relationships or gaps",
        input_schema=TaskSchema("analyst_input", [
            Field("evidence_refs", "list", required=True, item_type="str"),
            Field("claims_to_analyze", "list", required=False),
            Field("analysis_type", "str", required=False),
        ]),
        output_schema=TaskSchema("analyst_output", [
            Field("findings", "list", required=True),
            Field("relationships", "list", required=False),
            Field("gaps_identified", "list", required=False),
            Field("confidence", "str", required=False),
        ]),
        capabilities={
            Capability.READ_MEMORY, Capability.WRITE_MEMORY,
            Capability.VALIDATE_EVIDENCE, Capability.EXTRACT_CLAIMS,
        },
        model_config={"provider": "default", "model": "default", "temperature": 0.2},
        budget=AgentBudget(max_model_calls=10, max_tokens=8000, max_duration_seconds=120.0,
                          max_child_tasks=2, max_depth=2),
        timeout_seconds=180.0,
        max_retries=2,
        concurrency_limit=4,
        required_evidence=True,
        validation_criteria={"must_have_findings": True},
    ))

    # Verifier
    registry.register(AgentSpec(
        agent_id="builtin.verifier",
        name="Verifier",
        role=AgentRole.VERIFIER,
        description="Checks claims against evidence and reports contradictions",
        input_schema=TaskSchema("verifier_input", [
            Field("claims", "list", required=True),
            Field("evidence_pool", "list", required=False, item_type="str"),
            Field("verification_level", "str", required=False),
        ]),
        output_schema=TaskSchema("verifier_output", [
            Field("verified_claims", "list", required=True),
            Field("rejected_claims", "list", required=True),
            Field("contradictions", "list", required=False),
            Field("insufficient_evidence", "list", required=False),
        ]),
        capabilities={
            Capability.VALIDATE_EVIDENCE, Capability.EXTRACT_CLAIMS, Capability.READ_MEMORY,
        },
        model_config={"provider": "default", "model": "default", "temperature": 0.1},
        budget=AgentBudget(max_model_calls=10, max_tokens=8000, max_duration_seconds=120.0,
                          max_child_tasks=1, max_depth=1),
        timeout_seconds=180.0,
        max_retries=1,
        concurrency_limit=4,
        required_evidence=True,
        validation_criteria={"no_unverified_claims": True},
    ))

    # Synthesizer
    registry.register(AgentSpec(
        agent_id="builtin.synthesizer",
        name="Synthesizer",
        role=AgentRole.SYNTHESIZER,
        description="Creates final results from validated intermediate outputs",
        input_schema=TaskSchema("synthesizer_input", [
            Field("verified_claims", "list", required=True),
            Field("evidence_map", "dict", required=False),
            Field("output_format", "str", required=True),
            Field("citation_style", "str", required=False),
        ]),
        output_schema=TaskSchema("synthesizer_output", [
            Field("report", "str", required=True),
            Field("citations", "list", required=True),
            Field("claim_count", "int", required=False),
            Field("unsupported_claims", "int", required=False),
        ]),
        capabilities={
            Capability.GENERATE_REPORT, Capability.READ_MEMORY, Capability.WRITE_MEMORY,
        },
        model_config={"provider": "default", "model": "default", "temperature": 0.3},
        budget=AgentBudget(max_model_calls=8, max_tokens=12000, max_duration_seconds=120.0,
                          max_child_tasks=0, max_depth=1),
        timeout_seconds=180.0,
        max_retries=1,
        concurrency_limit=2,
        required_evidence=False,
        validation_criteria={"all_citations_resolve": True},
    ))

    # Executor
    registry.register(AgentSpec(
        agent_id="builtin.executor",
        name="Executor",
        role=AgentRole.EXECUTOR,
        description="Performs explicitly registered, permitted operations",
        input_schema=TaskSchema("executor_input", [
            Field("action", "str", required=True),
            Field("parameters", "dict", required=False),
            Field("requires_approval", "bool", required=False),
        ]),
        output_schema=TaskSchema("executor_output", [
            Field("result", "dict", required=False),
            Field("status", "str", required=True),
            Field("side_effects", "list", required=False),
        ]),
        capabilities={
            Capability.INTERNAL_API, Capability.EXTERNAL_ACTION, Capability.WRITE_MEMORY,
        },
        model_config={"provider": "default", "model": "default", "temperature": 0.0},
        budget=AgentBudget(max_model_calls=5, max_tokens=4000, max_duration_seconds=60.0,
                          max_child_tasks=0, max_depth=1),
        timeout_seconds=120.0,
        max_retries=0,
        concurrency_limit=2,
        required_evidence=False,
        validation_criteria={"action_must_be_registered": True},
    ))


__all__ = [
    "AgentBudget",
    "AgentRegistry",
    "AgentRole",
    "AgentSpec",
    "AgentStatus",
    "Capability",
    "get_agent_registry",
    "ROLE_BASE_CAPABILITIES",
]