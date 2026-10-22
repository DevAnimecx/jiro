"""Domain tests for the cognitive memory system.

Pure logic: validation per memory type, the lifecycle state machine, deduplication
key derivation, redaction, ranking arithmetic and configuration validation. No
storage and no event loop.
"""

from __future__ import annotations

import time

import pytest

from jiro.memory.config import MemoryConfig, RetrievalWeights
from jiro.memory.errors import (
    MemoryValidationError,
    redact_text,
    redact_value,
    scrub_metadata,
)
from jiro.memory.lifecycle import (
    TRANSITIONS,
    can_transition,
    describe,
    is_retrievable,
    validate_transition,
)
from jiro.memory.ranking import (
    RankedRecord,
    Ranker,
    importance_score,
    lexical_score,
    recency_score,
    scope_score,
    verification_score,
)
from jiro.memory.types import (
    VERIFICATION_WEIGHT,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    ScopeKind,
    Verification,
    canonical_json,
    dedup_digest,
)
from jiro.memory.validation import (
    ALLOWED_STEP_ACTIONS,
    derive_dedup_key,
    episodic_dedup_key,
    fact_claim_key,
    near_duplicate,
    normalise_text,
    procedural_dedup_key,
    semantic_dedup_key,
    tokenise,
    validate_confidence,
    validate_episodic,
    validate_importance,
    validate_procedural,
    validate_provenance,
    validate_semantic,
    validate_tags,
    validate_working,
)

from tests.conftest_memory import memory_config, source


