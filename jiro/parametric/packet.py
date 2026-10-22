"""Evidence packet contracts and compiler."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Tuple
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from jiro.parametric.compress import (
    TOKENIZER,
    estimate_tokens,
    select_claims,
)
from jiro.parametric.extract import extract_claims, hamming, normalize_text, simhash

PACKET_SCHEMA = "jiro.packet/v1"

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


class Source(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    url: str
    domain: str
    engine: str = ""
    fetched_at: str
    http_status: int | None = None
    content_hash: str = ""
    trust: Literal["untrusted", "allowlisted"] = "untrusted"
    title: str = ""


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    kind: Literal["fact", "quote", "numeric", "entity", "timeline"]
    source_ids: Tuple[str, ...]
    support: Literal["single", "corroborated", "contradicted", "unsupported"] = "single"
    observed_at: str | None = None
    confidence: float = Field(0.55, ge=0.0, le=1.0)
    taint: Tuple[str, ...] = ()
    score: float = 0.0


class EvidencePacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    packet_schema: str = Field(PACKET_SCHEMA, alias="schema")
    packet_id: str
    query: str
    tokenizer: str
    budget_tokens: int
    used_tokens: int
    claims: Tuple[Claim, ...]
    sources: Tuple[Source, ...]
    contradictions: Tuple[Tuple[str, str], ...] = ()
    omitted: Dict[str, int] = Field(default_factory=dict)
    gaps: Tuple[str, ...] = ()
    provenance_digest: str
    generated_at: str


class PacketValidationError(ValueError):
    pass


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _domain(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_sources(
    search_results: Iterable[Dict[str, Any]],
    pages: Iterable[Dict[str, Any]],
) -> Tuple[List[Source], Dict[str, str], int]:
    by_url: Dict[str, Dict[str, Any]] = {}
    for page in pages:
        if not isinstance(page, dict) or page.get("error"):
            continue
        url = str(page.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        content = str(page.get("content") or "")
        if not content.strip():
            continue
        by_url[url] = {
            "url": url,
            "title": str(page.get("title") or ""),
            "content": content,
            "engine": "",
            "fetched_at": str(page.get("fetched_at") or _now()),
            "http_status": page.get("status_code"),
            "content_hash": str(page.get("content_hash") or ""),
            "is_page": True,
        }

    for result in search_results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("link") or "")
        if not url.startswith(("http://", "https://")) or url in by_url:
            continue
        snippet = " ".join(
            part for part in (str(result.get("title") or ""), str(result.get("snippet") or "")) if part
        ).strip()
        if not snippet:
            continue
        by_url[url] = {
            "url": url,
            "title": str(result.get("title") or ""),
            "content": snippet,
            "engine": str(result.get("source") or ""),
            "fetched_at": _now(),
            "http_status": None,
            "content_hash": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
            "is_page": False,
            "snippet": True,
        }

    sources: List[Source] = []
    texts: Dict[str, str] = {}
    duplicates = 0
    for index, url in enumerate(sorted(by_url), start=1):
        item = by_url[url]
        source_id = f"s{index}"
        sources.append(
            Source(
                id=source_id,
                url=url,
                domain=_domain(url),
                engine=str(item.get("engine") or ""),
                fetched_at=str(item.get("fetched_at") or _now()),
                http_status=item.get("http_status"),
                content_hash=str(item.get("content_hash") or ""),
                title=str(item.get("title") or ""),
            )
        )
        texts[source_id] = str(item.get("content") or "")
        duplicates += 1
    return sources, texts, duplicates


def _dedupe_claims(claims: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    kept: List[Dict[str, Any]] = []
    exact: Dict[str, int] = {}
    duplicates = 0
    for claim in claims:
        key = normalize_text(str(claim.get("text") or "")).lower()
        if not key:
            continue
        if key in exact:
            existing = kept[exact[key]]
            existing["source_ids"] = sorted(
                set(existing.get("source_ids") or []) | set(claim.get("source_ids") or [])
            )
            existing["taint"] = sorted(set(existing.get("taint") or []) | set(claim.get("taint") or []))
            if len(existing["source_ids"]) > 1:
                existing["support"] = "corroborated"
                existing["confidence"] = max(float(existing.get("confidence") or 0), 0.75)
            duplicates += 1
            continue

        fingerprint = int(claim.get("simhash") or 0)
        near_index = None
        if fingerprint:
            for index, existing in enumerate(kept):
                if hamming(fingerprint, simhash(str(existing.get("text") or ""))) <= 3:
                    near_index = index
                    break
        if near_index is not None:
            existing = kept[near_index]
            existing["source_ids"] = sorted(
                set(existing.get("source_ids") or []) | set(claim.get("source_ids") or [])
            )
            existing["score"] = max(float(existing.get("score") or 0), float(claim.get("score") or 0))
            if len(existing["source_ids"]) > 1:
                existing["support"] = "corroborated"
                existing["confidence"] = max(float(existing.get("confidence") or 0), 0.75)
            duplicates += 1
            continue

        exact[key] = len(kept)
        kept.append(dict(claim))
    return kept, duplicates


def _contradictions(claims: List[Dict[str, Any]]) -> Tuple[Tuple[str, str], ...]:
    """Naive numeric contradiction detector.

    It intentionally only compares numeric claims with the same normalized
    wording. It is a surfacing heuristic, never an automatic truth judgment.
    """
    groups: Dict[str, List[Tuple[str, str]]] = {}
    for claim in claims:
        if claim.get("kind") != "numeric":
            continue
        text = str(claim.get("text") or "")
        numbers = tuple(_NUMBER_RE.findall(text))
        if not numbers:
            continue
        signature = _NUMBER_RE.sub("<n>", text.lower())
        groups.setdefault(signature, []).append((str(claim.get("id")), numbers[0]))

    pairs = []
    for group in groups.values():
        if len(group) < 2:
            continue
        distinct = {number for _, number in group}
        if len(distinct) > 1:
            pairs.append((group[0][0], group[1][0]))
    return tuple(pairs)


def build_packet(
    query: str,
    search_results: Iterable[Dict[str, Any]] | None = None,
    pages: Iterable[Dict[str, Any]] | None = None,
    *,
    max_tokens: int = 1200,
) -> Dict[str, Any]:
    """Compile search results and pages into a cited, budgeted packet."""
    query = " ".join((query or "").split())[:500]
    search_results = list(search_results or [])
    pages = list(pages or [])
    budget = max(64, int(max_tokens))

    sources, texts, _ = _build_sources(search_results, pages)

    candidates: List[Dict[str, Any]] = []
    for source in sources:
        text = texts.get(source.id, "")
        if not text:
            continue
        max_claims = 10 if source.http_status is not None else 3
        candidates.extend(
            extract_claims(
                text,
                source.id,
                max_claims=max_claims,
                observed_at=source.fetched_at,
            )
        )

    deduped, duplicate_count = _dedupe_claims(candidates)
    deduped.sort(
        key=lambda c: (
            -float(c.get("score") or 0),
            -len(c.get("source_ids") or []),
            str(c.get("text") or ""),
        )
    )

    reserved = estimate_tokens(query) + (8 * len(sources)) + 16
    reserved = min(reserved, max(0, budget - 40))
    selected, used, over_budget = select_claims(
        deduped, max_tokens=budget, reserved_tokens=reserved
    )
    used_tokens = min(budget, used + reserved)

    claims: List[Claim] = []
    for index, item in enumerate(selected, start=1):
        claims.append(
            Claim(
                id=f"c{index}",
                text=str(item.get("text") or ""),
                kind=item.get("kind") or "fact",
                source_ids=tuple(item.get("source_ids") or ()),
                support=item.get("support") or "single",
                observed_at=item.get("observed_at"),
                confidence=float(item.get("confidence") or 0.55),
                taint=tuple(item.get("taint") or ()),
                score=float(item.get("score") or 0),
            )
        )

    contradictions = _contradictions([c.model_dump() for c in claims])
    payload = {
        "schema": PACKET_SCHEMA,
        "query": query,
        "tokenizer": TOKENIZER,
        "budget_tokens": budget,
        "claims": [c.model_dump(mode="json") for c in claims],
        "sources": [s.model_dump(mode="json") for s in sources],
        "contradictions": contradictions,
        "omitted": {
            "duplicates": duplicate_count,
            "over_budget": over_budget,
        },
    }
    digest = _hash(payload)
    gaps: List[str] = []
    if not sources:
        gaps.append("no_sources")
    if not claims:
        gaps.append("no_claims")
    if over_budget:
        gaps.append("budget_truncated")

    packet = EvidencePacket(
        packet_id=f"p_{digest[:16]}",
        query=query,
        tokenizer=TOKENIZER,
        budget_tokens=budget,
        used_tokens=used_tokens,
        claims=tuple(claims),
        sources=tuple(sources),
        contradictions=contradictions,
        omitted={"duplicates": duplicate_count, "over_budget": over_budget},
        gaps=tuple(gaps),
        provenance_digest=digest,
        generated_at=_now(),
    )
    result = packet.model_dump(mode="json", by_alias=True)
    validate_packet(result)
    return result


def validate_packet(packet: Dict[str, Any]) -> None:
    if packet.get("schema") != PACKET_SCHEMA:
        raise PacketValidationError(f"unsupported packet schema: {packet.get('schema')!r}")
    budget = int(packet.get("budget_tokens") or 0)
    used = int(packet.get("used_tokens") or 0)
    if budget <= 0 or used > budget:
        raise PacketValidationError(f"packet over budget: {used} > {budget}")
    source_ids = {str(source.get("id")) for source in packet.get("sources") or []}
    for claim in packet.get("claims") or []:
        ids = [str(value) for value in claim.get("source_ids") or []]
        if not ids:
            raise PacketValidationError(f"claim {claim.get('id')!r} has no sources")
        missing = [value for value in ids if value not in source_ids]
        if missing:
            raise PacketValidationError(
                f"claim {claim.get('id')!r} references missing sources: {missing}"
            )
        if not str(claim.get("text") or "").strip():
            raise PacketValidationError(f"claim {claim.get('id')!r} has empty text")
    claim_ids = {str(claim.get("id")) for claim in packet.get("claims") or []}
    for pair in packet.get("contradictions") or []:
        if len(pair) != 2 or pair[0] not in claim_ids or pair[1] not in claim_ids:
            raise PacketValidationError(f"invalid contradiction pair: {pair!r}")
