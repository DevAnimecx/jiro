# ADR 0002 — Task identity is an API key, not a user

- **Status:** Accepted (v0.4.0 Phase 1)
- **Date:** 2026-10-12

## Context

Phase 1 acceptance requires that users cannot inspect or cancel another user's
tasks. Jiro has no user, account, or tenant table. `AuthContext` exposes only:

- `key_id` — the id of the `api_keys` row that authenticated the request
- `role` — `admin` / `user` / `anonymous`

`jiro/tenants.py` models tenants but is entirely in-memory, is never consulted
by the auth layer, and its access checks are never called from any router.

## Decision

Tasks are owned by `AuthContext.key_id`, stored in `tasks.owner_key_id`. Every
read, cancel and list goes through `_owns()` in `jiro/runtime/runtime.py`, which
enforces:

- a task with no owner is reachable only by a caller that also has no identity
- a task with an owner is reachable only by that owner
- "not yours" and "does not exist" both raise `TaskNotFound`, so the API cannot
  be used to discover that a task id exists

`list()` takes `owner_key_id` as a **required keyword argument**. Making it
mandatory removes the ambiguity between "I meant the anonymous namespace" and
"I forgot to scope this", the latter of which would be an unscoped read of every
task in the database.

## Consequences

**Known limitation, stated plainly:** `key_id` identifies an API *key*, not a
person. A user holding three keys has three separate task namespaces and cannot
see their own tasks across keys. Admin-role keys are not exempt — `enforce_owner`
is not bypassable from a transport adapter.

This is the strongest boundary available without a user table. It is a real
boundary, not a documentation convention: the scoping is enforced in the runtime
layer, so a new transport that forgets to pass an owner gets a `TypeError`
rather than an unscoped read.

## Follow-up

Introduce an `accounts` table and map `api_keys.account_id` to it, then change
`owner_key_id` to the account id. The runtime does not need to change — only
what the REST adapter passes as `owner_key_id`.

## MCP boundary

MCP has no per-client identity in Phase 1 (see ADR 0006). Rather than expose an
unscoped view, MCP operates inside a fixed `mcp` owner namespace and can only
read or cancel tasks it submitted itself.