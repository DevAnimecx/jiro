"""Per-type content validation and deduplication key derivation.

Each memory type has a genuinely different payload contract:

* **working** — a small ``text``/``value`` blob plus relevance hints. Nothing
  that deserves provenance or versioning.
* **episodic** — an event with an outcome and artifact *references*. Large
  results are referenced, never copied.
* **semantic** — a subject/predicate/value triple with validity and provenance.
* **procedural** — a declarative step list that is validated structurally and is
  never executed.

Deduplication keys are derived from *source identity* where a source exists,
because that is stable, and fall back to normalised content otherwise. Fuzzy
matching is used only to raise a review candidate, never to merge destructively.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from jiro.memory.errors import MemoryValidationError, redact_text, redact_value
from jiro.memory.types import (
    MemoryType,
    Provenance,
    canonical_json,
    dedup_digest,
)

#: Operations a stored procedure may name. A procedure step refers to one of
#: these *registered* operations; it never supplies a callable. Enforced so a
#: stored record cannot describe "run this Python expression".
ALLOWED_STEP_ACTIONS: FrozenSet[str] = frozenset({
    "search",
    "scrape",
    "extract",
    "compare",
    "summarize",
    "remember",
    "recall",
    "classify",
    "verify",
    "await_condition",
    "escalate",
})

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


def normalise_text(text: str) -> str:
    """Case-folded, NFKC-normalised, whitespace-collapsed, punctuation-stripped.

    Used for lexical comparison and for content-derived dedup keys. Deliberately
    conservative: it removes formatting noise, not meaning.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    # Punctuation is replaced before whitespace is collapsed, otherwise
    # "a, b" becomes "a  b" (a space either side of the comma) and token
    # counting drifts.
    despaced = _PUNCTUATION.sub(" ", folded)
    return _WHITESPACE.sub(" ", despaced).strip()


def tokenise(text: str) -> List[str]:
    """Content tokens for lexical scoring. Drops very short and stop-like words."""
    tokens = [t for t in normalise_text(text).split() if len(t) > 2]
    return [t for t in tokens if t not in _STOPWORDS]


_STOPWORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "from", "have", "has", "was",
    "were", "are", "but", "not", "you", "your", "its", "it's", "they", "them",
    "his", "her", "our", "out", "get", "got", "has", "had", "been", "being",
    "into", "than", "then", "there", "here", "will", "would", "could", "should",
    "about", "which", "while", "where", "these", "those", "some", "more",
    "most", "other", "such", "only", "also", "just", "like", "over", "under",
})


# --------------------------------------------------------------- working memory


def validate_working(
    content: Any, *, max_bytes: int, ttl_seconds: Optional[float], now: float
) -> Tuple[Dict[str, Any], str, Optional[float]]:
    """Validate a working-memory payload.

    Working memory is deliberately small: a short label plus either text or a
    JSON-scalar value. It is scratch context, not a document store.

    Returns:
        ``(content, searchable_text, expires_at)``.
    """
    if not isinstance(content, Mapping):
        raise MemoryValidationError(
            "working memory content must be an object",
            details={"received": type(content).__name__},
        )
    allowed = {"text", "value", "kind", "relevance", "source_task_id", "notes"}
    unknown = sorted(set(content) - allowed)
    if unknown:
        raise MemoryValidationError(
            f"unknown working memory field(s): {unknown}",
            details={"unknown": unknown, "allowed": sorted(allowed)},
        )

    text = content.get("text")
    value = content.get("value")
    if not text and value is None:
        raise MemoryValidationError(
            "working memory requires either 'text' or 'value'",
            details={"allowed": sorted(allowed)},
        )

    cleaned: Dict[str, Any] = {}
    if text is not None:
        if not isinstance(text, str):
            raise MemoryValidationError("working memory 'text' must be a string")
        cleaned["text"] = redact_text(text, max_chars=8192)
    if value is not None:
        # JSON scalars and small arrays only; a working entry is not a payload.
        if not isinstance(value, (str, int, float, bool)) and not (
            isinstance(value, (list, tuple))
            and all(isinstance(v, (str, int, float, bool)) for v in value)
        ):
            raise MemoryValidationError(
                "working memory 'value' must be a JSON scalar or a list of them"
            )
        cleaned["value"] = redact_value(value, max_chars=4096)
    kind = content.get("kind")
    if kind is not None:
        if not isinstance(kind, str) or not kind.strip():
            raise MemoryValidationError("working memory 'kind' must be a string")
        cleaned["kind"] = kind.strip()[:64]
    relevance = content.get("relevance")
    if relevance is not None:
        if isinstance(relevance, bool) or not isinstance(relevance, (int, float)):
            raise MemoryValidationError("working memory 'relevance' must be a number")
        cleaned["relevance"] = max(0.0, min(1.0, float(relevance)))
    source_task = content.get("source_task_id")
    if source_task is not None:
        if not isinstance(source_task, str):
            raise MemoryValidationError(
                "working memory 'source_task_id' must be a string"
            )
        cleaned["source_task_id"] = source_task[:64]
    notes = content.get("notes")
    if notes is not None:
        if not isinstance(notes, str):
            raise MemoryValidationError("working memory 'notes' must be a string")
        cleaned["notes"] = redact_text(notes, max_chars=2000)

    searchable = "\n".join(
        str(cleaned.get(k)) for k in ("text", "value", "kind", "notes")
        if cleaned.get(k) is not None
    )
    if len(searchable.encode("utf-8")) > max_bytes:
        raise MemoryValidationError(
            f"working memory exceeds {max_bytes} bytes",
            details={"bytes": len(searchable.encode('utf-8'))},
        )

    expires_at = (now + ttl_seconds) if ttl_seconds else None
    return cleaned, searchable, expires_at


