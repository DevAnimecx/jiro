# Phase 2 Report — Cognitive memory

- **Version:** v0.4.0
- **Date:** 2026-10-12
- **Status:** Complete
- **Tests:** 265 memory tests passing; Phase 1 runtime suite re-verified at 222 passing

## What was built

Four memory types over one shared envelope, one lifecycle and one security
boundary:

| Module | Lines | Role |
|---|---:|---|
| `jiro/memory/types.py` | 483 | Envelope, scope, facts, procedures, provenance, hits |
| `jiro/memory/errors.py` | 161 | Typed failure taxonomy |
| `jiro/memory/lifecycle.py` | 93 | Central transition rules |
| `jiro/memory/validation.py` | 667 | Four type-specific validators, redaction |
| `jiro/memory/config.py` | 292 | Validated configuration |
| `jiro/memory/repository.py` | 296 | Storage contract (no unscoped read exists) |
| `jiro/memory/sqlite_repo.py` | 927 | SQLite persistence, FTS5, outbox |
| `jiro/memory/ranking.py` | 210 | Explainable scoring |
| `jiro/memory/embeddings.py` | 278 | Optional vectors, degenerate-vector rejection |
| `jiro/memory/metrics.py` | 98 | Counters |
| `jiro/memory/outbox.py` | 166 | Durable deferred writes |
| `jiro/memory/service.py` | 1201 | Public facade |
| `jiro/memory/runtime_integration.py` | 227 | Terminal task → episode |
| `jiro/memory/app.py` | 235 | Composition and task handlers |

2,876 lines of implementation, 2,876 lines of test.

## Decisions

Recorded as [ADR 0008](0008-memory-identity-and-scope.md),
[ADR 0009](0009-memory-execution-and-integration.md) and
[ADR 0010](0010-retrieval-and-provenance.md).

Three decisions worth restating here:

**Procedural memory never executes.** Steps name registered actions and carry no
callable. `execute_procedure()` exists only to refuse, and there is no other
execution entry point on the service.

**A task never waits on memory.** Terminal outcomes are queued to
`memory_outbox`; the write happens on the drainer. A crash between the two loses
nothing.

**Ownership is never a payload field.** `TaskContext.owner_key_id` is populated
from the durable task record, and the memory task schemas declare no
`owner_key_id` at all.

## Bugs found and fixed

Most of these were found by tests written against the behaviour rather than
against the implementation.

**In the memory package**

- Anonymous-owner deduplication silently never fired, because `owner_key_id` is
  `NULL` and SQLite treats NULLs as distinct in a UNIQUE index. Fixed with a
  non-null `dedup_scope` echo column, mirroring Phase 1's
  `tasks.idempotency_scope`.
- Working-memory eviction was off by one and evicted across scopes.
- Retrieval applied scope as a *ranking* input rather than a filter, so a scoped
  query could return memories from other scopes.
- `include_superseded=True` did not actually include superseded rows.
- `dequeue()` returned rows an earlier drain had already claimed, so a second
  drainer would process the same work twice.
- `set_status()` supplied duplicate SQL placeholders.
- Resolving a contradiction did not record the resolution when the accepted
  fact's status did not change, because the method returned early on "already
  in this status".
- Episode deduplication collapsed the same event across different task scopes.

**In shared code**

- `jiro/db.py` — `execute()` and `transaction()` released a connection to the
  pool without rolling back when a statement failed. A UNIQUE violation, which
  is the *ordinary* "already queued" path for an idempotent enqueue, left that
  connection inside an open write transaction. It kept the database file's
  write lock, and every later writer blocked until its busy timeout expired.
  Both now roll back on any exception, including a failed commit.

  This one is worth flagging: it was not visible from any single unit test, and
  it surfaced as intermittent "database is locked" under concurrent runtime and
  memory writes. The memory integration suite went from 26s to 6s once fixed,
  because writes stopped timing out.

- `jiro/runtime/worker.py` — `TaskContext` had no owner, so a handler had no
  trusted way to scope owner-specific data. Added `owner_key_id`, populated from
  the task record.

- `jiro/memory/runtime_integration.py` — `_write_episode` read the owner from
  the wrong key of its payload and filed episodes under the anonymous scope,
  where their real owner could never find them again.

## Verification

```
tests/test_memory_domain.py         92
tests/test_memory_persistence.py    43
tests/test_memory_retrieval.py      32
tests/test_memory_types.py          42
tests/test_memory_security.py       36
tests/test_memory_integration.py    20
                                  ---
                                  265 passing
```

Phase 1 re-verified unchanged: `222 passed`.

The integration suite runs against a real SQLite file and a real worker, and
covers the paths that unit tests cannot: task completion → episode, a crash
between the two, a restart that drains the queued entry, owner isolation across
two keys, and a process restart confirming memories, deletions and supersession
all survive.

`ruff check` passes on every file added or modified in Phase 2. Ten
pre-existing lint errors in `tests/test_self_healing.py` and
`tests/test_social_normalize.py` were left alone as unrelated to this work.

## Known limitations

1. **Procedural memory is inert.** Stored, ranked, versioned, promoted — and
   never run. Deliberate; see ADR 0009.
2. **No embeddings by default.** `sentence-transformers` is not a dependency.
   Retrieval degrades to lexical + recency ranking, with `vector_used=False`.
3. **`key_id` is an API key, not a person.** A user with three keys has three
   memory namespaces and cannot recall across them. Inherited from ADR 0002.
4. **Anonymous callers share one namespace.** With no identity there is nothing
   to separate them by, and the code does not pretend otherwise.
5. **Embedding backfill has no scheduler.** `embed_pending()` exists and is
   bounded, but nothing calls it on a timer yet. With embeddings disabled this
   is inert; enabling them without adding that call would leave records
   permanently `pending`.
6. **No REST, MCP or CLI surface for memory.** Phase 2 delivers the runtime
   task types and the service facade. A public transport is Phase 3 work.
7. **Transaction rollback is tested via a failing statement, not a killed
   process.** The outbox covers the crash-between-writes case, but a genuine
   kill -9 mid-transaction is not exercised.

## Phase 0 caveat

The v0.4.0 Phase 0 handoff and its architecture and task documents do not exist
in this repository. Phase 2's decisions were therefore derived from the code as
it stands, cross-checked against Phase 1's ADRs. Where Phase 0 would have been
authoritative — particularly on scope names and the embedding backfill schedule
— this report states the assumption made instead.