"""Retrieval tests: scope, ranking, filters, bounds and graceful degradation.

These exercise the service's public retrieval surface against real SQLite. The
optional embedding path is deliberately broken in several tests to prove the
core works without it.
"""

from __future__ import annotations

import time

import pytest

from jiro.memory import (
    MemoryScope,
    MemoryStatus,
    MemoryType,
    ScopeKind,
    Verification,
)
from jiro.memory.embeddings import (
    EmbeddingAdapter,
    EmbeddingResult,
    ProviderHealth,
    cosine,
)
from jiro.memory.errors import MemoryProviderError
from jiro.memory.ranking import WIDER_SCOPE_MATCH

from tests.conftest_memory import (  # noqa: F401  (fixture re-export)
    global_scope,
    memory_db,
    memory_outbox,
    memory_repo,
    memory_service,
    other_task_scope,
    owner_a,
    owner_b,
    source,
    task_scope,
)
from tests.conftest_memory import memory_config


class TestScopeIsolation:
    async def test_another_owner_cannot_retrieve(self, memory_service, task_scope):
        await memory_service.record_episode(
            {"event": "secret internal failure", "task_id": "task_1"},
            scope=task_scope)
        mine = await memory_service.retrieve("secret internal", scope=task_scope)
        theirs = await memory_service.retrieve(
            "secret internal", owner_key_id="key_b")
        assert len(mine) == 1
        assert theirs == [], "memory leaked across owners"

    async def test_another_scope_of_the_same_owner_is_not_returned(
        self, memory_service, task_scope, other_task_scope
    ):
        await memory_service.record_episode(
            {"event": "task one completed", "task_id": "task_1"},
            scope=task_scope)
        hits = await memory_service.retrieve("task one", scope=task_scope)
        assert all(h.record.scope.scope_id == "task_1" for h in hits)

    async def test_extra_scopes_widen_within_one_owner(
        self, memory_service, task_scope, global_scope
    ):
        await memory_service.record_episode(
            {"event": "shared knowledge about scraping"},
            scope=global_scope)
        without = await memory_service.retrieve(
            "shared knowledge", scope=task_scope)
        with_global = await memory_service.retrieve(
            "shared knowledge", scope=task_scope, extra_scopes=(global_scope,))
        assert len(without) == 0
        assert len(with_global) == 1

    async def test_extra_scopes_cannot_cross_owners(
        self, memory_service, task_scope
    ):
        foreign = MemoryScope(kind=ScopeKind.GLOBAL, scope_id="",
                              owner_key_id="key_b")
        await memory_service.record_episode(
            {"event": "another tenant's private note"},
            scope=foreign)
        hits = await memory_service.retrieve(
            "another tenant", scope=task_scope, extra_scopes=(foreign,))
        assert hits == [], "a scope from another owner widened access"