# --------------------------------------------------------------- episodic memory


def validate_episodic(
    content: Any, *, max_bytes: int
) -> Tuple[Dict[str, Any], str]:
    """Validate an episodic payload.

    An episode is a record that something happened, with an outcome and
    references to any artifacts. Artifacts are *referenced*, never copied: a
    task result can be megabytes and a memory record is not a document store.
    """
    if not isinstance(content, Mapping):
        raise MemoryValidationError(
            "episodic content must be an object",
            details={"received": type(content).__name__},
        )
    allowed = {
        "event", "outcome", "task_id", "task_type", "task_state",
        "attempt", "duration_ms", "artifact_refs", "error_code",
        "links", "summary", "outcome_kind",
    }
    unknown = sorted(set(content) - allowed)
    if unknown:
        raise MemoryValidationError(
            f"unknown episodic field(s): {unknown}",
            details={"unknown": unknown, "allowed": sorted(allowed)},
        )

    event = content.get("event")
    if not isinstance(event, str) or not event.strip():
        raise MemoryValidationError("episodic memory requires a non-empty 'event'")

    cleaned: Dict[str, Any] = {"event": redact_text(event.strip(), max_chars=512)}
    for key in ("task_id", "task_type", "task_state", "error_code", "outcome_kind"):
        value = content.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise MemoryValidationError(f"episodic '{key}' must be a string")
            cleaned[key] = value[:128]
    duration = content.get("duration_ms")
    if duration is not None:
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise MemoryValidationError("episodic 'duration_ms' must be a number")
        if duration < 0:
            raise MemoryValidationError("episodic 'duration_ms' must be >= 0")
        cleaned["duration_ms"] = float(duration)
    attempt = content.get("attempt")
    if attempt is not None:
        if isinstance(attempt, bool) or not isinstance(attempt, int):
            raise MemoryValidationError("episodic 'attempt' must be an integer")
        cleaned["attempt"] = attempt

    refs = content.get("artifact_refs") or []
    if not isinstance(refs, (list, tuple)):
        raise MemoryValidationError("episodic 'artifact_refs' must be a list")
    if len(refs) > 50:
        raise MemoryValidationError(
            "episodic 'artifact_refs' accepts at most 50 entries",
            details={"received": len(refs)},
        )
    cleaned_refs: List[str] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            raise MemoryValidationError(
                "episodic 'artifact_refs' entries must be non-empty strings"
            )
        cleaned_refs.append(ref.strip()[:512])
    if cleaned_refs:
        cleaned["artifact_refs"] = cleaned_refs

    links = content.get("links") or []
    if not isinstance(links, (list, tuple)):
        raise MemoryValidationError("episodic 'links' must be a list")
    cleaned_links = [str(x)[:64] for x in links[:20] if str(x).strip()]
    if cleaned_links:
        cleaned["links"] = cleaned_links

    outcome = content.get("outcome")
    if outcome is not None:
        if not isinstance(outcome, str):
            raise MemoryValidationError("episodic 'outcome' must be a string")
        cleaned["outcome"] = redact_text(outcome, max_chars=max_bytes)
    summary = content.get("summary")
    if summary is not None:
        if not isinstance(summary, str):
            raise MemoryValidationError("episodic 'summary' must be a string")
        cleaned["summary"] = redact_text(summary, max_chars=max_bytes)

    searchable = " ".join(
        str(cleaned.get(k))
        for k in ("event", "outcome", "summary", "task_type", "task_state")
        if cleaned.get(k) is not None
    )
    if len(searchable.encode("utf-8")) > max_bytes:
        raise MemoryValidationError(
            f"episodic memory exceeds {max_bytes} bytes",
            details={"bytes": len(searchable.encode('utf-8'))},
        )
    return cleaned, searchable


