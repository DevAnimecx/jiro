"""Tests for the orchestration agent contract and registry."""

import pytest
import pytest_asyncio

from jiro.orchestration.agents import (
    AgentBudget,
    AgentRegistry,
    AgentRole,
    AgentSpec,
    AgentStatus,
    Capability,
    get_agent_registry,
    ROLE_BASE_CAPABILITIES,
)
from jiro.runtime.validation import Field, TaskSchema


class TestAgentSpec:
    def test_valid_agent_spec(self):
        """Test creating a valid agent spec."""
        spec = AgentSpec(
            agent_id="test.agent",
            name="Test Agent",
            role=AgentRole.RESEARCHER,
            description="A test agent",
            input_schema=TaskSchema("test_input", [
                Field("question", "str", required=True),
            ]),
            output_schema=TaskSchema("test_output", [
                Field("result", "str", required=True),
            ]),
            capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                          Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
            model_config={"provider": "test", "model": "test-model"},
            budget=AgentBudget(),
        )
        assert spec.agent_id == "test.agent"
        assert spec.role == AgentRole.RESEARCHER
        assert Capability.SEARCH in spec.capabilities

    def test_agent_spec_missing_required_capabilities_raises(self):
        """Test that missing base capabilities raises ValidationError."""
        from jiro.errors import ValidationError

        with pytest.raises(ValidationError, match="missing required capabilities"):
            AgentSpec(
                agent_id="test.agent",
                name="Test Agent",
                role=AgentRole.RESEARCHER,
                description="A test agent",
                input_schema=TaskSchema("test_input", [
                    Field("question", "str", required=True),
                ]),
                output_schema=TaskSchema("test_output", [
                    Field("result", "str", required=True),
                ]),
                capabilities={Capability.SEARCH},  # Missing RETRIEVE, SCRAPE, etc.
                model_config={"provider": "test", "model": "test-model"},
                budget=AgentBudget(),
            )

    def test_agent_spec_invalid_budget_raises(self):
        """Test that invalid budget raises ValidationError."""
        from jiro.errors import ValidationError

        with pytest.raises(ValidationError, match="max_model_calls must be positive"):
            AgentSpec(
                agent_id="test.agent",
                name="Test Agent",
                role=AgentRole.RESEARCHER,
                description="A test agent",
                input_schema=TaskSchema("test_input", [
                    Field("question", "str", required=True),
                ]),
                output_schema=TaskSchema("test_output", [
                    Field("result", "str", required=True),
                ]),
                capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                              Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
                model_config={"provider": "test", "model": "test-model"},
                budget=AgentBudget(max_model_calls=0),
            )

    def test_agent_spec_missing_model_config_raises(self):
        """Test that missing model config raises ValidationError."""
        from jiro.errors import ValidationError

        with pytest.raises(ValidationError, match="model_config must include"):
            AgentSpec(
                agent_id="test.agent",
                name="Test Agent",
                role=AgentRole.RESEARCHER,
                description="A test agent",
                input_schema=TaskSchema("test_input", [
                    Field("question", "str", required=True),
                ]),
                output_schema=TaskSchema("test_output", [
                    Field("result", "str", required=True),
                ]),
                capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                              Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
                model_config={},  # Missing provider and model
                budget=AgentBudget(),
            )

    def test_can_delegate_to(self):
        """Test delegation permission checks."""
        orchestrator = AgentSpec(
            agent_id="test.orchestrator",
            name="Orchestrator",
            role=AgentRole.ORCHESTRATOR,
            description="Orchestrates workflows",
            input_schema=TaskSchema("orchestrator_input", [
                Field("objective", "str", required=True),
            ]),
            output_schema=TaskSchema("orchestrator_output", [
                Field("status", "str", required=True),
            ]),
            capabilities={Capability.SEARCH, Capability.READ_MEMORY, Capability.INTERNAL_API},
            model_config={"provider": "test", "model": "test"},
            budget=AgentBudget(),
        )

        researcher = AgentSpec(
            agent_id="test.researcher",
            name="Researcher",
            role=AgentRole.RESEARCHER,
            description="Researches topics",
            input_schema=TaskSchema("researcher_input", [
                Field("question", "str", required=True),
            ]),
            output_schema=TaskSchema("researcher_output", [
                Field("result", "str", required=True),
            ]),
            capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                          Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
            model_config={"provider": "test", "model": "test"},
            budget=AgentBudget(),
        )

        # Orchestrator can delegate to any role
        assert orchestrator.can_delegate_to(AgentRole.RESEARCHER)
        assert orchestrator.can_delegate_to(AgentRole.ANALYST)
        assert orchestrator.can_delegate_to(AgentRole.VERIFIER)

        # Researcher can delegate to analyst and verifier
        assert researcher.can_delegate_to(AgentRole.ANALYST)
        assert researcher.can_delegate_to(AgentRole.VERIFIER)
        assert not researcher.can_delegate_to(AgentRole.ORCHESTRATOR)


