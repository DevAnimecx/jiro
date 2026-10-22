"""Task lifecycle state machine.

This module is the **only** place a task's state may legally change. The
repository and the worker both call :func:`validate_transition`; nothing else is
permitted to decide that a transition is acceptable.

    queued ──claim──> running ──ok────> succeeded
      │                  ├──retryable──> retry_wait ──backoff──> queued
      │                  └──terminal───> failed
      ├──cancel──> cancelled <──cancel──┘
      └──deadline exceeded──> failed

Terminal states ({succeeded, failed, cancelled}) have no outgoing edges, so a
completed task cannot return to active execution.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Mapping, Tuple

from jiro.runtime.errors import InvalidTransition
from jiro.runtime.types import CLAIMABLE_STATES, TERMINAL_STATES, TaskState

#: Legal (source, destination) pairs. Anything absent is rejected.
TRANSITIONS: Mapping[TaskState, FrozenSet[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.RETRY_WAIT,
            # Requeue without backoff: used by recovery to hand an expired-lease
            # task straight back to the pool when its budget allows.
            TaskState.QUEUED,
        }
    ),
    TaskState.RETRY_WAIT: frozenset({TaskState.QUEUED, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}

#: Terminal states that must survive a worker error without being rewritten.
#: Used to build idempotent `UPDATE ... WHERE id = ? AND state = ?` guards.
_COMPLETING_STATES: Tuple[TaskState, ...] = (
    TaskState.SUCCEEDED,
    TaskState.FAILED,
    TaskState.CANCELLED,
)


def is_terminal(state: TaskState) -> bool:
    return state in TERMINAL_STATES


def can_transition(source: TaskState, target: TaskState) -> bool:
    """Whether ``source -> target`` is permitted. Pure predicate, never raises."""
    return target in TRANSITIONS.get(source, frozenset())


def validate_transition(source: TaskState, target: TaskState) -> None:
    """Raise :class:`InvalidTransition` unless ``source -> target`` is legal.

    The message names both states so a caller can distinguish "already
    terminal" from "genuinely illegal" without inspecting the transition table.
    """
    if source == target and is_terminal(source):
        raise InvalidTransition(
            f"task is already {source.value}; terminal states cannot be re-entered",
            details={"from": source.value, "to": target.value},
        )
    if not can_transition(source, target):
        raise InvalidTransition(
            f"illegal task transition {source.value} -> {target.value}",
            details={
                "from": source.value,
                "to": target.value,
                "allowed": sorted(s.value for s in TRANSITIONS.get(source, frozenset())),
            },
        )


def validate_claimable(state: TaskState) -> None:
    """Raise unless a worker may atomically claim a task in ``state``."""
    if state not in CLAIMABLE_STATES:
        raise InvalidTransition(
            f"task in state {state.value} is not claimable",
            details={"from": state.value, "claimable": sorted(s.value for s in CLAIMABLE_STATES)},
        )


def completing_states() -> Tuple[TaskState, ...]:
    """States that a terminal write may legally overwrite.

    Used as the ``WHERE state IN (...)`` guard so a duplicate or stale
    completion cannot corrupt a task another worker already finished.
    """
    return _COMPLETING_STATES


def describe() -> Dict[str, list]:
    """Machine-readable lifecycle description for documentation and ops."""
    return {
        state.value: sorted(target.value for target in targets)
        for state, targets in TRANSITIONS.items()
    }


def assert_state(value: object) -> TaskState:
    """Coerce a raw column value into a :class:`TaskState`.

    Raises:
        InvalidTransition: if the value is not a known state. A corrupt row is
            a bug worth surfacing loudly, not coercing to a default.
    """
    if isinstance(value, TaskState):
        return value
    try:
        return TaskState(str(value))
    except ValueError as exc:
        raise InvalidTransition(
            f"unknown task state {value!r}",
            details={"known": [s.value for s in TaskState]},
        ) from exc


__all__ = [
    "TRANSITIONS",
    "assert_state",
    "can_transition",
    "completing_states",
    "describe",
    "is_terminal",
    "validate_claimable",
    "validate_transition",
]