# --------------------------------------------------------------- semantic memory


@dataclass(frozen=True)
class FactInput:
    """A caller-supplied fact, before it becomes a :class:`MemoryFact`."""

    subject: str
    predicate: str
    object_value: Any
    object_type: Optional[str] = None
    valid_from: Optional[float] = None
    valid_to: Optional[float] = None
    observed_at: Optional[float] = None
    verification_method: Optional[str] = None


def validate_semantic(content: Any, *, max_bytes: int) -> Tuple[Dict[str, Any], str]:
    """Validate a semantic payload into a fact-shaped content object.

    Facts are structured rather than prose because contradiction detection is
    only possible if two claims can be compared on ``(subject, predicate)``.
    """
    if not isinstance(content, Mapping):
        raise MemoryValidationError(
            "semantic content must be an object",
            details={"received": type(content).__name__},
        )
    allowed = {
        "subject", "predicate", "object", "object_type",
        "valid_from", "valid_to", "observed_at", "verification_method",
        "claim_key",
    }
    unknown = sorted(set(content) - allowed)
    if unknown:
        raise MemoryValidationError(
            f"unknown semantic field(s): {unknown}",
            details={"unknown": unknown, "allowed": sorted(allowed)},
        )

    for key in ("subject", "predicate"):
        value = content.get(key)
        if not isinstance(value, str) or not value.strip():
            raise MemoryValidationError(f"semantic memory requires a '{key}'")
    if "object" not in content or content["object"] is None:
        raise MemoryValidationError("semantic memory requires an 'object'")

    subject = content["subject"].strip()[:512]
    predicate = content["predicate"].strip()[:256]
    obj = content["object"]
    if isinstance(obj, (dict, list, tuple)):
        raise MemoryValidationError(
            "semantic 'object' must be a scalar or a flat list of scalars"
        )
    if isinstance(obj, str):
        obj_value: Any = redact_text(obj.strip(), max_chars=max_bytes)
    elif isinstance(obj, (int, float, bool)):
        obj_value = obj
    elif isinstance(obj, (list, tuple)):
        obj_value = [redact_value(v, max_chars=4096) for v in obj]
    else:
        raise MemoryValidationError("semantic 'object' type is not supported")

    cleaned: Dict[str, Any] = {
        "subject": subject,
        "predicate": predicate,
        "object": obj_value,
    }
    object_type = content.get("object_type")
    if object_type is not None:
        if not isinstance(object_type, str):
            raise MemoryValidationError("semantic 'object_type' must be a string")
        cleaned["object_type"] = object_type[:64]
    for key in ("valid_from", "valid_to", "observed_at"):
        value = content.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MemoryValidationError(f"semantic '{key}' must be a timestamp")
            cleaned[key] = float(value)
    if (cleaned.get("valid_from") is not None
            and cleaned.get("valid_to") is not None
            and cleaned["valid_to"] < cleaned["valid_from"]):
        raise MemoryValidationError("semantic 'valid_to' precedes 'valid_from'")
    method = content.get("verification_method")
    if method is not None:
        if not isinstance(method, str):
            raise MemoryValidationError(
                "semantic 'verification_method' must be a string"
            )
        cleaned["verification_method"] = method[:256]

    searchable = " ".join([subject, predicate, str(obj_value)])
    if len(searchable.encode("utf-8")) > max_bytes:
        raise MemoryValidationError(
            f"semantic memory exceeds {max_bytes} bytes",
            details={"bytes": len(searchable.encode('utf-8'))},
        )
    return cleaned, searchable


