"""Centralised retry policy.

The runtime has exactly one retry loop — in the worker — and it calls this
module for every decision. No other module may schedule a retry.

Determinism
-----------
Both the clock and the randomness source are injected. Tests pass a fixed
clock and a ``random.Random(0)`` so backoff schedules are reproducible while
production uses ``time.time`` and the system RNG.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Optional

from jiro.runtime.types import FailureKind, TaskLimits
from jiro.runtime.errors import is_retryable


@dataclass(frozen=True)
class RetryDecision:
    """Outcome of evaluating a failure against the policy.

    Attributes:
        retry: whether another attempt is permitted at all.
        delay: seconds to wait before the next attempt, or ``None``.
        reason: machine-readable explanation recorded on the retry event.
        kind: the classification the decision was based on.
    """

    retry: bool
    delay: Optional[float]
    reason: str
    kind: FailureKind

    @property
    def available_at_offset(self) -> Optional[float]:
        """Alias clarifying units at call sites that schedule wall-clock time."""
        return self.delay


class RetryPolicy:
    """Bounded exponential backoff with jitter.

    The delay for attempt *n* (1-based, counting the attempt that just failed)
    is ``min(backoff_max, backoff_base * 2 ** (n - 1))``, then randomised by up
    to ``jitter_ratio`` of that value. Jitter exists so that a batch of tasks
    failing against the same upstream does not retry in lockstep.
    """

    def __init__(
        self,
        limits: TaskLimits,
        *,
        rng: Optional[random.Random] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._limits = limits
        self._rng = rng if rng is not None else random.Random()
        self._clock = clock

    @property
    def clock(self) -> Callable[[], float]:
        return self._clock

    def backoff_for(self, attempt: int) -> float:
        """Delay in seconds before the attempt following ``attempt``.

        Saturates at ``retry_backoff_max`` rather than growing without bound,
        so a large attempt count cannot overflow into an absurd sleep.
        """
        if attempt < 1:
            attempt = 1
        exponent = min(attempt - 1, 32)  # 2**33 s is far past any sane ceiling
        delay = min(self._limits.retry_backoff_max, self._limits.retry_backoff_base * (2**exponent))
        if self._limits.retry_jitter_ratio:
            delay += self._rng.uniform(0.0, delay * self._limits.retry_jitter_ratio)
        return min(delay, self._limits.retry_backoff_max)

    def decide(
        self,
        *,
        kind: FailureKind,
        attempts: int,
        deadline_at: Optional[float] = None,
    ) -> RetryDecision:
        """Decide what happens after an attempt fails.

        Evaluation order, cheapest and most decisive first:

        1. Cancellation is never retried.
        2. Permanent failures are never retried.
        3. An exhausted attempt budget is never retried.
        4. A task whose deadline has already passed is failed now rather than
           woken later only to be rejected — a restart must not silently reset
           the total budget.
        """
        if kind is FailureKind.CANCELLED:
            return RetryDecision(False, None, "cancelled", kind)

        if not is_retryable(kind):
            return RetryDecision(False, None, f"non_retryable:{kind.value}", kind)

        if self._limits.remaining_attempts(attempts) <= 0:
            return RetryDecision(
                False,
                None,
                "attempts_exhausted",
                kind,
            )

        delay = self.backoff_for(attempts)
        now = self._clock()

        if deadline_at is not None:
            if now >= deadline_at:
                return RetryDecision(False, None, "deadline_exceeded", kind)
            # Never schedule a wake-up past the deadline; the remaining budget
            # would be consumed waiting for work that cannot legally run.
            if now + delay >= deadline_at:
                return RetryDecision(False, None, "deadline_exceeded_during_backoff", kind)

        return RetryDecision(True, delay, f"retryable:{kind.value}", kind)


__all__ = ["RetryDecision", "RetryPolicy"]
