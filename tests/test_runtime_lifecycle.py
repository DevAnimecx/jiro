"""Unit tests for the runtime domain core.

Covers state transitions, retry classification, backoff arithmetic, budget and
deadline validation, error sanitisation, and event metadata scrubbing. These
touch no storage and no event loop, so they are fast and fully deterministic.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from jiro.errors import (
    CacheError,
    EngineBlockedError,
    ForbiddenError,
    RateLimitError,
    ValidationError,
)
from jiro.runtime.errors import (
    FailureKind,
    InvalidTransition,
    ResultTooLarge,
    TaskValidationError,
    classify_failure,
    is_retryable,
    sanitize_error,
    sanitize_metadata,
)
from jiro.runtime.lifecycle import (
    TRANSITIONS,
    assert_state,
    can_transition,
    describe,
    validate_claimable,
    validate_transition,
)
from jiro.runtime.retry import RetryPolicy
from jiro.runtime.types import TaskLimits, TaskState
from jiro.runtime.validation import Field, TaskSchema, resolve_limits, validate_input


# --------------------------------------------------------------- state machine
class TestTransitions:
    def test_terminal_states_have_no_outgoing_edges(self):
        for state in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED):
            assert TRANSITIONS[state] == frozenset()
            assert state.is_terminal

    def test_terminal_task_cannot_return_to_running(self):
        with pytest.raises(InvalidTransition):
            validate_transition(TaskState.SUCCEEDED, TaskState.RUNNING)

    def test_reentering_a_terminal_state_is_rejected(self):
        with pytest.raises(InvalidTransition) as exc:
            validate_transition(TaskState.FAILED, TaskState.FAILED)
        assert "already failed" in str(exc.value)

    @pytest.mark.parametrize(
        "source,target",
        [
            (TaskState.QUEUED, TaskState.RUNNING),
            (TaskState.QUEUED, TaskState.CANCELLED),
            (TaskState.QUEUED, TaskState.FAILED),
            (TaskState.RUNNING, TaskState.SUCCEEDED),
            (TaskState.RUNNING, TaskState.RETRY_WAIT),
            (TaskState.RUNNING, TaskState.CANCELLED),
            (TaskState.RETRY_WAIT, TaskState.QUEUED),
            (TaskState.RETRY_WAIT, TaskState.CANCELLED),
        ],
    )
    def test_permitted_transitions(self, source, target):
        assert can_transition(source, target)
        validate_transition(source, target)

    @pytest.mark.parametrize(
        "source,target",
        [
            (TaskState.SUCCEEDED, TaskState.QUEUED),
            (TaskState.CANCELLED, TaskState.RUNNING),
            (TaskState.FAILED, TaskState.RETRY_WAIT),
            (TaskState.RETRY_WAIT, TaskState.RUNNING),  # must requeue first
            (TaskState.RETRY_WAIT, TaskState.SUCCEEDED),
            (TaskState.QUEUED, TaskState.SUCCEEDED),
        ],
    )
    def test_forbidden_transitions(self, source, target):
        assert not can_transition(source, target)
        with pytest.raises(InvalidTransition):
            validate_transition(source, target)

    def test_error_lists_allowed_targets(self):
        with pytest.raises(InvalidTransition) as exc:
            validate_transition(TaskState.RETRY_WAIT, TaskState.RUNNING)
        assert "queued" in exc.value.details["allowed"]

    def test_only_queued_and_retry_wait_are_claimable(self):
        validate_claimable(TaskState.QUEUED)
        validate_claimable(TaskState.RETRY_WAIT)
        for state in (
            TaskState.RUNNING,
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        ):
            with pytest.raises(InvalidTransition):
                validate_claimable(state)

    def test_describe_is_json_safe(self):
        described = describe()
        assert set(described) == {s.value for s in TaskState}
        assert described["succeeded"] == []

    def test_assert_state_rejects_corrupt_value(self):
        assert assert_state("queued") is TaskState.QUEUED
        assert assert_state(TaskState.FAILED) is TaskState.FAILED
        with pytest.raises(InvalidTransition):
            assert_state("half_done")


# --------------------------------------------------------------- classification
class TestFailureClassification:
    def test_cancellation_is_never_retryable(self):
        assert classify_failure(asyncio.CancelledError()) is FailureKind.CANCELLED
        assert not is_retryable(FailureKind.CANCELLED)

    @pytest.mark.parametrize(
        "exc",
        [
            TaskValidationError("bad"),
            ValidationError("bad"),
            ForbiddenError("nope"),
            ResultTooLarge("too big"),
        ],
    )
    def test_permanent_failures(self, exc):
        assert classify_failure(exc) is FailureKind.PERMANENT
        assert not is_retryable(FailureKind.PERMANENT)

    def test_bare_value_error_from_handler_is_unexpected_not_permanent(self):
        # A handler raising a plain ValueError is a bug we cannot classify; it
        # gets the retryable-unknown treatment, capped by the attempt budget.
        assert classify_failure(ValueError("plain")) is FailureKind.UNEXPECTED

    @pytest.mark.parametrize(
        "exc", [RateLimitError("slow down"), EngineBlockedError("blocked"), OSError("io")]
    )
    def test_retryable_failures(self, exc):
        assert is_retryable(classify_failure(exc))

    def test_timeout_classification(self):
        assert classify_failure(asyncio.TimeoutError()) is FailureKind.TIMEOUT
        assert classify_failure(TimeoutError()) is FailureKind.TIMEOUT

    def test_storage_failures_are_infrastructure(self):
        assert classify_failure(CacheError("db gone")) is FailureKind.INFRASTRUCTURE

    def test_unexpected_handler_error(self):
        assert classify_failure(RuntimeError("boom")) is FailureKind.UNEXPECTED
        assert is_retryable(FailureKind.UNEXPECTED)

    def test_broad_jiro_error_does_not_trigger_retry_storm(self):
        # An unlisted JiroError signals a bug, not a transient fault.
        from jiro.errors import JiroError

        assert classify_failure(JiroError("?")) is FailureKind.PERMANENT


# -------------------------------------------------------------- sanitisation
class TestErrorSanitisation:
    def test_traceback_is_never_included(self):
        try:
            raise RuntimeError("kaboom")
        except RuntimeError as exc:
            _, message = sanitize_error(exc)
        assert "Traceback" not in message
        assert "File " not in message

    def test_bearer_token_is_redacted(self):
        _, message = sanitize_error(RuntimeError("failed with Bearer sk-abc123def456"))
        assert "sk-abc123def456" not in message
        assert "redacted" in message

    def test_openai_style_key_is_redacted(self):
        _, message = sanitize_error(RuntimeError("auth failed for sk-proj-AAAABBBBCCCC"))
        assert "AAAABBBBCCCC" not in message

    def test_long_message_is_truncated(self):
        _, message = sanitize_error(RuntimeError("x" * 5000), max_length=100)
        assert len(message) <= 100

    def test_error_code_derived_from_class(self):
        assert sanitize_error(ValidationError("bad"))[0] == "validation_error"
        assert sanitize_error(asyncio.TimeoutError())[0] == "attempt_timeout"
        assert sanitize_error(RuntimeError("x"))[0] == "unexpected_error"

    def test_metadata_drops_secret_keys(self):
        cleaned = sanitize_metadata(
            {
                "api_key": "secret-value",
                "authorization": "Bearer x",
                "password": "hunter2",
                "attempts": 2,
            }
        )
        assert cleaned["api_key"] == "[redacted]"
        assert cleaned["authorization"] == "[redacted]"
        assert cleaned["password"] == "[redacted]"
        assert cleaned["attempts"] == 2

    def test_metadata_replaces_structures_with_type_name(self):
        cleaned = sanitize_metadata({"payload": {"big": "dict"}, "items": [1, 2, 3]})
        assert cleaned["payload"] == "dict"
        assert cleaned["items"] == "list"


# --------------------------------------------------------------------- backoff
def _limits(**kwargs) -> TaskLimits:
    base = dict(
        max_attempts=5,
        attempt_timeout=10.0,
        retry_backoff_base=1.0,
        retry_backoff_max=60.0,
        retry_jitter_ratio=0.0,
    )
    base.update(kwargs)
    return TaskLimits(**base)


class TestBackoff:
    def test_exponential_growth(self):
        policy = RetryPolicy(_limits(), rng=random.Random(0))
        assert policy.backoff_for(1) == 1.0
        assert policy.backoff_for(2) == 2.0
        assert policy.backoff_for(3) == 4.0
        assert policy.backoff_for(4) == 8.0

    def test_saturates_at_ceiling(self):
        policy = RetryPolicy(_limits(retry_backoff_max=10.0), rng=random.Random(0))
        assert policy.backoff_for(20) == 10.0

    def test_never_negative_for_zero_attempt(self):
        policy = RetryPolicy(_limits(), rng=random.Random(0))
        assert policy.backoff_for(0) == 1.0

    def test_jitter_adds_bounded_variation(self):
        policy = RetryPolicy(_limits(retry_jitter_ratio=0.5), rng=random.Random(7))
        samples = {policy.backoff_for(3) for _ in range(50)}
        assert len(samples) > 1
        assert all(4.0 <= s <= 6.0 for s in samples)

    def test_jitter_is_deterministic_with_seeded_rng(self):
        a = RetryPolicy(_limits(retry_jitter_ratio=0.5), rng=random.Random(42))
        b = RetryPolicy(_limits(retry_jitter_ratio=0.5), rng=random.Random(42))
        assert [a.backoff_for(2) for _ in range(10)] == [b.backoff_for(2) for _ in range(10)]


# -------------------------------------------------------------- retry decision
class TestRetryDecision:
    def test_retries_while_budget_remains(self):
        policy = RetryPolicy(_limits(max_attempts=3), rng=random.Random(0))
        decision = policy.decide(kind=FailureKind.TRANSIENT, attempts=1)
        assert decision.retry
        assert decision.delay == 1.0
        assert "transient" in decision.reason

    def test_budget_exhaustion_is_terminal(self):
        policy = RetryPolicy(_limits(max_attempts=2), rng=random.Random(0))
        decision = policy.decide(kind=FailureKind.TRANSIENT, attempts=2)
        assert not decision.retry
        assert decision.reason == "attempts_exhausted"

    def test_permanent_failure_never_retried(self):
        policy = RetryPolicy(_limits(), rng=random.Random(0))
        decision = policy.decide(kind=FailureKind.PERMANENT, attempts=1)
        assert not decision.retry
        assert decision.reason == "non_retryable:permanent"

    def test_cancellation_never_retried(self):
        policy = RetryPolicy(_limits(), rng=random.Random(0))
        assert not policy.decide(kind=FailureKind.CANCELLED, attempts=1).retry

    def test_expired_deadline_is_not_rescheduled(self):
        clock_value = [1000.0]
        policy = RetryPolicy(_limits(), rng=random.Random(0), clock=lambda: clock_value[0])
        decision = policy.decide(kind=FailureKind.TRANSIENT, attempts=1, deadline_at=999.0)
        assert not decision.retry
        assert decision.reason == "deadline_exceeded"

    def test_backoff_would_overshoot_deadline(self):
        clock_value = [1000.0]
        policy = RetryPolicy(
            _limits(retry_backoff_base=10.0), rng=random.Random(0), clock=lambda: clock_value[0]
        )
        decision = policy.decide(kind=FailureKind.TRANSIENT, attempts=1, deadline_at=1005.0)
        assert not decision.retry
        assert decision.reason == "deadline_exceeded_during_backoff"


# ------------------------------------------------------------------ validation
class TestInputValidation:
    def test_valid_payload_is_normalised(self):
        schema = TaskSchema(
            "t", [Field("name", "str", required=True), Field("count", "int", default=1)]
        )
        assert validate_input(schema, {"name": "x"}) == {"name": "x", "count": 1}

    def test_unknown_field_is_rejected(self):
        schema = TaskSchema("t", [Field("name", "str")])
        with pytest.raises(TaskValidationError) as exc:
            validate_input(schema, {"name": "x", "evil": 1})
        assert "evil" in str(exc.value)

    def test_missing_required_field(self):
        schema = TaskSchema("t", [Field("name", "str", required=True)])
        with pytest.raises(TaskValidationError):
            validate_input(schema, {})

    def test_non_object_payload_rejected(self):
        with pytest.raises(TaskValidationError):
            validate_input(TaskSchema("t", []), ["not", "a", "dict"])

    def test_wrong_type_rejected(self):
        schema = TaskSchema("t", [Field("n", "int")])
        with pytest.raises(TaskValidationError):
            validate_input(schema, {"n": "twelve"})

    def test_bool_is_not_accepted_as_int(self):
        schema = TaskSchema("t", [Field("n", "int")])
        with pytest.raises(TaskValidationError):
            validate_input(schema, {"n": True})

    def test_choices_enforced(self):
        schema = TaskSchema("t", [Field("engine", "str", choices=["a", "b"])])
        assert validate_input(schema, {"engine": "a"})["engine"] == "a"
        with pytest.raises(TaskValidationError):
            validate_input(schema, {"engine": "z"})

    def test_numeric_bounds(self):
        schema = TaskSchema("t", [Field("n", "int", min_value=1, max_value=10)])
        with pytest.raises(TaskValidationError):
            validate_input(schema, {"n": 0})
        with pytest.raises(TaskValidationError):
            validate_input(schema, {"n": 11})

    def test_oversized_string_rejected(self):
        schema = TaskSchema("t", [Field("body", "str", max_bytes=32)])
        with pytest.raises(TaskValidationError):
            validate_input(schema, {"body": "x" * 1000})

    def test_list_items_are_type_checked(self):
        schema = TaskSchema("t", [Field("urls", "list", item_type="str")])
        assert validate_input(schema, {"urls": ["a", "b"]})["urls"] == ["a", "b"]
        with pytest.raises(TaskValidationError):
            validate_input(schema, {"urls": ["a", 1]})

    def test_error_names_the_field(self):
        schema = TaskSchema("t", [Field("name", "str", required=True)])
        with pytest.raises(TaskValidationError) as exc:
            validate_input(schema, {})
        assert exc.value.details["field"] == "name"


class TestLimitValidation:
    def _config(self, **runtime):
        from jiro.runtime.config import RuntimeConfig

        from tests.conftest_runtime import make_settings

        settings = make_settings(runtime=runtime) if runtime else make_settings()
        return RuntimeConfig.from_settings(settings)

    def test_defaults_applied_when_nothing_requested(self):
        limits = resolve_limits(self._config(), None, now=1000.0)
        assert limits.max_attempts == 2
        assert limits.attempt_timeout == 1.0
        assert limits.deadline is None

    def test_caller_may_request_less(self):
        limits = resolve_limits(self._config(), {"max_attempts": 1}, now=1000.0)
        assert limits.max_attempts == 1

    def test_request_above_ceiling_is_refused_not_clamped(self):
        from jiro.runtime.errors import TaskLimitExceeded

        with pytest.raises(TaskLimitExceeded):
            resolve_limits(self._config(), {"max_attempts": 99}, now=1000.0)

    def test_zero_timeout_rejected(self):
        with pytest.raises(TaskValidationError):
            resolve_limits(self._config(), {"attempt_timeout_seconds": 0}, now=1000.0)

    def test_timeout_above_ceiling_refused(self):
        from jiro.runtime.errors import TaskLimitExceeded

        with pytest.raises(TaskLimitExceeded):
            resolve_limits(self._config(), {"attempt_timeout_seconds": 10_000}, now=1000.0)

    def test_relative_deadline_resolves_to_epoch(self):
        limits = resolve_limits(self._config(), {"deadline_in_seconds": 60}, now=1000.0)
        assert limits.deadline == 1060.0

    def test_tighter_of_two_deadlines_wins(self):
        limits = resolve_limits(
            self._config(),
            {"deadline_at": 1030.0, "deadline_in_seconds": 600},
            now=1000.0,
        )
        assert limits.deadline == 1030.0

    def test_unknown_limit_field_rejected(self):
        with pytest.raises(TaskValidationError):
            resolve_limits(self._config(), {"nonsense": 1}, now=1000.0)


class TestConfigValidation:
    def test_zero_concurrency_rejected(self):
        from jiro.runtime.config import RuntimeConfig
        from jiro.runtime.errors import TaskValidationError

        from tests.conftest_runtime import make_settings

        with pytest.raises(TaskValidationError):
            RuntimeConfig.from_settings(make_settings(runtime={"max_concurrency": 0}))

    def test_lease_shorter_than_max_timeout_warns_but_loads(self):
        """Leases are renewed on a heartbeat, so this is legal - but flagged."""
        from jiro.runtime.config import RuntimeConfig

        from tests.conftest_runtime import make_settings

        cfg = RuntimeConfig.from_settings(
            make_settings(
                runtime={
                    "lease_ttl_seconds": 10,
                    "max_attempt_timeout_seconds": 60,
                    "default_attempt_timeout_seconds": 5,
                }
            )
        )
        assert cfg.lease_ttl == 10.0

    def test_lease_ttl_below_floor_is_rejected(self):
        from jiro.runtime.config import RuntimeConfig
        from jiro.runtime.errors import TaskValidationError

        from tests.conftest_runtime import make_settings

        with pytest.raises(TaskValidationError) as exc:
            RuntimeConfig.from_settings(
                make_settings(
                    runtime={
                        "lease_ttl_seconds": 1,
                        "max_attempt_timeout_seconds": 5,
                        "default_attempt_timeout_seconds": 1,
                    }
                )
            )
        assert "lease_ttl_seconds" in str(exc.value)

    def test_heartbeat_is_a_third_of_the_ttl(self):
        from jiro.runtime.config import RuntimeConfig

        from tests.conftest_runtime import make_settings

        cfg = RuntimeConfig.from_settings(
            make_settings(
                runtime={
                    "lease_ttl_seconds": 30,
                    "max_attempt_timeout_seconds": 20,
                    "default_attempt_timeout_seconds": 5,
                }
            )
        )
        assert cfg.heartbeat_interval == 10.0

    def test_valid_config_loads(self):
        from jiro.runtime.config import RuntimeConfig

        from tests.conftest_runtime import make_settings

        cfg = RuntimeConfig.from_settings(make_settings())
        assert cfg.max_concurrency == 2
