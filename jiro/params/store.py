"""Parameter set persistence, binding, decay, diff, and merge."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from jiro.parametric.compress import estimate_tokens, select_claims
from jiro.parametric.extract import normalize_text
from jiro.parametric.packet import Claim, Source, validate_packet
from jiro.params.model import Binding, ParameterSet

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

DEFAULT_TTL_POLICY: Dict[str, Any] = {
    "default_hours": 168,
    "by_kind": {
        "timeline": 24,
        "numeric": 168,
        "fact": 168,
        "entity": 720,
        "quote": 720,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def build_from_packet(
    packet: Dict[str, Any],
    session_id: str,
    *,
    ttl_policy: Optional[Dict[str, Any]] = None,
) -> ParameterSet:
    validate_packet(packet)
    if not session_id:
        raise ValueError("session_id is required")
    claims = tuple(Claim(**claim) for claim in packet.get("claims") or [])
    sources = tuple(Source(**source) for source in packet.get("sources") or [])
    policy = dict(DEFAULT_TTL_POLICY)
    if ttl_policy:
        policy.update(ttl_policy)
    payload = {
        "session_id": session_id,
        "packet": packet.get("provenance_digest"),
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "sources": [source.model_dump(mode="json") for source in sources],
        "ttl_policy": policy,
    }
    digest = _hash(payload)
    return ParameterSet(
        id=f"ps_{digest[:16]}",
        session_id=session_id,
        version=1,
        created_at=_now(),
        claims=claims,
        sources=sources,
        ttl_policy=policy,
        freshness={claim.id: 1.0 for claim in claims},
        provenance_digest=digest,
    )


def _key(claim: Claim) -> str:
    return normalize_text(claim.text).lower()


def bind(
    param_set: ParameterSet,
    *,
    max_tokens: int = 1200,
    model_profile: str = "generic",
) -> Binding:
    if max_tokens < 64:
        raise ValueError("max_tokens must be at least 64")

    candidates = []
    for claim in param_set.claims:
        item = claim.model_dump(mode="json")
        item["score"] = round(
            claim.confidence * float(param_set.freshness.get(claim.id, 1.0)), 6
        )
        candidates.append(item)

    source_overhead = min(8 * len(param_set.sources), max(0, max_tokens // 3))
    reserved = 16 + source_overhead + estimate_tokens("Evidence set")
    selected, claim_tokens, omitted = select_claims(
        candidates, max_tokens=max_tokens, reserved_tokens=reserved
    )
    used = min(max_tokens, reserved + claim_tokens)

    notes: List[str] = []
    if omitted:
        notes.append(f"{omitted} claims omitted for budget")
    if any(value < 0.5 for value in param_set.freshness.values()):
        notes.append("some claims are stale; run decay or refresh the watch")

    return Binding(
        set_id=param_set.id,
        version=param_set.version,
        model_profile=model_profile,
        max_tokens=max_tokens,
        used_tokens=used,
        claims=tuple(selected),
        sources=tuple(
            {
                "id": source.id,
                "url": source.url,
                "domain": source.domain,
                "title": source.title,
                "fetched_at": source.fetched_at,
            }
            for source in param_set.sources
        ),
        freshness=dict(param_set.freshness),
        notes=tuple(notes),
    )


def decay(
    param_set: ParameterSet,
    *,
    now: Optional[datetime] = None,
) -> ParameterSet:
    now = now or datetime.now(timezone.utc)
    policy = param_set.ttl_policy or DEFAULT_TTL_POLICY
    by_kind = policy.get("by_kind") or {}
    default_hours = float(policy.get("default_hours") or 168)
    freshness: Dict[str, float] = {}
    for claim in param_set.claims:
        observed = _parse_time(claim.observed_at)
        if observed is None:
            freshness[claim.id] = 0.5
            continue
        age_hours = max(0.0, (now - observed).total_seconds() / 3600)
        half_life = float(by_kind.get(claim.kind, default_hours))
        freshness[claim.id] = round(0.5 ** (age_hours / max(1.0, half_life)), 4)
    return param_set.model_copy(update={"freshness": freshness})


def diff(old: ParameterSet, new: ParameterSet) -> Dict[str, Any]:
    old_claims = {_key(claim): claim for claim in old.claims}
    new_claims = {_key(claim): claim for claim in new.claims}
    added = [new_claims[key].model_dump(mode="json") for key in sorted(new_claims.keys() - old_claims.keys())]
    removed = [old_claims[key].model_dump(mode="json") for key in sorted(old_claims.keys() - new_claims.keys())]
    changed = []
    for key in sorted(old_claims.keys() & new_claims.keys()):
        before, after = old_claims[key], new_claims[key]
        if (
            before.support != after.support
            or before.taint != after.taint
            or before.observed_at != after.observed_at
        ):
            changed.append(
                {
                    "before": before.model_dump(mode="json"),
                    "after": after.model_dump(mode="json"),
                }
            )

    old_sources = {source.url for source in old.sources}
    new_sources = {source.url for source in new.sources}
    return {
        "from_set": old.id,
        "to_set": new.id,
        "from_version": old.version,
        "to_version": new.version,
        "added": added,
        "removed": removed,
        "changed": changed,
        "sources_added": sorted(new_sources - old_sources),
        "sources_removed": sorted(old_sources - new_sources),
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
    }


def merge(a: ParameterSet, b: ParameterSet) -> ParameterSet:
    if a.session_id != b.session_id:
        raise ValueError("cannot merge parameter sets from different sessions")
    merged_claims: Dict[str, Claim] = {}
    for claim in list(a.claims) + list(b.claims):
        key = _key(claim)
        current = merged_claims.get(key)
        if current is None:
            merged_claims[key] = claim
            continue
        # Keep the higher-confidence version; union source ids.
        source_ids = tuple(sorted(set(current.source_ids) | set(claim.source_ids)))
        winner = claim if claim.confidence > current.confidence else current
        merged_claims[key] = winner.model_copy(
            update={
                "source_ids": source_ids,
                "support": "corroborated" if len(source_ids) > 1 else winner.support,
            }
        )
    merged_sources: Dict[str, Source] = {source.url: source for source in a.sources}
    for source in b.sources:
        merged_sources.setdefault(source.url, source)

    claims = tuple(sorted(merged_claims.values(), key=lambda c: c.id))
    sources = tuple(sorted(merged_sources.values(), key=lambda s: s.url))
    payload = {
        "session_id": a.session_id,
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "sources": [source.model_dump(mode="json") for source in sources],
        "parents": [a.id, b.id],
    }
    digest = _hash(payload)
    return ParameterSet(
        id=f"ps_{digest[:16]}",
        session_id=a.session_id,
        version=max(a.version, b.version) + 1,
        created_at=_now(),
        claims=claims,
        sources=sources,
        ttl_policy=dict(a.ttl_policy or DEFAULT_TTL_POLICY),
        freshness={**a.freshness, **b.freshness},
        provenance_digest=digest,
    )


class ParameterStore:
    """File-backed local store. One JSON document per parameter set."""

    def __init__(self, root: Optional[Path | str] = None) -> None:
        self.root = Path(root).expanduser() if root else Path("~/.jiro/params").expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, set_id: str) -> Path:
        if not _ID_RE.match(set_id):
            raise ValueError(f"invalid parameter set id: {set_id!r}")
        return self.root / f"{set_id}.json"

    def save(self, param_set: ParameterSet) -> Path:
        path = self._path(param_set.id)
        tmp = path.with_suffix(".tmp")
        payload = param_set.model_dump(mode="json", by_alias=True)
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, set_id: str) -> ParameterSet:
        path = self._path(set_id)
        return ParameterSet.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self, session_id: Optional[str] = None) -> List[ParameterSet]:
        sets: List[ParameterSet] = []
        for path in sorted(self.root.glob("ps_*.json")):
            try:
                item = ParameterSet.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if session_id is None or item.session_id == session_id:
                sets.append(item)
        sets.sort(key=lambda item: item.created_at)
        return sets

    def delete(self, set_id: str) -> bool:
        path = self._path(set_id)
        if not path.exists():
            return False
        path.unlink()
        return True
