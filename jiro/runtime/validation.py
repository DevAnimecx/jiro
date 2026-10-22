"""Task input and limit validation.

Two distinct jobs live here:

* :func:`validate_input` checks a submission against the schema declared by its
  registered handler. This is a closed-world, allow-list validator — an unknown
  field is rejected rather than ignored, so a typo cannot silently change
  behaviour and a hostile payload cannot smuggle extra keys through.
* :func:`resolve_limits` applies the requested execution limits on top of
  server-side defaults and hard ceilings.

Neither function ever executes caller-supplied code. Validation inspects data
only.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from jiro.runtime.config import (
    MAX_ATTEMPTS_CEILING,
    MAX_ATTEMPT_TIMEOUT_CEILING,
    MAX_CONCURRENCY_CEILING,
    RuntimeConfig,
)
from jiro.runtime.errors import TaskLimitExceeded, TaskValidationError
from jiro.runtime.types import TaskLimits

#: JSON-ish scalars a task input field may declare.
FieldType = str

_STR_TYPES = ("str",)
_NUM_TYPES = ("int", "float")
_BOOL_TYPES = ("bool",)


class Field:
    """Declaration for one field of a task input schema.

    Args:
        name: field name as it appears in the task input.
        type: one of ``str``, ``int``, ``float``, ``bool``, ``list``, ``dict``.
        required: reject a missing value rather than using ``default``.
        default: value substituted when absent and not required.
        choices: closed set of permitted values, for enums.
        max_length: cap on ``str`` length or ``list``/``dict`` item count.
        min_value / max_value: inclusive numeric bounds.
        max_bytes: cap on the serialised size of a ``str``/``dict``/``list``.
        item_type: element type for ``list`` fields.
    """

    __slots__ = (
        "name",
        "type",
        "required",
        "default",
        "choices",
        "max_length",
        "min_value",
        "max_value",
        "max_bytes",
        "item_type",
    )

    def __init__(
        self,
        name: str,
        type: FieldType = "str",
        *,
        required: bool = False,
        default: Any = None,
        choices: Optional[Sequence[Any]] = None,
        max_length: Optional[int] = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        max_bytes: Optional[int] = 65_536,
        item_type: FieldType = "str",
    ) -> None:
        self.name = name
        self.type = type
        self.required = required
        self.default = default
        self.choices = tuple(choices) if choices is not None else None
        self.max_length = max_length
        self.min_value = min_value
        self.max_value = max_value
        self.max_bytes = max_bytes
        self.item_type = item_type


class TaskSchema:
    """Allow-list schema for one task type."""

    def __init__(self, name: str, fields: Sequence[Field]) -> None:
        self.name = name
        self.fields = tuple(fields)
        self._by_name = {f.name: f for f in fields}
        if len(self._by_name) != len(self.fields):
            raise TaskValidationError(
                f"schema '{name}' declares duplicate field names",
                details={"schema": name},
            )

    def field_names(self) -> tuple:
        """Declared field names, in declaration order."""
        return tuple(self._by_name)

    def describe(self) -> Dict[str, Any]:
        """JSON-schema-shaped description, used by CLI and MCP help output."""
        props: Dict[str, Any] = {}
        required: list[str] = []
        for f in self.fields:
            spec: Dict[str, Any] = {"type": f.type}
            if f.choices:
                spec["enum"] = list(f.choices)
            if f.default is not None:
                spec["default"] = f.default
            if f.min_value is not None:
                spec["minimum"] = f.min_value
            if f.max_value is not None:
                spec["maximum"] = f.max_value
            props[f.name] = spec
            if f.required:
                required.append(f.name)
        return {"type": "object", "properties": props, "required": required}


def _type_name(value: Any) -> str:
    return type(value).__name__


def _serialised_size(value: Any) -> int:
    import json

    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return 0


def _validate_field(schema_name: str, field: Field, value: Any) -> Any:
    if field.type in _BOOL_TYPES:
        if not isinstance(value, bool):
            raise TaskValidationError(
                f"field '{field.name}' must be a boolean",
                details={"schema": schema_name, "field": field.name, "received": _type_name(value)},
            )
    elif field.type == "int":
        # bool is a subclass of int in Python; a boolean is not an integer here.
        if isinstance(value, bool) or not isinstance(value, int):
            raise TaskValidationError(
                f"field '{field.name}' must be an integer",
                details={"schema": schema_name, "field": field.name, "received": _type_name(value)},
            )
    elif field.type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TaskValidationError(
                f"field '{field.name}' must be a number",
                details={"schema": schema_name, "field": field.name, "received": _type_name(value)},
            )
        value = float(value)
    elif field.type in _STR_TYPES:
        if not isinstance(value, str):
            raise TaskValidationError(
                f"field '{field.name}' must be a string",
                details={"schema": schema_name, "field": field.name, "received": _type_name(value)},
            )
    elif field.type == "list":
        if not isinstance(value, list):
            raise TaskValidationError(
                f"field '{field.name}' must be a list",
                details={"schema": schema_name, "field": field.name, "received": _type_name(value)},
            )
    elif field.type == "dict":
        if not isinstance(value, dict):
            raise TaskValidationError(
                f"field '{field.name}' must be an object",
                details={"schema": schema_name, "field": field.name, "received": _type_name(value)},
            )
    else:  # pragma: no cover - guarded at Field construction time in practice
        raise TaskValidationError(
            f"schema '{schema_name}' declares unsupported type '{field.type}'",
            details={"schema": schema_name, "field": field.name},
        )

    if field.choices is not None and value not in field.choices:
        raise TaskValidationError(
            f"field '{field.name}' must be one of {sorted(map(str, field.choices))}",
            details={
                "schema": schema_name,
                "field": field.name,
                "received": value,
                "allowed": list(field.choices),
            },
        )

    if field.max_length is not None:
        length = len(value) if isinstance(value, (str, list, dict)) else None
        if length is not None and length > field.max_length:
            raise TaskValidationError(
                f"field '{field.name}' exceeds max length {field.max_length}",
                details={
                    "schema": schema_name,
                    "field": field.name,
                    "length": length,
                    "max": field.max_length,
                },
            )

    if (
        field.min_value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        if value < field.min_value:
            raise TaskValidationError(
                f"field '{field.name}' must be >= {field.min_value}",
                details={"schema": schema_name, "field": field.name, "received": value},
            )
    if (
        field.max_value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        if value > field.max_value:
            raise TaskValidationError(
                f"field '{field.name}' must be <= {field.max_value}",
                details={"schema": schema_name, "field": field.name, "received": value},
            )

    if field.max_bytes is not None and isinstance(value, (str, list, dict)):
        size = _serialised_size(value)
        if size > field.max_bytes:
            raise TaskValidationError(
                f"field '{field.name}' exceeds {field.max_bytes} bytes",
                details={"schema": schema_name, "field": field.name, "bytes": size},
            )

    if field.type == "list" and field.item_type:
        for index, item in enumerate(value):
            _validate_field(
                schema_name,
                Field(f"{field.name}[{index}]", field.item_type, max_bytes=None),
                item,
            )
    return value


def validate_input(schema: TaskSchema, payload: Any) -> Dict[str, Any]:
    """Validate ``payload`` against ``schema`` and return a normalised copy.

    Unknown fields are rejected. Missing optional fields are filled from their
    declared default. The returned dict contains only declared fields, so it is
    safe to persist verbatim.

    Raises:
        TaskValidationError: on any structural, type, or bound violation.
    """
    if not isinstance(payload, Mapping):
        raise TaskValidationError(
            f"task input must be an object, got {_type_name(payload)}",
            details={"schema": schema.name},
        )

    allowed = schema.field_names()
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise TaskValidationError(
            f"unknown field(s) for task type '{schema.name}': {unknown}",
            details={"schema": schema.name, "unknown": unknown, "allowed": list(allowed)},
        )

    cleaned: Dict[str, Any] = {}
    for field in schema.fields:
        if field.name in payload:
            cleaned[field.name] = _validate_field(schema.name, field, payload[field.name])
        elif field.required:
            raise TaskValidationError(
                f"missing required field '{field.name}' for task type '{schema.name}'",
                details={"schema": schema.name, "field": field.name},
            )
        elif field.default is not None:
            cleaned[field.name] = field.default
    return cleaned


def resolve_limits(
    config: RuntimeConfig,
    requested: Optional[Mapping[str, Any]] = None,
    *,
    now: float,
) -> TaskLimits:
    """Merge caller-requested limits over server defaults, enforcing ceilings.

    A caller may request *less* than the defaults. Requesting more than a hard
    ceiling is an error rather than a silent clamp, so a client that believes it
    got a 24-hour budget finds out immediately.

    Args:
        config: validated server configuration supplying defaults and ceilings.
        requested: untrusted client-supplied overrides.
        now: epoch seconds used to resolve a relative ``deadline_in_seconds``.

    Returns:
        Fully populated, validated :class:`~jiro.runtime.types.TaskLimits`.

    Raises:
        TaskValidationError: if the request is malformed.
        TaskLimitExceeded: if a request exceeds a server hard ceiling.
    """
    data = dict(requested or {})

    max_attempts = int(data.pop("max_attempts", config.default_max_attempts))
    if not 1 <= max_attempts <= config.max_max_attempts:
        raise TaskLimitExceeded(
            f"max_attempts must be within [1, {config.max_max_attempts}]",
            details={"requested": max_attempts, "max": config.max_max_attempts},
        )
    if max_attempts > MAX_ATTEMPTS_CEILING:  # defence in depth
        raise TaskLimitExceeded(
            f"max_attempts must be <= {MAX_ATTEMPTS_CEILING}",
            details={"requested": max_attempts},
        )

    attempt_timeout = float(data.pop("attempt_timeout_seconds", config.default_attempt_timeout))
    if attempt_timeout <= 0:
        raise TaskValidationError(
            "attempt_timeout_seconds must be > 0",
            details={"requested": attempt_timeout},
        )
    if attempt_timeout > min(config.max_attempt_timeout, MAX_ATTEMPT_TIMEOUT_CEILING):
        raise TaskLimitExceeded(
            f"attempt_timeout_seconds must be <= "
            f"{min(config.max_attempt_timeout, MAX_ATTEMPT_TIMEOUT_CEILING)}",
            details={
                "requested": attempt_timeout,
                "max": min(config.max_attempt_timeout, MAX_ATTEMPT_TIMEOUT_CEILING),
            },
        )

    # Absolute deadline. Two forms are accepted; both resolve to an epoch.
    deadline_at: Optional[float] = None
    if "deadline_at" in data and data["deadline_at"] is not None:
        try:
            deadline_at = float(data.pop("deadline_at"))
        except (TypeError, ValueError) as exc:
            raise TaskValidationError(
                "deadline_at must be an epoch timestamp", details={"received": data["deadline_at"]}
            ) from exc
    relative = data.pop("deadline_in_seconds", None)
    if relative is not None:
        try:
            span = float(relative)
        except (TypeError, ValueError) as exc:
            raise TaskValidationError(
                "deadline_in_seconds must be a number", details={"received": relative}
            ) from exc
        if span <= 0:
            raise TaskValidationError("deadline_in_seconds must be > 0", details={"received": span})
        resolved = now + span
        # An absolute deadline supplied alongside a relative one must not be
        # silently discarded; take the tighter of the two.
        deadline_at = resolved if deadline_at is None else min(deadline_at, resolved)

    if data:
        raise TaskValidationError(
            f"unknown limit field(s): {sorted(data)}",
            details={"unknown": sorted(data)},
        )

    return TaskLimits(
        max_attempts=max_attempts,
        attempt_timeout=attempt_timeout,
        deadline=deadline_at,
        retry_backoff_base=config.retry_backoff_base,
        retry_backoff_max=config.retry_backoff_max,
        retry_jitter_ratio=config.retry_jitter_ratio,
        max_result_bytes=config.max_result_bytes,
    )


def clamp_concurrency(requested: int, config: RuntimeConfig) -> int:
    """Validate a caller-requested concurrency against the server ceiling."""
    value = int(requested)
    ceiling = min(config.max_concurrency, MAX_CONCURRENCY_CEILING)
    if value < 1 or value > ceiling:
        raise TaskLimitExceeded(
            f"concurrency must be within [1, {ceiling}]",
            details={"requested": value, "max": ceiling},
        )
    return value


#: Convenience alias so handlers can declare a schema with a single argument.
SchemaBuilder = Callable[..., TaskSchema]


__all__ = [
    "Field",
    "SchemaBuilder",
    "TaskSchema",
    "clamp_concurrency",
    "resolve_limits",
    "validate_input",
]
