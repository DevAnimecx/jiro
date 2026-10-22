# Jiro Durable Task Runtime

**Status:** v0.4.0 Phase 1 · stable

A persistent, bounded, cancellable execution substrate. Submit a validated
task; it is stored before acceptance is reported, executed by a fixed worker
pool under explicit limits, and reconciled after a crash.

- **Guarantees** — [ADRs 0001](adr/0001-durable-runtime.md) through [0007](adr/0007-schema-and-migration.md)
- **Scope and limits** — [ADR 0006](adr/0006-phase1-scope.md)

---

## Quick start

```python
from jiro.runtime import HandlerRegistry, TaskRuntime, Field, TaskSchema

registry = HandlerRegistry()

async def greet(ctx):
    return {"message": f"hello {ctx.input['name']}"}

registry.register(
    "greet",
    greet,
    TaskSchema("greet", [Field("name", "str", required=True, max_length=100)]),
)

runtime = await TaskRuntime.create(settings, db, registry)
await runtime.start()                      # reconciles, then starts workers

record = await runtime.submit("greet", {"name": "world"}, owner_key_id="key_abc")
done  = await runtime.wait_for(record.id, timeout=30)
assert done.state.value == "succeeded"

await runtime.stop()
```

Over HTTP:

```bash
curl -X POST localhost:8000/tasks -H 'X-API-Key: jsk_...' \
     -d '{"type": "ai_search", "payload": {"query": "..."}}'
```

From the CLI:

```bash
jiro tasks types
jiro tasks submit ai_search --payload '{"query": "..."}' --wait
jiro tasks status task_abc123
jiro tasks cancel task_abc123
jiro tasks events task_abc123
jiro tasks recover
```

---

## Lifecycle

```
                  claim                 ok
   queued ─────────────────> running ──────────> succeeded
     │                          │
     │                          ├── retryable, budget left ──> retry_wait
     │                          │                                      │
     │                          │                            backoff ──┘──> queued
     │                          │
     │                          ├── permanent / budget spent ──> failed
     │                          ├── deadline exceeded ──────────> failed
     │                          └── cancelled ─────────────────> cancelled
     │
     ├── cancelled ────────────────────────────────────────────> cancelled
     └── deadline exceeded ───────────────────────────────────> failed
```

Terminal states — `succeeded`, `failed`, `cancelled` — have **no** outgoing
edges. A completed task cannot return to execution.

The transition table lives in exactly one place,
`jiro/runtime/lifecycle.py::TRANSITIONS`. Every write path validates through
`validate_transition()`; no router or worker mutates state directly.

### What "succeeded" means

Only after the handler returned successfully **and** the result plus terminal
state were committed. `POST /tasks` returns `202` after the row and its events
are committed — durable acceptance, not success. Polling `GET /tasks/{id}` is
the only way to learn the outcome.

---

## Configuration

All settings live under `runtime:` in the existing config tree, so they inherit
Jiro's usual precedence: defaults → YAML → `JIRO_RUNTIME__*` environment
overrides.

```yaml
runtime:
  enabled: true
  db_path: ""                    # "" = reuse db.path (one SQLite writer per process)
  max_concurrency: 4             # worker count == max simultaneous handler executions
  poll_interval_seconds: 0.5
  lease_ttl_seconds: 30          # renewed on a heartbeat; bounds crash-recovery latency
  default_attempt_timeout_seconds: 60
  max_attempt_timeout_seconds: 3600
  default_max_attempts: 3
  max_max_attempts: 10
  retry_backoff_base_seconds: 1.0
  retry_backoff_max_seconds: 60.0
  retry_jitter_ratio: 0.2
  shutdown_grace_seconds: 15
  max_result_bytes: 262144
  event_retention_days: 7        # 0 disables pruning
  reconcile_interval_seconds: 30 # 0 disables the periodic sweep
```

```bash
export JIRO_RUNTIME__MAX_CONCURRENCY=8
```

