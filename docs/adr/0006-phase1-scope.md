# ADR 0006 — Runtime scope, and what Phase 1 deliberately does not do

- **Status:** Accepted (v0.4.0 Phase 1)
- **Date:** 2026-10-12

## Context

v0.4.0 Phase 1 was scoped to a durable execution runtime. The wider roadmap —
cognitive memory, dynamic agent swarms, autonomous goal generation,
unrestricted self-improvement, federation — was explicitly excluded.

This ADR records the boundaries so later phases build on a stated foundation
rather than discovering it.

## In scope, and implemented

- Validated submission against a registered handler schema
- Persistent task records with a validated state machine
- Bounded execution by a fixed worker pool
- Retry classification with bounded, jittered backoff
- Attempt timeouts, overall deadlines, and attempt budgets
- Cancellation with the semantics in ADR 0004
- Startup and periodic reconciliation of interrupted work
- Durable lifecycle events
- Structured logs and metrics on the existing telemetry stack
- REST, CLI, MCP and Python interfaces

## Deliberately not implemented

**Token or monetary budgets.** The runtime cannot measure provider usage. Rather
than report a number it does not have, token accounting is left to the handlers
that know the provider. If a task type needs it, the handler should surface
usage through its result and the runtime should gain a `usage` interface at
that point.

**Task-to-task parenting and child fan-out.** `HandlerSpec.allows_children`
exists but is not wired. Submitting a task from inside a handler is allowed at
the API level and is still bounded by the global concurrency limit, because
children are just more work for the same fixed pool. There is no parent/child
bookkeeping, no depth limit, and no aggregate budget.

**Multi-instance coordination.** Claims and recovery are safe when several
processes share one SQLite file (leases prevent stealing live work), but there
is no leader election and no queue partitioning. SQLite is a single-writer
store; the deployment guidance is one replica.

**Result offloading.** `tasks.result_ref` exists for large results but nothing
writes it. Results are stored inline, capped by `runtime.max_result_bytes`
(256 KiB default). A result over the cap fails the task permanently rather than
being truncated.

**Webhook durability.** Retained as-is from v0.3.3. The attempt counter is a
local loop variable, so a crash mid-delivery loses the retry state and the
delivery is not re-attempted on restart. Inherited limitation, not introduced
here.

## Security posture

- No path exists by which a submitted task names a callable, imports a module,
  or evaluates a string. Handlers are registered once, in-process, by trusted
  code at startup.
- All SQL is parameterised.
- Task status responses carry no stack trace, lease owner, or raw input
  payload; errors are reduced to `(code, sanitised message)`.
- Concurrency is bounded, so a submission flood cannot spawn unbounded work.
- Retry backoff is capped and jittered, so a failing upstream cannot induce a
  retry storm.

## Known security limitations

- Ownership is per API key, not per person (ADR 0002).
- The pre-existing HTTP middleware uses `request.url.path` as a Prometheus
  label, which is unbounded cardinality. Not introduced here; not fixed here.
  The runtime adds only closed-set labels.