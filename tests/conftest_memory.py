"""Shared fixtures for the cognitive memory test suite.

Every fixture uses a real SQLite file in ``tmp_path`` and the real
:class:`~jiro.memory.sqlite_repo.SqlMemoryRepository`. No memory test substitutes
an in-memory fake for persistence.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional

import pytest
import pytest_asyncio

from jiro.config import DEFAULT_CONFIG, Settings
from jiro.db import Database
from jiro.memory import (
    MemoryConfig,
    MemoryOutbox,
    MemoryScope,
    MemoryService,
    ScopeKind,
    SqlMemoryRepository,
)
from jiro.memory.runtime_integration import TaskMemoryBridge

#: Small, fast limits. Production defaults live in ``DEFAULT_CONFIG``.
TEST_MEMORY_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "working_capacity": 5,
    "working_ttl_seconds": 300,
    "episodic_retention_days": 0,
    "semantic_retention_days": 0,
    "procedural_retention_days": 0,
    "max_content_bytes": 4096,
    "max_memories_per_scope": 200,
    "default_retrieval_limit": 10,
    "max_retrieval_limit": 50,
    "context_budget_chars": 1000,
    "max_context_memories": 10,
    "recency_half_life_seconds": 3600.0,
    "retrieval": {
        "weights": {
            "text": 0.55,
            "recency": 0.15,
            "importance": 0.10,
            "verification": 0.12,
            "scope": 0.08,
        },
    },
    "embedding": {"enabled": False, "model": "", "dimensions": 384},
    "outbox": {
        "enabled": True,
        "drain_interval_seconds": 0.05,
        "max_attempts": 3,
        "batch_size": 20,
    },
    "auto_episodes": True,
    "promotion_min_evaluations": 2,
}


def make_memory_settings(
    tmp_path: Any = None, *, db_path: Optional[str] = None,
    memory: Optional[Dict[str, Any]] = None,
) -> Settings:
    """Hermetic Settings built on the project's own defaults."""
    raw = copy.deepcopy(DEFAULT_CONFIG)
    _deep_update(raw["memory"], copy.deepcopy(TEST_MEMORY_CONFIG))
    if memory:
        _deep_update(raw["memory"], memory)
    base = tmp_path if tmp_path is not None else "memory"
    raw["db"]["path"] = db_path or f"{base}/memory.db"
    raw["auth"]["enabled"] = False
    raw["logging"]["level"] = "critical"
    return Settings(raw=raw)


def _deep_update(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def memory_config(**overrides: Any) -> MemoryConfig:
    """Build a validated MemoryConfig directly, without a Settings object."""
    raw = copy.deepcopy(TEST_MEMORY_CONFIG)
    for dotted, value in overrides.items():
        node = raw
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    settings = Settings(raw=copy.deepcopy(DEFAULT_CONFIG))
    settings.raw["memory"] = raw
    return MemoryConfig.from_settings(settings)


@pytest_asyncio.fixture
async def memory_db(tmp_path) -> Any:
    database = Database(str(tmp_path / "memory.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def memory_repo(memory_db) -> Any:
    repo = SqlMemoryRepository(memory_db)
    await repo.ensure_search_index()
    return repo


@pytest_asyncio.fixture
async def memory_service(memory_repo) -> Any:
    service = MemoryService(memory_repo, memory_config())
    await service.initialize()
    return service


@pytest_asyncio.fixture
async def memory_outbox(memory_repo) -> Any:
    return MemoryOutbox(memory_repo, memory_config())


@pytest_asyncio.fixture
async def memory_bridge(memory_service, memory_outbox) -> Any:
    return TaskMemoryBridge(memory_service, memory_outbox)


@pytest.fixture
def owner_a() -> Optional[str]:
    return "key_a"


@pytest.fixture
def owner_b() -> Optional[str]:
    return "key_b"


@pytest.fixture
def task_scope(owner_a) -> MemoryScope:
    return MemoryScope(kind=ScopeKind.TASK, scope_id="task_1",
                       owner_key_id=owner_a)


@pytest.fixture
def other_task_scope(owner_a) -> MemoryScope:
    return MemoryScope(kind=ScopeKind.TASK, scope_id="task_2",
                       owner_key_id=owner_a)


@pytest.fixture
def global_scope(owner_a) -> MemoryScope:
    return MemoryScope(kind=ScopeKind.GLOBAL, owner_key_id=owner_a)


def source(kind: str = "document", source_id: str = "doc1", **kwargs: Any):
    """Build a Provenance record without repeating the required fields."""
    from jiro.memory.types import Provenance

    return Provenance(
        source_kind=kind, source_id=source_id,
        method=kwargs.pop("method", "extracted"),
        source_url=kwargs.pop("source_url", None),
        **kwargs,
    )


__all__ = [
    "TEST_MEMORY_CONFIG",
    "make_memory_settings",
    "memory_bridge",
    "memory_config",
    "memory_db",
    "memory_outbox",
    "memory_repo",
    "memory_service",
    "source",
]