| Setting | Default | Meaning |
|---|---|---|
| `max_concurrency` | 4 | Number of worker coroutines. Also the hard cap on simultaneous handler executions. |
| `lease_ttl_seconds` | 30 | How long a lease survives without renewal. Renewed every `ttl/3` while a handler runs. Bounds how long an interrupted task waits for recovery. |
| `default_attempt_timeout_seconds` | 60 | Wall-clock cap for one attempt. |
| `max_attempt_timeout_seconds` | 3600 | Ceiling a caller may request. |
| `default_max_attempts` / `max_max_attempts` | 3 / 10 | Attempt budget, and its ceiling. |
| `retry_backoff_base_seconds` | 1.0 | First retry delay. Doubles per attempt. |
| `retry_backoff_max_seconds` | 60.0 | Ceiling for a single delay. |
| `retry_jitter_ratio` | 0.2 | Fraction of each delay randomised. |
| `shutdown_grace_seconds` | 15 | Time allowed for in-flight work on shutdown. |
| `max_result_bytes` | 262144 | Larger results fail the task permanently rather than being truncated. |
| `event_retention_days` | 7 | Lifecycle-event pruning window. |
| `reconcile_interval_seconds` | 30 | Periodic reconciliation sweep. |

Configuration is validated at startup. An invalid value raises
`TaskValidationError` naming the setting, before any worker starts.

---

## Budget semantics

| Term | Definition | Enforcement |
|---|---|---|
| **Attempt timeout** | Max wall-clock duration of one execution | `asyncio.wait_for` around the handler; recorded as `attempt_timeout` |
| **Retry limit** | Max executions permitted, including the first | `TaskLimits.max_attempts`; enforced by `RetryPolicy` |
| **Concurrency limit** | Max simultaneous executions | Exactly `max_concurrency` worker coroutines |
| **Task deadline** | Latest allowed completion time | Absolute epoch second; checked before claiming, before scheduling a retry, and by recovery |
| **Resource budget** | A measurable, explicitly defined limit | Only `max_result_bytes` and the attempt/timeout limits above |
| **Shutdown grace** | Time allowed for graceful termination | `WorkerPool.stop(grace=...)` |

Elapsed durations are measured with `time.monotonic()` inside a process. Durable
deadlines and availability times are stored as absolute epoch seconds so they
survive a restart — **a restart never resets a task's total budget**, because
`attempts` and `deadline_at` are persisted and recovery respects both.

**Token and monetary budgets are not implemented.** The runtime cannot measure
provider usage and will not report a number it does not have. See
[ADR 0006](adr/0006-phase1-scope.md).

---

## Retry classification

One centralised policy — `jiro/runtime/retry.py`. No other module schedules a
retry.

| Failure kind | Examples | Retried? |
|---|---|---|
| `transient` | `RateLimitError`, `EngineBlockedError`, `httpx.TransportError`, `ScrapeError` | yes, while budget remains |
| `timeout` | `asyncio.TimeoutError`, `TimeoutError` | yes, while budget remains |
| `infrastructure` | `CacheError`, `OSError` | yes, while budget remains |
| `unexpected` | any other handler exception | yes, capped by attempts |
| `permanent` | `ValidationError`, `ConfigError`, `ForbiddenError`, `ResultTooLarge`, unknown `JiroError` | **never** |
| `cancelled` | `CancelledError` | **never** |

Backoff for the attempt after *n* is `min(max, base · 2^(n-1))`, then randomised
by up to `retry_jitter_ratio`. Both the clock and the RNG are injectable, so
schedules are deterministic under test.

A retry is refused — and the task failed instead — when the attempt budget is
spent, when the deadline has already passed, or when the backoff would
overshoot the deadline.

Every retry decision is recorded as a `retry.scheduled` event carrying the
delay and the reason.

---

## Cancellation

See [ADR 0004](adr/0004-cancellation.md) for the full rationale. In short:

- **Queued / retry-waiting** → `cancelled` immediately. Execution cannot start.
- **Running** → `cancel_requested` is persisted, the in-process attempt is
  cancelled. The task reports `running` with `cancel_requested: true` until the
  handler actually unwinds, then `cancelled`.
- **Terminal** → no-op. Returns the terminal state.

The database never reports `cancelled` while a handler is still executing.
Repeating the call is always safe.

Handlers must be `async def`. A synchronous handler is refused at registration
because a thread cannot be cancelled — wrapping one would produce a runtime that
reports cancellation while work continues.

---

## Recovery

