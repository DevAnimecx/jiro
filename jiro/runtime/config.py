"""Runtime configuration, loaded from ``jiro.config.Settings``.

All settings live under the ``runtime:`` key of the existing config tree, so
they inherit the project's established precedence (defaults -> YAML ->
``JIRO_RUNTIME__*`` environment overrides) without inventing a second mechanism.

Every limit is validated at construction. An invalid value raises
:class:`~jiro.runtime.errors.TaskValidationError` immediately rather than
failing later inside a worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from jiro.config import Settings
from jiro.log import get_logger
from jiro.runtime.errors import TaskValidationError

log = get_logger("jiro.runtime.config")

#: Section name inside the existing Jiro config tree.
CONFIG_SECTION = "runtime"

#: Absolute ceilings. A caller may request less; never more.
MAX_CONCURRENCY_CEILING = 64
MAX_ATTEMPTS_CEILING = 10
MAX_ATTEMPT_TIMEOUT_CEILING = 3600.0
MAX_POLL_INTERVAL_CEILING = 60.0
MAX_SHUTDOWN_GRACE_CEILING = 300.0
MAX_BACKOFF_CEILING = 3600.0
MAX_RESULT_BYTES_CEILING = 8 * 1024 * 1024
#: Above ``MAX_ATTEMPT_TIMEOUT_CEILING``: a lease must outlive the longest
#: attempt the runtime will ever permit, or the default configuration warns
#: about its own constants the moment it is built.
MAX_LEASE_TTL_CEILING = 7200.0


def _get(section: Mapping[str, Any], key: str, default: Any) -> Any:
    value = section.get(key, default)
    return default if value is None else value


@dataclass(frozen=True)
class RuntimeConfig:
    """Validated runtime limits.

    Attributes:
        enabled: when false the runtime still constructs but starts no workers.
        db_path: SQLite file for task storage. Empty string means "reuse the
            main Jiro database" (``db.path``), keeping one writer per process.
        max_concurrency: number of worker coroutines. Also the hard cap on
            simultaneous handler executions.
        poll_interval: idle sleep between claim attempts when the queue is dry.
        lease_ttl: how long a claimed task's lease stays valid without renewal. The
            worker renews it on a heartbeat while the handler runs, so this
            bounds recovery latency after a hard process kill rather than the
            maximum attempt duration.
        default_attempt_timeout: attempt cap applied when a task requests none.
        max_attempt_timeout: hard ceiling for a task-requested attempt timeout.
        default_max_attempts: attempt budget applied when a task requests none.
        max_max_attempts: hard ceiling for a task-requested attempt budget.
        retry_backoff_base: first retry delay in seconds.
        retry_backoff_max: ceiling for a single backoff delay.
        retry_jitter_ratio: fraction of a delay randomised.
        shutdown_grace: seconds allowed for in-flight work during shutdown.
        max_result_bytes: largest serialised result the runtime will store.
        event_retention_days: lifecycle events older than this are pruned.
            ``0`` disables pruning.
        reconcile_interval: how often durable state is re-reconciled while the
            runtime is up. Startup recovery alone is not enough: a task whose
            owner dies while *this* process keeps running would sit in
            ``running`` until the next restart, because its lease only becomes
            recoverable after it lapses. ``0`` disables the periodic sweep.
    """

    enabled: bool = True
    db_path: str = ""
    max_concurrency: int = 4
    poll_interval: float = 0.5
    lease_ttl: float = 3900.0
    default_attempt_timeout: float = 60.0
    max_attempt_timeout: float = 3600.0
    default_max_attempts: int = 3
    max_max_attempts: int = 10
    retry_backoff_base: float = 1.0
    retry_backoff_max: float = 60.0
    retry_jitter_ratio: float = 0.2
    shutdown_grace: float = 15.0
    max_result_bytes: int = 262_144
    event_retention_days: int = 7
    reconcile_interval: float = 30.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "RuntimeConfig":
        """Build a validated config from the project's Settings tree."""
        section = settings.get(CONFIG_SECTION, {}) or {}
        if not isinstance(section, Mapping):
            raise TaskValidationError("runtime config section must be a mapping")

        default_timeout = float(_get(section, "default_attempt_timeout_seconds", 60.0))
        max_timeout = float(_get(section, "max_attempt_timeout_seconds", 3600.0))

        cfg = cls(
            enabled=bool(_get(section, "enabled", True)),
            db_path=str(_get(section, "db_path", "")),
            max_concurrency=int(_get(section, "max_concurrency", 4)),
            poll_interval=float(_get(section, "poll_interval_seconds", 0.5)),
            lease_ttl=float(_get(section, "lease_ttl_seconds", 3900.0)),
            default_attempt_timeout=default_timeout,
            max_attempt_timeout=max_timeout,
            default_max_attempts=int(_get(section, "default_max_attempts", 3)),
            max_max_attempts=int(_get(section, "max_max_attempts", 10)),
            retry_backoff_base=float(_get(section, "retry_backoff_base_seconds", 1.0)),
            retry_backoff_max=float(_get(section, "retry_backoff_max_seconds", 60.0)),
            retry_jitter_ratio=float(_get(section, "retry_jitter_ratio", 0.2)),
            shutdown_grace=float(_get(section, "shutdown_grace_seconds", 15.0)),
            max_result_bytes=int(_get(section, "max_result_bytes", 262_144)),
            event_retention_days=int(_get(section, "event_retention_days", 7)),
            reconcile_interval=float(_get(section, "reconcile_interval_seconds", 30.0)),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Fail fast on an unusable configuration.

        Raises:
            TaskValidationError: with a message naming the offending setting.
        """
        if self.max_concurrency < 1:
            raise TaskValidationError(
                "runtime.max_concurrency must be >= 1",
                details={"value": self.max_concurrency},
            )
        if self.max_concurrency > MAX_CONCURRENCY_CEILING:
            raise TaskValidationError(
                f"runtime.max_concurrency must be <= {MAX_CONCURRENCY_CEILING}",
                details={"value": self.max_concurrency, "max": MAX_CONCURRENCY_CEILING},
            )
        if self.poll_interval <= 0 or self.poll_interval > MAX_POLL_INTERVAL_CEILING:
            raise TaskValidationError(
                f"runtime.poll_interval_seconds must be in (0, {MAX_POLL_INTERVAL_CEILING}]",
                details={"value": self.poll_interval},
            )
        if not 5.0 <= self.lease_ttl <= MAX_LEASE_TTL_CEILING:
            raise TaskValidationError(
                f"runtime.lease_ttl_seconds must be within [5, {MAX_LEASE_TTL_CEILING}]",
                details={"value": self.lease_ttl},
            )
        if self.default_attempt_timeout <= 0:
            raise TaskValidationError(
                "runtime.default_attempt_timeout_seconds must be > 0",
                details={"value": self.default_attempt_timeout},
            )
        if self.max_attempt_timeout < self.default_attempt_timeout:
            raise TaskValidationError(
                "runtime.max_attempt_timeout_seconds must be >= "
                "runtime.default_attempt_timeout_seconds",
                details={
                    "max": self.max_attempt_timeout,
                    "default": self.default_attempt_timeout,
                },
            )
        if self.max_attempt_timeout > MAX_ATTEMPT_TIMEOUT_CEILING:
            raise TaskValidationError(
                f"runtime.max_attempt_timeout_seconds must be <= {MAX_ATTEMPT_TIMEOUT_CEILING}",
                details={"value": self.max_attempt_timeout},
            )
        if not 1 <= self.default_max_attempts <= self.max_max_attempts:
            raise TaskValidationError(
                "runtime.default_max_attempts must be between 1 and runtime.max_max_attempts",
                details={
                    "default": self.default_max_attempts,
                    "max": self.max_max_attempts,
                },
            )
        if self.max_max_attempts > MAX_ATTEMPTS_CEILING:
            raise TaskValidationError(
                f"runtime.max_max_attempts must be <= {MAX_ATTEMPTS_CEILING}",
                details={"value": self.max_max_attempts},
            )
        if self.retry_backoff_base <= 0:
            raise TaskValidationError(
                "runtime.retry_backoff_base_seconds must be > 0",
                details={"value": self.retry_backoff_base},
            )
        if self.retry_backoff_max < self.retry_backoff_base:
            raise TaskValidationError(
                "runtime.retry_backoff_max_seconds must be >= runtime.retry_backoff_base_seconds",
                details={"max": self.retry_backoff_max, "base": self.retry_backoff_base},
            )
        if self.retry_backoff_max > MAX_BACKOFF_CEILING:
            raise TaskValidationError(
                f"runtime.retry_backoff_max_seconds must be <= {MAX_BACKOFF_CEILING}",
                details={"value": self.retry_backoff_max},
            )
        if not 0.0 <= self.retry_jitter_ratio <= 1.0:
            raise TaskValidationError(
                "runtime.retry_jitter_ratio must be within [0.0, 1.0]",
                details={"value": self.retry_jitter_ratio},
            )
        if not 0 <= self.shutdown_grace <= MAX_SHUTDOWN_GRACE_CEILING:
            raise TaskValidationError(
                f"runtime.shutdown_grace_seconds must be within [0, {MAX_SHUTDOWN_GRACE_CEILING}]",
                details={"value": self.shutdown_grace},
            )
        if not 1024 <= self.max_result_bytes <= MAX_RESULT_BYTES_CEILING:
            raise TaskValidationError(
                f"runtime.max_result_bytes must be within [1024, {MAX_RESULT_BYTES_CEILING}]",
                details={"value": self.max_result_bytes},
            )
        if self.event_retention_days < 0:
            raise TaskValidationError(
                "runtime.event_retention_days must be >= 0 (0 disables pruning)",
                details={"value": self.event_retention_days},
            )
        if self.reconcile_interval < 0:
            raise TaskValidationError(
                "runtime.reconcile_interval_seconds must be >= 0 (0 disables the periodic sweep)",
                details={"value": self.reconcile_interval},
            )
        # Leases are renewed on a heartbeat while a handler runs, so a TTL
        # shorter than the longest permitted attempt is not a correctness
        # violation. It is still worth flagging: an attempt that blocks the
        # event loop long enough to miss heartbeats can be reclaimed while it
        # is still running.
        if self.lease_ttl <= self.max_attempt_timeout:
            log.warning(
                "runtime.lease_ttl_seconds is not greater than "
                "runtime.max_attempt_timeout_seconds; a long attempt that blocks "
                "the event loop could be reclaimed and re-executed",
                extra={
                    "lease_ttl_seconds": self.lease_ttl,
                    "max_attempt_timeout_seconds": self.max_attempt_timeout,
                },
            )

    def resolve_db_path(self, fallback: str) -> str:
        """Effective SQLite path: the override if set, else the main Jiro DB."""
        return self.db_path or fallback

    @property
    def heartbeat_interval(self) -> float:
        """How often a running worker renews its lease.

        A third of the TTL, so two consecutive renewals can be missed (one
        transient storage error, one scheduling delay) before the lease is
        considered lapsed.
        """
        return self.lease_ttl / 3.0


__all__ = [
    "CONFIG_SECTION",
    "MAX_ATTEMPTS_CEILING",
    "MAX_ATTEMPT_TIMEOUT_CEILING",
    "MAX_CONCURRENCY_CEILING",
    "RuntimeConfig",
]