class TestRankingBehaviour:
    async def test_lexical_match_ranks_first(self, memory_service, task_scope):
        await memory_service.record_episode(
            {"event": "kubernetes ingress controller timed out"},
            scope=task_scope, importance=0.1)
        await memory_service.record_episode(
            {"event": "completely unrelated database migration"},
            scope=task_scope, importance=0.9)
        hits = await memory_service.retrieve("kubernetes ingress",
                                             scope=task_scope)
        assert hits[0].record.title != "completely unrelated database migration"
        assert "text" in hits[0].match_reason

    async def test_breakdown_is_populated_for_every_hit(self, memory_service,
                                                        task_scope):
        await memory_service.record_episode({"event": "e", "task_id": "t"},
                                            scope=task_scope)
        hits = await memory_service.retrieve("e", scope=task_scope)
        assert hits
        for hit in hits:
            assert set(hit.score_breakdown) >= {
                "text", "recency", "importance", "verification", "scope"}

    async def test_importance_breaks_a_tie(self, memory_service, task_scope):
        for name, importance in (("low", 0.1), ("high", 0.9)):
            await memory_service.record_episode(
                {"event": f"{name} priority event", "task_id": name},
                scope=task_scope, importance=importance, title=name)
        hits = await memory_service.retrieve("priority event",
                                             scope=task_scope)
        assert hits[0].record.title == "high"

    async def test_recency_signal_decays(self, memory_service, task_scope):
        fresh = await memory_service.record_episode(
            {"event": "recent occurrence", "task_id": "recent"}, scope=task_scope)
        stale = await memory_service.record_episode(
            {"event": "recent occurrence", "task_id": "stale"}, scope=task_scope)
        # Age the second one well past the configured half-life.
        old_ts = time.time() - 7200
        await memory_service.repo.update(
            stale[0].id, owner_key_id="key_a",
            changes={"updated_at": old_ts, "created_at": old_ts},
            now=old_ts)
        hits = {h.record.id: h for h in await memory_service.retrieve(
            "recent occurrence", scope=task_scope)}
        assert hits[fresh[0].id].score_breakdown["recency"] == \
            pytest.approx(1.0, abs=0.01)
        assert hits[stale[0].id].score_breakdown["recency"] < 0.3

    async def test_wider_scope_scores_below_exact(self, memory_service,
                                                  task_scope, global_scope):
        await memory_service.record_episode(
            {"event": "task scoped note"}, scope=task_scope)
        await memory_service.record_episode(
            {"event": "task scoped note"}, scope=global_scope)
        hits = await memory_service.retrieve(
            "task scoped note", scope=task_scope,
            extra_scopes=(global_scope,))
        exact = [h for h in hits if h.record.scope.kind is ScopeKind.TASK][0]
        wider = [h for h in hits if h.record.scope.kind is ScopeKind.GLOBAL][0]
        assert exact.score_breakdown["scope"] == 1.0
        assert wider.score_breakdown["scope"] == WIDER_SCOPE_MATCH

    async def test_verification_affects_score(self, memory_service, task_scope):
        observed = await memory_service.record_episode(
            {"event": "observed outage", "task_id": "a"}, scope=task_scope)
        inferred = await memory_service.record_fact(
            "service", "status", "degraded", scope=task_scope,
            verification=Verification.INFERRED, provenance=[source()])
        hits = {h.record.id: h for h in await memory_service.retrieve(
            "outage degraded", scope=task_scope, types=())}
        assert hits[observed[0].id].score_breakdown["verification"] > \
            hits[inferred[0].id].score_breakdown["verification"]


class TestFilters:
    async def test_type_filter(self, memory_service, task_scope):
        await memory_service.record_episode({"event": "an episode"},
                                            scope=task_scope)
        await memory_service.record_fact(
            "s", "p", "o", scope=task_scope, provenance=[source()])
        episodes = await memory_service.retrieve(
            "episode", scope=task_scope, types=(MemoryType.EPISODIC,))
        assert all(h.record.memory_type is MemoryType.EPISODIC for h in episodes)
        facts = await memory_service.retrieve(
            "episode", scope=task_scope, types=(MemoryType.SEMANTIC,))
        assert all(h.record.memory_type is MemoryType.SEMANTIC for h in facts)

    async def test_tag_filter_requires_all(self, memory_service, task_scope):
        await memory_service.record_episode(
            {"event": "tagged alpha event"}, scope=task_scope, tags=["alpha"])
        await memory_service.record_episode(
            {"event": "tagged alpha beta event"}, scope=task_scope,
            tags=["alpha", "beta"])
        hits = await memory_service.retrieve(
            "tagged", scope=task_scope, tags=["alpha", "beta"])
        assert len(hits) == 1

    async def test_time_range_filter(self, memory_service, task_scope):
        now = time.time()
        await memory_service.record_episode({"event": "recent window"},
                                            scope=task_scope)
        old = await memory_service.record_episode({"event": "ancient window"},
                                                   scope=task_scope)
        await memory_service.repo.update(
            old[0].id, owner_key_id="key_a", changes={"created_at": now - 86400},
            now=now)
        recent_only = await memory_service.retrieve(
            "window", scope=task_scope, created_after=now - 60)
        assert all(h.record.id != old[0].id for h in recent_only)

    async def test_expired_records_are_excluded(self, memory_service, task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "ephemeral note", "task_id": "t"}, scope=task_scope)
        # Force expiry without waiting for the sweep.
        await memory_service.repo.update(
            record.id, owner_key_id="key_a",
            changes={"expires_at": time.time() - 1}, now=time.time())
        assert await memory_service.retrieve("ephemeral", scope=task_scope) == []

    async def test_superseded_excluded_by_default(self, memory_service,
                                                   task_scope):
        older = await memory_service.record_episode(
            {"event": "version one claim", "task_id": "v1"}, scope=task_scope)
        newer = await memory_service.record_episode(
            {"event": "version two claim", "task_id": "v2"}, scope=task_scope)
        await memory_service.supersede(newer[0].id, older[0].id,
                                       scope=task_scope)
        default = await memory_service.retrieve("version claim",
                                                scope=task_scope)
        assert older[0].id not in {h.record.id for h in default}
        with_history = await memory_service.retrieve(
            "version claim", scope=task_scope, include_superseded=True)
        assert older[0].id in {h.record.id for h in with_history}

    async def test_limit_is_bounded_by_configuration(self, memory_service,
                                                      task_scope):
        for index in range(60):
            await memory_service.record_episode(
                {"event": f"bounded event {index}", "task_id": f"t{index}"},
                scope=task_scope)
        hits = await memory_service.retrieve("bounded event", scope=task_scope,
                                             limit=999)
        assert len(hits) <= memory_service.config.max_retrieval_limit

    async def test_zero_limit_rejected(self, memory_service, task_scope):
        from jiro.memory.errors import MemoryValidationError

        with pytest.raises(MemoryValidationError):
            await memory_service.retrieve("x", scope=task_scope, limit=0)


