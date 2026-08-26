"""Jiro CLI (Typer + Rich)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import warnings
from typing import List, Optional

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from jiro import __version__
from jiro.cli_plugins import plugin_app
from jiro.config import Settings


def _quiet_settings(settings: Settings) -> Settings:
    """Silence the in-process app's logging for one-shot CLI commands."""
    settings.raw["logging"]["level"] = "critical"
    settings.raw["logging"]["file"] = ""
    return settings

app = typer.Typer(
    name="jiro",
    help="Jiro Search API — local-first, AI-native web search & scraping.",
    add_completion=False,
    no_args_is_help=True,
)
search_app = typer.Typer(help="Search the web from the CLI.", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys.", no_args_is_help=True)
config_app = typer.Typer(help="Configuration management.", no_args_is_help=True)
app.add_typer(search_app, name="search")
app.add_typer(keys_app, name="keys")
app.add_typer(config_app, name="config")
app.add_typer(plugin_app, name="plugins")

console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"jiro {__version__}")
        raise typer.Exit()


@app.callback()
def main(version: bool = typer.Option(False, "--version", callback=version_callback,
                                      is_eager=True, help="Show version.")) -> None:
    pass


# --------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------
@app.command(help="Start the Jiro API server.")
def serve(
    host: str = typer.Option(None, help="Bind host (default: from config)"),
    port: int = typer.Option(None, help="Bind port (default: from config)"),
    workers: int = typer.Option(None, help="Number of workers"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
    insecure: bool = typer.Option(
        False, "--insecure",
        help="Allow binding to 0.0.0.0 with auth DISABLED (dangerous; only for sandboxes)",
    ),
    config: str = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    settings = Settings.load(config)
    _host = host or settings.host
    _port = port or settings.port
    _workers = workers or settings.workers
    if insecure:
        # Propagate to the app via env so create_app() sees server.insecure.
        os.environ["JIRO_SERVER__INSECURE"] = "true"
    console.print(f"[bold green]jiro[/] v{__version__} serving on "
                  f"http://{_host}:{_port}  (docs: /docs)")
    uvicorn.run(
        "jiro.server:create_app",
        factory=True,
        host=_host,
        port=_port,
        workers=1 if reload else _workers,
        reload=reload,
        timeout_graceful_shutdown=30,
        log_level=settings.logging.get("level", "info"),
    )


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------
@search_app.command("web", help="Web search (JSON output).")
def search_web(
    q: str = typer.Argument(..., help="Search query"),
    engine: str = typer.Option("google", "--engine", "-e"),
    type: str = typer.Option("web", "--type", "-t"),
    num: int = typer.Option(10, "--num", "-n"),
    location: str = typer.Option("us", "--location", "-l"),
    language: str = typer.Option("en", "--language"),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    asyncio.run(_cli_search(q, engine, type, num, location, language,
                            json_output, config))


async def _cli_search(q, engine, type, num, location, language, json_output, config):
    warnings.filterwarnings("ignore")
    from jiro.server import create_app
    from jiro.models import SearchRequest
    from starlette.testclient import TestClient

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.get("/search.json", params={
            "q": q, "engine": engine, "type": type, "num": num,
            "location": location, "language": language,
        })
        data = resp.json()
        if json_output:
            console.print(json.dumps(data, indent=2, default=str))
            return
        meta = data.get("search_metadata", {})
        console.print(f"[bold]{meta.get('engine', '?')}[/] · "
                      f"[dim]cached={meta.get('cached', False)} "
                      f"time={meta.get('total_time_taken', 0)}s[/]")
        table = Table(title=f"Results for “{q}”")
        table.add_column("#", justify="right")
        table.add_column("Title")
        table.add_column("Source")
        table.add_column("Snippet", overflow="fold")
        for r in data.get("organic_results", []):
            table.add_row(str(r.get("position", "")), r.get("title", ""),
                          r.get("source", ""), (r.get("snippet") or "")[:140])
        console.print(table)


# --------------------------------------------------------------------------
# scrape
# --------------------------------------------------------------------------
@app.command(help="Scrape a URL and print readable content.")
def scrape(
    url: str = typer.Argument(..., help="URL to scrape"),
    format: str = typer.Option("markdown", "--format", "-f"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.post("/scrape", json={"url": url, "format": format})
        data = resp.json()
        if resp.status_code != 200:
            console.print(f"[red]{data.get('error', resp.text)}[/]")
            raise typer.Exit(1)
        console.print(f"[bold]{data.get('title', '')}[/]  [dim]({data.get('url')})[/]")
        console.print(data.get("content", "")[:4000])


# --------------------------------------------------------------------------
# ai
# --------------------------------------------------------------------------
@app.command(help="Ask a research question (agentic search with citations).")
def ask(
    query: str = typer.Argument(..., help="Research question"),
    max_sources: int = typer.Option(5, "--max-sources"),
    json_output: bool = typer.Option(False, "--json"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.post("/ai/search", json={"query": query,
                                               "max_sources": max_sources})
        data = resp.json()
        if json_output:
            console.print(json.dumps(data, indent=2, default=str))
            return
        console.print(data.get("answer", ""))
        console.print("\n[bold]Sources:[/]")
        for i, c in enumerate(data.get("citations", []), start=1):
            console.print(f"  [{i}] {c.get('title', '')} — {c.get('url', '')}")


# --------------------------------------------------------------------------
# mcp
# --------------------------------------------------------------------------
@app.command(help="Start the MCP server for AI agents.")
def mcp(
    transport: str = typer.Option("stdio", "--transport", "-t",
                                  help="stdio | http (Streamable HTTP + SSE)"),
    host: str = typer.Option(None, "--host", help="HTTP bind host"),
    port: int = typer.Option(None, "--port", help="HTTP port (default from config)"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    settings = Settings.load(config)
    if transport == "http":
        _host = host or settings.host
        _port = port or settings.port
        console.print(f"[bold green]jiro[/] MCP over HTTP on "
                      f"http://{_host}:{_port}/mcp  (+ /sse legacy transport, /docs)")
        import uvicorn
        from jiro.server import create_app
        uvicorn.run(create_app(settings), host=_host, port=_port,
                    log_level=settings.logging.get("level", "info"))
        return
    from jiro.mcp import run_mcp_stdio
    run_mcp_stdio(settings)


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------
def _admin_key_required() -> str:
    key = typer.prompt("Admin API key", hide_input=True)
    return key


@keys_app.command("create", help="Create an API key (requires an admin key).")
def keys_create(
    name: str = typer.Option(..., "--name"),
    role: str = typer.Option("user", "--role", help="admin | user"),
    rate_limit: int = typer.Option(0, "--rate-limit", help="RPM (0 = default)"),
    admin_key: Optional[str] = typer.Option(None, "--admin-key", envvar="JIRO_ADMIN_KEY"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        headers = {"X-API-Key": admin_key or _admin_key_required()}
        resp = client.post("/api-keys", json={"name": name, "role": role,
                                              "rate_limit_rpm": rate_limit},
                           headers=headers)
        if resp.status_code != 200:
            console.print(f"[red]{resp.json().get('error', resp.text)}[/]")
            raise typer.Exit(1)
        data = resp.json()
        console.print(f"[bold green]✓[/] key created: [bold]{data['api_key']}[/]")
        console.print("[yellow]Store it now — it will not be shown again.[/]")


@keys_app.command("list", help="List API keys (requires an admin key).")
def keys_list(
    admin_key: Optional[str] = typer.Option(None, "--admin-key", envvar="JIRO_ADMIN_KEY"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        headers = {"X-API-Key": admin_key or _admin_key_required()}
        resp = client.get("/api-keys", headers=headers)
        if resp.status_code != 200:
            console.print(f"[red]{resp.json().get('error', resp.text)}[/]")
            raise typer.Exit(1)
        table = Table(title="API keys")
        table.add_column("ID")
        table.add_column("Name")
        table.add_column("Prefix")
        table.add_column("Role")
        table.add_column("Created")
        for k in resp.json():
            table.add_row(k["id"][:12], k["name"], k["key_prefix"], k["role"],
                          k["created_at"][:10])
        console.print(table)


@keys_app.command("revoke", help="Revoke an API key (requires an admin key).")
def keys_revoke(
    key_id: str = typer.Argument(...),
    admin_key: Optional[str] = typer.Option(None, "--admin-key", envvar="JIRO_ADMIN_KEY"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        headers = {"X-API-Key": admin_key or _admin_key_required()}
        resp = client.delete(f"/api-keys/{key_id}", headers=headers)
        if resp.status_code != 200:
            console.print(f"[red]{resp.json().get('error', resp.text)}[/]")
            raise typer.Exit(1)
        console.print(f"[bold green]✓[/] key {key_id} revoked")


# --------------------------------------------------------------------------
# usage
# --------------------------------------------------------------------------
@app.command(help="Show usage statistics (requires an admin key).")
def usage(
    days: int = typer.Option(7, "--days"),
    admin_key: Optional[str] = typer.Option(None, "--admin-key", envvar="JIRO_ADMIN_KEY"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        headers = {"X-API-Key": admin_key or _admin_key_required()}
        resp = client.get("/usage", params={"days": days}, headers=headers)
        if resp.status_code != 200:
            console.print(f"[red]{resp.json().get('error', resp.text)}[/]")
            raise typer.Exit(1)
        data = resp.json()
        console.print(f"requests: [bold]{data['requests']}[/]  "
                      f"cached: {data['cached']}  tokens: {data['tokens_in'] + data['tokens_out']}")
        table = Table(title=f"By endpoint (last {days} days)")
        table.add_column("Endpoint")
        table.add_column("Requests")
        for row in data["by_endpoint"]:
            table.add_row(row["endpoint"], str(row["n"]))
        console.print(table)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
@config_app.command("init", help="Write the default config file.")
def config_init(
    path: str = typer.Option(None, "--path", help="Output path (default ~/.jiro/config.yaml)"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    from pathlib import Path
    target = Path(path).expanduser() if path else Path("~/.jiro/config.yaml").expanduser()
    settings = Settings.load(path)
    if target.exists() and not force:
        console.print(f"[yellow]{target} already exists (use --force to overwrite).[/]")
        raise typer.Exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    target.write_text(yaml.safe_dump(settings.dump(), sort_keys=False))
    console.print(f"[bold green]✓[/] wrote {target}")


@config_app.command("show", help="Show the effective configuration.")
def config_show(config: str = typer.Option(None, "--config", "-c")) -> None:
    settings = Settings.load(config)
    console.print(json.dumps(settings.dump(), indent=2, default=str))


if __name__ == "__main__":
    app()
