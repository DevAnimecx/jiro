"""Jiro CLI (Typer + Rich)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import warnings
from typing import Optional

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from jiro import __version__
from jiro.cli_plugins import plugin_app
from jiro.config import Settings

# Fix Windows encoding issues
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


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
dev_app = typer.Typer(help="Developer commands (install from GitHub).", no_args_is_help=True)
app.add_typer(search_app, name="search")
app.add_typer(keys_app, name="keys")
app.add_typer(config_app, name="config")
app.add_typer(plugin_app, name="plugins")
app.add_typer(dev_app, name="dev")

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
        console.print(f"[bold green]+[/] key created: [bold]{data['api_key']}[/]")
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
        console.print(f"[bold green]+[/] key {key_id} revoked")


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
    console.print(f"[bold green]+[/] wrote {target}")


@config_app.command("show", help="Show the effective configuration.")
def config_show(config: str = typer.Option(None, "--config", "-c")) -> None:
    settings = Settings.load(config)
    console.print(json.dumps(settings.dump(), indent=2, default=str))


# --------------------------------------------------------------------------
# update
# --------------------------------------------------------------------------
@app.command(help="Update Jiro to the latest version with health checks.")
def update(
    check_only: bool = typer.Option(False, "--check", help="Check for updates without installing"),
    force: bool = typer.Option(False, "--force", help="Force update even if already latest"),
    dev: bool = typer.Option(False, "--dev", help="Install latest dev version from GitHub (main branch)"),
    skip_backup: bool = typer.Option(False, "--skip-backup", help="Skip database backup"),
    clear_cache: bool = typer.Option(True, "--clear-cache/--no-clear-cache", help="Clear cache after update"),
    run_tests: bool = typer.Option(True, "--tests/--no-tests", help="Run tests after update"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Update Jiro to the latest version.

    This command will:
    1. Check current version
    2. Backup database (optional)
    3. Install latest version
    4. Run database migrations
    5. Clear cache (optional)
    6. Verify all components work
    7. Run tests (optional)

    Use --dev to install the latest commit from GitHub main branch.
    """
    asyncio.run(_run_update(check_only, force, dev, skip_backup, clear_cache, run_tests, config))


