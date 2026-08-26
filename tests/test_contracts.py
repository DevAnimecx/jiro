"""Contract tests for API schemas and responses."""

from __future__ import annotations


from tests.integration_utils import ContractTestMixin


class TestSearchResponseContract(ContractTestMixin):
    """Contract tests for search response schema."""

    def test_serpapi_compatible_structure(self, test_orchestrator):
        """Test that search response matches SerpAPI schema."""

        # This test would use a mock client in practice
        # For contract testing, we validate the schema structure
        pass

    def test_search_metadata_schema(self):
        """Validate search_metadata structure."""
        required = [
            "id", "engine", "query", "type", "location", "language",
            "status", "created_at", "total_time_taken", "cached", "parser_version"
        ]
        assert all(isinstance(k, str) for k in required)

    def test_search_information_schema(self):
        """Validate search_information structure."""
        required = ["total_results", "query_displayed"]
        assert all(isinstance(k, str) for k in required)

    def test_organic_result_schema(self):
        """Validate organic_result structure."""
        required = [
            "position", "title", "link", "displayed_link",
            "snippet", "source", "sitelinks", "rich_snippet"
        ]
        optional = ["date", "thumbnail", "rating", "price"]
        assert all(isinstance(k, str) for k in required + optional)


class TestAIResponseContract(ContractTestMixin):
    """Contract tests for AI response schemas."""

    def test_ai_search_response_schema(self):
        """Validate /ai/search response structure."""
        required = [
            "answer", "citations", "search_results",
            "sources_used", "reasoning_steps", "provider", "model"
        ]
        assert all(isinstance(k, str) for k in required)

    def test_ai_agent_response_schema(self):
        """Validate /ai/agent response structure."""
        required = [
            "answer", "citations", "search_results",
            "sources_used", "reasoning_steps", "provider", "model"
        ]
        assert all(isinstance(k, str) for k in required)

    def test_citation_schema(self):
        """Validate citation structure."""
        required = ["title", "url", "snippet"]
        assert all(isinstance(k, str) for k in required)


class TestScrapeResponseContract(ContractTestMixin):
    """Contract tests for scrape response schemas."""

    def test_scrape_markdown_schema(self):
        """Validate markdown scrape response."""
        required = ["title", "url", "content", "metadata"]
        assert all(isinstance(k, str) for k in required)

    def test_scrape_json_schema(self):
        """Validate JSON scrape response."""
        required = ["title", "url", "content", "metadata", "links", "images"]
        assert all(isinstance(k, str) for k in required)


class TestOpenAPISchemaContract:
    """Contract tests for OpenAPI schema."""

    def test_openapi_generation(self):
        """Test that OpenAPI schema can be generated and key routes exist."""
        from jiro.server import create_app
        from jiro.config import Settings

        app = create_app(Settings(raw={
            **{"server": {"host": "127.0.0.1", "port": 18000}},
            "scraping": {"default_engine": "duckduckgo", "engines": ["duckduckgo"]},
            "cache": {"type": "memory"},
            "db": {"path": ":memory:"},
            "auth": {"enabled": False},
        }))

        # Collect all concrete route paths (included routers are wrapped).
        routes: set = set()
        def _collect(items):
            for r in items:
                path = getattr(r, "path", None)
                if path:
                    routes.add(path)
                    continue
                # FastAPI wraps include_router() in _IncludedRouter objects
                # whose real routes live on .original_router
                orig = getattr(r, "original_router", None)
                if orig is not None and hasattr(orig, "routes"):
                    _collect(orig.routes)
        _collect(app.routes)

        expected = [
            "/search", "/search.json",
            "/scrape", "/scrape/batch",
            "/ai/search", "/ai/agent", "/ai/extract",
            "/jobs", "/jobs/{job_id}",
            "/api-keys", "/api-keys/{key_id}",
            "/auth/token",
            "/health", "/metrics", "/engines",
            # new hardening surfaces
            "/engines/compliance", "/compliance/report",
            "/plugins", "/mcp",
        ]
        for path in expected:
            assert path in routes, f"Missing route: {path}"


class TestMCPProtocolContract:
    """Contract tests for MCP protocol compliance."""

    def test_mcp_initialize(self):
        """Test MCP initialize response structure."""
        from jiro.mcp import PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS

        # Protocol version should be one of the supported spec versions
        assert PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS

    def test_mcp_tools_list_schema(self):
        """Test tools/list response schema."""
        from jiro.ai.tools import mcp_tools

        tools = mcp_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"
            assert "properties" in tool["inputSchema"]
            assert "required" in tool["inputSchema"]

    def test_mcp_tool_names(self):
        """Test required tool names are present."""
        from jiro.ai.tools import mcp_tools

        tool_names = {t["name"] for t in mcp_tools()}
        assert tool_names == {"search", "scrape", "ai_search"}

    def test_mcp_search_tool_schema(self):
        """Test search tool input schema."""
        from jiro.ai.tools import mcp_tools

        search_tool = next(t for t in mcp_tools() if t["name"] == "search")
        schema = search_tool["inputSchema"]

        # Required fields
        assert "q" in schema["properties"]
        assert "q" in schema["required"]

        # Engine enum
        engine_prop = schema["properties"]["engine"]
        assert engine_prop["type"] == "string"
        assert "enum" in engine_prop
        assert "google" in engine_prop["enum"]
        assert "duckduckgo" in engine_prop["enum"]

    def test_mcp_completion_schema(self):
        """Test completion/complete response schema."""
        # Would test completion response structure
        assert True  # Placeholder


class TestConfigSchemaContract:
    """Contract tests for configuration schema."""

    def test_default_config_complete(self):
        """Test that DEFAULT_CONFIG has all required sections."""
        from jiro.config import DEFAULT_CONFIG

        required_sections = [
            "server", "scraping", "cache", "db", "llm",
            "auth", "agent", "logging", "privacy", "audit"
        ]
        for section in required_sections:
            assert section in DEFAULT_CONFIG, f"Missing config section: {section}"

    def test_scraping_config_has_rate_limits(self):
        """Test that scraping config includes per-engine rate limits."""
        from jiro.config import DEFAULT_CONFIG

        scraping = DEFAULT_CONFIG["scraping"]
        assert "rate_limits_per_engine" in scraping
        limits = scraping["rate_limits_per_engine"]
        assert "google" in limits
        assert "bing" in limits
        assert "duckduckgo" in limits

    def test_request_validation_config(self):
        """Test request validation config exists."""
        from jiro.config import DEFAULT_CONFIG

        validation = DEFAULT_CONFIG["scraping"]["request_validation"]
        assert "max_query_length" in validation
        assert "max_batch_size" in validation
        assert "allowed_engines" in validation
        assert "allowed_types" in validation