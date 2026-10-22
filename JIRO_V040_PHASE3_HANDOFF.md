# JIRO v0.4.0 — Phase 3 Handoff

- **Written:** 2026-10-12
- **Covers:** Phase 2 (cognitive memory), complete
- **State at handoff:** 265 memory tests passing, Phase 1 suite re-verified at 222

Phase 0 artifacts (`JIRO_V040_PHASE0_HANDOFF.md`, the v0.4.0 architecture and task
documents) are **not present in this repository**. Phase 1 and Phase 2 were both
derived from the code as it stands and recorded their decisions as ADRs. Phase 3
should not assume a Phase 0 document exists to consult.

## What is done

**Phase 1 — durable task runtime.** Lifecycle, persistence, recovery,
cancellation, ownership, at-least-once execution. `docs/runtime.md`, ADRs
0001–0007, `docs/adr/PHASE1_REPORT.md`.

**Phase 2 — cognitive memory.** Working, episodic, semantic and procedural
memory with owner-scoped storage, provenance, contradiction handling, retention,
verified deletion, explainable retrieval, an optional embedding path, and a
durable outbox wired to task outcomes. `docs/memory.md`, ADRs 0008–0010,
`docs/adr/PHASE2_REPORT.md`.

## The three things Phase 3 must not undo

1. **Ownership comes from the durable record, never the payload.**
   `TaskContext.owner_key_id` is populated by the worker from `TaskRecord`. The
   memory task schemas deliberately declare no `owner_key_id` field, so a
   spoofing attempt fails validation rather than being honoured. Any new memory
   transport must take the owner from the auth context and must not reintroduce a
   caller-supplied owner field.

2. **Stored procedures do not execute.** Steps name registered actions and carry
   no callable. See ADR 0009 before writing anything that runs a
   `MemoryProcedure`.

3. **`Database.execute()` and `Database.transaction()` roll back on failure.**
   This is not a style preference. Returning a connection to the pool inside an
   open write transaction keeps the file's write lock and deadlocks every later
   writer until its busy timeout expires. It cost a long debugging detour during
   Phase 2; `docs/adr/PHASE2_REPORT.md` records the symptom.

## Where Phase 3 could go

Ordered by what the code most obviously wants next, not by priority.

**A public memory surface.** Memory currently has a service facade and three
runtime task types, but no REST endpoint, MCP tool or CLI command. The service
is the interface; a transport is mostly plumbing. Note that a new transport must
derive owner from the auth context (point 1 above).

**An embedding backfill schedule.** `MemoryService.embed_pending()` is
implemented and bounded, but nothing calls it periodically. This is only inert
while `memory.embedding.enabled` is false — turning it on without a scheduler
leaves every record permanently `pending`. `docs/memory.md` documents the
configuration.

**A procedure executor.** Deferred to Phase 3+ by ADR 0009, and the most
dangerous item on this list. It must resolve `action` names through a registry of
registered callables, check `required_capabilities`, and never reach stored data
as code.

**Crash-injection tests.** Phase 2 covers a crash between "task committed" and
"episode written" via the outbox, but not a real `kill -9` mid-transaction. The
rollback fix in point 3 is currently tested through a failing statement rather
than a killed process.

## Working with this code

```powershell
# Memory suite
python -m pytest tests/test_memory_domain.py tests/test_memory_persistence.py `
  tests/test_memory_retrieval.py tests/test_memory_types.py `
  tests/test_memory_security.py tests/test_memory_integration.py

# Phase 1 regression, after any runtime or db.py change
python -m pytest tests/test_runtime_lifecycle.py tests/test_runtime_persistence.py `
  tests/test_runtime_integration.py tests/test_runtime_recovery.py `
  tests/test_runtime_api.py

# Lint
python -m ruff check jiro/memory jiro/db.py jiro/runtime tests/conftest_memory.py tests/test_memory_*.py
```

Ten pre-existing lint errors remain in `tests/test_self_healing.py` and
`tests/test_social_normalize.py`. They are unrelated to Phases 1 and 2 and were
deliberately left untouched.

There is an uncommitted working tree containing both phase's work and
unrelated user changes. `tests/test_mcp.py` in particular carries a formatting-
only diff from an over-broad `ruff --fix` during Phase 1; its logic is
unchanged. Do not revert user changes when tidying up.

## Two things worth knowing before you design

**`owner_key_id` is an API key, not a person.** A user holding three keys has
three separate memory namespaces and cannot recall across them. This is
inherited from ADR 0002 and is a real limitation, not a documentation
convention. If Phase 3 introduces a user concept, `owner_key_id` becomes a
foreign key and the existing scoping does not change — the queries already carry
it.

**Anonymous callers share one memory namespace.** With `owner_key_id = NULL`
there is nothing to separate them by. Jiro does not pretend otherwise.