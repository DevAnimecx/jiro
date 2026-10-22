# ADR 0003 — At-least-once execution, with leases and renewal

- **Status:** Accepted (v0.4.0 Phase 1)
- **Date:** 2026-10-12

## Context

Task state lives in SQLite. A process can die between a handler's external side
effect and the commit that records its outcome. No amount of database
correctness makes those two operations atomic, because the external effect is
not a database write.

## Decision

### Guarantee: at-least-once

A task interrupted after its handler performed an external effect will be
re-executed. This is documented rather than hidden:
`tests/test_runtime_recovery.py::test_crash_after_external_side_effect_before_commit`
kills a real process at that boundary and asserts the effect occurred **twice**.

Exactly-once is not claimed and is not achievable here. `TaskContext` exposes
`idempotency_key` and `task_id`; a handler performing a non-idempotent operation
must pass one of those to the downstream provider.

### Claiming: compare-and-swap

`SqlTaskRepository.claim` selects a candidate then issues

```sql
UPDATE tasks SET state='running', attempts=attempts+1, lease_owner=?, ...
WHERE id=? AND state IN ('queued','retry_wait') AND cancel_requested=0
```

The state guard is re-checked inside the UPDATE. Under SQLite's single-writer
model this is atomic: the loser of a race gets `rowcount == 0` and retries. No
read-then-write sequence exists, and no transaction is held open across a
handler.

### Leases with renewal

A claimed task gets `lease_owner = "<instance>:<worker index>"` and
`lease_expires_at`. A running worker **renews** the lease on a heartbeat every
`lease_ttl / 3`.

Renewal exists because the alternative is worse. Without it, the TTL must
exceed the longest permitted attempt, so recovery of a task interrupted 1s into
a 60-minute attempt would wait 60 minutes. With renewal, the TTL tracks real
liveness and bounds recovery latency after a hard kill.

A task whose lease has lapsed is presumed abandoned. A task with a *live* lease
is never touched, which is what makes the design safe when more than one process
shares the database.

Every worker write carries `AND lease_owner = ?`. A worker that has lost its
lease cannot overwrite the state of the task that replaced it — verified by
`test_stale_worker_cannot_overwrite_recovered_task`.

## Consequences

- A long attempt that blocks the event loop past `lease_ttl` can be reclaimed
  and re-executed. Config logs a warning when `lease_ttl <=
  max_attempt_timeout`; it is a warning, not an error, because renewal normally
  prevents it.
- Multi-process deployments sharing one SQLite file are supported for claiming
  and recovery, but `PRAGMA busy_timeout` (10s) is the only thing smoothing
  write contention. Run one replica, per the existing `db.py` guidance.

## Considered and rejected

**A transaction held across the handler.** Would serialise every other writer
in the process for the duration of every task. Explicitly forbidden by the
design and by the docstring on `Database.transaction()`.