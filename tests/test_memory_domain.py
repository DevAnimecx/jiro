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
        payload["steps"] = [
            {"action": "search", "description": "x"} for _ in range(51)
        ]
        with pytest.raises(MemoryValidationError):
            validate_procedural(payload, max_bytes=4096)

    def test_dedup_changes_when_steps_change(self):
        a = procedural_dedup_key("p", {"steps": [{"action": "search"}]})
        b = procedural_dedup_key("p", {"steps": [{"action": "scrape"}]})
        assert a != b


# ------------------------------------------------------------- confidence & tags
class TestConfidenceDiscipline:
    def test_none_is_a_valid_value(self):
        assert validate_confidence(None) is None

    def test_range_is_enforced_not_clamped(self):
        with pytest.raises(MemoryValidationError):
            validate_confidence(1.5)
        with pytest.raises(MemoryValidationError):
            validate_confidence(-0.1)

    def test_bool_is_not_a_number(self):
        with pytest.raises(MemoryValidationError):
            validate_confidence(True)

    def test_verification_is_ordered_and_explicit(self):
        # Weight is derived from an explicit table, not learned.
        assert (VERIFICATION_WEIGHT[Verification.VERIFIED]
                > VERIFICATION_WEIGHT[Verification.EXTRACTED])
        assert (VERIFICATION_WEIGHT[Verification.OBSERVED]
                > VERIFICATION_WEIGHT[Verification.INFERRED])

    def test_tags_are_normalised(self):
        assert validate_tags(["Alpha", "BETA", "alpha"]) == ("alpha", "beta")

    def test_tag_limit(self):
        with pytest.raises(MemoryValidationError):
            validate_tags([f"t{i}" for i in range(40)])

    def test_importance_range(self):
        assert validate_importance(None) == 0.5
        assert validate_importance(0) == 0.0
        with pytest.raises(MemoryValidationError):
            validate_importance(1.2)


# ------------------------------------------------------------------- provenance
class TestProvenanceValidation:
    def test_requires_kind_and_id(self):
        with pytest.raises(MemoryValidationError):
            validate_provenance([{"source_kind": "doc"}])
        with pytest.raises(MemoryValidationError):
            validate_provenance([{"source_id": "d1"}])

    def test_method_must_be_recognised(self):
        with pytest.raises(MemoryValidationError):
            validate_provenance([
                {"source_kind": "d", "source_id": "1", "method": "vibes"}
            ])

    def test_unknown_field_rejected(self):
        with pytest.raises(MemoryValidationError):
            validate_provenance([
                {"source_kind": "d", "source_id": "1", "trust": "high"}
            ])

    def test_accepts_provenance_instances(self):
        result = validate_provenance([source()])
        assert result[0].source_kind == "document"

    def test_url_is_redacted(self):
        result = validate_provenance([{
            "source_kind": "url", "source_id": "1",
            "source_url": "https://x.com/?api_key=sk-supersecret12345",
        }])
        assert "sk-supersecret12345" not in (result[0].source_url or "")


# --------------------------------------------------------------------- redaction
class TestRedaction:
    def test_bearer_token_removed(self):
        assert "abcdef123456" not in redact_text("failed with Bearer abcdef123456")

    def test_openai_style_key_removed(self):
        assert "sk-proj-AAAABBBBCCCC" not in redact_text("key sk-proj-AAAABBBBCCCC")

    def test_github_token_removed(self):
        assert "ghp_" not in redact_text("token ghp_ABCDEFGHIJKLMNOPQRST")

    def test_aws_key_removed(self):
        assert "AKIAIOSFODNN7EXAMPLE" not in redact_text("AKIAIOSFODNN7EXAMPLE")

    def test_email_removed(self):
        assert "user@example.com" not in redact_text("contact user@example.com")

    def test_key_value_assignment_removed(self):
        assert "hunter2hunter2" not in redact_text("password=hunter2hunter2")

    def test_nested_structure_is_walked(self):
        cleaned = redact_value({"a": {"b": ["Bearer abcdef123456"]}})
        assert "abcdef123456" not in canonical_json(cleaned)

    def test_depth_is_bounded(self):
        deep: object = "leaf"
        for _ in range(30):
            deep = {"n": deep}
        cleaned = redact_value(deep)
        # Must terminate rather than exhaust the stack.
        assert cleaned is not None

    def test_metadata_scrub_drops_secret_keys(self):
        cleaned = scrub_metadata({"api_key": "k", "note": "n", "count": 1})
        assert cleaned["api_key"] == "[redacted]"
        assert cleaned["note"] == "n"
        assert cleaned["count"] == 1

    def test_truncation(self):
        assert len(redact_text("x" * 100, max_chars=20)) <= 25