class TestAgentRegistry:
    @pytest.fixture
    def registry(self):
        return AgentRegistry()

    def test_register_and_get(self, registry):
        """Test registering and retrieving an agent."""
        spec = AgentSpec(
            agent_id="test.agent",
            name="Test Agent",
            role=AgentRole.RESEARCHER,
            description="A test agent",
            input_schema=TaskSchema("test_input", [
                Field("question", "str", required=True),
            ]),
            output_schema=TaskSchema("test_output", [
                Field("result", "str", required=True),
            ]),
            capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                          Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
            model_config={"provider": "test", "model": "test-model"},
            budget=AgentBudget(),
        )
        registry.register(spec)

        retrieved = registry.get("test.agent")
        assert retrieved is not None
        assert retrieved.agent_id == "test.agent"

    def test_get_disabled_agent_returns_none(self, registry):
        """Test that disabled agents are not returned."""
        spec = AgentSpec(
            agent_id="test.agent",
            name="Test Agent",
            role=AgentRole.RESEARCHER,
            description="A test agent",
            input_schema=TaskSchema("test_input", [
                Field("question", "str", required=True),
            ]),
            output_schema=TaskSchema("test_output", [
                Field("result", "str", required=True),
            ]),
            capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                          Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
            model_config={"provider": "test", "model": "test-model"},
            budget=AgentBudget(),
            status=AgentStatus.DISABLED,
        )
        registry.register(spec)

        retrieved = registry.get("test.agent")
        assert retrieved is None

    def test_duplicate_registration_raises(self, registry):
        """Test that duplicate registration with different spec raises."""
        from jiro.errors import ValidationError

        spec1 = AgentSpec(
            agent_id="test.agent",
            name="Test Agent",
            role=AgentRole.RESEARCHER,
            description="A test agent",
            input_schema=TaskSchema("test_input", [
                Field("question", "str", required=True),
            ]),
            output_schema=TaskSchema("test_output", [
                Field("result", "str", required=True),
            ]),
            capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                          Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
            model_config={"provider": "test", "model": "test-model"},
            budget=AgentBudget(),
        )
        registry.register(spec1)

        # Same version and capabilities - should be idempotent
        registry.register(spec1)

        # Different version - should raise
        spec2 = AgentSpec(
            agent_id="test.agent",
            name="Test Agent v2",
            role=AgentRole.RESEARCHER,
            description="A test agent v2",
            input_schema=TaskSchema("test_input", [
                Field("question", "str", required=True),
            ]),
            output_schema=TaskSchema("test_output", [
                Field("result", "str", required=True),
            ]),
            capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                          Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
            model_config={"provider": "test", "model": "test-model"},
            budget=AgentBudget(),
            version="2.0",
        )
        with pytest.raises(ValidationError, match="already registered with different definition"):
            registry.register(spec2)

    def test_get_by_role(self, registry):
        """Test getting agents by role."""
        spec1 = AgentSpec(
            agent_id="test.researcher1",
            name="Researcher 1",
            role=AgentRole.RESEARCHER,
            description="First researcher",
            input_schema=TaskSchema("researcher_input", [
                Field("question", "str", required=True),
            ]),
            output_schema=TaskSchema("researcher_output", [
                Field("result", "str", required=True),
            ]),
            capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                          Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
            model_config={"provider": "test", "model": "test-model"},
            budget=AgentBudget(),
        )
        spec2 = AgentSpec(
            agent_id="test.analyst1",
            name="Analyst 1",
            role=AgentRole.ANALYST,
            description="First analyst",
            input_schema=TaskSchema("analyst_input", [
                Field("evidence_refs", "list", required=True, item_type="str"),
            ]),
            output_schema=TaskSchema("analyst_output", [
                Field("findings", "list", required=True, item_type="str"),
            ]),
            capabilities={Capability.READ_MEMORY, Capability.WRITE_MEMORY,
                          Capability.VALIDATE_EVIDENCE, Capability.EXTRACT_CLAIMS},
            model_config={"provider": "test", "model": "test-model"},
            budget=AgentBudget(),
        )
        registry.register(spec1)
        registry.register(spec2)

        researchers = registry.get_by_role(AgentRole.RESEARCHER)
        analysts = registry.get_by_role(AgentRole.ANALYST)

        assert len(researchers) == 1
        assert len(analysts) == 1
        assert researchers[0].agent_id == "test.researcher1"
        assert analysts[0].agent_id == "test.analyst1"

    def test_unregister(self, registry):
        """Test unregistering an agent."""
        spec = AgentSpec(
            agent_id="test.agent",
            name="Test Agent",
            role=AgentRole.RESEARCHER,
            description="A test agent",
            input_schema=TaskSchema("test_input", [
                Field("question", "str", required=True),
            ]),
            output_schema=TaskSchema("test_output", [
                Field("result", "str", required=True),
            ]),
            capabilities={Capability.SEARCH, Capability.RETRIEVE, Capability.SCRAPE,
                          Capability.CREATE_RESEARCH, Capability.READ_MEMORY},
            model_config={"provider": "test", "model": "test-model"},
            budget=AgentBudget(),
        )
        registry.register(spec)
        assert registry.get("test.agent") is not None

        result = registry.unregister("test.agent")
        assert result is True
        assert registry.get("test.agent") is None

        # Unregistering non-existent returns False
        result = registry.unregister("test.agent")
        assert result is False