class TestProvenanceInResults:
    async def test_every_hit_carries_provenance(self, memory_service, task_scope):
        await memory_service.record_episode(
            {"event": "sourced event", "task_id": "t"}, scope=task_scope,
            provenance=[source("task", "task_9", method="observed")])
        hits = await memory_service.retrieve("sourced event", scope=task_scope)
        assert hits[0].provenance
        assert hits[0].provenance[0].source_id == "task_9"

    async def test_semantic_hit_includes_the_fact(self, memory_service,
                                                   task_scope):
        await memory_service.record_fact(
            "python", "created_by", "guido", scope=task_scope,
            provenance=[source()])
        hits = await memory_service.retrieve("python created", scope=task_scope)
        fact_hits = [h for h in hits if h.fact is not None]
        assert fact_hits and fact_hits[0].fact.subject == "python"

    async def test_procedural_hit_includes_the_procedure(self, memory_service,
                                                         task_scope):
        await memory_service.propose_procedure(
            {"name": "compare sources", "purpose": "compare two documents",
             "steps": [{"action": "search", "description": "find them"}]},
            scope=task_scope)
        hits = await memory_service.retrieve("compare sources", scope=task_scope)
        proc_hits = [h for h in hits if h.procedure is not None]
        assert proc_hits and proc_hits[0].procedure.name == "compare sources"


class TestContextAssembly:
    async def test_context_is_delimited_and_bounded(self, memory_service,
                                                     task_scope):
        for index in range(10):
            await memory_service.record_episode(
                {"event": f"context event number {index} with detail"},
                scope=task_scope)
        context = await memory_service.build_context("context event",
                                                     scope=task_scope)
        assert context.startswith("=== RECALLED MEMORY")
        assert context.endswith("=== END RECALLED MEMORY ===")
        assert "not instructions" in context

    async def test_context_respects_total_budget(self, memory_service,
                                                 task_scope):
        for index in range(30):
            await memory_service.record_episode(
                {"event": f"budget event {index} " + "x" * 400},
                scope=task_scope)
        context = await memory_service.build_context(
            "budget event", scope=task_scope, total_budget_chars=800)
        body = context.split("===")[2]
        assert len(body) <= 900, "context exceeded its budget"

    async def test_context_is_empty_when_nothing_matches(self, memory_service,
                                                          task_scope):
        assert await memory_service.build_context("nothing at all",
                                                  scope=task_scope) == ""


