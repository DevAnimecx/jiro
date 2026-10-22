# ADR 0007 — Schema changes are additive; the migration hook stays empty

- **Status:** Accepted (v0.4.0 Phase 1)
- **Date:** 2026-10-12

## Context

`Database` already has a migration mechanism: `Database.MIGRATIONS` (an ordered
list of SQL strings) applied once each, tracked by a `schema_version` table, run
inside `_apply_migrations` at `connect()`.

An initial plan was to add `MIGRATIONS` entry 1 as
`ALTER TABLE tasks ADD COLUMN owner_key_id TEXT`. That is wrong: `SCHEMA`
already creates `tasks` with that column, `executescript(SCHEMA)` runs *before*
`_apply_migrations`, and SQLite has no `ADD COLUMN IF NOT EXISTS`. The statement
would fail on every fresh database.

## Decision

`tasks` and `task_events` are new tables, created complete by `SCHEMA`, exactly
as every other table in this database is. `MIGRATIONS` stays empty.

This is correct rather than a shortcut:

- A database created by v0.3.3 has no `tasks` table. On next `connect()`,
  `CREATE TABLE IF NOT EXISTS` creates it complete — same as a fresh one.
- No existing table or column is altered, so no user data can be lost.
- Verified by `test_upgrade_from_pre_runtime_schema`, which builds a
  v0.3.3-shaped database with raw sqlite3, opens it through `Database`, and
  asserts the runtime tables appear while the pre-existing `jobs` row survives.

`MIGRATIONS` remains the documented extension point for genuinely additive
*column* changes. It is append-only and 1-indexed; never reorder or edit an
entry.

### Safety net

`Database.verify_schema()` checks that required tables exist, and the runtime
calls it at startup. A half-created or corrupt database fails immediately with a
precise message naming the missing table, instead of surfacing later as a
cryptic "no such column" during the first submission.

### Idempotency needed a non-null scope column

`UNIQUE (owner_key_id, idempotency_key)` does not work: SQLite treats NULLs as
distinct, so an anonymous caller (`owner_key_id IS NULL`) submitting the same
idempotency key twice would never conflict, and idempotency would silently fail
for exactly the local installs most likely to use it.

Fixed with a non-null `idempotency_scope` column and a partial unique index:

```sql
CREATE UNIQUE INDEX idx_tasks_idempotency
    ON tasks(idempotency_scope, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
```

The partial predicate lets any number of tasks have no idempotency key; the
non-null scope makes the rule work for anonymous owners.

## Backup and rollback

**Backup.** Stop the process, then copy the database. With WAL enabled, copy
`jiro.db`, `jiro.db-wal` and `jiro.db-shm` together, or run
`sqlite3 jiro.db ".backup backup.db"` which is safe against a live writer.

Note: `jiro update` uses `shutil.copy2` on the main file only, which is **not**
WAL-safe. This is pre-existing and not changed here.

**Rollback.** The runtime tables are independent of every other table. Rolling
back the code leaves `tasks` and `task_events` in place as inert data. No
down-migration is needed or provided. If a future migration adds a column and
must be reverted, restore from backup — `schema_version` has no down path.