def fact_claim_key(subject: str, predicate: str) -> str:
    """Key identifying "a claim about this subject and predicate".

    Two facts sharing this key but disagreeing on the object are a
    contradiction, not a duplicate. Normalising case and whitespace is what
    stops ``"Python"`` and ``"python "`` from being treated as different claims.
    """
    return dedup_digest(normalise_text(subject), normalise_text(predicate))


# ------------------------------------------------------------ procedural memory


def validate_procedural(content: Any, *, max_bytes: int) -> Tuple[Dict[str, Any], str]:
    """Validate a procedural payload.

    Steps are checked for *structure* and against :data:`ALLOWED_STEP_ACTIONS`.
    Nothing here executes a step; the point of the action allow-list is that a
    stored record can only ever name an operation that was registered in code.
    """
    if not isinstance(content, Mapping):
        raise MemoryValidationError(
            "procedural content must be an object",
            details={"received": type(content).__name__},
        )
    allowed = {
        "name", "purpose", "preconditions", "input_schema", "steps",
        "required_capabilities", "expected_outputs", "validation_criteria",
        "known_limitations", "compatibility",
    }
    unknown = sorted(set(content) - allowed)
    if unknown:
        raise MemoryValidationError(
            f"unknown procedural field(s): {unknown}",
            details={"unknown": unknown, "allowed": sorted(allowed)},
        )

    name = content.get("name")
    purpose = content.get("purpose")
    if not isinstance(name, str) or not name.strip():
        raise MemoryValidationError("procedural memory requires a 'name'")
    if not isinstance(purpose, str) or not purpose.strip():
        raise MemoryValidationError("procedural memory requires a 'purpose'")

    steps = content.get("steps")
    if not isinstance(steps, (list, tuple)):
        raise MemoryValidationError("procedural memory requires a 'steps' list")
    if not steps:
        raise MemoryValidationError("procedural memory requires at least one step")
    if len(steps) > 50:
        raise MemoryValidationError(
            "procedural memory accepts at most 50 steps",
            details={"received": len(steps)},
        )

    cleaned_steps: List[Dict[str, Any]] = []
    for index, step in enumerate(steps):
        cleaned_steps.append(_validate_step(index, step))

    cleaned: Dict[str, Any] = {
        "name": name.strip()[:256],
        "purpose": purpose.strip()[:1000],
        "steps": cleaned_steps,
    }
    for key in ("preconditions", "required_capabilities",
                "expected_outputs", "validation_criteria", "known_limitations"):
        value = content.get(key)
        if value is None:
            continue
        if not isinstance(value, (list, tuple)):
            raise MemoryValidationError(f"procedural '{key}' must be a list")
        items = []
        for entry in value:
            if not isinstance(entry, str) or not entry.strip():
                raise MemoryValidationError(
                    f"procedural '{key}' entries must be non-empty strings"
                )
            items.append(entry.strip()[:500])
        if items:
            cleaned[key] = items

    schema = content.get("input_schema")
    if schema is not None:
        if not isinstance(schema, Mapping):
            raise MemoryValidationError("procedural 'input_schema' must be an object")
        cleaned["input_schema"] = redact_value(dict(schema), max_chars=8192)
    compat = content.get("compatibility")
    if compat is not None:
        if not isinstance(compat, Mapping):
            raise MemoryValidationError("procedural 'compatibility' must be an object")
        cleaned["compatibility"] = redact_value(dict(compat), max_chars=4096)

    searchable = " ".join([cleaned["name"], cleaned["purpose"]])
    if len(searchable.encode("utf-8")) > max_bytes:
        raise MemoryValidationError(
            f"procedural memory exceeds {max_bytes} bytes",
            details={"bytes": len(searchable.encode('utf-8'))},
        )
    return cleaned, searchable


