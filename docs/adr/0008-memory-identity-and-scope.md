# ADR 0008 — Memory identity and scope

- **Status:** Accepted (v0.4.0 Phase 2)
- **Date:** 2026-10-12

## Context

Phase 2 adds durable memory. A memory is the longest-lived data Jiro stores and
the easiest to leak: one unscoped `SELECT` returns a user's entire history.

ADR 0002 established that the only durable principal is `AuthContext.key_id`.
Phase 2 inherits that decision rather than inventing a second identity, and has
to answer two questions the runtime never had to:

1. **Who owns a memory?** The same `owner_key_id`, with the same guarantee that
   "not yours" and "does not exist" are indistinguishable.
2. **Which memories may one owner see?** An owner has several namespaces —
   system-wide knowledge, per-key knowledge, and per-task working notes. Some of
   those must not be readable from every task.

The codebase has `jiro/tenants.py`, but ADR 0002 already established it is
in-memory, never consulted by auth, and unreachable from any router. Memory
therefore does not use it.

## Decision

Every memory carries `owner_key_id` plus a two-part scope
(`scope_kind`, `scope_id`). Kinds, widest first: `global`, `key`, `session`,
`task`.

Three rules, all enforced in SQL rather than in Python filters:

- **Owner is a WHERE clause on every read.** `SqlMemoryRepository` requires
  `owner_key_id` on `get`, `search`, `list_for_scope`, `count`, `expired`,
  `dependents_of_source` and the outbox helpers. There is no unscoped read
  method, so a missing filter is a `TypeError`, not a silent data leak.
- **Widening is additive, never transitive.** `RetrievalQuery.scopes` holds the
  caller's *own* scope plus any additional scopes the caller is permitted to
  read. A caller never gets another owner's scope into that tuple.
- **Idempotency is scoped by a non-null column.** `owner_key_id` can be `NULL`
  for anonymous callers, and SQLite treats NULLs as distinct in a UNIQUE index —
  so a plain `(owner_key_id, dedup_key)` index would never fire for anonymous
  callers. `dedup_scope` carries a non-null echo of the owner and the index is
  built on that. This mirrors `tasks.idempotency_scope` from Phase 1.

`MemoryNotFound` is raised for another owner's id with the same message as for
an id that never existed, and the error carries no title, content or owner in
its text.

## Consequences

**A task may widen its read, never its write.** The memory task handlers
(`memory_recall`, `memory_remember`, `memory_forget`) take owner from
`TaskContext.owner_key_id`, which the worker populates from the durable task
record — never from the task payload. `memory_remember` and `memory_forget`
declare no scope fields at all in their schemas, so a payload naming
`scope_kind=global` is rejected by validation before a handler runs.

**Anonymous callers share one namespace.** With `owner_key_id = NULL`, every
anonymous caller reads and writes the same global scope. That is the honest
consequence of having no identity: Jiro does not pretend to separate them.

**`key_id` is still a key, not a person.** As in ADR 0002, a user holding three
keys has three memory namespaces and cannot recall across them.

## Follow-up

If a real user table appears, `owner_key_id` becomes a foreign key and the
scoping in this ADR is unchanged — the queries already carry it.