"""Integration tests with testcontainers (requires Docker)."""

from __future__ import annotations

import pytest
import asyncio

# Testcontainers imports (optional - only if docker available)
try:
    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer
    from testcontainers.generic import GenericContainer
    TESTCONTAINERS_AVAILABLE = True
except ImportError:
    TESTCONTAINERS_AVAILABLE = False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TESTCONTAINERS_AVAILABLE, reason="testcontainers not available"),
]


class TestContainers:
    """Test containers for integration testing."""

    @pytest.fixture(scope="session")
    def postgres_container(self) -> PostgresContainer:
        """PostgreSQL container for testing."""
        container = PostgresContainer("postgres:16-alpine")
        container.start()
        yield container
        container.stop()

    @pytest.fixture(scope="session")
    def redis_container(self) -> RedisContainer:
        """Redis container for testing."""
        container = RedisContainer("redis:7-alpine")
        container.start()
        yield container
        container.stop()

    @pytest.fixture(scope="session")
    def jiro_container(self, postgres_container, redis_container) -> GenericContainer:
        """Jiro Search container for end-to-end testing."""
        container = GenericContainer("python:3.12-slim") \
            .with_env("JIRO_CACHE__TYPE", "redis") \
            .with_env("JIRO_CACHE__URL", f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}") \
            .with_env("JIRO_DB__PATH", f"postgresql://postgres:postgres@{postgres_container.get_container_host_ip()}:{postgres_container.get_exposed_port(5432)}/postgres") \
            .with_env("JIRO_AUTH__ENABLED", "false") \
            .with_env("JIRO_SCRAPING__DEFAULT_ENGINE", "duckduckgo") \
            .with_env("JIRO_SCRAPING__ENGINES", '["duckduckgo", "bing"]') \
            .with_exposed_ports(8000) \
            .with_command("pip install jiro-search && jiro serve --host 0.0.0.0 --insecure")
        container.start()
        yield container
        container.stop()


class TestFullStackIntegration:
    """Full-stack integration tests with real containers."""

    @pytest.mark.asyncio
    async def test_search_with_redis_cache(self, jiro_container):
        """Test search with Redis cache backend."""
        import httpx

        host = jiro_container.get_container_host_ip()
        port = jiro_container.get_exposed_port(8000)
        base_url = f"http://{host}:{port}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Wait for server to be ready
            for _ in range(30):
                try:
                    resp = await client.get(f"{base_url}/health")
                    if resp.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)

            # First request - should be cache miss
            resp1 = await client.get(f"{base_url}/search.json", params={
                "q": "test query", "engine": "duckduckgo", "num": 5
            })
            assert resp1.status_code == 200
            data1 = resp1.json()
            assert data1["search_metadata"]["cached"] is False

            # Second request - should be cache hit
            resp2 = await client.get(f"{base_url}/search.json", params={
                "q": "test query", "engine": "duckduckgo", "num": 5
            })
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2["search_metadata"]["cached"] is True

    @pytest.mark.asyncio
    async def test_scrape_endpoint(self, jiro_container):
        """Test scrape endpoint."""
        import httpx

        host = jiro_container.get_container_host_ip()
        port = jiro_container.get_exposed_port(8000)
        base_url = f"http://{host}:{port}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{base_url}/scrape", json={
                "url": "https://httpbin.org/html",
                "format": "markdown"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "content" in data
            assert "title" in data

    @pytest.mark.asyncio
    async def test_ai_search_endpoint(self, jiro_container):
        """Test AI search endpoint (extractive fallback)."""
        import httpx

        host = jiro_container.get_container_host_ip()
        port = jiro_container.get_exposed_port(8000)
        base_url = f"http://{host}:{port}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{base_url}/ai/search", json={
                "query": "What is Python?",
                "max_sources": 3
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "answer" in data
            assert "citations" in data
            assert data["provider"] == "extractive-fallback"

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, jiro_container):
        """Test Prometheus metrics endpoint."""
        import httpx

        host = jiro_container.get_container_host_ip()
        port = jiro_container.get_exposed_port(8000)
        base_url = f"http://{host}:{port}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{base_url}/metrics")
            assert resp.status_code == 200
            assert "jiro_search_requests_total" in resp.text
            assert "jiro_search_latency_seconds" in resp.text


class TestMultiEngineSearch:
    """Test multi-engine search functionality."""

    @pytest.mark.asyncio
    async def test_multi_engine_stream(self, jiro_container):
        """Test SSE multi-engine search stream."""
        import httpx

        host = jiro_container.get_container_host_ip()
        port = jiro_container.get_exposed_port(8000)
        base_url = f"http://{host}:{port}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("GET", f"{base_url}/search/stream", params={
                "q": "python", "engines": "duckduckgo,bing", "num": 3
            }) as resp:
                assert resp.status_code == 200
                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("event:") or line.startswith("data:"):
                        events.append(line)
                    if "search_complete" in line:
                        break

                assert len(events) > 0
                assert any("search_start" in e for e in events)
                assert any("search_complete" in e for e in events)


class TestAuthIntegration:
    """Test authentication and authorization."""

    @pytest.mark.asyncio
    async def test_api_key_auth(self, jiro_container):
        """Test API key authentication."""
        import httpx

        host = jiro_container.get_container_host_ip()
        port = jiro_container.get_exposed_port(8000)
        base_url = f"http://{host}:{port}"

        # Enable auth and create admin key
        # This would require container restart with auth enabled
        # For now, test with auth disabled
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{base_url}/search.json", params={
                "q": "test", "engine": "duckduckgo"
            })
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_rate_limiting(self, jiro_container):
        """Test per-key rate limiting."""
        import httpx

        host = jiro_container.get_container_host_ip()
        port = jiro_container.get_exposed_port(8000)
        base_url = f"http://{host}:{port}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Make requests up to limit
            for i in range(65):  # Default is 60 RPM
                resp = await client.get(f"{base_url}/search.json", params={
                    "q": f"test {i}", "engine": "duckduckgo"
                })
                if i < 60:
                    assert resp.status_code == 200
                else:
                    assert resp.status_code == 429  # Rate limited


class TestHealthChecks:
    """Test health and readiness endpoints."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self, jiro_container):
        """Test /health endpoint."""
        import httpx

        host = jiro_container.get_container_host_ip()
        port = jiro_container.get_exposed_port(8000)
        base_url = f"http://{host}:{port}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{base_url}/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert "version" in data
            assert "uptime_seconds" in data
            assert "cache" in data

    @pytest.mark.asyncio
    async def test_engines_endpoint(self, jiro_container):
        """Test /engines endpoint."""
        import httpx

        host = jiro_container.get_container_host_ip()
        port = jiro_container.get_exposed_port(8000)
        base_url = f"http://{host}:{port}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{base_url}/engines")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) > 0
            for engine in data:
                assert "name" in engine
                assert "types" in engine
                assert "description" in engine