class TestEmbeddingDegradation:
    def test_cosine_guards(self):
        assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        # Length mismatch must degrade to no signal, never raise.
        assert cosine([1.0], [1.0, 0.0]) == 0.0
        assert cosine([], [1.0]) == 0.0

    def test_degenerate_vector_detected(self):
        from jiro.memory.embeddings import MIN_VECTOR_NORM

        zero = EmbeddingResult(vector=[0.0] * 8, model="m", dimensions=8)
        real = EmbeddingResult(vector=[0.1] * 8, model="m", dimensions=8)
        assert zero.is_degenerate()
        assert not real.is_degenerate()
        assert MIN_VECTOR_NORM > 0

    def test_zero_vector_from_missing_model_is_rejected(self):
        """The trap this module exists for: a silent all-zero fallback."""
        from jiro.memory.embeddings import LocalEmbeddingProvider
        import jiro.search.embeddings as embeddings_module

        async def fake_embed(texts, model_name=None):
            import numpy as np

            return np.zeros((len(texts), 384), dtype="float32")

        original = embeddings_module.embed_texts
        embeddings_module.embed_texts = fake_embed
        try:
            provider = LocalEmbeddingProvider()
            import asyncio

            with pytest.raises(MemoryProviderError) as exc:
                asyncio.run(provider.embed(["hello"]))
            assert "zero vector" in str(exc.value)
        finally:
            embeddings_module.embed_texts = original

    def test_dimension_mismatch_is_rejected(self):
        from jiro.memory.embeddings import LocalEmbeddingProvider
        import jiro.search.embeddings as embeddings_module
        import asyncio

        async def wrong_dim(texts, model_name=None):
            import numpy as np

            return np.ones((len(texts), 1536), dtype="float32")

        original = embeddings_module.embed_texts
        embeddings_module.embed_texts = wrong_dim
        try:
            provider = LocalEmbeddingProvider(dimensions=384)
            with pytest.raises(MemoryProviderError) as exc:
                asyncio.run(provider.embed(["hello"]))
            assert "dimension mismatch" in str(exc.value)
        finally:
            embeddings_module.embed_texts = original

    def test_incompatible_model_is_not_compared(self):
        class StubProvider:
            model_id = "model-b"
            dimensions = 4

            async def embed(self, texts):
                return [EmbeddingResult([1.0] * 4, "model-b", 4)
                        for _ in texts]

        adapter = EmbeddingAdapter(provider=StubProvider(), enabled=True)
        assert adapter.compatible(model="model-b", dimensions=4)
        assert not adapter.compatible(model="model-a", dimensions=4)
        assert not adapter.compatible(model="model-b", dimensions=1536)

    async def test_retrieval_works_with_a_broken_provider(self, memory_service,
                                                           task_scope):
        """Core memory must not depend on the optional vector path."""
        await memory_service.record_episode(
            {"event": "resilient retrieval event"}, scope=task_scope)

        class BrokenProvider:
            model_id = "broken"
            dimensions = 4

            async def embed(self, texts):
                raise MemoryProviderError("provider is down")

        memory_service._embeddings = EmbeddingAdapter(
            provider=BrokenProvider(), enabled=True,
            health=ProviderHealth(available=True))
        hits = await memory_service.retrieve("resilient retrieval",
                                             scope=task_scope)
        assert hits, "retrieval failed because the optional provider failed"
        # No record has a stored vector, so the provider is never consulted at
        # all: the lexical path is authoritative and complete.
        assert memory_service._embeddings.health.consecutive_failures == 0
        assert all("vector" not in h.score_breakdown for h in hits)

    async def test_embedding_disabled_by_default(self, memory_service):
        assert memory_service.stats()["embedding"]["enabled"] is False
        assert memory_service.stats()["embedding"]["reason"]

    async def test_embed_pending_is_a_noop_when_disabled(self, memory_service):
        result = await memory_service.embed_pending()
        assert result["embedded"] == 0
        assert result["reason"]

    async def test_vector_signal_blends_when_compatible(self, memory_service,
                                                        task_scope):
        await memory_service.record_episode(
            {"event": "vector blended event"}, scope=task_scope)
        record = (await memory_service.retrieve("vector blended",
                                               scope=task_scope))[0].record

        class StubProvider:
            model_id = "stub"
            dimensions = 4

            async def embed(self, texts):
                return [EmbeddingResult([0.1, 0.2, 0.3, 0.4], "stub", 4)
                        for _ in texts]

        memory_service._embeddings = EmbeddingAdapter(
            provider=StubProvider(), enabled=True,
            health=ProviderHealth(available=True))
        await memory_service.repo.set_embedding(
            record.id, vector=[0.1, 0.2, 0.3, 0.4], model="stub", dimensions=4,
            status=__import__("jiro.memory.types", fromlist=["x"])
            .EmbeddingStatus.READY)
        hits = await memory_service.retrieve("vector blended", scope=task_scope)
        assert hits and "vector" in hits[0].score_breakdown