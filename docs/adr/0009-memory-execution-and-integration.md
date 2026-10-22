# ADR 0009 — Memory never executes, and never blocks a task

- **Status:** Accepted (v0.4.0 Phase 2)
- **Date:** 2026-10-12

## Context

Phase 2 stores procedures — repeatable sequences of steps the system learns from
successful work. That is the most dangerous thing in the memory design, because
"remembered how to do X" and "do X" are one character apart.

Phase 1 also established a second constraint: the durable task runtime has a
hard promise about latency and failure. A task that has reached `succeeded` is
done, and nothing that happens afterwards may change or delay that.

The two constraints collide. Writing a memory is an extra database transaction,
possibly with an embedding computation in it; running a learned procedure is
arbitrary action selected by stored data.

## Decision

**Stored procedures are declarative and are never executed by the memory layer.**
`MemoryProcedure.steps` is a list of dicts with a closed key set (`action`,
`description`, `requires`, `capture_as`, `notes`). `action` must name a
registered action from `ALLOWED_STEP_ACTIONS`; anything else fails
validation at write time.
`MemoryService.execute_procedure()` exists only so the refusal is explicit and
testable — it always raises `MemoryNotExecutable`. There is no `execute`, `run`,
`eval`, `invoke` or `apply` entry point on the service.

**Episode capture is queued, never inlined.** The worker calls
`on_terminal(record)` after a task's terminal state is already committed.
`TaskMemoryBridge` writes one row to `memory_outbox` and returns. The actual
memory write happens later, on the outbox drainer. A memory provider being slow,
down, or broken cannot delay a task or change its result.

**The hook is awaited, and awaited as a coroutine.** `WorkerPool._emit_terminal`
awaits the hook when it returns an awaitable. Scheduling it and moving on would
let a shutdown land between "task committed" and "episode queued", losing the
episode. The hook itself never raises: a failure is logged and the task's
terminal state stands.

**A failed statement rolls back before its connection is pooled.**
`Database.execute()` and `Database.transaction()` roll back on any exception,
including a failed commit. Without this a UNIQUE violation — the ordinary
"already queued" path for an idempotent enqueue — returned its connection to the
pool still inside an open write transaction, and that connection held the
database file's write lock until every later writer hit its busy timeout.

## Consequences

**Learned procedures are inert until a Phase 3 surface executes them.** They are
stored, ranked, versioned and promoted, but nothing runs them. This is the
intended state: the dangerous half of the feature is deferred until there is a
capability-checked executor to point at.

**Terminal-task capture is at-least-once, and deduplicated.** A replay after a
crash re-queues the same `(task_id, attempt)` dedup key, so draining twice
converges on one record.

**Handler registration does not survive a restart.** Outbox entry kinds are
registered in-process, so a restart must rebuild the bridge before draining;
an entry whose kind has no handler is dead-lettered rather than retried forever.
`jiro.memory.app.build_memory_service` is the single place that does this, and
the integration tests restart through it.

## Follow-up

Phase 3 may add an executor, but only behind a capability check against
`required_capabilities` and a registry that resolves `action` names to
registered callables. It must not reach stored data as code.