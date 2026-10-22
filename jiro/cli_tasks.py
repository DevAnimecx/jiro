"""``jiro tasks`` CLI commands.

Kept in its own module so ``jiro/cli.py`` only needs an import and a
``add_typer`` call. Each command opens the database, builds a runtime, and
performs one operation against the same durable store the server uses — the CLI
is a first-class client of the runtime, not a second implementation.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console
from rich.table import Table

from jiro.db import Database
from jiro.runtime import HandlerRegistry, SqlTaskRepository, TaskRuntime
from jiro.runtime.builtin import register_builtin_handlers
from jiro.runtime.types import TaskState

tasks_app = typer.Typer(help="Durable tasks: submit, inspect, cancel.", no_args_is_help=True)

console = Console()


def _error(exc: Exception) -> None:
    console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
    raise typer.Exit(1)


async def _with_runtime(action, *, start_workers: bool = True):
    """Open the database, build a runtime, run ``action``, then clean up.

    Args:
        action: coroutine function receiving the runtime.
        start_workers: whether to start the worker pool. Reads do not need it;
            ``submit --wait`` does. Starting it also runs recovery, which is why
            ``recover`` deliberately does not need a separate path.
    """
    from jiro.config import Settings

    settings = Settings.load()
    db = Database(settings.db_path)
    await db.connect()
    registry = HandlerRegistry()
    register_builtin_handlers(registry, services=_LocalServices())
    runtime = TaskRuntime(SqlTaskRepository(db), registry, _config(settings))
    try:
        if start_workers:
            await runtime.start()
        else:
            # Recovery still runs; only the workers stay idle.
            await runtime.start()
            await runtime.stop()
        return await action(runtime)
    finally:
        await runtime.stop()
        await db.close()


def _config(settings: Any):
    from jiro.runtime.config import RuntimeConfig

    return RuntimeConfig.from_settings(settings)


class _LocalServices:
    """Stand-in collaborators for CLI-side execution.

    The CLI has no FastAPI app, so the built-in handlers get an object with no
    ``agent`` or ``scraper_client``. A task needing one fails honestly with a
    clear message instead of pretending to have run.
    """

    agent = None
    scraper_client = None


def _render_status(record: Any) -> None:
    colour = {
        TaskState.SUCCEEDED: "green",
        TaskState.FAILED: "red",
        TaskState.CANCELLED: "yellow",
        TaskState.RUNNING: "cyan",
    }.get(record.state, "white")
    console.print(
        f"[bold]{record.id}[/bold]  [{colour}]{record.state.value}[/{colour}]"
        f"  type={record.type} attempts={record.attempts}/{record.limits.max_attempts}"
    )
    if record.cancel_requested and not record.is_terminal:
        console.print("  [yellow]cancellation requested[/yellow]")
    if record.error_code:
        console.print(f"  error: [red]{record.error_code}[/red] {record.error_message or ''}")


@tasks_app.command("types", help="List registered task types and their input schemas.")
def list_types() -> None:
    """Show what can be submitted, and what each type accepts."""

    async def action(runtime: TaskRuntime) -> Dict[str, Any]:
        return dict(runtime.registry.describe())

    described = asyncio.run(_with_runtime(action, start_workers=False))
    if not described:
        console.print("[yellow]No task types registered.[/yellow]")
        return
    table = Table("type", "description", "fields")
    for name, info in described.items():
        fields = ", ".join(info["schema"].get("properties", {})) or "-"
        table.add_row(name, info.get("description", ""), fields)
    console.print(table)


@tasks_app.command("submit", help="Submit a durable task.")
def submit_task(
    type: str = typer.Argument(..., help="Registered task type."),
    payload: str = typer.Option(
        "{}", "--payload", "-p", help="JSON object matching the type's schema."
    ),
    max_attempts: Optional[int] = typer.Option(None, "--max-attempts"),
    timeout: Optional[float] = typer.Option(None, "--timeout", help="Attempt timeout in seconds."),
    idempotency_key: Optional[str] = typer.Option(None, "--idempotency-key", "-k"),
    wait: bool = typer.Option(False, "--wait", help="Block until terminal."),
) -> None:
    """Validate and durably persist a task, optionally waiting for the result."""
    try:
        data: Dict[str, Any] = json.loads(payload or "{}")
    except ValueError as exc:
        console.print(f"[red]--payload is not valid JSON: {exc}[/red]")
        raise typer.Exit(1)

    limits = {
        k: v
        for k, v in (
            ("max_attempts", max_attempts),
            ("attempt_timeout_seconds", timeout),
        )
        if v is not None
    }

    async def action(runtime: TaskRuntime) -> Any:
        record = await runtime.submit(
            type, data, idempotency_key=idempotency_key, limits=limits or None
        )
        console.print(f"[green]submitted[/green] {record.id}")
        if not wait:
            return record
        console.print("waiting…")
        final = await runtime.wait_for(record.id, timeout=600)
        return final

    try:
        record = asyncio.run(_with_runtime(action, start_workers=wait))
    except Exception as exc:  # surface the runtime's own error taxonomy
        _error(exc)
        return
    _render_status(record)
    if wait and record.result:
        console.print(json.dumps(record.result, indent=2, default=str))


@tasks_app.command("status", help="Show a task's status and result.")
def task_status(
    task_id: str = typer.Argument(..., help="Task id, e.g. task_abc123."),
) -> None:
    async def action(runtime: TaskRuntime) -> Any:
        return await runtime.get(task_id, owner_key_id=None, enforce_owner=False)

    try:
        record = asyncio.run(_with_runtime(action, start_workers=False))
    except Exception as exc:
        _error(exc)
        return
    _render_status(record)
    if record.result:
        console.print(json.dumps(record.result, indent=2, default=str))


@tasks_app.command("list", help="List tasks, newest first.")
def list_tasks(
    limit: int = typer.Option(20, "--limit", "-n"),
    state: Optional[str] = typer.Option(None, "--state", help="Filter by state."),
) -> None:
    states = [TaskState(state)] if state else None

    async def action(runtime: TaskRuntime) -> List[Any]:
        return await runtime.list(owner_key_id=None, limit=limit, states=states)

    try:
        records = asyncio.run(_with_runtime(action, start_workers=False))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        _error(exc)
        return
    if not records:
        console.print("[yellow]No tasks.[/yellow]")
        return
    table = Table("id", "type", "state", "attempts", "created")
    for record in records:
        table.add_row(
            record.id,
            record.type,
            record.state.value,
            f"{record.attempts}/{record.limits.max_attempts}",
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record.created_at)),
        )
    console.print(table)


@tasks_app.command("cancel", help="Request cancellation of a task.")
def cancel_task(task_id: str = typer.Argument(...)) -> None:
    async def action(runtime: TaskRuntime) -> Any:
        return await runtime.cancel(task_id, owner_key_id=None, enforce_owner=False)

    try:
        record = asyncio.run(_with_runtime(action, start_workers=False))
    except Exception as exc:
        _error(exc)
        return
    _render_status(record)
    if not record.is_terminal:
        console.print(
            "[yellow]Cancellation requested. The task stops once its handler "
            "unwinds; re-run this command to check.[/yellow]"
        )


@tasks_app.command("events", help="Show a task's lifecycle events.")
def task_events(task_id: str = typer.Argument(...)) -> None:
    async def action(runtime: TaskRuntime) -> List[Any]:
        return await runtime.events(task_id, owner_key_id=None)

    try:
        events = asyncio.run(_with_runtime(action, start_workers=False))
    except Exception as exc:
        _error(exc)
        return
    table = Table("ts", "type", "attempt", "metadata")
    for event in events:
        table.add_row(
            time.strftime("%H:%M:%S", time.gmtime(event.ts)),
            event.type.value,
            str(event.attempt or "-"),
            json.dumps(event.metadata, default=str),
        )
    console.print(table)


@tasks_app.command("recover", help="Reconcile durable state (runs at startup too).")
def recover() -> None:
    """Run a reconciliation pass and report what changed."""

    async def action(runtime: TaskRuntime) -> Any:
        return await runtime.recover()

    try:
        report = asyncio.run(_with_runtime(action, start_workers=False))
    except Exception as exc:
        _error(exc)
        return
    console.print(json.dumps(report.to_dict(), indent=2, default=str))
    if report.errors:
        console.print(f"[red]{len(report.errors)} task(s) could not be reconciled[/red]")


__all__ = ["tasks_app"]