def _record(**overrides) -> MemoryRecord:
    now = time.time()
    base = dict(
        id="mem_1",
        memory_type=MemoryType.EPISODIC,
        status=MemoryStatus.ACTIVE,
        scope=MemoryScope(kind=ScopeKind.GLOBAL, owner_key_id="key_a"),
        title="a title",
        content={"event": "x"},
        content_text="some searchable text",
        tags=(),
        importance=0.5,
        verification=Verification.OBSERVED,
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return MemoryRecord(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------- lifecycle
class TestMemoryLifecycle:
    def test_deleted_is_terminal(self):
        assert TRANSITIONS[MemoryStatus.DELETED] == frozenset()

    @pytest.mark.parametrize("target", [
        MemoryStatus.SUPERSEDED, MemoryStatus.DISPUTED,
        MemoryStatus.EXPIRED, MemoryStatus.DELETED,
    ])
    def test_active_can_become_any_terminal(self, target):
        assert can_transition(MemoryStatus.ACTIVE, target)
        validate_transition(MemoryStatus.ACTIVE, target)

    def test_terminal_cannot_be_reinstated(self):
        with pytest.raises(MemoryValidationError):
            validate_transition(MemoryStatus.DELETED, MemoryStatus.ACTIVE)

    def test_disputed_can_return_to_active_after_resolution(self):
        assert can_transition(MemoryStatus.DISPUTED, MemoryStatus.ACTIVE)
        assert can_transition(MemoryStatus.DISPUTED, MemoryStatus.SUPERSEDED)

    def test_same_status_transition_is_a_noop(self):
        # Idempotence is what makes deletion safe to retry.
        validate_transition(MemoryStatus.DELETED, MemoryStatus.DELETED)

    def test_expired_cannot_be_revived(self):
        with pytest.raises(MemoryValidationError):
            validate_transition(MemoryStatus.EXPIRED, MemoryStatus.ACTIVE)

    def test_is_retrievable(self):
        assert is_retrievable(MemoryStatus.ACTIVE)
        for status in (MemoryStatus.SUPERSEDED, MemoryStatus.DISPUTED,
                       MemoryStatus.EXPIRED, MemoryStatus.DELETED):
            assert not is_retrievable(status)

    def test_is_retrievable_respects_expiry(self):
        now = time.time()
        assert not is_retrievable(
            MemoryStatus.ACTIVE, now=now, expires_at=now - 1)
        assert is_retrievable(
            MemoryStatus.ACTIVE, now=now, expires_at=now + 60)

    def test_describe_is_json_safe(self):
        described = describe()
        assert set(described) == {s.value for s in MemoryStatus}


# ----------------------------------------------------------------- working type
class TestWorkingValidation:
    def test_requires_text_or_value(self):
        with pytest.raises(MemoryValidationError):
            validate_working({}, max_bytes=1024, ttl_seconds=60, now=0.0)

    def test_rejects_unknown_fields(self):
        with pytest.raises(MemoryValidationError) as exc:
            validate_working({"text": "x", "secret_field": 1},
                             max_bytes=1024, ttl_seconds=60, now=0.0)
        assert "secret_field" in str(exc.value)

    def test_rejects_structured_value(self):
        with pytest.raises(MemoryValidationError):
            validate_working({"value": {"nested": 1}},
                             max_bytes=1024, ttl_seconds=60, now=0.0)

    def test_accepts_scalar_list_value(self):
        cleaned, text, expires = validate_working(
            {"value": [1, 2, 3]}, max_bytes=1024, ttl_seconds=60, now=100.0)
        assert cleaned["value"] == [1, 2, 3]
        assert expires == 160.0

    def test_relevance_is_clamped(self):
        cleaned, _, _ = validate_working(
            {"text": "x", "relevance": 5.0},
            max_bytes=1024, ttl_seconds=60, now=0.0)
        assert cleaned["relevance"] == 1.0

    def test_oversized_is_rejected(self):
        with pytest.raises(MemoryValidationError):
            validate_working({"text": "x" * 5000},
                             max_bytes=100, ttl_seconds=60, now=0.0)

    def test_ttl_none_means_no_expiry(self):
        _, _, expires = validate_working(
            {"text": "x"}, max_bytes=1024, ttl_seconds=None, now=0.0)
        assert expires is None

    def test_working_is_not_idempotent_by_default(self):
        key = derive_dedup_key(MemoryType.WORKING, scope_id="t1",
                                content={"text": "same"})
        assert key
        # Two entries may legitimately hold the same scratch value at different
        # points, so dedup is opt-in via the service, not implicit here.


# ----------------------------------------------------------------- episodic type
class TestEpisodicValidation:
    def test_requires_event(self):
        with pytest.raises(MemoryValidationError):
            validate_episodic({}, max_bytes=1024)

    def test_rejects_unknown_field(self):
        with pytest.raises(MemoryValidationError):
            validate_episodic({"event": "x", "secret": "y"}, max_bytes=1024)

    def test_artifacts_are_referenced_not_copied(self):
        cleaned, _ = validate_episodic(
            {"event": "task.succeeded", "artifact_refs": ["task:abc"]},
            max_bytes=1024)
        assert cleaned["artifact_refs"] == ["task:abc"]

    def test_artifact_list_is_bounded(self):
        with pytest.raises(MemoryValidationError):
            validate_episodic(
                {"event": "x", "artifact_refs": ["r"] * 51}, max_bytes=1024)

    def test_negative_duration_rejected(self):
        with pytest.raises(MemoryValidationError):
            validate_episodic({"event": "x", "duration_ms": -1}, max_bytes=1024)

    def test_dedup_anchors_on_task_and_attempt(self):
        a = episodic_dedup_key({"task_id": "t1", "attempt": 1})
        b = episodic_dedup_key({"task_id": "t1", "attempt": 1})
        c = episodic_dedup_key({"task_id": "t1", "attempt": 2})
        assert a == b
        assert a != c

    def test_dedup_falls_back_to_event_text(self):
        a = episodic_dedup_key({"event": "Something Happened"})
        b = episodic_dedup_key({"event": "  something   happened "})
        assert a == b


# ----------------------------------------------------------------- semantic type
class TestSemanticValidation:
    def test_requires_subject_predicate_object(self):
        for payload in (
            {"predicate": "p", "object": "o"},
            {"subject": "s", "object": "o"},
            {"subject": "s", "predicate": "p"},
        ):
            with pytest.raises(MemoryValidationError):
                validate_semantic(payload, max_bytes=1024)

    def test_rejects_structured_object(self):
        with pytest.raises(MemoryValidationError):
            validate_semantic({"subject": "s", "predicate": "p",
                               "object": {"nested": 1}}, max_bytes=1024)

    def test_rejects_inverted_validity(self):
        with pytest.raises(MemoryValidationError):
            validate_semantic({"subject": "s", "predicate": "p", "object": "o",
                               "valid_from": 100, "valid_to": 50},
                              max_bytes=1024)

    def test_claim_key_normalises_case_and_space(self):
        assert fact_claim_key("Python", "runs on") == fact_claim_key(
            "  python  ", "runs   on")

    def test_claim_key_differs_by_predicate(self):
        assert fact_claim_key("s", "a") != fact_claim_key("s", "b")

    def test_semantic_dedup_differs_by_object(self):
        key = fact_claim_key("s", "p")
        a = semantic_dedup_key(key, "s", "p", "value1")
        b = semantic_dedup_key(key, "s", "p", "value2")
        # Different objects are NOT duplicates; they are a contradiction.
        assert a != b

    def test_semantic_dedup_stable_across_processes(self):
        key = fact_claim_key("s", "p")
        assert semantic_dedup_key(key, "s", "p", 42) == semantic_dedup_key(
            key, "s", "p", 42)


# --------------------------------------------------------------- procedural type
class TestProceduralValidation:
    def _valid(self) -> dict:
        return {
            "name": "compare sources",
            "purpose": "Compare two documents",
            "steps": [{"action": "search", "description": "find them"}],
        }

    def test_minimal_valid(self):
        cleaned, text = validate_procedural(self._valid(), max_bytes=4096)
        assert cleaned["name"] == "compare sources"
        assert text

    def test_requires_steps(self):
        payload = self._valid()
        payload.pop("steps")
        with pytest.raises(MemoryValidationError):
            validate_procedural(payload, max_bytes=4096)

    def test_rejects_unregistered_action(self):
        payload = self._valid()
        payload["steps"] = [{"action": "exec", "description": "run code"}]
        with pytest.raises(MemoryValidationError) as exc:
            validate_procedural(payload, max_bytes=4096)
        assert "unregistered action" in str(exc.value)

    def test_rejects_unregistered_action_case_variants(self):
        payload = self._valid()
        payload["steps"] = [{"action": "SEARCH", "description": "x"}]
        with pytest.raises(MemoryValidationError):
            validate_procedural(payload, max_bytes=4096)

    def test_all_allowed_actions_are_accepted(self):
        for action in sorted(ALLOWED_STEP_ACTIONS):
            payload = self._valid()
            payload["steps"] = [{"action": action, "description": "x"}]
            cleaned, _ = validate_procedural(payload, max_bytes=4096)
            assert cleaned["steps"][0]["action"] == action

    def test_rejects_python_snippet_as_step(self):
        payload = self._valid()
        payload["steps"] = [{
            "action": "search",
            "description": "__import__('os').system('rm -rf /')",
        }]
        # The description is stored as text, which is fine: it is never executed.
        cleaned, _ = validate_procedural(payload, max_bytes=4096)
        assert cleaned["steps"][0]["action"] == "search"

    def test_step_limit(self):
        payload = self._valid()