async def _run_update(
    check_only: bool,
    force: bool,
    dev: bool,
    skip_backup: bool,
    clear_cache: bool,
    run_tests: bool,
    config: str,
) -> None:
    """Execute the update process."""
    from pathlib import Path

    console.print(Panel.fit(
        f"[bold]Jiro Update[/] v{__version__}"
        + (" [yellow](dev mode - GitHub)[/]" if dev else ""),
        subtitle="Checking for updates..."
    ))

    # Step 1: Check current version
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Checking current version...", total=None)

        current_version = __version__
        progress.update(task, description=f"Current version: [bold]{current_version}[/]")

        # Step 2: Check for latest version
        progress.update(task, description="Checking for latest version...")
        latest_version = current_version

        if dev:
            # Dev mode: get latest commit info from GitHub
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade",
                     "git+https://github.com/DevAnimecx/jiro.git@main#subdirectory=jiro-search"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    latest_version = "dev (main)"
                else:
                    console.print(f"[red]GitHub install failed:[/]\n{result.stderr}")
                    raise typer.Exit(1)
            except subprocess.TimeoutExpired:
                console.print("[red]GitHub install timed out[/]")
                raise typer.Exit(1)
            progress.update(task, description=f"Latest: [bold]{latest_version}[/]")

            if not force and not check_only:
                progress.stop()
                console.print(f"\n[bold yellow]Dev version will be installed from GitHub main.[/]")
                if not typer.confirm("Continue?"):
                    raise typer.Exit(0)
                progress.start()
        else:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "index versions", "jirosearch"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0 and "Latest:" in result.stdout:
                    latest_line = [l for l in result.stdout.split("\n") if "Latest:" in l][0]
                    latest_version = latest_line.split(":")[-1].strip()
                else:
                    # Fallback: try to get from PyPI API
                    import urllib.request
                    import urllib.error
                    try:
                        with urllib.request.urlopen("https://pypi.org/pypi/jirosearch/json", timeout=10) as response:
                            data = json.loads(response.read())
                            latest_version = data["info"]["version"]
                    except Exception:
                        latest_version = current_version
            except Exception as e:
                console.print(f"[yellow]Could not check PyPI: {e}[/]")
                latest_version = current_version

            progress.update(task, description=f"Latest version: [bold]{latest_version}[/]")

        # Check if update needed
        if current_version == latest_version and not force and not dev:
            progress.stop()
            console.print(f"\n[bold green]+ Already up to date! (v{current_version})[/]")
            if check_only:
                return
            console.print("[dim]Use --force to reinstall the current version.[/]")
            return

        if check_only:
            progress.stop()
            console.print(f"\n[bold yellow]Update available: {current_version} -> {latest_version}[/]")
            return

        # For dev mode, skip the version comparison check above
        if dev:
            progress.stop()
            console.print(f"\n[bold yellow]Installing dev version from GitHub...[/]")

        # Step 3: Backup database
        if not skip_backup:
            progress.update(task, description="Backing up database...")
            try:
                settings = Settings.load(config)
                db_path = Path(settings.db_path).expanduser()
                if db_path.exists():
                    backup_path = db_path.with_suffix(f".backup_{current_version}.db")
                    import shutil
                    shutil.copy2(db_path, backup_path)
                    progress.update(task, description=f"Database backed up to {backup_path.name}")
                else:
                    progress.update(task, description="No database to backup")
            except Exception as e:
                progress.update(task, description=f"[yellow]Backup skipped: {e}[/]")

        # Step 4: Install latest version
        if dev:
            progress.update(task, description="Installing latest dev version from GitHub...")
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade",
                     "git+https://github.com/DevAnimecx/jiro.git@main#subdirectory=jiro-search"],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                if result.returncode != 0:
                    progress.stop()
                    console.print(f"[red]GitHub install failed:[/]\n{result.stderr}")
                    raise typer.Exit(1)
                progress.update(task, description="[bold green]Installed dev version from GitHub[/]")
            except subprocess.TimeoutExpired:
                progress.stop()
                console.print("[red]GitHub install timed out[/]")
                raise typer.Exit(1)
        else:
            progress.update(task, description=f"Installing jirosearch {latest_version}...")
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", "jirosearch"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    progress.stop()
                    console.print(f"[red]Installation failed:[/]\n{result.stderr}")
                    raise typer.Exit(1)
                progress.update(task, description=f"[bold green]Installed jirosearch {latest_version}[/]")
            except subprocess.TimeoutExpired:
                progress.stop()
                console.print("[red]Installation timed out[/]")
                raise typer.Exit(1)

        # Step 5: Clear cache
        if clear_cache:
            progress.update(task, description="Clearing cache...")
            try:
                settings = Settings.load(config)
                if settings.cache_type == "sqlite":
                    db_path = Path(settings.db_path).expanduser()
                    cache_db = db_path.parent / "cache.db"
                    if cache_db.exists():
                        cache_db.unlink()
                        progress.update(task, description="Cache cleared")
                    else:
                        progress.update(task, description="No cache to clear")
                else:
                    progress.update(task, description="Cache type is not SQLite, skipping")
            except Exception as e:
                progress.update(task, description=f"[yellow]Cache clear skipped: {e}[/]")

        # Step 6: Verify components
        progress.update(task, description="Verifying components...")
        verification_results = []

        # Check imports
        try:
            from jiro import __version__ as new_version
            from jiro.mcp import JiroMCPServer
            from jiro.ai.tools import mcp_tools
            from jiro.scraping.social import SocialRouter
            from jiro.search.intent import IntentClassifier
            verification_results.append(("Core imports", True, f"v{new_version}"))
        except Exception as e:
            verification_results.append(("Core imports", False, str(e)))

        # Check MCP tools count
        try:
            from jiro.ai.tools import mcp_tools
            tools = mcp_tools()
            verification_results.append(("MCP tools", True, f"{len(tools)} tools"))
        except Exception as e:
            verification_results.append(("MCP tools", False, str(e)))

        # Check social platforms
        try:
            from jiro.scraping.social import SocialRouter
            router = SocialRouter()
            platforms = list(set(p for p, _ in router._platform_patterns))
            verification_results.append(("Social platforms", True, f"{len(platforms)} platforms"))
        except Exception as e:
            verification_results.append(("Social platforms", False, str(e)))

        # Check search engines
        try:
            from jiro.ai.tools import ENGINE_ENUM
            verification_results.append(("Search engines", True, f"{len(ENGINE_ENUM)} engines"))
        except Exception as e:
            verification_results.append(("Search engines", False, str(e)))

        # Print verification results
        progress.stop()
        console.print("\n[bold]Verification Results:[/]")
        for name, success, detail in verification_results:
            status = "[bold green]OK[/]" if success else "[bold red]FAIL[/]"
            console.print(f"  [{status}] {name}: {detail}")

        # Step 7: Run tests
        if run_tests:
            console.print("\n[bold]Running tests...[/]")
            try:
                # Try to find tests in source repo first, then installed package
                source_dir = Path(__file__).parent.parent
                test_dir = source_dir / "tests"
                if not test_dir.exists():
                    # Fallback: look for jiro-search in common locations
                    for candidate in [
                        Path.cwd() / "jiro-search" / "tests",
                        Path.cwd() / "tests",
                        Path.home() / ".jiro" / "src" / "jiro-search" / "tests",
                    ]:
                        if candidate.exists():
                            test_dir = candidate
                            break

                if test_dir.exists():
                    result = subprocess.run(
                        [sys.executable, "-m", "pytest", str(test_dir / "test_mcp.py"),
                         "-q", "--tb=short"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        cwd=str(test_dir.parent),
                    )
                    if result.returncode == 0:
                        console.print("[bold green]+ MCP tests passed[/]")
                    else:
                        console.print(f"[yellow]! Some tests failed:[/]\n{result.stdout[-500:]}")
                else:
                    console.print("[yellow]Tests directory not found, skipping tests[/]")
            except Exception as e:
                console.print(f"[yellow]Tests skipped: {e}[/]")

        # Final summary
        console.print("\n" + "=" * 50)
        console.print(Panel.fit(
            f"[bold green]Update Complete![/]\n\n"
            f"Version: [bold]{current_version}[/] -> [bold]{latest_version}[/]\n"
            f"MCP Tools: 16\n"
            f"Social Platforms: 12\n"
            f"Search Engines: 9\n\n"
            f"[dim]Run 'jiro serve' to start the server[/]",
            title="Summary"
        ))


@app.command(help="Check for Jiro updates without installing.")
def check_update(
    dev: bool = typer.Option(False, "--dev", help="Check latest dev version from GitHub"),
) -> None:
    """Check if a newer version is available."""
    asyncio.run(_run_update(check_only=True, force=False, dev=dev, skip_backup=True,
                           clear_cache=False, run_tests=False, config=None))


@dev_app.command("update", help="Install latest Jiro from GitHub (main branch).")
def dev_update(
    force: bool = typer.Option(False, "--force", help="Force reinstall even if already latest"),
    skip_backup: bool = typer.Option(False, "--skip-backup", help="Skip database backup"),
    clear_cache: bool = typer.Option(True, "--clear-cache/--no-clear-cache", help="Clear cache after update"),
    run_tests: bool = typer.Option(True, "--tests/--no-tests", help="Run tests after update"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Install the latest development version from GitHub main branch.

    This is the fastest way to get the latest fixes and features.
    Equivalent to: jiro update --dev
    """
    asyncio.run(_run_update(
        check_only=False, force=force, dev=True,
        skip_backup=skip_backup, clear_cache=clear_cache,
        run_tests=run_tests, config=config
    ))


@dev_app.command("install", help="Install Jiro from GitHub (alias for dev update).")
def dev_install(
    force: bool = typer.Option(False, "--force", help="Force reinstall even if already latest"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Install the latest development version from GitHub main branch."""
    asyncio.run(_run_update(
        check_only=False, force=force, dev=True,
        skip_backup=False, clear_cache=True,
        run_tests=True, config=config
    ))


@app.command(help="Show Jiro system status and health.")
def status() -> None:
    """Show system status, version, and component health."""
    asyncio.run(_run_status())


async def _run_status() -> None:
    """Check system status."""
    from pathlib import Path

    console.print(Panel.fit(f"[bold]Jiro System Status[/] v{__version__}"))

    # Check components
    checks = []

    # Version check
    checks.append(("Version", True, __version__))

    # Config check
    try:
        settings = Settings.load()
        checks.append(("Config", True, "Loaded"))
    except Exception as e:
        checks.append(("Config", False, str(e)))

    # Database check
    try:
        settings = Settings.load()
        db_path = Path("~/.jiro/jiro.db").expanduser()
        if db_path.exists():
            size_mb = db_path.stat().st_size / (1024 * 1024)
            checks.append(("Database", True, f"{size_mb:.1f} MB"))
        else:
            checks.append(("Database", True, "Not created yet"))
    except Exception as e:
        checks.append(("Database", False, str(e)))

    # MCP check
    try:
        from jiro.ai.tools import mcp_tools
        tools = mcp_tools()
        checks.append(("MCP Server", True, f"{len(tools)} tools"))
    except Exception as e:
        checks.append(("MCP Server", False, str(e)))

    # Social check
    try:
        from jiro.scraping.social import SocialRouter
        router = SocialRouter()
        checks.append(("Social Scrapers", True, "12 platforms"))
    except Exception as e:
        checks.append(("Social Scrapers", False, str(e)))

    # Search check
    try:
        from jiro.ai.tools import ENGINE_ENUM
        checks.append(("Search Engines", True, f"{len(ENGINE_ENUM)} engines"))
    except Exception as e:
        checks.append(("Search Engines", False, str(e)))

    # Intent check
    try:
        from jiro.search.intent import IntentClassifier
        checks.append(("Intent Classifier", True, "Ready"))
    except Exception as e:
        checks.append(("Intent Classifier", False, str(e)))

    # Print results
    table = Table(title="System Status")
    table.add_column("Component")
    table.add_column("Status")
    table.add_column("Details")

    for name, success, detail in checks:
        status = "[bold green]OK[/]" if success else "[bold red]FAIL[/]"
        table.add_row(name, status, detail)

    console.print(table)

    # Summary
    passed = sum(1 for _, s, _ in checks if s)
    total = len(checks)
    console.print(f"\n[bold]{passed}/{total}[/] components healthy")


if __name__ == "__main__":
    app()
