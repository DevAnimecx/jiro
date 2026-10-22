"""Memory configuration, loaded from ``jiro.config.Settings``.

Settings live under ``memory:`` in the existing config tree, so they inherit
Jiro's established precedence: defaults → YAML → ``JIRO_MEMORY__*`` environment
overrides.

Every limit is validated at construction. An invalid value raises
:class:`~jiro.memory.errors.MemoryValidationError` immediately rather than
failing later inside a worker or a request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from jiro.config import Settings
from jiro.log import get_logger
from jiro.memory.errors import MemoryValidationError

log = get_logger("jiro.memory.config")

CONFIG_SECTION = "memory"

#: Absolute ceilings. Configuration may request less, never more.
MAX_CONTENT_BYTES_CEILING = 1024 * 1024
MAX_WORKING_CAPACITY_CEILING = 2000
MAX_RETRIEVAL_LIMIT_CEILING = 500
MAX_RETENTION_DAYS_CEILING = 3650
MAX_CONTEXT_BUDGET_CEILING = 200_000


def _get(section: Mapping[str, Any], key: str, default: Any) -> Any:
    value = section.get(key, default)
    return default if value is None else value


@dataclass(frozen=True)
class RetrievalWeights:
    """Relative contribution of each ranking signal.

    Documented and explicit rather than tuned or hidden. Each weight is a
    multiplier on a signal that is already normalised to ``[0, 1]``; the final
    score is their weighted sum. No term is scaled by an unexplained factor.

    Attributes:
        text: lexical relevance to the query.
        recency: how recently the memory was created or updated.
        importance: the author-supplied importance weight on the record.
        verification: how the content came to be believed.
        scope: how closely the record's scope matches the requesting scope.
    """

    text: float = 0.55
    recency: float = 0.15
    importance: float = 0.10
    verification: float = 0.12
    scope: float = 0.08

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "recency": self.recency,
            "importance": self.importance,
            "verification": self.verification,
            "scope": self.scope,
        }

    def validate(self) -> None:
        for name, value in self.as_dict().items():
            if value < 0 or value > 1:
                raise MemoryValidationError(
                    f"memory.retrieval.weights.{name} must be within [0, 1]",
                    details={"value": value},
                )
        if sum(self.as_dict().values()) <= 0:
            raise MemoryValidationError(
                "memory.retrieval.weights must not all be zero"
            )


@dataclass(frozen=True)
class MemoryConfig:
    """Validated memory limits.

    Attributes:
        enabled: when false the service still constructs and can read, but
            refuses writes. Retrieval of already-stored memory keeps working.
        working_capacity: maximum working entries per scope. Eviction is
            deterministic (lowest importance, then oldest).
        working_ttl_seconds: working-memory lifetime. ``0`` disables expiry.
        episodic_retention_days: ``0`` keeps episodes indefinitely.
        semantic_retention_days: ``0`` keeps facts indefinitely.
        procedural_retention_days: ``0`` keeps procedures indefinitely.
        max_content_bytes: largest searchable projection stored.
        max_memories_per_scope: hard ceiling per (owner, scope).
        default_retrieval_limit / max_retrieval_limit.
        context_budget_chars: default per-memory prompt budget.
        max_context_memories: how many memories may be placed into one context.
        recency_half_life_seconds: used by the recency signal.
        weights: ranking weights.
        embedding_enabled: opt-in vector retrieval.
        embedding_model: empty means "the project's configured model".
        embedding_dimensions: expected vector width. A mismatch is refused
            rather than compared.
        embedding_fail_open: lexical retrieval always works; this controls only
            whether a provider error is recorded as a metric.
        outbox_enabled: durable deferred writes for runtime integration.
        outbox_drain_interval_seconds.
        outbox_max_attempts.
        outbox_batch_size.
        auto_episodes: whether terminal tasks produce episodic records.
        promotion_min_evaluations: gates a procedure promotion.
    """

    enabled: bool = True
    working_capacity: int = 50
    working_ttl_seconds: float = 3600.0
    episodic_retention_days: int = 90
    semantic_retention_days: int = 0
    procedural_retention_days: int = 0
    max_content_bytes: int = 65536
    max_memories_per_scope: int = 2000
    default_retrieval_limit: int = 10
    max_retrieval_limit: int = 100
    context_budget_chars: int = 2000
    max_context_memories: int = 20
    recency_half_life_seconds: float = 604800.0
    weights: RetrievalWeights = RetrievalWeights()
    embedding_enabled: bool = False
    embedding_model: str = ""
    embedding_dimensions: int = 384
    embedding_fail_open: bool = True
    outbox_enabled: bool = True
    outbox_drain_interval_seconds: float = 5.0
    outbox_max_attempts: int = 5
    outbox_batch_size: int = 50
    auto_episodes: bool = True
    promotion_min_evaluations: int = 2

    @classmethod
    def from_settings(cls, settings: Settings) -> "MemoryConfig":
        section = settings.get(CONFIG_SECTION, {}) or {}
        if not isinstance(section, Mapping):
            raise MemoryValidationError("memory config section must be a mapping")

        raw_weights = section.get("retrieval", {}) or {}
        weights_raw = raw_weights.get("weights", {}) if isinstance(
            raw_weights, Mapping) else {}
        weights = RetrievalWeights(**{
            field_name: weights_raw[field_name]
            for field_name in RetrievalWeights().as_dict()
            if isinstance(weights_raw, Mapping) and field_name in weights_raw
        })

        cfg = cls(
            enabled=bool(_get(section, "enabled", True)),
            working_capacity=int(_get(section, "working_capacity", 50)),
            working_ttl_seconds=float(_get(section, "working_ttl_seconds", 3600.0)),
            episodic_retention_days=int(
                _get(section, "episodic_retention_days", 90)),
            semantic_retention_days=int(
                _get(section, "semantic_retention_days", 0)),
            procedural_retention_days=int(
                _get(section, "procedural_retention_days", 0)),
            max_content_bytes=int(_get(section, "max_content_bytes", 65536)),
            max_memories_per_scope=int(
                _get(section, "max_memories_per_scope", 2000)),
            default_retrieval_limit=int(
                _get(section, "default_retrieval_limit", 10)),
            max_retrieval_limit=int(
                _get(section, "max_retrieval_limit", 100)),
            context_budget_chars=int(
                _get(section, "context_budget_chars", 2000)),
            max_context_memories=int(
                _get(section, "max_context_memories", 20)),
            recency_half_life_seconds=float(
                _get(section, "recency_half_life_seconds", 604800.0)),
            weights=weights,
            embedding_enabled=bool(
                _get(section.get("embedding", {}) or {}, "enabled", False)),
            embedding_model=str(
                _get(section.get("embedding", {}) or {}, "model", "")),
            embedding_dimensions=int(
                _get(section.get("embedding", {}) or {}, "dimensions", 384)),
            embedding_fail_open=bool(
                _get(section.get("embedding", {}) or {}, "fail_open", True)),
            outbox_enabled=bool(_get(section, "outbox", {}).get("enabled", True)
                                if isinstance(section.get("outbox"), Mapping)
                                else True),
            outbox_drain_interval_seconds=float(
                _get(section.get("outbox", {}) or {}, "drain_interval_seconds", 5.0)),
            outbox_max_attempts=int(
                _get(section.get("outbox", {}) or {}, "max_attempts", 5)),
            outbox_batch_size=int(
                _get(section.get("outbox", {}) or {}, "batch_size", 50)),
            auto_episodes=bool(_get(section, "auto_episodes", True)),
            promotion_min_evaluations=int(
                _get(section, "promotion_min_evaluations", 2)),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Fail fast on an unusable configuration."""
        if not 1 <= self.working_capacity <= MAX_WORKING_CAPACITY_CEILING:
            raise MemoryValidationError(
                f"memory.working_capacity must be within "
                f"[1, {MAX_WORKING_CAPACITY_CEILING}]",
                details={"value": self.working_capacity},
            )
        if self.working_ttl_seconds < 0:
            raise MemoryValidationError(
                "memory.working_ttl_seconds must be >= 0 (0 disables expiry)",
                details={"value": self.working_ttl_seconds},
            )
        for name in ("episodic_retention_days", "semantic_retention_days",
                     "procedural_retention_days"):
            value = getattr(self, name)
            if not 0 <= value <= MAX_RETENTION_DAYS_CEILING:
                raise MemoryValidationError(
                    f"memory.{name} must be within [0, {MAX_RETENTION_DAYS_CEILING}]",
                    details={"value": value},
                )
        if not 256 <= self.max_content_bytes <= MAX_CONTENT_BYTES_CEILING:
            raise MemoryValidationError(
                f"memory.max_content_bytes must be within "
                f"[256, {MAX_CONTENT_BYTES_CEILING}]",
                details={"value": self.max_content_bytes},
            )
        if self.max_memories_per_scope < 1:
            raise MemoryValidationError(
                "memory.max_memories_per_scope must be >= 1",
                details={"value": self.max_memories_per_scope},
            )
        if not 1 <= self.default_retrieval_limit <= self.max_retrieval_limit:
            raise MemoryValidationError(
                "memory.default_retrieval_limit must be within "
                "[1, memory.max_retrieval_limit]",
                details={
                    "default": self.default_retrieval_limit,
                    "max": self.max_retrieval_limit,
                },
            )
        if self.max_retrieval_limit > MAX_RETRIEVAL_LIMIT_CEILING:
            raise MemoryValidationError(
                f"memory.max_retrieval_limit must be <= "
                f"{MAX_RETRIEVAL_LIMIT_CEILING}",
                details={"value": self.max_retrieval_limit},
            )
        if not 100 <= self.context_budget_chars <= MAX_CONTEXT_BUDGET_CEILING:
            raise MemoryValidationError(
                f"memory.context_budget_chars must be within "
                f"[100, {MAX_CONTEXT_BUDGET_CEILING}]",
                details={"value": self.context_budget_chars},
            )
        if not 1 <= self.max_context_memories <= 200:
            raise MemoryValidationError(
                "memory.max_context_memories must be within [1, 200]",
                details={"value": self.max_context_memories},
            )
        if self.recency_half_life_seconds <= 0:
            raise MemoryValidationError(
                "memory.recency_half_life_seconds must be > 0",
                details={"value": self.recency_half_life_seconds},
            )
        self.weights.validate()
        if self.embedding_dimensions < 8:
            raise MemoryValidationError(
                "memory.embedding.dimensions must be >= 8",
                details={"value": self.embedding_dimensions},
            )
        if self.outbox_drain_interval_seconds <= 0:
            raise MemoryValidationError(
                "memory.outbox.drain_interval_seconds must be > 0",
                details={"value": self.outbox_drain_interval_seconds},
            )
        if not 1 <= self.outbox_max_attempts <= 20:
            raise MemoryValidationError(
                "memory.outbox.max_attempts must be within [1, 20]",
                details={"value": self.outbox_max_attempts},
            )
        if not 1 <= self.outbox_batch_size <= 1000:
            raise MemoryValidationError(
                "memory.outbox.batch_size must be within [1, 1000]",
                details={"value": self.outbox_batch_size},
            )
        if self.promotion_min_evaluations < 1:
            raise MemoryValidationError(
                "memory.promotion_min_evaluations must be >= 1",
                details={"value": self.promotion_min_evaluations},
            )

        if self.working_capacity > self.max_memories_per_scope:
            log.warning(
                "memory.working_capacity exceeds max_memories_per_scope; the "
                "per-scope ceiling will bind first",
                extra={
                    "working_capacity": self.working_capacity,
                    "max_memories_per_scope": self.max_memories_per_scope,
                },
            )

    def retention_seconds(self, memory_type: str) -> Optional[float]:
        """Retention window in seconds for a memory type, or ``None`` for forever."""
        days = {
            "episodic": self.episodic_retention_days,
            "semantic": self.semantic_retention_days,
            "procedural": self.procedural_retention_days,
        }.get(memory_type, 0)
        return days * 86400 if days else None


__all__ = [
    "CONFIG_SECTION",
    "MAX_CONTENT_BYTES_CEILING",
    "MAX_RETRIEVAL_LIMIT_CEILING",
    "MAX_WORKING_CAPACITY_CEILING",
    "MemoryConfig",
    "RetrievalWeights",
]