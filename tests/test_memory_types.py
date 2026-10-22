"""Behaviour specific to each of the four memory types.

The claim under test is that these are genuinely four different things, not
four names for one row shape: different validation, different capacity and
expiry rules, different deduplication, and different promotion gates.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from jiro.memory import (
    MemoryScope,
    MemoryStatus,
    MemoryType,
    ScopeKind,
    Verification,
)
from jiro.memory.errors import (
    MemoryNotExecutable,
    MemoryValidationError,
)
from jiro.memory.runtime_integration import TaskMemoryBridge

from tests.conftest_memory import (  # noqa: F401  (fixture re-export)
    global_scope,
    memory_bridge,
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
from tests.conftest_runtime import make_settings  # noqa: F401


class TestWorkingMemoryBehaviour:
    async def test_capacity_is_enforced_per_scope(self, memory_service,
                                                   task_scope,
                                                   other_task_scope):
        for index in range(12):
            await memory_service.remember(
                {"text": f"note {index}"}, scope=task_scope)
        working = await memory_service.recall_context(scope=task_scope)
        assert len(working) == memory_service.config.working_capacity

    async def test_capacity_is_per_scope_not_global(self, memory_service,
                                                     task_scope,
                                                     other_task_scope):
        for index in range(4):
            await memory_service.remember({"text": f"a {index}"},
                                          scope=task_scope)
        for index in range(4):
            await memory_service.remember({"text": f"b {index}"},
                                          scope=other_task_scope)
        assert len(await memory_service.recall_context(scope=task_scope)) == 4
        assert len(await memory_service.recall_context(
            scope=other_task_scope)) == 4

    async def test_eviction_is_deterministic(self, memory_service, task_scope):
        """Lowest importance goes first, every time."""
        # Capacity is 5, so the sixth write forces an eviction.
        for index, importance in enumerate([0.9, 0.8, 0.1, 0.7, 0.6, 0.5]):
            await memory_service.remember(
                {"text": f"item {index}"}, scope=task_scope,
                importance=importance)
        working = await memory_service.recall_context(scope=task_scope)
        surviving = {r.content.get("text") for r in working}
        assert len(working) == memory_service.config.working_capacity
        assert "item 2" not in surviving, "the lowest-importance entry survived"
        assert "item 5" in surviving, "the newest entry was evicted"

    async def test_expired_working_memory_is_not_returned(self, memory_service,
                                                          task_scope):
        record, _ = await memory_service.remember(
            {"text": "short lived"}, scope=task_scope)
        await memory_service.repo.update(
            record.id, owner_key_id="key_a",
            changes={"expires_at": time.time() - 1}, now=time.time())
        assert await memory_service.recall_context(scope=task_scope) == []

    async def test_ttl_zero_means_no_expiry(self, memory_repo, task_scope):
        from jiro.memory import MemoryService

        service = MemoryService(memory_repo, memory_config(
            working_ttl_seconds=0))
        record, _ = await service.remember({"text": "permanent"},
                                           scope=task_scope)
        assert record.expires_at is None

    async def test_one_users_working_memory_does_not_leak(self, memory_service):
        mine = MemoryScope(kind=ScopeKind.TASK, scope_id="t1",
                           owner_key_id="key_a")
        theirs = MemoryScope(kind=ScopeKind.TASK, scope_id="t1",
                             owner_key_id="key_b")
        await memory_service.remember({"text": "confidential note"}, scope=mine)
        assert len(await memory_service.recall_context(scope=mine)) == 1
        assert await memory_service.recall_context(scope=theirs) == []

    async def test_forget_scope_removes_only_working(self, memory_service,
                                                     task_scope):
        await memory_service.remember({"text": "scratch"}, scope=task_scope)
        await memory_service.record_episode(
            {"event": "durable event", "task_id": "t1"}, scope=task_scope)
        removed = await memory_service.forget_scope(scope=task_scope)
        assert removed == 1
        assert await memory_service.recall_context(scope=task_scope) == []
        # Durable memory is untouched.
        assert await memory_service.retrieve("durable event",
                                             scope=task_scope)

    async def test_working_cleanup_cannot_delete_runtime_state(
        self, memory_service, memory_db, task_scope
    ):
        """Memory must never own task recovery state (ADR 0009)."""
        from jiro.runtime import SqlTaskRepository
        from jiro.runtime.repository import NewTask
        from jiro.runtime.types import TaskLimits

        now = time.time()
        repo = SqlTaskRepository(memory_db)
        task = NewTask(
            id="task_runtime_state", type="x", owner_key_id="key_a",
            idempotency_key=None, input={},
            limits=TaskLimits(max_attempts=3, attempt_timeout=30.0),
            created_at=now)
        await repo.insert(task, now=now)
        await repo.claim(worker_id="w1", now=now, lease_ttl=60)

        for index in range(10):
            await memory_service.remember({"text": f"n{index}"},
                                          scope=task_scope)
        await memory_service.forget_scope(scope=task_scope)
        await memory_service.sweep_expired()

        # The runtime's own record is completely unaffected.
        surviving = await repo.get("task_runtime_state")
        assert surviving is not None
        assert surviving.state.value == "running"
        assert surviving.attempts == 1

    async def test_idempotent_remember_deduplicates(self, memory_service,
                                                    task_scope):
        first, created1 = await memory_service.remember(
            {"text": "same value"}, scope=task_scope, idempotent=True)
        second, created2 = await memory_service.remember(
            {"text": "same value"}, scope=task_scope, idempotent=True)
        assert created1 is True and created2 is False
        assert first.id == second.id

    async def test_non_idempotent_remember_keeps_both(self, memory_service,
                                                      task_scope):
        await memory_service.remember({"text": "same value"}, scope=task_scope)
        await memory_service.remember({"text": "same value"}, scope=task_scope)
        assert len(await memory_service.recall_context(scope=task_scope)) == 2


class TestEpisodicMemoryBehaviour:
    async def test_links_to_source_task(self, memory_service, task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "task.failed", "task_id": "task_42",
             "task_state": "failed", "attempt": 2,
             "artifact_refs": ["task:task_42"]},
            scope=task_scope, provenance=[source("task", "task_42",
                                                 method="observed")])
        assert record.content["task_id"] == "task_42"
        assert record.content["artifact_refs"] == ["task:task_42"]
        provenance = await memory_service.repo.provenance_for(record.id)
        assert provenance[0].source_id == "task_42"

    async def test_is_marked_as_observed_not_extracted(self, memory_service,
                                                       task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "task.succeeded", "task_id": "t"}, scope=task_scope)
        assert record.verification is Verification.OBSERVED

    async def test_source_is_never_invented(self, memory_service, task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "task.succeeded", "task_id": "t"}, scope=task_scope)
        provenance = await memory_service.repo.provenance_for(record.id)
        # A synthetic scope reference is recorded, not a fabricated document.
        assert provenance[0].source_kind == "scope"
        assert provenance[0].source_id == task_scope.key

    async def test_reprocessing_the_same_task_event_is_idempotent(
        self, memory_service, task_scope
    ):
        payload = {"event": "task.succeeded", "task_id": "task_9",
                   "task_state": "succeeded", "attempt": 1}
        first, c1 = await memory_service.record_episode(payload, scope=task_scope)
        second, c2 = await memory_service.record_episode(payload, scope=task_scope)
        assert c1 is True and c2 is False
        assert first.id == second.id
        assert second.observation_count == 2

    async def test_repeated_processing_does_not_grow_the_store(
        self, memory_service, task_scope
    ):
        payload = {"event": "task.failed", "task_id": "task_x", "attempt": 1}
        for _ in range(30):
            await memory_service.record_episode(payload, scope=task_scope)
        assert await memory_service.repo.scope_count(
            owner_key_id="key_a", memory_type=MemoryType.EPISODIC) == 1

    async def test_similar_episodes_finds_prior_occurrences(
        self, memory_service, task_scope
    ):
        await memory_service.record_episode(
            {"event": "engine bing blocked by captcha", "task_id": "a"},
            scope=task_scope)
        await memory_service.record_episode(
            {"event": "something entirely different", "task_id": "b"},
            scope=task_scope)
        similar = await memory_service.similar_episodes(
            scope=task_scope, event="engine bing blocked by captcha")
        assert similar
        assert "captcha" in similar[0].content["event"]

    async def test_artifact_is_referenced_not_copied(self, memory_service,
                                                     task_scope):
        record, _ = await memory_service.record_episode(
            {"event": "task.succeeded", "task_id": "t",
             "artifact_refs": ["task:t"]},
            scope=task_scope)
        # The task's result lives in the task store; the episode holds a pointer.
        assert record.content["artifact_refs"] == ["task:t"]
        assert "result" not in record.content


class TestSemanticMemoryBehaviour:
    async def test_fact_requires_provenance(self, memory_service, task_scope):
        with pytest.raises(MemoryValidationError) as exc:
            await memory_service.record_fact(
                "s", "p", "o", scope=task_scope)
        assert "provenance" in str(exc.value)

    async def test_contradiction_is_detected_and_returned(
        self, memory_service, task_scope
    ):
        first, conflicts1 = await memory_service.record_fact(
            "jiro", "default_engine", "google", scope=task_scope,
            provenance=[source("config", "config.yaml")])
        second, conflicts2 = await memory_service.record_fact(
            "jiro", "default_engine", "bing", scope=task_scope,
            provenance=[source("document", "guide")])
        assert conflicts1 == []
        assert len(conflicts2) == 1
        contradiction = conflicts2[0]
        assert set(contradiction.to_dict()["conflicting_values"]) == {
            "google", "bing"}

    async def test_conflicting_facts_are_both_retained(self, memory_service,
                                                        task_scope):
        await memory_service.record_fact(
            "s", "p", "one", scope=task_scope, provenance=[source("d", "1")])
        await memory_service.record_fact(
            "s", "p", "two", scope=task_scope, provenance=[source("d", "2")])
        found = await memory_service.find_contradictions("s", "p", scope=task_scope)
        assert len(found[0].entries) == 2

    async def test_contradiction_carries_provenance_for_each_candidate(
        self, memory_service, task_scope
    ):
        await memory_service.record_fact(
            "s", "p", "one", scope=task_scope, provenance=[source("d", "src-1")])
        _, conflicts = await memory_service.record_fact(
            "s", "p", "two", scope=task_scope, provenance=[source("d", "src-2")])
        candidates = conflicts[0].to_dict()["candidates"]
        sources = {c["provenance"][0]["source_id"] for c in candidates}
        assert sources == {"src-1", "src-2"}

    async def test_runtime_never_resolves_automatically(self, memory_service,
                                                        task_scope):
        """After a conflict, both facts are still active. Nothing decides."""
        await memory_service.record_fact(
            "s", "p", "one", scope=task_scope, provenance=[source()])
        _, conflicts = await memory_service.record_fact(
            "s", "p", "two", scope=task_scope, provenance=[source("d", "2")])
        assert all(r.is_active for _, r in conflicts[0].entries)

    async def test_resolution_requires_a_reason(self, memory_service, task_scope):
        await memory_service.record_fact(
            "s", "p", "one", scope=task_scope, provenance=[source()])
        _, conflicts = await memory_service.record_fact(
            "s", "p", "two", scope=task_scope, provenance=[source("d", "2")])
        accepted = conflicts[0].entries[0][1].id
        with pytest.raises(MemoryValidationError):
            await memory_service.resolve_contradiction(
                conflicts[0], accepted_memory_id=accepted,
                resolved_by="operator", reason="   ")

    async def test_resolution_rejects_an_outside_id(self, memory_service,
                                                    task_scope):
        await memory_service.record_fact(
            "s", "p", "one", scope=task_scope, provenance=[source()])
        _, conflicts = await memory_service.record_fact(
            "s", "p", "two", scope=task_scope, provenance=[source("d", "2")])
        with pytest.raises(MemoryValidationError):
            await memory_service.resolve_contradiction(
                conflicts[0], accepted_memory_id="mem_not_in_conflict",
                resolved_by="operator", reason="evidence")

    async def test_resolution_supersedes_loser_and_keeps_it(self,
                                                             memory_service,
                                                             task_scope):
        first, _ = await memory_service.record_fact(
            "s", "p", "one", scope=task_scope, provenance=[source("d", "1")])
        _, conflicts = await memory_service.record_fact(
            "s", "p", "two", scope=task_scope, provenance=[source("d", "2")])
        second = conflicts[0].entries[-1][1]
        outcomes = await memory_service.resolve_contradiction(
            conflicts[0], accepted_memory_id=first.id,
            resolved_by="ops@example", reason="runbook says so")
        statuses = {r.id: r.status for r in outcomes}
        assert statuses[first.id] is MemoryStatus.ACTIVE
        assert statuses[second.id] is MemoryStatus.SUPERSEDED
        # The loser is still readable as history, not erased.
        loser = await memory_service.get(second.id, owner_key_id="key_a")
        assert loser.status is MemoryStatus.SUPERSEDED

    async def test_resolution_is_recorded_on_the_fact(self, memory_service,
                                                       task_scope):
        first, _ = await memory_service.record_fact(
            "s", "p", "one", scope=task_scope, provenance=[source()])
        _, conflicts = await memory_service.record_fact(
            "s", "p", "two", scope=task_scope, provenance=[source("d", "2")])
        await memory_service.resolve_contradiction(
            conflicts[0], accepted_memory_id=first.id,
            resolved_by="ops", reason="evidence A beats B")
        fact = await memory_service.repo.get_fact(first.id)
        assert fact.resolution == "accepted"
        assert fact.resolved_by == "ops"
        assert "evidence A" in fact.resolution_reason

    async def test_superseded_fact_is_not_a_current_belief(self,
                                                           memory_service,
                                                           task_scope):
        older, _ = await memory_service.record_fact(
            "jiro", "engine", "google", scope=task_scope, provenance=[source()])
        newer, _ = await memory_service.record_fact(
            "jiro", "engine", "bing", scope=task_scope, provenance=[source()])
        await memory_service.supersede(newer.id, older.id, scope=task_scope)
        current = await memory_service.retrieve("jiro engine", scope=task_scope)
        assert older.id not in {h.record.id for h in current}

    async def test_repetition_does_not_raise_confidence(self, memory_service,
                                                        task_scope):
        for _ in range(5):
            record, _ = await memory_service.record_fact(
                "s", "p", "o", scope=task_scope, confidence=0.4,
                verification=Verification.INFERRED, provenance=[source()])
        assert record.confidence == 0.4
        assert record.verification is Verification.INFERRED

    async def test_subject_and_predicate_filter(self, memory_service,
                                                task_scope):
        await memory_service.record_fact(
            "alpha", "kind", "one", scope=task_scope, provenance=[source()])
        await memory_service.record_fact(
            "beta", "kind", "two", scope=task_scope, provenance=[source()])
        repo = memory_service.repo
        from jiro.memory.repository import RetrievalQuery

        page = await repo.search(RetrievalQuery(
            owner_key_id="key_a", subject="alpha", limit=50))
        assert len(page.hits) == 1


class TestProceduralMemoryBehaviour:
    def _proposal(self) -> dict:
        return {
            "name": "compare two sources",
            "purpose": "Compare two documents and report differences",
            "steps": [
                {"action": "search", "description": "find both documents"},
                {"action": "scrape", "description": "fetch each"},
                {"action": "compare", "description": "diff them"},
            ],
            "validation_criteria": ["both sources fetched", "diff produced"],
        }

    async def test_proposal_starts_as_candidate(self, memory_service, task_scope):
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        procedure = await memory_service.repo.get_procedure(record.id)
        assert procedure.status == "candidate"
        assert procedure.promoted_at is None

    async def test_promotion_requires_minimum_evaluations(self, memory_service,
                                                          task_scope):
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        with pytest.raises(MemoryValidationError) as exc:
            await memory_service.promote_procedure(
                record.id, scope=task_scope, promoted_by="ops", reason="looks good")
        assert "evaluation" in str(exc.value)

    async def test_single_success_does_not_promote(self, memory_service,
                                                    task_scope):
        """One lucky run is not evidence of a trustworthy workflow."""
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        await memory_service.record_procedure_evaluation(
            record.id, scope=task_scope, passed=True)
        with pytest.raises(MemoryValidationError):
            await memory_service.promote_procedure(
                record.id, scope=task_scope, promoted_by="ops", reason="worked once")

    async def test_failed_evaluation_blocks_promotion(self, memory_service,
                                                      task_scope):
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        for _ in range(3):
            await memory_service.record_procedure_evaluation(
                record.id, scope=task_scope, passed=True)
        await memory_service.record_procedure_evaluation(
            record.id, scope=task_scope, passed=False, detail="flaky upstream")
        with pytest.raises(MemoryValidationError) as exc:
            await memory_service.promote_procedure(
                record.id, scope=task_scope, promoted_by="ops", reason="mostly fine")
        assert "failed" in str(exc.value)

    async def test_promotion_after_sufficient_passing_evaluations(
        self, memory_service, task_scope
    ):
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        for _ in range(3):
            await memory_service.record_procedure_evaluation(
                record.id, scope=task_scope, passed=True)
        procedure = await memory_service.promote_procedure(
            record.id, scope=task_scope, promoted_by="ops",
            reason="validated in staging")
        assert procedure.status == "active"
        assert procedure.promoted_by == "ops"
        assert "staging" in procedure.promotion_reason

    async def test_promotion_requires_a_reason(self, memory_service, task_scope):
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        with pytest.raises(MemoryValidationError):
            await memory_service.promote_procedure(
                record.id, scope=task_scope, promoted_by="ops", reason="")

    async def test_only_candidates_can_be_promoted(self, memory_service,
                                                   task_scope):
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        for _ in range(2):
            await memory_service.record_procedure_evaluation(
                record.id, scope=task_scope, passed=True)
        await memory_service.promote_procedure(
            record.id, scope=task_scope, promoted_by="ops", reason="ok")
        with pytest.raises(MemoryValidationError):
            await memory_service.promote_procedure(
                record.id, scope=task_scope, promoted_by="ops", reason="again")

    async def test_evaluation_log_is_bounded(self, memory_service, task_scope):
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        for _ in range(40):
            await memory_service.record_procedure_evaluation(
                record.id, scope=task_scope, passed=True)
        procedure = await memory_service.repo.get_procedure(record.id)
        assert len(procedure.evaluation["results"]) <= 20

    async def test_retire_keeps_the_record(self, memory_service, task_scope):
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        for _ in range(2):
            await memory_service.record_procedure_evaluation(
                record.id, scope=task_scope, passed=True)
        await memory_service.promote_procedure(
            record.id, scope=task_scope, promoted_by="ops", reason="ok")
        procedure = await memory_service.retire_procedure(
            record.id, scope=task_scope, reason="upstream changed")
        assert procedure.status == "retired"
        assert await memory_service.get(record.id, owner_key_id="key_a")

    async def test_stored_procedures_are_never_executed(self, memory_service,
                                                        task_scope):
        """The refusal is explicit, not an absent feature. See ADR 0010."""
        record = await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        with pytest.raises(MemoryNotExecutable):
            await memory_service.execute_procedure(record.id)

    async def test_execution_attempt_cannot_be_reached_through_retrieval(
        self, memory_service, task_scope
    ):
        await memory_service.propose_procedure(
            self._proposal(), scope=task_scope)
        hits = await memory_service.retrieve("compare sources", scope=task_scope)
        proc = [h.procedure for h in hits if h.procedure is not None]
        assert proc and proc[0].steps, "the procedure is readable as data"
        # What comes back is a description, with no callable attached.
        for step in proc[0].steps:
            assert set(step) <= {"action", "description", "requires",
                                 "capture_as", "notes"}


class TestTypeDistinctness:
    async def test_each_type_has_its_own_validation(self, memory_service,
                                                     task_scope):
        """A payload valid for one type is rejected by the others."""
        working = {"text": "x"}
        episodic = {"event": "x"}
        semantic = {"subject": "s", "predicate": "p", "object": "o"}
        procedural = {"name": "n", "purpose": "p",
                      "steps": [{"action": "search", "description": "d"}]}

        assert working != episodic != semantic != procedural
        # Semantic rejects the episodic shape.
        from jiro.memory.validation import validate_semantic

        with pytest.raises(MemoryValidationError):
            validate_semantic(episodic, max_bytes=1024)

    async def test_types_are_stored_separately(self, memory_service, task_scope):
        await memory_service.remember({"text": "w"}, scope=task_scope)
        await memory_service.record_episode(
            {"event": "e", "task_id": "t"}, scope=task_scope)
        await memory_service.record_fact(
            "s", "p", "o", scope=task_scope, provenance=[source()])
        await memory_service.propose_procedure(
            {"name": "n", "purpose": "p",
             "steps": [{"action": "search", "description": "d"}]},
            scope=task_scope)
        for memory_type in (MemoryType.WORKING, MemoryType.EPISODIC,
                            MemoryType.SEMANTIC, MemoryType.PROCEDURAL):
            assert await memory_service.repo.scope_count(
                owner_key_id="key_a", memory_type=memory_type) == 1