Runs at startup **and** on a `reconcile_interval_seconds` sweep. The sweep is
not optional bookkeeping: a task whose owner died while this process kept
running holds a live lease at startup, so startup recovery correctly skips it,
and it would otherwise sit in `running` until the next restart.

| Durable state | Action |
|---|---|
| `running`, lease lapsed, cancellation flagged | → `cancelled` |
| `running`, lease lapsed, deadline passed | → `failed` |
| `running`, lease lapsed, attempts exhausted | → `failed` |
| `running`, lease lapsed, budget remains | → `retry_wait`, backoff recomputed from **remaining** budget |
| `running`, lease **live** | untouched — another instance owns it |
| `retry_wait`, backoff elapsed, deadline passed | → `failed` |
| `retry_wait`, backoff elapsed, budget remains | → `queued` |
| `queued`, cancellation flagged | → `cancelled` |
| `queued`, deadline passed | → `failed` |

Every decision is logged and recorded as a `recovery.applied` event. Recovery
is idempotent: running it twice changes nothing the second time.

**Guarantee: at-least-once.** A task interrupted after its handler performed an
external side effect is re-executed. See
[ADR 0003](adr/0003-execution-guarantee.md) — including the test that
demonstrates the duplicate side effect rather than hiding it.

---

## Concurrency model

The pool starts exactly `max_concurrency` worker coroutines at
`WorkerPool.start()`. Each loops: claim one task, execute it, repeat.

Nothing is spawned per task. There is no semaphore to leak permits from and no
unbounded growth when a client floods the API — in-flight executions cannot
exceed the worker count, by construction.

One bad task cannot take down the runtime. A worker loop catches `BaseException`
around each iteration and continues, because silently losing a worker would
shrink concurrency while health checks still reported the runtime healthy.
`KeyboardInterrupt` and `SystemExit` are re-raised so operator signals still
reach the process.

Claiming is a compare-and-swap `UPDATE` guarded by the current state, so two
workers racing produce exactly one winner. Every worker write additionally
carries `AND lease_owner = ?`, so a worker that has lost its lease cannot
overwrite the task that replaced it.

---

## Interfaces

### REST

All routes require authentication and are scoped to `ctx.key_id`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/tasks` | Submit. `202` after durable commit. |
| `GET` | `/tasks` | List your tasks. `?state=`, `?limit=`, `?offset=` |
| `GET` | `/tasks/{id}` | Status and result |
| `POST` | `/tasks/{id}/cancel` | Request cancellation |
| `GET` | `/tasks/{id}/events` | Durable lifecycle events |
| `GET` | `/tasks/runtime/stats` | Worker and queue state |

```jsonc
// POST /tasks
{
  "type": "ai_search",
  "payload": { "query": "..." },
  "idempotency_key": "nightly-2026-10-12",   // per-owner; repeating returns the same task
  "max_attempts": 3,
  "attempt_timeout_seconds": 120,
  "deadline_in_seconds": 900
}
```

### CLI

`jiro tasks {types,submit,status,list,cancel,events,recover}`

The CLI is a first-class client of the same runtime, not a second
implementation. `--wait` on `submit` blocks until a terminal state.

### MCP

`task_submit`, `task_status`, `task_cancel`.

MCP has no per-client identity in Phase 1, so it operates inside a fixed `mcp`
owner namespace and can only see tasks it submitted itself. See
[ADR 0002](adr/0002-task-ownership.md).

### Python

`TaskRuntime` is the only object transports talk to. It owns validation,
durable submission, cancellation and lifecycle, and contains no SQL and no
framework types.

---

## Handlers

A handler is an `async def handler(ctx) -> dict`, registered explicitly with a
schema. There is **no** path by which a submitted task names a callable, imports
a module, or evaluates a string.

```python
from jiro.runtime import Field, TaskSchema

async def research(ctx):
    if await ctx.cancel_requested():
        raise asyncio.CancelledError()
    result = await my_provider.query(ctx.input["query"],
                                     idempotency_key=ctx.idempotency_key)
    return {"answer": result}