def _validate_step(index: int, step: Any) -> Dict[str, Any]:
    if not isinstance(step, Mapping):
        raise MemoryValidationError(
            f"procedural step {index} must be an object",
            details={"index": index},
        )
    allowed = {"action", "description", "requires", "capture_as", "notes"}
    unknown = sorted(set(step) - allowed)
    if unknown:
        raise MemoryValidationError(
            f"procedural step {index} has unknown field(s): {unknown}",
            details={"index": index, "unknown": unknown},
        )
    action = step.get("action")
    if not isinstance(action, str) or not action.strip():
        raise MemoryValidationError(
            f"procedural step {index} requires an 'action'",
            details={"index": index},
        )
    action = action.strip()
    if action not in ALLOWED_STEP_ACTIONS:
        raise MemoryValidationError(
            f"procedural step {index} names an unregistered action '{action}'",
            details={
                "index": index,
                "action": action,
                "allowed": sorted(ALLOWED_STEP_ACTIONS),
            },
        )
    description = step.get("description")
    if not isinstance(description, str) or not description.strip():
        raise MemoryValidationError(
            f"procedural step {index} requires a 'description'",
            details={"index": index},
        )
    cleaned: Dict[str, Any] = {
        "action": action,
        "description": redact_text(description.strip(), max_chars=1000),
    }
    requires = step.get("requires")
    if requires is not None:
        if not isinstance(requires, (list, tuple)):
            raise MemoryValidationError(
                f"procedural step {index} 'requires' must be a list"
            )
        cleaned["requires"] = [str(r)[:64] for r in requires[:10]]
    capture = step.get("capture_as")
    if capture is not None:
        if not isinstance(capture, str) or not capture.strip():
            raise MemoryValidationError(
                f"procedural step {index} 'capture_as' must be a string"
            )
        cleaned["capture_as"] = capture.strip()[:64]
    notes = step.get("notes")
    if notes is not None:
        if not isinstance(notes, str):
            raise MemoryValidationError(
                f"procedural step {index} 'notes' must be a string"
            )
        cleaned["notes"] = redact_text(notes, max_chars=500)
    return cleaned


# -------------------------------------------------------------- deduplication


def working_dedup_key(scope_id: str, content: Mapping[str, Any]) -> str:
    """Content-derived key for working memory.

    Working memory is deliberately *not* idempotent by source: two entries can
    legitimately hold the same scratch value at different points in a task.
    Deduping them would silently collapse distinct context.
    """
    return dedup_digest("working", scope_id, canonical_json(content))


def episodic_dedup_key(content: Mapping[str, Any], scope_id: str = "") -> str:
    """Source-identity key for an episode.

    Anchored on the task and attempt rather than the prose, so a re-run that
    produces a slightly different summary is still recognised as the same
    event.

    Falls back to the event text plus the scope, because two tasks can
    legitimately record the same event ("task.succeeded") and collapsing them
    would lose one of the two experiences.
    """
    task_id = content.get("task_id")
    if task_id:
        return dedup_digest("episodic", str(task_id), str(content.get("attempt", 0)))
    return dedup_digest("episodic", scope_id,
                        normalise_text(str(content.get("event", ""))))


def semantic_dedup_key(claim_key: str, subject: str, predicate: str,
                       object_value: Any) -> str:
    """Claim identity for a semantic fact.

    Includes the object, so two facts about the same subject/predicate with
    different objects are *not* duplicates — they are a contradiction, and the
    service needs to see both to detect it.
    """
    return dedup_digest("semantic", claim_key, subject.strip(), predicate.strip(),
                        str(object_value))


def procedural_dedup_key(name: str, content: Mapping[str, Any]) -> str:
    """Identity for a procedure.

    Anchored on name *and* structural content, so editing a procedure creates a
    new candidate rather than silently overwriting the validated one. The
    versioned lifecycle handles the rest.
    """
    steps = canonical_json(content.get("steps", []))
    return dedup_digest("procedural", normalise_text(name), steps)


def derive_dedup_key(
    memory_type: MemoryType,
    *,
    scope_id: str,
    content: Mapping[str, Any],
) -> str:
    """Pick the right dedup strategy for a memory type."""
    if memory_type is MemoryType.WORKING:
        return working_dedup_key(scope_id, content)
    if memory_type is MemoryType.EPISODIC:
        return episodic_dedup_key(content, scope_id)
    if memory_type is MemoryType.SEMANTIC:
        claim_key = fact_claim_key(str(content["subject"]), str(content["predicate"]))
        return semantic_dedup_key(
            claim_key, str(content["subject"]), str(content["predicate"]),
            content.get("object"),
        )
    return procedural_dedup_key(str(content.get("name", "")), content)


def near_duplicate(left: str, right: str, *, threshold: float = 0.9) -> bool:
    """Cheap token-overlap similarity used only to raise a *review candidate*.

    Never used to merge. Two records with different sources or different
    verification status stay separate regardless of how similar their text is.
    """
    left_tokens = set(tokenise(left))
    right_tokens = set(tokenise(right))
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    overlap = len(left_tokens & right_tokens)
    return overlap / min(len(left_tokens), len(right_tokens)) >= threshold


