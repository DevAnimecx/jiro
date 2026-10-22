# ADR 0005 — `/jobs` compatibility facade with two execution modes

- **Status:** Accepted (v0.4.0 Phase 1)
- **Date:** 2026-10-12

## Context

`POST /jobs`, `GET /jobs` and `GET /jobs/{id}` are published in the OpenAPI
document, exercised by SDKs in four languages, and covered by existing tests
(`tests/test_phase2.py`, `tests/test_phase5.py`). Their response model is
`JobOut`, which has ten fields and returns `status` values of `queued`,
`running`, `completed`, `failed`.

The runtime's vocabulary is `queued`, `running`, `retry_wait`, `succeeded`,
`failed`, `cancelled`. Overlaying them naively would break every client.

## Decision

Keep `JobManager` as a facade over the runtime, with a fixed mapping:

| Runtime state | `/jobs` status |
|---|---|
| `queued`, `retry_wait` | `queued` |
| `running` | `running` |
| `succeeded` | `completed` |
| `failed`, `cancelled` | `failed` |

`cancelled` maps to `failed` because `/jobs` has no cancelled status and adding
one would break clients that switch on the existing four values.

### Two execution modes

`JobManager.submit` selects automatically:

- **Durable** (a runtime is bound, which is what application startup does).
  `runner` is ignored; the registered handler for the job type executes.
- **In-process** (no runtime bound). The original closure-based path, retained
  because `JobManager` is a public class that existing callers construct
  directly, and because existing tests cover that behaviour.

Jobs run in-process do **not** survive a restart, are not retried, and cannot be
cancelled. The application's own `/jobs` route always uses the durable mode,
because startup binds a runtime before serving.

### Idempotency keys stay in the runtime namespace

`TaskRuntime.submit` supports an `idempotency_key`. `JobManager` does not expose
one, because the old `/jobs` contract has no such field. It is available via
`POST /tasks`.

## Consequences

- `JobManager` grew rather than shrank. The compatibility surface is now
  roughly a third of the file; the rest is the legacy in-process path, marked as
  deprecated.
- Two sets of job-like records exist. `jobs` is a compatibility record written
  by `JobManager`; `tasks` is authoritative. Nothing else should read `jobs`
  expecting execution state.
- `list_jobs()` now reads the durable store rather than an in-process dict,
  which fixes jobs disappearing from the listing after a restart.

## Follow-up

Once `/jobs` deprecation is agreed, delete `JobManager` and the `jobs` table.
Do not do this in Phase 1 — it would be a breaking API change.