registry.register("research", research, TaskSchema("research", [
    Field("query", "str", required=True, max_length=2000),
]))
```

`TaskContext` exposes `task_id`, `task_type`, `input`, `attempt`,
`idempotency_key`, `deadline_at`, `limits`, and `await cancel_requested()`.

Input validation is a closed-world allow-list: an unknown field is **rejected**,
not ignored, so a typo surfaces immediately and a hostile payload cannot smuggle
extra keys through.

### Built-in task types

`ai_search`, `ai_agent` and `batch_scrape` are registered at startup, matching
the types `/jobs` has always accepted. Their handlers are thin adapters over the
existing agent and scraper; see `jiro/runtime/builtin.py`.

---

## Observability

Structured logs use the existing `jiro.log` JSON formatter; metrics use the
existing hand-rolled `jiro.telemetry` collector. No new dependency.

Metrics (`jiro/runtime/metrics.py`):

```
jiro_runtime_tasks_submitted          jiro_runtime_queue_depth{state}
jiro_runtime_tasks_succeeded          jiro_runtime_workers_active
jiro_runtime_tasks_failed             jiro_runtime_workers_total
jiro_runtime_tasks_cancelled          jiro_runtime_task_duration_seconds{state}
jiro_runtime_task_retries             jiro_runtime_recovery_events
jiro_runtime_task_timeouts            jiro_runtime_worker_errors
jiro_runtime_cancellations_requested  jiro_runtime_lease_lost
```

Label values come from closed sets only — task state, outcome. Task ids, inputs
and error text are never used as labels.

Lifecycle events in `task_events`: `task.submitted`, `task.validated`,
`execution.started`, `attempt.failed`, `retry.scheduled`,
`cancellation.requested`, `execution.cancelled`, `recovery.applied`,
`task.succeeded`, `task.failed`, `task.cancelled`, `deadline.exceeded`.

Event metadata is scrubbed on write: secret-looking keys are redacted, nested
structures are replaced by their type name, and values are truncated.

---

## Storage

Two tables, added by `SCHEMA` and independent of every existing table. Epoch
`float` timestamps in `REAL` columns, matching the rest of this database;
`INTEGER` for booleans.

```sql
tasks(id, type, state, owner_key_id, idempotency_key, idempotency_scope,
      input, result, result_ref, error_code, error_message,
      attempts, max_attempts, attempt_timeout_s, deadline_at,
      retry_backoff_base, retry_backoff_max, retry_jitter_ratio,
      max_result_bytes, lease_owner, lease_expires_at, cancel_requested,
      available_at, created_at, updated_at, started_at, completed_at)

task_events(id, task_id, type, attempt, ts, metadata)
```

Indexes match actual lookups: claim `(state, available_at)`, recovery
`(state, lease_expires_at)`, deadline sweep `(state, deadline_at)`, owner
listing `(owner_key_id, created_at)`.

`Database.transaction()` was added for atomic multi-statement units — a task row
and its events commit together, so a crash cannot leave a task whose
submission events never arrived.

Backup and rollback procedures: [ADR 0007](adr/0007-schema-and-migration.md).

---

## Operational notes

**One writer per process.** SQLite permits one writer. The runtime defaults to
sharing `db.path` so the process keeps a single SQLite writer. Running several
replicas against one file is *safe for correctness* (leases prevent stealing live
work) but `PRAGMA busy_timeout` is the only thing smoothing contention. Run one
replica, as `jiro/db.py` already advises.

**Shutdown order.** The runtime is stopped before the database closes, so
in-flight attempts can still record their outcome. This is wired in the FastAPI
lifespan.

**Disabling.** `runtime.enabled: false` still constructs the runtime and runs
recovery, but starts no workers. Tasks stay `queued`.

---

## Limitations

Stated plainly, because they are real:

1. Ownership is per **API key**, not per person (ADR 0002).
2. Execution is **at-least-once**, not exactly-once (ADR 0003).
3. Cancellation of a non-cooperative handler takes effect only when its attempt
   timeout fires.
4. No token or monetary budgets (ADR 0006).
5. Results are stored inline, capped at `max_result_bytes`; `result_ref` is
   unused.
6. No task-to-task parenting or aggregate budgets.
7. Webhook retry state is still in-memory, inherited from v0.3.3.
8. A long attempt that blocks the event loop past `lease_ttl` can be reclaimed
   and re-executed; the runtime warns at startup if the TTL looks too short.