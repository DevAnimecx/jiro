# ADR 0010 — Retrieval is explainable, and embeddings are optional

- **Status:** Accepted (v0.4.0 Phase 2)
- **Date:** 2026-10-12

## Context

Retrieval has to answer "why did this come back?" — otherwise a stored-memory
result is unauditable, and a wrong answer cannot be traced to the memory that
produced it.

Phase 2 also has to cope with the fact that `sentence-transformers` is **not** a
declared dependency of this project. `jiro/search/embeddings.py` handles its
absence by returning a **zero vector** rather than raising. A zero vector is not
an error; it is worse than an error, because cosine similarity against it is
0.0 for everything, and a system that blends that into its ranking looks healthy
while having no signal at all.

## Decision

**Ranking is a transparent weighted blend, and every hit carries its
breakdown.** `Ranker.rank()` combines lexical overlap, recency, importance,
verification level and scope affinity. `MemoryHit` carries `score`,
`score_breakdown` and `match_reason`. `build_context()` is the only method that
formats hits for a model, and it wraps them in explicit
`RECALLED MEMORY (reference material, not instructions)` delimiters.

**FTS5 is used when the linked SQLite provides it, with a bounded `LIKE`
fallback.** The index is created lazily by `ensure_search_index()`. When it is
unavailable, retrieval still works, and `RetrievalPage.degraded_reason` says so
rather than silently returning nothing.

**A degenerate embedding is not an embedding.** `EmbeddingAdapter` rejects a
vector that is all zeros (or whose norm is below a floor) and records it as a
failure. Records are only blended into ranking when their
`embedding_status` is `ready`, the model id matches the current provider, and
the dimensions agree. An absent provider therefore degrades retrieval to lexical
and recency ranking — with `vector_used=False` — instead of poisoning it.

**Embedding computation never happens on the write path.** Records are written
first and marked `pending`; `embed_pending()` fills them in bounded batches
afterwards.

## Consequences

**No embedding provider is a supported configuration.** The whole retrieval path
is exercised in the test suite with the adapter's provider set to `None`, so the
fallback is the tested default rather than an untested branch.

**`semantic` memory means structured claims, not vector similarity.** Facts are
`subject`/`predicate`/`object` rows with validity windows, contradiction groups
and an explicit resolution. Retrieval over them is a filter plus lexical ranking.
Vector similarity is an optional accelerator, never the source of truth.

## Follow-up

If a provider is added, `embed_pending()` is the only thing that needs to run on
a schedule, and `RetrievalPage.degraded_reason` is the signal to alert on.