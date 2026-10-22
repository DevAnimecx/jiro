# ADR 0004 — Cancellation reports the truth, never a race

- **Status:** Accepted (v0.4.0 Phase 1)
- **Date:** 2026-10-12

## Context

"Cancel" is the easiest thing to fake and the most damaging thing to fake. A
task reported `cancelled` while its handler keeps running produces a system
where the database, the logs and the caller's expectations all disagree.

## Decision

Cancellation semantics differ by state, and the difference is the point.

| State at request | Behaviour | Reported state |
|---|---|---|
| `queued`, `retry_wait` | Atomic conditional UPDATE to terminal | `cancelled`, immediately |
| `running` | Persist `cancel_requested=1`, then `.cancel()` the in-process task | `running` + `cancel_requested` until the handler unwinds, then `cancelled` |
| terminal | No-op | unchanged terminal state |

The running case never writes `cancelled` optimistically. The worker holds the
`CancelledError`, records the terminal state, and only then does a status read
report `cancelled`. Between the request and the unwind, the API reports
`running` with `cancel_requested: true` — which is the truth.

Repeated cancellation is a safe no-op. Cancelling a succeeded task does not
resurrect it and does not raise.

## Sync handlers are refused, not adapted

`HandlerRegistry.register` rejects any handler that is not async. The reason is
cancellation: `asyncio.to_thread` cannot be cancelled, so wrapping a sync
handler would produce a runtime that reports `cancelled` while the handler
continues — exactly the dishonesty this ADR exists to prevent.

The attempt timeout is the backstop for a handler that ignores cancellation,
not a guarantee that it stopped. `wait_for` cancels the inner task; an async
handler that does not yield cannot be forced.

## Shutdown is not cancellation

If the worker pool is stopped while an attempt is in flight, the attempt is
cancelled but the task is **not** marked `cancelled`. `_on_cancelled` checks the
durable `cancel_requested` flag; without it, the task is left with a lapsing
lease so recovery requeues it on next start. An operator-initiated shutdown is
not recorded as a user request.

## Consequences

- `POST /tasks/{id}/cancel` can return `running`. That is correct, and the
  response body carries a `hint` explaining it.
- A handler that wants prompt cancellation should poll
  `await ctx.cancel_requested()` during long operations.
- Handlers registered as sync functions are a registration error, not a
  runtime surprise.