def validate_tags(tags: Any, *, max_tags: int = 32) -> Tuple[str, ...]:
    """Normalise a tag list: lowercase, deduplicated, bounded."""
    if tags is None:
        return ()
    if not isinstance(tags, (list, tuple, set)):
        raise MemoryValidationError("tags must be a list of strings")
    if len(tags) > max_tags:
        raise MemoryValidationError(
            f"at most {max_tags} tags are allowed",
            details={"received": len(tags)},
        )
    seen: List[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            raise MemoryValidationError("tags must be strings")
        cleaned = tag.strip().lower()[:64]
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return tuple(seen)


def validate_importance(value: Any) -> float:
    """Clamp an explicit importance weight into ``[0, 1]``."""
    if value is None:
        return 0.5
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MemoryValidationError("importance must be a number")
    if value < 0.0 or value > 1.0:
        raise MemoryValidationError("importance must be within [0, 1]")
    return float(value)


def validate_confidence(value: Any) -> Optional[float]:
    """Validate a confidence hint.

    ``None`` means "not meaningful for this record type" and is a first-class
    value. Rejects non-numbers and out-of-range values rather than clamping,
    because a silently clamped confidence would be read as a real number.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MemoryValidationError("confidence must be a number or None")
    if value < 0.0 or value > 1.0:
        raise MemoryValidationError("confidence must be within [0, 1]")
    return float(value)


def validate_provenance(items: Any, *, max_items: int = 20) -> Tuple[Provenance, ...]:
    """Validate caller-supplied provenance records.

    A source is never invented here: ``source_kind`` and ``source_id`` are both
    required, and ``method`` is constrained to a known set so a record cannot
    claim to be verified when it was merely guessed.
    """
    if items is None:
        return ()
    if not isinstance(items, (list, tuple)):
        raise MemoryValidationError("provenance must be a list")
    if len(items) > max_items:
        raise MemoryValidationError(
            f"at most {max_items} provenance records are allowed",
            details={"received": len(items)},
        )
    allowed_methods = {
        "observed", "extracted", "user_provided", "imported", "summary", "derived",
    }
    out: List[Provenance] = []
    for item in items:
        if isinstance(item, Provenance):
            provenance = item
        elif isinstance(item, Mapping):
            unknown = sorted(set(item) - {
                "source_kind", "source_id", "source_url", "locator", "method",
                "observed_at", "published_at", "note",
            })
            if unknown:
                raise MemoryValidationError(
                    f"unknown provenance field(s): {unknown}",
                    details={"unknown": unknown},
                )
            if not item.get("source_kind") or not item.get("source_id"):
                raise MemoryValidationError(
                    "provenance requires both 'source_kind' and 'source_id'"
                )
            method = item.get("method") or "observed"
            if method not in allowed_methods:
                raise MemoryValidationError(
                    f"provenance method '{method}' is not recognised",
                    details={"allowed": sorted(allowed_methods)},
                )
            provenance = Provenance(
                source_kind=str(item["source_kind"])[:64],
                source_id=str(item["source_id"])[:512],
                source_url=(redact_text(str(item["source_url"]), max_chars=2048)
                            if item.get("source_url") else None),
                locator=(str(item["locator"])[:512]
                         if item.get("locator") else None),
                method=method,
                observed_at=(float(item["observed_at"])
                             if item.get("observed_at") else None),
                published_at=(float(item["published_at"])
                              if item.get("published_at") else None),
                note=(redact_text(str(item["note"]), max_chars=1000)
                      if item.get("note") else None),
            )
        else:
            raise MemoryValidationError(
                "provenance entries must be objects or Provenance instances"
            )
        out.append(provenance)
    return tuple(out)


__all__ = [
    "ALLOWED_STEP_ACTIONS",
    "FactInput",
    "derive_dedup_key",
    "episodic_dedup_key",
    "fact_claim_key",
    "near_duplicate",
    "normalise_text",
    "procedural_dedup_key",
    "semantic_dedup_key",
    "tokenise",
    "validate_confidence",
    "validate_episodic",
    "validate_importance",
    "validate_procedural",
    "validate_provenance",
    "validate_semantic",
    "validate_tags",
    "validate_working",
    "working_dedup_key",
]