"""Optional embedding provider adapter.

The core memory system never requires this module. Retrieval works fully
without it, using deterministic lexical ranking. When a provider *is* enabled,
this adapter is used to narrow candidates and to add a similarity signal.

Two traps this module exists to avoid
--------------------------------------

**The zero-vector trap.** :func:`jiro.search.embeddings.embed_texts` returns an
all-zero vector when ``sentence-transformers`` is not installed, rather than
raising. ``sentence-transformers`` is *not* a declared dependency of this
project, so on a default install that is exactly what happens. A cosine against
zero vectors is 0.0 for everything, which would silently degrade semantic
retrieval to "returns nothing" while reporting success.
:func:`_reject_degenerate` detects that case and refuses to use the vector.

**The mixed-space trap.** Vectors from different models do not share a vector
space. Every stored vector records its model and dimension, and
:meth:`EmbeddingAdapter.compatible` refuses to compare against a stored set
produced by a different model. A model change re-indexes rather than
silently mixing.
"""

from __future__ import annotations

import abc
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from jiro.log import get_logger
from jiro.memory.errors import MemoryProviderError

log = get_logger("jiro.memory.embeddings")

#: A vector whose L2 norm is below this is treated as degenerate. A real
#: embedding from any current model has a norm well above 1.
MIN_VECTOR_NORM = 1e-6


@dataclass(frozen=True)
class EmbeddingResult:
    """A single embedding plus the identity needed to interpret it."""

    vector: List[float]
    model: str
    dimensions: int

    def is_degenerate(self) -> bool:
        norm = math.sqrt(sum(x * x for x in self.vector))
        return norm < MIN_VECTOR_NORM


class EmbeddingProvider(abc.ABC):
    """A source of text vectors.

    Implementations must be explicit about model identity. A provider that
    cannot name its model cannot be used safely here.
    """

    @property
    @abc.abstractmethod
    def model_id(self) -> str:
        """Stable identity of the embedding model. Compared, never mixed."""

    @property
    @abc.abstractmethod
    def dimensions(self) -> int:
        """Vector width produced by this model."""

    @abc.abstractmethod
    async def embed(self, texts: Sequence[str]) -> List[EmbeddingResult]:
        """Embed a batch. Must raise rather than return degenerate vectors."""


@dataclass
class ProviderHealth:
    """Rolling availability, so a persistent provider failure is visible."""

    available: bool = True
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_success_at: Optional[float] = None
    disabled_reason: Optional[str] = None

    def record_success(self) -> None:
        self.available = True
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success_at = time.time()

    def record_failure(self, error: str, *, disable_after: int = 3) -> None:
        self.consecutive_failures += 1
        self.last_error = error[:500]
        if self.consecutive_failures >= disable_after:
            self.available = False
            self.disabled_reason = (
                f"disabled after {self.consecutive_failures} consecutive "
                f"failures: {error[:200]}"
            )


class LocalEmbeddingProvider(EmbeddingProvider):
    """Adapter over the project's existing :mod:`jiro.search.embeddings`.

    Reuses an existing local model rather than adding a dependency. Validates
    the returned vectors and refuses the documented zero-vector fallback.
    """

    def __init__(self, model_name: Optional[str] = None, *, dimensions: int = 384) -> None:
        self._model_name = model_name or None
        self._dimensions = dimensions
        self.health = ProviderHealth()

    @property
    def model_id(self) -> str:
        from jiro.search.embeddings import _MODEL_NAME

        return self._model_name or _MODEL_NAME

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> List[EmbeddingResult]:
        if not texts:
            return []
        try:
            from jiro.search.embeddings import embed_texts
        except ImportError as exc:  # pragma: no cover - module is part of the package
            raise MemoryProviderError(
                "embedding module unavailable") from exc

        try:
            vectors = await embed_texts(list(texts), self._model_name)
        except Exception as exc:
            self.health.record_failure(str(exc))
            raise MemoryProviderError(f"embedding failed: {exc}") from exc

        results: List[EmbeddingResult] = []
        for raw in vectors:
            vector = [float(x) for x in raw]
            result = EmbeddingResult(
                vector=vector,
                model=self.model_id,
                dimensions=len(vector),
            )
            if result.is_degenerate():
                # Almost certainly the ImportError fallback in embeddings.py.
                self.health.record_failure("provider returned a degenerate vector")
                raise MemoryProviderError(
                    "embedding provider returned a zero vector, which means the "
                    "model is not installed; install the 'recipes' extra or "
                    "disable memory.embedding"
                )
            results.append(result)

        if results and results[0].dimensions != self._dimensions:
            self.health.record_failure(
                f"dimension mismatch: expected {self._dimensions}, "
                f"got {results[0].dimensions}")
            raise MemoryProviderError(
                f"embedding dimension mismatch: expected {self._dimensions}, "
                f"got {results[0].dimensions}"
            )

        self.health.record_success()
        return results