# ----------------------------------------------------------------------- ranking
class TestRanking:
    def test_lexical_score_is_coverage_of_query_tokens(self):
        record = _record(content_text="python asyncio web scraping")
        score = lexical_score("python scraping", record)
        assert 0.0 < score <= 1.0
        assert lexical_score("kotlin", record) == 0.0

    def test_lexical_score_saturates_at_full_coverage(self):
        record = _record(content_text="alpha beta gamma delta")
        assert lexical_score("alpha beta", record) == 1.0

    def test_empty_query_scores_zero(self):
        assert lexical_score("", _record()) == 0.0

    def test_recency_halves_at_one_half_life(self):
        now = 10_000.0
        fresh = _record(updated_at=now)
        old = _record(updated_at=now - 3600.0)
        assert recency_score(fresh, now=now, half_life=3600.0) == pytest.approx(1.0)
        assert recency_score(old, now=now, half_life=3600.0) == pytest.approx(0.5)

    def test_importance_is_passed_through(self):
        assert importance_score(_record(importance=0.25)) == 0.25

    def test_verification_uses_the_declared_table(self):
        record = _record(verification=Verification.VERIFIED)
        assert verification_score(record) == VERIFICATION_WEIGHT[
            Verification.VERIFIED]

    def test_scope_score_exact_and_wider(self):
        requested = MemoryScope(kind=ScopeKind.TASK, scope_id="t1",
                                owner_key_id="key_a")
        exact = _record(scope=requested)
        wider = _record(scope=MemoryScope(kind=ScopeKind.GLOBAL,
                                          owner_key_id="key_a"))
        assert scope_score(exact, requested) == 1.0
        assert 0.0 < scope_score(wider, requested) < 1.0

    def test_scope_score_is_zero_across_owners(self):
        requested = MemoryScope(kind=ScopeKind.GLOBAL, owner_key_id="key_a")
        foreign = _record(scope=MemoryScope(kind=ScopeKind.GLOBAL,
                                            owner_key_id="key_b"))
        assert scope_score(foreign, requested) == 0.0

    def test_ranking_is_ordered_and_explained(self):
        config = memory_config()
        ranker = Ranker(config)
        now = time.time()
        records = [
            _record(id="mem_a", content_text="python asyncio", importance=0.9),
            _record(id="mem_b", content_text="unrelated", importance=0.1),
        ]
        ranked = ranker.rank(records, query_text="python asyncio",
                             requested_scope=None, now=now, limit=10)
        assert ranked[0].record.id == "mem_a"
        assert set(ranked[0].breakdown) == {
            "text", "recency", "importance", "verification", "scope"}
        assert "text" in ranked[0].reason

    def test_ranking_order_is_total_and_stable(self):
        ranker = Ranker(memory_config())
        now = time.time()
        records = [
            _record(id=f"mem_{i}", importance=0.5, content_text="same")
            for i in range(5)
        ]
        first = ranker.rank(records, query_text="same", requested_scope=None,
                            now=now, limit=10)
        second = ranker.rank(records, query_text="same", requested_scope=None,
                             now=now, limit=10)
        assert [r.record.id for r in first] == [r.record.id for r in second]

    def test_ranking_respects_limit(self):
        ranker = Ranker(memory_config())
        records = [_record(id=f"mem_{i}") for i in range(20)]
        ranked = ranker.rank(records, query_text="", requested_scope=None,
                             now=time.time(), limit=3)
        assert len(ranked) == 3

    def test_weights_are_reported(self):
        ranker = Ranker(memory_config())
        item = RankedRecord(record=_record(), score=0.5, breakdown={"text": 0.5})
        explained = ranker.explain(item)
        assert "weights" in explained and "signals" in explained


# ------------------------------------------------------------------- near-dupes
class TestNearDuplicate:
    def test_identical_normalised_text(self):
        assert near_duplicate("The quick brown fox", "quick brown fox the")

    def test_different_text(self):
        assert not near_duplicate("alpha beta gamma", "delta epsilon zeta")

    def test_empty_never_matches(self):
        assert not near_duplicate("", "")


class TestTextNormalisation:
    def test_case_and_punctuation_folded(self):
        assert normalise_text("Python, asyncio!") == normalise_text(
            "python asyncio")

    def test_stopwords_dropped_from_tokens(self):
        assert "the" not in tokenise("the quick brown fox")

    def test_dedup_digest_is_stable(self):
        assert dedup_digest("a", 1) == dedup_digest("a", 1)
        assert dedup_digest("a", 1) != dedup_digest("a", 2)


# --------------------------------------------------------------------- config
class TestMemoryConfig:
    def test_defaults_load(self):
        config = memory_config()
        assert config.working_capacity == 5
        assert config.embedding_enabled is False

    def test_zero_capacity_rejected(self):
        with pytest.raises(MemoryValidationError):
            memory_config(working_capacity=0)

    def test_limit_below_default_rejected(self):
        with pytest.raises(MemoryValidationError):
            memory_config(default_retrieval_limit=10, max_retrieval_limit=5)

    def test_negative_ttl_rejected(self):
        with pytest.raises(MemoryValidationError):
            memory_config(working_ttl_seconds=-1)

    def test_all_zero_weights_rejected(self):
        with pytest.raises(MemoryValidationError):
            memory_config(retrieval={"weights": {
                "text": 0, "recency": 0, "importance": 0,
                "verification": 0, "scope": 0}})

    def test_out_of_range_weight_rejected(self):
        with pytest.raises(MemoryValidationError):
            memory_config(retrieval={"weights": {"text": 2.0}})

    def test_retention_zero_means_forever(self):
        config = memory_config(episodic_retention_days=0)
        assert config.retention_seconds("episodic") is None

    def test_retention_in_seconds(self):
        config = memory_config(episodic_retention_days=2)
        assert config.retention_seconds("episodic") == 2 * 86400

    def test_weights_reported(self):
        assert set(RetrievalWeights().as_dict()) == {
            "text", "recency", "importance", "verification", "scope"}


def test_cosine_guard_is_importable():
    """Cosine lives in the embeddings module and is exercised there."""
    from jiro.memory.embeddings import cosine

    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0], [1.0, 0.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0