class TestBuiltinAgents:
    def test_builtin_agents_registered(self):
        """Test that built-in agents are registered."""
        registry = get_agent_registry()

        # Should have all 6 built-in agents
        assert registry.get("builtin.orchestrator") is not None
        assert registry.get("builtin.researcher") is not None
        assert registry.get("builtin.analyst") is not None
        assert registry.get("builtin.verifier") is not None
        assert registry.get("builtin.synthesizer") is not None
        assert registry.get("builtin.executor") is not None

    def test_builtin_agent_roles(self):
        """Test that built-in agents have correct roles."""
        registry = get_agent_registry()

        assert registry.get("builtin.orchestrator").role == AgentRole.ORCHESTRATOR
        assert registry.get("builtin.researcher").role == AgentRole.RESEARCHER
        assert registry.get("builtin.analyst").role == AgentRole.ANALYST
        assert registry.get("builtin.verifier").role == AgentRole.VERIFIER
        assert registry.get("builtin.synthesizer").role == AgentRole.SYNTHESIZER
        assert registry.get("builtin.executor").role == AgentRole.EXECUTOR

    def test_builtin_agent_capabilities(self):
        """Test that built-in agents have expected capabilities."""
        registry = get_agent_registry()

        orchestrator = registry.get("builtin.orchestrator")
        assert Capability.SEARCH in orchestrator.capabilities
        assert Capability.INTERNAL_API in orchestrator.capabilities

        researcher = registry.get("builtin.researcher")
        assert Capability.SEARCH in researcher.capabilities
        assert Capability.SCRAPE in researcher.capabilities
        assert Capability.CREATE_RESEARCH in researcher.capabilities

        executor = registry.get("builtin.executor")
        assert Capability.EXTERNAL_ACTION in executor.capabilities
        assert Capability.INTERNAL_API in executor.capabilities


class TestRoleBaseCapabilities:
    def test_all_roles_have_base_capabilities(self):
        """Test that all roles have defined base capabilities."""
        for role in AgentRole:
            assert role in ROLE_BASE_CAPABILITIES
            assert len(ROLE_BASE_CAPABILITIES[role]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])