@dataclass
class EmbeddingAdapter:
    """Thin wrapper adding batching, caching and failure tolerance.

    Vectorisation is never performed inside the memory write path. Doing so
    would make core memory writes depend on provider latency, which is exactly
    the coupling that makes a system fragile when the provider is down.
    """

    provider: Optional[EmbeddingProvider] = None
    batch_size: int = 32
    cache: Dict[str, List[float]] = field(default_factory=dict)
    cache_limit: int = 2048
    enabled: bool = True
    health: ProviderHealth = field(default_factory=ProviderHealth)

    @property
    def model_id(self) -> Optional[str]:
        return self.provider.model_id if self.provider else None

    @property
    def dimensions(self) -> Optional[int]:
        return self.provider.dimensions if self.provider else None

    def available(self) -> bool:
        """Whether a vector operation should be attempted at all."""
        return bool(self.enabled and self.provider is not None and self.health.available)

    def compatible(self, *, model: Optional[str], dimensions: Optional[int]) -> bool:
        """Whether stored vectors can be compared with ours.

        A mismatch on either model identity or dimension means the vectors live
        in different spaces. Comparing them anyway would produce meaningless
        scores, so the caller must fall back to lexical.
        """
        if not self.available():
            return False
        if self.model_id is None or model != self.model_id:
            return False
        if dimensions is not None and dimensions != self.dimensions:
            return False
        return True

    async def embed(self, texts: Sequence[str]) -> List[EmbeddingResult]:
        """Embed a batch, honouring the cache and never raising to the caller."""
        if not self.available():
            return []
        pending: List[str] = []
        pending_index: Dict[str, int] = {}
        for text in texts:
            if text in self.cache:
                continue
            if text not in pending_index:
                pending_index[text] = len(pending)
                pending.append(text)

        if pending:
            try:
                for offset in range(0, len(pending), self.batch_size):
                    chunk = pending[offset:offset + self.batch_size]
                    for text, result in zip(
                        chunk, await self.provider.embed(chunk)
                    ):
                        self.cache[text] = result.vector
                    self._trim_cache()
            except MemoryProviderError as exc:
                self.health.record_failure(str(exc))
                log.warning("embedding provider unavailable", extra={
                    "error": str(exc)[:200],
                    "fallback": "lexical",
                })
                return []

        results: List[EmbeddingResult] = []
        for text in texts:
            vector = self.cache.get(text)
            if vector is None:
                return []
            results.append(EmbeddingResult(
                vector=vector,
                model=self.provider.model_id,  # type: ignore[union-attr]
                dimensions=len(vector),
            ))
        return results

    async def embed_one(self, text: str) -> Optional[EmbeddingResult]:
        results = await self.embed([text])
        return results[0] if results else None

    def _trim_cache(self) -> None:
        if len(self.cache) <= self.cache_limit:
            return
        # Plain truncation: this cache is an accelerator, and eviction order
        # does not affect correctness.
        for key in list(self.cache)[: len(self.cache) - self.cache_limit]:
            self.cache.pop(key, None)

    def invalidate(self) -> None:
        self.cache.clear()


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity. Returns 0.0 for empty or length-mismatched vectors.

    Length mismatch returns 0.0 rather than raising: a mixed-space vector must
    degrade to "no signal", never to a crash or a misleading score.
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm < MIN_VECTOR_NORM or right_norm < MIN_VECTOR_NORM:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def build_adapter(
    config: Any, *, metrics: Optional[Any] = None
) -> EmbeddingAdapter:
    """Construct an adapter from validated configuration.

    Returns a disabled adapter when embedding is off or the provider cannot be
    constructed. Never raises: memory must work without this.
    """
    if not getattr(config, "embedding_enabled", False):
        return EmbeddingAdapter(
            provider=None, enabled=False,
            health=ProviderHealth(
                available=False, disabled_reason="disabled by configuration"),
        )
    try:
        provider = LocalEmbeddingProvider(
            config.embedding_model or None,
            dimensions=config.embedding_dimensions,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("embedding provider could not be constructed",
                    extra={"error": str(exc)[:200]})
        return EmbeddingAdapter(
            provider=None, enabled=False,
            health=ProviderHealth(
                available=False,
                disabled_reason=f"provider unavailable: {exc}"[:200]),
        )
    adapter = EmbeddingAdapter(provider=provider, enabled=True)
    if metrics is not None:
        _ = metrics
    return adapter


__all__ = [
    "MIN_VECTOR_NORM",
    "EmbeddingAdapter",
    "EmbeddingProvider",
    "EmbeddingResult",
    "LocalEmbeddingProvider",
    "ProviderHealth",
    "build_adapter",
    "cosine",
]