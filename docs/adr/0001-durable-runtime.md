# ADR 0001 — Durable task runtime replaces in-memory job execution

- **Status:** Accepted (v0.4.0 Phase 1)
- **Date:** 2026-10-12
- **Context:** Jiro v0.3.3

## Context

`jiro/jobs.py` accepted a job, wrote a row to the `jobs` table, and immediately
fired `asyncio.create_task(self._run(...))`. Consequences, all confirmed by
reading the code at `jiro/jobs.py:32-200` before this change:

- A process that died between persisting a job and starting it left the row in
  `queued` forever. Nothing scanned for it.
- A process that died mid-execution left the row in `running` forever.
- `list_jobs()` read `self._memory` only, so after a restart `GET /jobs`
  returned an empty list while rows sat in the table.
- There was no retry for job execution (only for webhook delivery), no attempt
  accounting, no timeout, and no cancellation.
- `_tasks` was populated but never read, so no job could ever be cancelled.

Phase 0 of v0.4.0 was expected to specify this subsystem, but no Phase 0
artifacts (handoff, architecture proposal, ADRs, task plan) exist in the
repository or on any remote branch. These decisions were therefore derived from
the code itself, and this ADR records the reasoning.

## Decision

Build a new package, `jiro/runtime/`, implementing a durable execution
substrate. Keep `jiro/jobs.py` and its public contract; re-point it at the new
runtime so `/jobs` behaviour is unchanged for existing clients.

The alternative — extending `JobManager` in place — was rejected because the
required guarantees (durable acceptance, a validated state machine, leases,
recovery, cancellation) do not fit the shape of a class that also serves a
compatibility response model, and retrofitting them would have been a larger
and less testable change than building the layer properly and delegating.

## Consequences

- `JobManager` gains a second, deprecated execution mode (see ADR 0005).
- Two sets of job-like records exist: `jobs` (compatibility) and `tasks`
  (authoritative). `JobManager` is the only writer of `jobs`.
- The runtime is the single execution path for anything durable. New features
  should target it rather than `JobManager`.

## Alternatives considered

- **Postgres / Redis queue.** Rejected: the requirement is a single-process
  SQLite deployment, and introducing a broker would add an operational
  dependency with no demonstrated need.
- **Extend the `jobs` table in place.** Rejected: it would have required
  changing `GET /jobs` to return `succeeded` instead of `completed`, a breaking
  change to a published API.

## Phase 0 artifacts

`JIRO_V040_PHASE0_HANDOFF.md`, `JIRO_V040_PHASE1_ARCHITECTURE.md`,
`JIRO_V040_PHASE1_TASKS.md` and any ADRs were searched for across the whole
workspace and all four Git remotes. None exist. This ADR, and ADRs 0002-0007,
stand in their place and should be reconciled against the real Phase 0
documents if they are later produced.