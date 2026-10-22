"""Live Parameter Layer tests (no network)."""

from __future__ import annotations

from datetime import datetime, timezone

from jiro.parametric.packet import build_packet
from jiro.params.store import (
    ParameterStore,
    bind,
    build_from_packet,
    decay,
    diff,
    merge,
)


def _packet(text_a: str, text_b: str = "", *, url: str = "https://a.example/x") -> dict:
    pages = [
        {
            "url": url,
            "title": "A",
            "content": text_a,
            "status_code": 200,
            "content_hash": "a",
            "fetched_at": "2026-10-21T00:00:00Z",
        }
    ]
    if text_b:
        pages.append(
            {
                "url": "https://b.example/y",
                "title": "B",
                "content": text_b,
                "status_code": 200,
                "content_hash": "b",
                "fetched_at": "2026-10-21T00:00:00Z",
            }
        )
    return build_packet("topic", [], pages, max_tokens=400)


def test_build_bind_save_load(tmp_path):
    packet = _packet("Rust ownership prevents data races in safe code at compile time.")
    pset = build_from_packet(packet, "session-1")
    assert pset.claim_count() >= 1
    binding = bind(pset, max_tokens=200)
    assert binding.used_tokens <= 200
    assert binding.claims
    assert "https://a.example/x" in binding.to_prompt()

    store = ParameterStore(tmp_path)
    path = store.save(pset)
    assert path.exists()
    loaded = store.load(pset.id)
    assert loaded.id == pset.id
    assert len(store.list("session-1")) == 1
    assert store.list("other") == []


def test_decay_reduces_freshness_for_old_claims():
    packet = _packet("The price is 10 dollars for the pro plan today.")
    pset = build_from_packet(packet, "s")
    old = pset.model_copy(
        update={
            "claims": tuple(
                claim.model_copy(update={"observed_at": "2020-01-01T00:00:00Z"})
                for claim in pset.claims
            )
        }
    )
    decayed = decay(old, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert all(value < 0.5 for value in decayed.freshness.values())


def test_diff_detects_added_removed_and_changed():
    a = build_from_packet(
        _packet("Alpha fact is supported by a source.", url="https://a.example/x"), "s"
    )
    b = build_from_packet(
        _packet("Beta fact is supported by a different source.", url="https://b.example/y"), "s"
    )
    result = diff(a, b)
    assert result["counts"]["added"] >= 1
    assert result["counts"]["removed"] >= 1
    assert result["sources_added"] == ["https://b.example/y"] or result["sources_removed"] == [
        "https://a.example/x"
    ]


def test_merge_unions_sources_and_preserves_conflicts():
    a = build_from_packet(
        _packet("The price is 10 dollars for the pro plan.", url="https://a.example/x"), "s"
    )
    b = build_from_packet(
        _packet("The price is 20 dollars for the pro plan.", url="https://b.example/y"), "s"
    )
    merged = merge(a, b)
    assert merged.version == 2
    assert len(merged.sources) == 2
    assert merged.claim_count() >= 2
