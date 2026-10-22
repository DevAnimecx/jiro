"""Memory lifecycle state machine.

The single place a memory's status may legally change, mirroring
``jiro/runtime/lifecycle.py`` so the two subsystems read the same way.

    active ──┬──> superseded   (a newer version replaced it)
             ├──> disputed     (a contradiction is open against it)
             ├──> expired      (TTL elapsed)
             └──> deleted      (explicit removal)

``deleted`` is terminal. Nothing transitions out of it, including recovery,
which is what makes deletion idempotent and safe to retry.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Mapping, Optional

from jiro.memory.errors import MemoryError, MemoryValidationError
from jiro.memory.types import MemoryStatus

#: Legal (source, destination) pairs. Anything absent is rejected.
TRANSITIONS: Mapping[MemoryStatus, FrozenSet[MemoryStatus]] = {
    MemoryStatus.ACTIVE: frozenset({
        MemoryStatus.SUPERSEDED,
        MemoryStatus.DISPUTED,
        MemoryStatus.EXPIRED,
        MemoryStatus.DELETED,
    }),
    # A disputed memory can be withdrawn by a resolution, or deleted outright.
    MemoryStatus.DISPUTED: frozenset({
        MemoryStatus.ACTIVE,      # resolution accepted this one
        MemoryStatus.SUPERSEDED,  # resolution accepted the other one
        MemoryStatus.DELETED,
    }),
    MemoryStatus.SUPERSEDED: frozenset({MemoryStatus.DELETED}),
    MemoryStatus.EXPIRED: frozenset({MemoryStatus.DELETED}),
    MemoryStatus.DELETED: frozenset(),
}

TERMINAL_STATUSES = frozenset({MemoryStatus.DELETED})


def can_transition(source: MemoryStatus, target: MemoryStatus) -> bool:
    """Whether ``source -> target`` is permitted. Pure predicate, never raises."""
    return target in TRANSITIONS.get(source, frozenset())


def validate_transition(source: MemoryStatus, target: MemoryStatus) -> None:
    """Raise :class:`MemoryValidationError` unless the transition is legal."""
    if source == target:
        # Idempotent no-ops are legal: deleting an already-deleted memory must
        # succeed quietly so callers can retry without checking first.
        return
    if not can_transition(source, target):
        raise MemoryValidationError(
            f"illegal memory transition {source.value} -> {target.value}",
            details={
                "from": source.value,
                "to": target.value,
                "allowed": sorted(s.value for s in TRANSITIONS.get(source, frozenset())),
            },
        )


def is_retrievable(status: MemoryStatus, *, now: Optional[float] = None,
                   expires_at: Optional[float] = None) -> bool:
    """Whether a memory with this state may be returned by ordinary retrieval.

    Expiry is evaluated here rather than in the query so the rule lives in one
    place and is testable without a database.
    """
    if status is not MemoryStatus.ACTIVE:
        return False
    if expires_at is not None and now is not None:

        if now >= expires_at:
            return False
    return True


def describe() -> Dict[str, list]:
    """Machine-readable lifecycle description for documentation and ops."""
    return {
        status.value: sorted(t.value for t in targets)
        for status, targets in TRANSITIONS.items()
    }


def assert_status(value: object) -> MemoryStatus:
    """Coerce a raw column value into a :class:`MemoryStatus`.

    Raises:
        MemoryError: if the value is not a known status. A corrupt row is a bug
            worth surfacing loudly, not coercing to a default.
    """
    if isinstance(value, MemoryStatus):
        return value
    try:
        return MemoryStatus(str(value))
    except ValueError as exc:
        raise MemoryError(
            f"unknown memory status {value!r}",
            details={"known": [s.value for s in MemoryStatus]},
        ) from exc


__all__ = [
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "assert_status",
    "can_transition",
    "describe",
    "is_retrievable",
    "validate_transition",
]