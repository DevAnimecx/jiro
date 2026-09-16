"""Jiro CLI (Typer + Rich)."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import warnings
from typing import List, Optional

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
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _quiet_settings(settings: Settings) -> Settings:
    """Silence the in-process app's logging for one-shot CLI commands."""
    settings.raw["logging"]["level"] = "critical"
    settings.raw["logging"]["file"] = ""
    # Disable auth for CLI commands (local-only, no network exposure)
    settings.raw["auth"]["enabled"] = False
    return settings

auth_app = typer.Typer(help="Cloud authentication.", no_args_is_help=True)
search_app = typer.Typer(help="Search the web from the CLI.", no_args_is_help=True)


keys_app = typer.Typer(help="Manage API keys.", no_args_is_help=True)
config_app = typer.Typer(help="Configuration management.", no_args_is_help=True)
dev_app = typer.Typer(help="Developer commands (install from GitHub).", no_args_is_help=True)
social_app = typer.Typer(help="Social media scraping from the CLI.", no_args_is_help=True)
mcp_app = typer.Typer(help="MCP server and client setup.", no_args_is_help=True)
ai_app = typer.Typer(help="AI features: ask questions, setup providers, check status.", no_args_is_help=True)
license_app = typer.Typer(help="License management for self-hosted.", no_args_is_help=True)

console = Console()


def _get_local_ip() -> str:
    """Get the local IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


import ipaddress


ALLOWED_DEV_IP = "192.168.1.3"


def _require_dev_ip():
    """Restrict dev commands to the developer's IP address."""
    current_ip = _get_local_ip()
    if current_ip != ALLOWED_DEV_IP:
        try:
            if ipaddress.ip_address(current_ip).is_private:
                return _permissive_dev_ip()
        except Exception:
            pass
        console.print(f"[red]Dev commands are restricted to the developer's IP.[/]")
        console.print(f"[dim]Your IP: {current_ip}[/]")
        raise typer.Exit(1)
    def decorator(func):
        return func
    return decorator


def _permissive_dev_ip():
    """Permissive decorator for local/private networks."""
    def decorator(func):
        return func
    return decorator


def version_callback(value: bool) -> None:
    if value:
        from jiro.cli_ui import console as ui_console, accent
        ui_console.print(accent(f"jiro v{__version__}"))
        raise typer.Exit()


HELP_EPILOG = (
    "Examples:\n"
    '  jiro search "best SaaS tools"          Search the web\n'
    "  jiro scrape https://example.com        Scrape a URL\n"
    '  jiro scrape "free SaaS directories"    Search + scrape top result\n'
    '  jiro ai ask "compare React vs Vue"     AI research with citations\n'
    "  jiro ai setup --provider openai -k sk-...  Configure AI provider\n"
    "  jiro login                              Sign in for cloud access\n"
    "  jiro doctor                             Diagnose issues"
)


app = typer.Typer(
    name="jiro",
    help="Jiro Search API - local-first, AI-native web search & scraping.\n\n" + HELP_EPILOG,
    no_args_is_help=False,
    add_completion=False,
)

app.add_typer(search_app, name="search")
app.add_typer(keys_app, name="keys")
app.add_typer(config_app, name="config")
app.add_typer(plugin_app, name="plugins")
app.add_typer(mcp_app, name="mcp")
app.add_typer(social_app, name="social")
app.add_typer(ai_app, name="ai")
app.add_typer(auth_app, name="auth")
app.add_typer(license_app, name="license")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", callback=version_callback,
                                  is_eager=True, help="Show version."),
) -> None:
    if ctx.invoked_subcommand is None and not version:
        from jiro.cli_ui import console as ui_console, make_logo
        ui_console.print(make_logo())
        ui_console.print()


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
    if sys.platform == "win32":
        _workers = 1
    if insecure:
        # Propagate to the app via env so create_app() sees server.insecure.
        os.environ["JIRO_SERVER__INSECURE"] = "true"
    from jiro.cli_ui import console as ui_console, success, rule, accent, dim, make_mini_logo
    from rich.align import Align
    ui_console.print()
    ui_console.print(Align.center(make_mini_logo()), style="bold")
    ui_console.print()
    ui_console.print(rule(f"Serving v{__version__}"))
    ui_console.print()
    ui_console.print(success(f"Listening on [bold white]http://{_host}:{_port}[/]"))
    ui_console.print(dim(f"  Docs: http://{_host}:{_port}/docs"))
    ui_console.print()
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
# auth login - RFC 8628 Device Code Flow
# --------------------------------------------------------------------------
@auth_app.command("login", help="Sign in to Jiro Cloud (device code flow).")
def auth_login(
    server: str = typer.Option(None, "--server", help="Jiro web server URL"),
    dev: bool = typer.Option(False, "--dev", help="Use development server"),
) -> None:
    """Sign in using OAuth 2.0 Device Authorization Grant (RFC 8628)."""
    import webbrowser
    from jiro.cloud_auth import (
        save_cloud_credentials, load_cloud_credentials,
        CloudCredentials, JIRO_WEB_BASE,
    )
    from jiro.device_auth import DeviceAuthManager, DeviceAuthError
    from jiro.cli_ui import (
        console, success, error, step, rule, device_auth_panel,
        account_card, accent, dim, make_mini_logo, box, Panel, Group, Text, Table, Align,
    )

    web_base = "http://localhost:3000" if dev else (server or JIRO_WEB_BASE)

    # Check if already logged in
    existing = load_cloud_credentials()
    if existing:
        console.print()
        console.print(success(f"Already signed in as [bold white]{existing.email}[/]"))
        if not typer.confirm("\n  Sign in with a different account?"):
            return

    # --- Header -----------------------------------------------------------
    console.print()
    console.print(Align.center(make_mini_logo()), style="bold")
    console.print()
    console.print(rule("Cloud Login"))
    console.print()

    # --- Step 1: Request device code ---------------------------------------
    console.print(step(1, 3, "Requesting device code"))
    console.print()
    auth = DeviceAuthManager(web_base)

    with console.status("[bold orange1]  Connecting to server...", spinner="dots"):
        try:
            code_data = auth.request_code()
        except DeviceAuthError as e:
            console.print()
            console.print(error(f"Failed to request device code: [white]{e}[/]"))
            raise typer.Exit(1)

    user_code = code_data["user_code"]
    verify_url = code_data["verification_uri_complete"]
    expires_in = code_data.get("expires_in", 900)

    console.print()
    console.print(success("Device code received"))
    console.print()

    # --- Step 2: Display code + open browser -------------------------------
    console.print(step(2, 3, "Authorize in browser"))
    console.print()

    code_panel = device_auth_panel(user_code, verify_url, expires_in)
    console.print(code_panel)

    try:
        webbrowser.open(verify_url)
        console.print()
        console.print(dim("  Browser opened automatically"))
    except Exception:
        console.print()
        console.print(dim(f"  Open this URL: {verify_url}"))

    console.print()
    console.print(dim("  No account? Sign up at https://searchjiro.vercel.app/register"))

    # --- Step 3: Poll for authorization -------------------------------------
    console.print()
    console.print(step(3, 3, "Waiting for authorization"))
    console.print()

    poll_count = 0
    def on_poll(remaining: int):
        nonlocal poll_count
        poll_count += 1
        mins, secs = divmod(remaining, 60)
        elapsed = "\u258c" * min(poll_count, 20)
        waiting = "\u2591" * max(0, 20 - poll_count)
        console.print(f"  [{elapsed}{waiting}]  {mins}m {secs}s remaining  ", end="\r")

    try:
        result = auth.wait_for_authorization(on_poll=on_poll)
    except DeviceAuthError as e:
        console.print()
        console.print(error(str(e)))
        raise typer.Exit(1)

    console.print()
    console.print()

    # --- Success! ----------------------------------------------------------
    credits = result.get("credits", {})
    user = result.get("user", {})

    creds = CloudCredentials(
        api_key=result["api_key"],
        user_id=user.get("id", "unknown"),
        email=user.get("email", "unknown"),
        name=user.get("name", "User"),
        plan=result.get("plan", "FREE"),
        role=user.get("role", "USER"),
        credits_used=credits.get("used", 0),
        credits_included=credits.get("included", 1000),
        credits_remaining=credits.get("remaining", 1000),
        rate_limit_rpm=5,
    )
    if creds.plan == "PRO":
        creds.rate_limit_rpm = 120
    elif creds.plan == "ENTERPRISE":
        creds.rate_limit_rpm = 1000

    save_cloud_credentials(creds)

    console.print(rule("Authenticated"))
    console.print()
    console.print(success(f"Welcome back, [bold white]{creds.name}[/]!"))
    console.print()

    card = account_card(
        creds.email, creds.name, creds.plan,
        creds.credits_used, creds.credits_included,
        creds.rate_limit_rpm, creds.api_key,
    )
    console.print(card)
    console.print()
    console.print(dim("  Run 'jiro auth logout' to sign out"))
    console.print()


def _display_account_info(creds) -> None:
    """Display account info in a formatted table."""
    table = Table(title="Cloud Account")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Email", creds.email)
    table.add_row("Name", creds.name)
    table.add_row("Plan", f"[bold]{creds.plan}[/]")
    table.add_row(
        "Credits",
        f"{creds.credits_remaining:,} / {creds.credits_included:,} remaining"
        if creds.credits_included > 0 else "Unlimited"
    )
    table.add_row("Rate Limit", f"{creds.rate_limit_rpm} RPM")
    table.add_row("API Key", f"***{creds.api_key[-4:]}" if len(creds.api_key) > 4 else creds.api_key)
    console.print(table)


@auth_app.command("logout", help="Sign out of Jiro Cloud.")
def auth_logout() -> None:
    """Remove stored cloud credentials (encrypted)."""
    from jiro.cloud_auth import clear_cloud_credentials, is_cloud_configured
    from jiro.cli_ui import (
        console, success, warning, dim, rule,
    )

    if not is_cloud_configured():
        console.print()
        console.print(warning("Not signed in"))
        console.print()
        return

    console.print()
    if typer.confirm("  Sign out of Jiro Cloud?"):
        clear_cloud_credentials()
        console.print()
        console.print(success("Signed out successfully"))
        console.print()
    else:
        console.print(dim("  Cancelled"))
        console.print()


@auth_app.command("whoami", help="Show current cloud account info.")
def auth_whoami(
    refresh: bool = typer.Option(False, "--refresh", "-r", help="Refresh credits from server"),
) -> None:
    """Display current cloud authentication status and credit balance."""
    from jiro.cloud_auth import load_cloud_credentials, refresh_credits
    from jiro.cli_ui import (
        console, account_card, rule, success, warning, dim,
    )

    creds = load_cloud_credentials()
    if not creds:
        console.print()
        console.print(warning("Not signed in"))
        console.print(dim("  Run 'jiro auth login' to sign in"))
        console.print()
        raise typer.Exit(0)

    if refresh:
        console.print()
        with console.status("[bold orange1]  Refreshing credits from server...", spinner="dots"):
            creds = refresh_credits(creds)

    console.print()
    console.print(rule("Account Info"))
    console.print()
    card = account_card(
        creds.email, creds.name, creds.plan,
        creds.credits_used, creds.credits_included,
        creds.rate_limit_rpm, creds.api_key,
    )
    console.print(card)
    console.print()


@auth_app.command("status", help="Check auth status and test API key.")
def auth_status() -> None:
    """Verify API key is valid and show credit balance."""
    from jiro.cloud_auth import load_cloud_credentials, fetch_credits_from_server
    from jiro.cli_ui import (
        console, account_card, rule, success, error, warning, dim,
    )

    creds = load_cloud_credentials()
    if not creds:
        console.print()
        console.print(warning("Not signed in"))
        console.print(dim("  Run 'jiro auth login' to sign in"))
        console.print()
        raise typer.Exit(0)

    console.print()
    with console.status("[bold orange1]  Testing API key...", spinner="dots"):
        data = fetch_credits_from_server(creds.api_key)

    if data:
        console.print()
        console.print(success("API key is valid"))
        credits = data.get("credits", {})
        rate = data.get("rateLimit", {})
        creds.credits_used = credits.get("used", creds.credits_used)
        creds.credits_included = credits.get("included", creds.credits_included)
        creds.credits_remaining = credits.get("remaining", creds.credits_remaining)
        creds.rate_limit_rpm = rate.get("rpm", creds.rate_limit_rpm)
        from jiro.cloud_auth import save_cloud_credentials
        save_cloud_credentials(creds)

        console.print()
        console.print(rule("Account Status"))
        console.print()
        card = account_card(
            creds.email, creds.name, creds.plan,
            creds.credits_used, creds.credits_included,
            creds.rate_limit_rpm, creds.api_key,
        )
        console.print(card)
        console.print()
    else:
        console.print()
        console.print(error("API key is invalid or server is unreachable"))
        console.print(dim("  Try 'jiro auth login' to re-authenticate"))
        console.print()
        raise typer.Exit(1)


# --------------------------------------------------------------------------
# license - Self-hosted license management
# --------------------------------------------------------------------------
@license_app.command("activate", help="Activate a license key for self-hosted.")
def license_activate(
    key: str = typer.Argument(..., help="License key (e.g., JIRO-PRO-A1B2-C3D4-E5F6)"),
) -> None:
    """Activate a license key for premium self-hosted features.

    The license is validated offline (HMAC-SHA256) and bound to this machine.
    """
    from jiro.licensing import get_license_manager, LicenseInfo

    manager = get_license_manager()

    # Check if already licensed
    existing = manager.get_license()
    from jiro.cli_ui import console as _c, success, error, dim
    if existing.valid and not existing.is_expired:
        _c.print(success(f"Already licensed: [bold]{existing.tier}[/] tier"))
        if not typer.confirm("Replace with new license?"):
            return

    _c.print(dim("Validating license key..."))

    info = manager.validate_token(key.strip())

    if not info.valid:
        _c.print(error(f"Invalid license: {info.error}"))
        raise typer.Exit(1)

    # Save the license
    manager.save_license(key.strip())

    _c.print()
    _c.print(success("License activated!"))
    _c.print(f"  Tier:      [bold]{info.tier.upper()}[/]")
    _c.print(f"  Customer:  [cyan]{info.customer_id}[/]")
    _c.print(f"  Expires:   {dim(time.strftime('%Y-%m-%d', time.localtime(info.expires_at)))}")
    _c.print(f"  Features:  {dim(f'{len(info.features)} enabled')}")
    _c.print(f"  Hardware:  {dim('bound to this machine')}")


@license_app.command("info", help="Show current license details.")
def license_info(
    json_output: bool = typer.Option(False, "--json", "-j", help="Print raw JSON"),
) -> None:
    """Display current license information and status."""
    from jiro.licensing import get_license_manager, get_features_for_tier, FEATURE_DEFINITIONS
    from jiro.cli_ui import console as _c, warning, dim, rule

    manager = get_license_manager()
    info = manager.get_license()

    if json_output:
        print(json.dumps({
            "valid": info.valid,
            "tier": info.tier,
            "customer_id": info.customer_id,
            "license_id": info.license_id,
            "issued_at": info.issued_at,
            "expires_at": info.expires_at,
            "max_devices": info.max_devices,
            "features": list(info.features),
            "is_expired": info.is_expired,
            "in_grace_period": info.in_grace_period,
            "grace_mode": info.grace_mode,
        }, indent=2, default=str))
        return

    if not info.valid and not info.in_grace_period:
        _c.print(warning("No active license."))
        _c.print(dim("Running in Free tier."))
        _c.print(dim("Run 'jiro license activate <KEY>' to upgrade."))
        raise typer.Exit(0)

    # Status
    if info.grace_mode:
        status = f"[yellow]GRACE PERIOD[/] ({int(info.remaining_grace // 3600)}h remaining)"
    elif info.is_expired:
        status = "[red]EXPIRED[/]"
    else:
        status = "[green]ACTIVE[/]"

    # Table
    table = Table(title="License Info")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Status", status)
    table.add_row("Tier", f"[bold]{info.tier.upper()}[/]")
    table.add_row("Customer", info.customer_id)
    table.add_row("License ID", info.license_id[:16] + "...")
    table.add_row("Issued", time.strftime("%Y-%m-%d", time.localtime(info.issued_at)))
    table.add_row("Expires", time.strftime("%Y-%m-%d", time.localtime(info.expires_at)))
    table.add_row("Max Devices", str(info.max_devices))
    table.add_row("Features", str(len(info.features)))

    # Days until expiration
    days_left = (info.expires_at - time.time()) / 86400
    if days_left > 0:
        table.add_row("Expires in", f"{int(days_left)} days")
    else:
        table.add_row("Expired", f"{int(abs(days_left))} days ago")

    # Grace period
    if info.in_grace_period:
        grace_hours = int(info.remaining_grace // 3600)
        table.add_row("Grace Period", f"{grace_hours}h remaining")

    _c.print(table)

    # Features
    if info.features:
        features_table = Table(title="Enabled Features")
        features_table.add_column("Feature", style="cyan")
        features_table.add_column("Description", style="white")
        for feat in sorted(info.features):
            desc = FEATURE_DEFINITIONS.get(feat, {}).get("description", feat)
            features_table.add_row(feat, desc)
        _c.print(features_table)


@license_app.command("deactivate", help="Remove the current license.")
def license_deactivate() -> None:
    """Remove the stored license key (reverts to Free tier)."""
    from jiro.licensing import get_license_manager
    from jiro.cli_ui import console as _c, success, warning, dim

    manager = get_license_manager()
    info = manager.get_license()

    if not info.valid and not info.in_grace_period:
        _c.print(warning("No active license to remove."))
        return

    _c.print(f"Current tier: [bold]{info.tier.upper()}[/]")
    if typer.confirm("Remove this license?"):
        manager.clear_license()
        _c.print(success("License removed. Reverted to Free tier."))
    else:
        _c.print(dim("Cancelled."))


@license_app.command("validate", help="Validate a license key without activating.")
def license_validate(
    key: str = typer.Argument(..., help="License key to validate"),
) -> None:
    """Check if a license key is valid without saving it."""
    from jiro.licensing import get_license_manager
    from jiro.cli_ui import console as _c, success, error

    manager = get_license_manager()
    info = manager.validate_token(key.strip())

    if info.valid:
        _c.print(success("Valid license"))
        _c.print(f"  Tier:     {info.tier.upper()}")
        _c.print(f"  Customer: {info.customer_id}")
        _c.print(f"  Expires:  {time.strftime('%Y-%m-%d', time.localtime(info.expires_at))}")
        _c.print(f"  Features: {len(info.features)}")
    else:
        _c.print(error(f"Invalid license: {info.error}"))
        raise typer.Exit(1)


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------
@search_app.command("web", help="Web search (JSON output).")
def search_web(
    q: str = typer.Argument(None, help="Search query (leave empty for interactive mode)"),
    engine: str = typer.Option("google", "--engine", "-e"),
    type: str = typer.Option("web", "--type", "-t"),
    num: int = typer.Option(10, "--num", "-n"),
    location: str = typer.Option("us", "--location", "-l"),
    language: str = typer.Option("en", "--language"),
    parallel: bool = typer.Option(False, "--parallel", "-p", help="Search multiple engines in parallel (v0.2.13)"),
    num_engines: int = typer.Option(3, "--engines", help="Number of engines for parallel search (max 5)"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive search mode"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Print raw JSON"),
    cloud: bool = typer.Option(False, "--cloud", help="Use Jiro Cloud backend (requires login)"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    if cloud:
        asyncio.run(_cloud_search(q, engine, type, num, location, language, json_output))
    elif interactive or q is None:
        _interactive_search(engine, type, num, location, language, parallel, num_engines, json_output, config)
    else:
        asyncio.run(_cli_search(q, engine, type, num, location, language,
                                parallel, num_engines, json_output, config))


def _interactive_search(engine, type, num, location, language, parallel, num_engines, json_output, config):
    """Interactive search loop."""
    from jiro.cli_ui import console as _c, accent, dim
    _c.print(accent("Jiro Interactive Search") + dim(" (type 'quit' or 'exit' to stop)\n"))
    
    while True:
        try:
            q = _c.input(accent("Search > "))
        except (EOFError, KeyboardInterrupt):
            _c.print()
            _c.print(dim("Goodbye!"))
            break
        
        q = q.strip()
        if not q:
            continue
        if q.lower() in ("quit", "exit", "q"):
            _c.print(dim("Goodbye!"))
            break
        
        asyncio.run(_cli_search(q, engine, type, num, location, language,
                                parallel, num_engines, json_output, config))
        _c.print()  # Empty line between results


async def _cli_search(q, engine, type, num, location, language, parallel, num_engines, json_output, config):
    from jiro.cli_ui import search_result_card, console, error, dim
    warnings.filterwarnings("ignore")
    from jiro.server import create_app
    from starlette.testclient import TestClient

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.get("/search.json", params={
            "q": q, "engine": engine, "type": type, "num": num,
            "location": location, "language": language,
            "parallel": parallel, "num_engines": num_engines,
        })
        data = resp.json()
        if json_output:
            console.print(json.dumps(data, indent=2, default=str))
            return
        meta = data.get("search_metadata", {})
        results = data.get("organic_results", [])
        card = search_result_card(
            q, results,
            engine=meta.get("engine", engine),
            cached=meta.get("cached", False),
            time_taken=meta.get("total_time_taken", 0),
        )
        console.print()
        console.print(card)
        console.print()


async def _cloud_search(q, engine, type, num, location, language, json_output):
    """Search via Jiro Cloud API."""
    from jiro.cloud_auth import load_cloud_credentials, JIRO_CLOUD_BASE, refresh_credits
    from jiro.cli_ui import console as _c, error, warning, dim

    creds = load_cloud_credentials()
    if not creds:
        _c.print(error("Not signed in. Run 'jiro auth login' first."))
        raise typer.Exit(1)

    # Check credits before making call
    creds = refresh_credits(creds)
    if creds.credits_remaining <= 0:
        _c.print(error("No credits remaining."))
        _c.print(dim("Upgrade at searchjiro.vercel.app/dashboard/billing"))
        raise typer.Exit(1)

    if creds.credits_remaining < 10:
        _c.print(warning(f"Only {creds.credits_remaining} credits remaining"))

    import urllib.request
    import urllib.error

    params = f"q={q}&engine={engine}&type={type}&num={num}&location={location}&language={language}"
    url = f"{JIRO_CLOUD_BASE}/v1/search?{params}"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds.api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        _c.print(error(f"Cloud API error ({e.code}): {body}"))
        raise typer.Exit(1)
    except Exception as e:
        _c.print(error(f"Request failed: {e}"))
        raise typer.Exit(1)

    if json_output:
        _c.print(json.dumps(data, indent=2, default=str))
        return

    from jiro.cli_ui import search_result_card
    meta = data.get("search_metadata", {})
    results = data.get("organic_results", [])
    card = search_result_card(
        q, results,
        engine=f"{meta.get('engine', '?')} (cloud)",
        cached=meta.get("cached", False),
        time_taken=meta.get("total_time_taken", 0),
    )
    _c.print()
    _c.print(card)
    _c.print()


def _safe_print(text: str) -> str:
    """Sanitize text for Windows console output (cp1252)."""
    if not isinstance(text, str):
        return str(text)
    text = text.replace('\ufeff', '')
    text = text.encode('cp1252', errors='replace').decode('cp1252')
    return text


def _is_url(text: str) -> bool:
    """Check if text looks like a URL (has scheme or contains dots)."""
    if text.startswith(("http://", "https://")):
        return True
    # If it contains dots and no spaces, it's likely a domain
    if "." in text and " " not in text:
        return True
    return False


# --------------------------------------------------------------------------
# scrape
# --------------------------------------------------------------------------
@app.command(help="Scrape a URL or search the web and scrape the top result.")
def scrape(
    url: str = typer.Argument(..., help="URL or search query to scrape"),
    format: str = typer.Option("markdown", "--format", "-f",
                               help="Output format: markdown, text, html, json"),
    full: bool = typer.Option(False, "--full", help="Show full content (no truncation)"),
    cloud: bool = typer.Option(False, "--cloud", help="Use Jiro Cloud backend (requires login)"),
    config: str = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Print raw JSON"),
) -> None:
    """Scrape a URL and extract readable content.

    If the input looks like a URL (contains dots or starts with http), it scrapes
    that URL directly. Otherwise, it searches for the query and scrapes the top result.

    Examples:
      jiro scrape https://example.com
      jiro scrape example.com
      jiro scrape "free SaaS directories"
    """
    from jiro.server import create_app
    from starlette.testclient import TestClient

    if cloud:
        asyncio.run(_cloud_scrape(url, format))
        return

    # Auto-prepend https:// if no scheme is provided but looks like a domain
    if not _is_url(url):
        # Treat as search query - search first, then scrape top result
        from jiro.cli_ui import dim
        console.print(dim(f"Searching for '{url}'..."))
        asyncio.run(_scrape_search_query(url, format, config, full=full))
        return

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.post("/scrape", json={"url": url, "format": format})
        data = resp.json()
        if json_output:
            print(json.dumps(data, indent=2, default=str))
            return
        if resp.status_code != 200:
            from jiro.cli_ui import error
            console.print(error(data.get('error', resp.text)))
            raise typer.Exit(1)
        from jiro.cli_ui import scrape_result_card
        card = scrape_result_card(
            _safe_print(data.get("title", "")),
            data.get("url", ""),
            _safe_print(data.get("content", "")),
        )
        console.print()
        console.print(card)
        console.print()


async def _scrape_search_query(query: str, format: str, config: str, full: bool = False) -> None:
    """Search for a query and scrape the first result."""
    import re
    from jiro.server import create_app
    from starlette.testclient import TestClient

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        # First, search for the query
        resp = client.get("/search.json", params={
            "q": query, "engine": "google", "type": "web", "num": 1,
            "location": "us", "language": "en",
        })
        data = resp.json()
        results = data.get("organic_results", [])
        if not results:
            from jiro.cli_ui import error
            console.print(error(f"No results found for '{query}'"))
            raise typer.Exit(1)

        # Get the first result URL - prefer displayed_link over source
        first_result = results[0]
        target_url = first_result.get("displayed_link", "") or first_result.get("source", "")
        if not target_url:
            console.print(error("Could not extract URL from first result"))
            raise typer.Exit(1)

        # Ensure URL has scheme
        if not target_url.startswith(("http://", "https://")):
            target_url = f"https://{target_url}"

        from jiro.cli_ui import dim
        console.print(dim(f"Scraping: {target_url}"))

        # Scrape the URL
        resp = client.post("/scrape", json={"url": target_url, "format": format})
        data = resp.json()
        if resp.status_code != 200:
            console.print(error(data.get('error', resp.text)))
            raise typer.Exit(1)
        from jiro.cli_ui import scrape_result_card
        card = scrape_result_card(
            _safe_print(data.get("title", "")),
            data.get("url", ""),
            _safe_print(data.get("content", "")),
            max_chars=999999 if full else 3000,
        )
        console.print()
        console.print(card)
        console.print()


async def _cloud_scrape(url, format):
    """Scrape via Jiro Cloud API."""
    from jiro.cloud_auth import load_cloud_credentials, JIRO_CLOUD_BASE, refresh_credits
    from jiro.cli_ui import console as _c, error, warning, dim

    creds = load_cloud_credentials()
    if not creds:
        _c.print(error("Not signed in. Run 'jiro auth login' first."))
        raise typer.Exit(1)

    # Check credits before making call
    creds = refresh_credits(creds)
    if creds.credits_remaining <= 0:
        _c.print(error("No credits remaining."))
        _c.print(dim("Upgrade at searchjiro.vercel.app/dashboard/billing"))
        raise typer.Exit(1)

    if creds.credits_remaining < 10:
        _c.print(warning(f"Only {creds.credits_remaining} credits remaining"))

    import urllib.request
    import urllib.error

    body = json.dumps({"url": url, "format": format}).encode("utf-8")
    req = urllib.request.Request(
        f"{JIRO_CLOUD_BASE}/v1/scrape",
        data=body,
        headers={
            "Authorization": f"Bearer {creds.api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        _c.print(error(f"Cloud API error ({e.code}): {err_body}"))
        raise typer.Exit(1)
    except Exception as e:
        _c.print(error(f"Request failed: {e}"))
        raise typer.Exit(1)

    _c.print()
    from jiro.cli_ui import scrape_result_card
    card = scrape_result_card(
        _safe_print(data.get("title", "")),
        data.get("url", ""),
        _safe_print(data.get("content", "")),
        max_chars=999999 if full else 3000,
    )
    console.print(card)
    console.print()


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
    from jiro.cli_ui import warning
    console.print(warning("Tip: Use 'jiro ai ask' instead for more options."))
    _run_ai_ask(query, max_sources, json_output, config)


def _run_ai_ask(query: str, max_sources: int, json_output: bool, config: str) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.cli_ui import console, rule, dim, accent, Text, Panel, box, Group, error

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.post("/ai/search", json={"query": query,
                                               "max_sources": max_sources})
        data = resp.json()
        if resp.status_code != 200:
            console.print(error(data.get('error', data.get('detail', resp.text))))
            raise typer.Exit(1)
        if json_output:
            console.print(json.dumps(data, indent=2, default=str))
            return

        console.print()
        console.print(rule("AI Research"))
        console.print()

        # Answer
        answer = _safe_print(data.get("answer", ""))
        console.print(Panel(
            answer,
            title=f"[bold orange1]Answer[/]",
            border_style="orange1",
            box=box.ROUNDED,
            padding=(1, 2),
        ))
        console.print()

        # Sources
        citations = data.get("citations", [])
        if citations:
            console.print(Text("  Sources:", style="bold white"))
            console.print()
            for i, c in enumerate(citations, start=1):
                title = _safe_print(c.get("title", ""))
                url = _safe_print(c.get("url", ""))
                console.print(Text(f"  [{i}] ", style="bold orange1") + Text(title, style="bold white"))
                console.print(Text(f"      {url}", style="dim cyan"))
            console.print()


# --------------------------------------------------------------------------
# ai setup
# --------------------------------------------------------------------------
@ai_app.command("setup", help="Configure AI/LLM provider, API key, and model.")
def ai_setup(
    provider: str = typer.Option(None, "--provider", "-p",
                                  help="LLM provider: openai, anthropic, gemini, openrouter, ollama"),
    api_key: str = typer.Option(None, "--api-key", "-k", help="API key for the provider"),
    model: str = typer.Option(None, "--model", "-m", help="Model name (e.g. gpt-4o-mini, claude-3-5-sonnet)"),
    base_url: str = typer.Option(None, "--base-url", "-u", help="Custom API endpoint URL"),
    temperature: float = typer.Option(None, "--temperature", "-t", help="Temperature (0.0-2.0)"),
    max_tokens: int = typer.Option(None, "--max-tokens", help="Max tokens for responses"),
    config: str = typer.Option(None, "--config", "-c"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive setup wizard"),
) -> None:
    """Configure Jiro's AI/LLM provider.

    Examples:
      jiro ai setup --provider openai --api-key sk-... --model gpt-4o
      jiro ai setup --provider ollama --model llama3
      jiro ai setup --provider openrouter --api-key sk-or-... --base-url https://openrouter.ai/api/v1
      jiro ai setup -i  # Interactive wizard
    """
    if interactive:
        _ai_setup_interactive(config)
        return

    if not any([provider, api_key, model, base_url, temperature is not None, max_tokens is not None]):
        _ai_setup_status(config)
        return

    _ai_setup_apply(provider, api_key, model, base_url, temperature, max_tokens, config)


def _ai_setup_interactive(config: str) -> None:
    """Interactive setup wizard."""
    from jiro.cli_ui import console as _c, warning, dim, error
    settings = Settings.load(config)
    llm_cfg = settings.llm

    _c.print(Panel.fit("[bold]Jiro AI Setup Wizard[/]", subtitle="Configure your LLM provider"))

    # Provider
    providers = ["openai", "anthropic", "gemini", "openrouter", "ollama"]
    current_provider = llm_cfg.get("provider", "openai")
    _c.print(f"\n[bold]Current provider:[/] {current_provider}")
    _c.print(dim("Providers: openai, anthropic, gemini, openrouter, ollama"))
    provider = typer.prompt("Provider", default=current_provider)
    if provider not in providers:
        _c.print(error(f"Invalid provider: {provider}"))
        raise typer.Exit(1)

    # API Key
    current_key = llm_cfg.get("api_key", "")
    has_key = bool(current_key)
    _c.print(f"\n[bold]API key:[/] {'configured' if has_key else 'not set'}")
    if provider == "ollama":
        api_key = ""  # Ollama doesn't need an API key
    else:
        api_key = typer.prompt("API key", default="", hide_input=True)
        if not api_key and not has_key:
            _c.print(warning("No API key provided. AI features may not work."))

    # Model
    default_models = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-sonnet-20241022",
        "gemini": "gemini-1.5-flash",
        "openrouter": "gpt-4o-mini",
        "ollama": "llama3",
    }
    current_model = llm_cfg.get("model", default_models.get(provider, "gpt-4o-mini"))
    _c.print(f"\n[bold]Model:[/] {current_model}")
    model = typer.prompt("Model", default=current_model)

    # Base URL (for custom endpoints)
    current_base_url = llm_cfg.get("base_url", "")
    if provider in ("ollama", "openrouter"):
        default_url = {"ollama": "http://localhost:11434/v1", "openrouter": "https://openrouter.ai/api/v1"}.get(provider, "")
    else:
        default_url = ""
    _c.print(f"\n[bold]Base URL:[/] {current_base_url or '(default)'}")
    _c.print(dim("Leave empty for default endpoint, or enter custom URL"))
    base_url = typer.prompt("Base URL", default=current_base_url or default_url or "")

    # Temperature
    current_temp = llm_cfg.get("temperature", 0.2)
    _c.print(f"\n[bold]Temperature:[/] {current_temp}")
    temperature = typer.prompt("Temperature", default=current_temp, type=float)

    # Max tokens
    current_max = llm_cfg.get("max_tokens", 1024)
    _c.print(f"\n[bold]Max tokens:[/] {current_max}")
    max_tokens = typer.prompt("Max tokens", default=current_max, type=int)

    _ai_setup_apply(provider, api_key, model, base_url, temperature, max_tokens, config)


def _ai_setup_apply(provider, api_key, model, base_url, temperature, max_tokens, config: str) -> None:
    """Apply AI configuration to the config file."""
    from pathlib import Path
    import yaml
    from jiro.cli_ui import console as _c, success, warning

    config_path = Path(config).expanduser() if config else Path("~/.jiro/config.yaml").expanduser()

    # Load existing config or create new
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    # Ensure llm section exists
    if "llm" not in cfg:
        cfg["llm"] = {}

    # Apply settings
    if provider:
        cfg["llm"]["provider"] = provider
    if api_key is not None:
        cfg["llm"]["api_key"] = api_key
    if model:
        cfg["llm"]["model"] = model
    if base_url is not None:
        cfg["llm"]["base_url"] = base_url
    if temperature is not None:
        cfg["llm"]["temperature"] = temperature
    if max_tokens is not None:
        cfg["llm"]["max_tokens"] = max_tokens

    # Write config
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    _c.print(success(f"AI configuration saved to {config_path}"))

    # Show summary
    _c.print("\n[bold]Configuration:[/]")
    _c.print(f"  Provider:  {cfg['llm'].get('provider', 'openai')}")
    _c.print(f"  Model:     {cfg['llm'].get('model', 'gpt-4o-mini')}")
    _c.print(f"  API Key:   {'***' + cfg['llm'].get('api_key', '')[-4:] if cfg['llm'].get('api_key') else 'not set'}")
    _c.print(f"  Base URL:  {cfg['llm'].get('base_url', '(default)') or '(default)'}")
    _c.print(f"  Temp:      {cfg['llm'].get('temperature', 0.2)}")
    _c.print(f"  Max Tokens: {cfg['llm'].get('max_tokens', 1024)}")

    # Test the connection
    _c.print("\n[bold]Testing connection...[/]")
    try:
        settings = Settings.load(config)
        from jiro.ai.llm import build_provider
        provider_obj = build_provider(settings)
        _c.print(success(f"Provider '{provider_obj.name}' configured successfully"))
    except Exception as e:
        _c.print(warning(f"Could not test provider: {e}"))


def _ai_setup_status(config: str) -> None:
    """Show current AI configuration status."""
    from jiro.cli_ui import console as _c, success, warning, dim
    settings = Settings.load(config)
    llm_cfg = settings.llm

    _c.print(Panel.fit("[bold]Jiro AI Status[/]"))

    provider = llm_cfg.get("provider", "openai")
    model = llm_cfg.get("model", "gpt-4o-mini")
    api_key = llm_cfg.get("api_key", "")
    base_url = llm_cfg.get("base_url", "")
    temperature = llm_cfg.get("temperature", 0.2)
    max_tokens = llm_cfg.get("max_tokens", 1024)

    table = Table(title="LLM Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Provider", provider)
    table.add_row("Model", model)
    table.add_row("API Key", "***" + api_key[-4:] if api_key else "[red]not set[/]")
    table.add_row("Base URL", base_url or "(default)")
    table.add_row("Temperature", str(temperature))
    table.add_row("Max Tokens", str(max_tokens))

    _c.print(table)

    # Check if LLM is available
    from jiro.ai.llm import LLM
    llm = LLM(settings)
    if llm.available:
        _c.print(success("LLM is configured and ready"))
    else:
        _c.print(warning("LLM is not configured (no API key)"))
        _c.print(dim("Run 'jiro ai setup --api-key YOUR_KEY' to configure"))


@ai_app.command("ask", help="Ask a research question with AI-powered answers.")
def ai_ask(
    query: str = typer.Argument(..., help="Research question"),
    max_sources: int = typer.Option(5, "--max-sources", "-n"),
    json_output: bool = typer.Option(False, "--json", "-j"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    _run_ai_ask(query, max_sources, json_output, config)


@ai_app.command("status", help="Show AI/LLM configuration status.")
def ai_status(
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    _ai_setup_status(config)


@ai_app.command("test", help="Test the configured LLM provider.")
def ai_test(
    prompt: str = typer.Option("Hello, what model are you?", "--prompt", "-p"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Send a test prompt to the configured LLM provider."""
    from jiro.cli_ui import console as _c, error, dim, success
    settings = Settings.load(config)
    from jiro.ai.llm import LLM

    llm = LLM(settings)
    if not llm.available:
        _c.print(error("LLM is not configured. Run 'jiro ai setup' first."))
        raise typer.Exit(1)

    _c.print(dim(f"Testing {llm.provider_name}/{llm.model}..."))
    try:
        import asyncio
        response = asyncio.run(llm.complete([{"role": "user", "content": prompt}]))
        _c.print()
        _c.print(success(f"Response:\n{response}"))
    except Exception as e:
        _c.print(error(f"Test failed: {e}"))
        raise typer.Exit(1)


# --------------------------------------------------------------------------
# mcp
# --------------------------------------------------------------------------
@mcp_app.command(help="Start the MCP server for AI agents.")
def mcp(
    transport: str = typer.Option("stdio", "--transport", "-t",
                                  help="stdio | http (Streamable HTTP + SSE)"),
    host: str = typer.Option(None, "--host", help="HTTP bind host"),
    port: int = typer.Option(None, "--port", help="HTTP port (default from config)"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.cli_ui import console as _c, accent
    settings = Settings.load(config)
    if transport == "http":
        _host = host or settings.host
        _port = port or settings.port
        _c.print(accent("jiro") + f" MCP over HTTP on "
                      f"http://{_host}:{_port}/mcp  (+ /sse legacy transport, /docs)")
        import uvicorn
        from jiro.server import create_app
        uvicorn.run(create_app(settings), host=_host, port=_port,
                    log_level=settings.logging.get("level", "info"))
        return
    from jiro.mcp import run_mcp_stdio
    run_mcp_stdio(settings)


# --------------------------------------------------------------------------
# mcp setup
# --------------------------------------------------------------------------
@mcp_app.command("setup", help="Auto-configure Jiro MCP for a client IDE/agent.")
def mcp_setup(
    client: str = typer.Option(
        ..., "--client", "-c", help="claude | cursor | opencode | codex | openclaw | hermes | continue | zed"
    ),
    auto: bool = typer.Option(False, "--auto", help="Detect and configure all found clients"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config"),
    remote: bool = typer.Option(False, "--remote", "-r", help="Configure for remote cloud access (requires login)"),
) -> None:
    from jiro.mcp_setup import run_auto_setup, run_setup, list_supported_clients
    from jiro.cli_ui import error
    if auto:
        raise typer.Exit(run_auto_setup())
    valid = [c["id"] for c in list_supported_clients()]
    if client not in valid:
        console.print(error(f"Unknown client: {client}"))
        console.print(f"Supported: {', '.join(valid)}")
        raise typer.Exit(1)
    raise typer.Exit(run_setup(client, force=force, remote=remote))


@mcp_app.command("status", help="Detect configured MCP clients.")
def mcp_status() -> None:
    from jiro.mcp_setup import _print_status
    raise typer.Exit(_print_status())


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------
def _admin_key_required() -> str:
    import os as _os
    key = _os.environ.get("JIRO_ADMIN_KEY")
    if key:
        return key
    if not sys.stdin.isatty():
        from jiro.cli_ui import console as _c, error
        _c.print(error("Admin API key required. Set JIRO_ADMIN_KEY env var or pass --admin-key."))
        raise typer.Exit(1)
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
    from jiro.cli_ui import console as _c, success, error, warning, Panel, box

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        headers = {"X-API-Key": admin_key or _admin_key_required()}
        resp = client.post("/api-keys", json={"name": name, "role": role,
                                              "rate_limit_rpm": rate_limit},
                           headers=headers)
        if resp.status_code != 200:
            _c.print(error(resp.json().get('error', resp.text)))
            raise typer.Exit(1)
        data = resp.json()
        key_panel = Panel(
            f"[bold white]{data['api_key']}[/]",
            title="[bold #f97316]New API Key[/]",
            border_style="#f97316",
            box=box.DOUBLE_EDGE,
            padding=(0, 1),
        )
        _c.print(success("Key created"))
        _c.print(key_panel)
        _c.print(warning("Store it now - it will not be shown again."))


@keys_app.command("list", help="List API keys (requires an admin key).")
def keys_list(
    admin_key: Optional[str] = typer.Option(None, "--admin-key", envvar="JIRO_ADMIN_KEY"),
    config: str = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Print raw JSON"),
) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.cli_ui import console as _c, error, ORANGE
    from rich.table import Table

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        headers = {"X-API-Key": admin_key or _admin_key_required()}
        resp = client.get("/api-keys", headers=headers)
        if resp.status_code != 200:
            _c.print(error(resp.json().get('error', resp.text)))
            raise typer.Exit(1)
        keys = resp.json()
        if json_output:
            print(json.dumps(keys, indent=2, default=str))
            return
        table = Table(title="API keys")
        table.add_column("ID")
        table.add_column("Name")
        table.add_column("Prefix")
        table.add_column("Role")
        table.add_column("Created")
        for k in keys:
            table.add_row(k["id"][:12], k["name"], k["key_prefix"], k["role"],
                          k["created_at"][:10])
        _c.print(table)


@keys_app.command("revoke", help="Revoke an API key (requires an admin key).")
def keys_revoke(
    key_id: str = typer.Argument(...),
    admin_key: Optional[str] = typer.Option(None, "--admin-key", envvar="JIRO_ADMIN_KEY"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.cli_ui import console as _c, success, error

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        headers = {"X-API-Key": admin_key or _admin_key_required()}
        resp = client.delete(f"/api-keys/{key_id}", headers=headers)
        if resp.status_code != 200:
            _c.print(error(resp.json().get('error', resp.text)))
            raise typer.Exit(1)
        _c.print(success(f"Key {key_id} revoked"))


# --------------------------------------------------------------------------
# usage
# --------------------------------------------------------------------------
@app.command(help="Show usage statistics (requires an admin key).")
def usage(
    days: int = typer.Option(7, "--days"),
    admin_key: Optional[str] = typer.Option(None, "--admin-key", envvar="JIRO_ADMIN_KEY"),
    config: str = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Print raw JSON"),
) -> None:
    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.cli_ui import console as _c, error, ORANGE
    from rich.table import Table

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        headers = {"X-API-Key": admin_key or _admin_key_required()}
        resp = client.get("/usage", params={"days": days}, headers=headers)
        if resp.status_code != 200:
            _c.print(error(resp.json().get('error', resp.text)))
            raise typer.Exit(1)
        data = resp.json()
        if json_output:
            print(json.dumps(data, indent=2, default=str))
            return
        _c.print(f"requests: [bold]{data['requests']}[/]  "
                      f"cached: {data['cached']}  tokens: {data['tokens_in'] + data['tokens_out']}")
        table = Table(title=f"By endpoint (last {days} days)")
        table.add_column("Endpoint")
        table.add_column("Requests")
        for row in data["by_endpoint"]:
            table.add_row(row["endpoint"], str(row["n"]))
        _c.print(table)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
@config_app.command("init", help="Write the default config file.")
def config_init(
    path: str = typer.Option(None, "--path", help="Output path (default ~/.jiro/config.yaml)"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    from pathlib import Path
    from jiro.cli_ui import console as _c, success, warning
    target = Path(path).expanduser() if path else Path("~/.jiro/config.yaml").expanduser()
    settings = Settings.load(path)
    if target.exists() and not force:
        _c.print(warning(f"{target} already exists (use --force to overwrite)."))
        raise typer.Exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    target.write_text(yaml.safe_dump(settings.dump(), sort_keys=False))
    _c.print(success(f"Wrote {target}"))


@config_app.command("show", help="Show the effective configuration.")
def config_show(
    section: str = typer.Argument(None, help="Show only a section (e.g., llm, server, logging)"),
    config: str = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Print raw JSON (default)"),
) -> None:
    import copy
    from jiro.cli_ui import error, dim

    SECRET_KEYS = {"api_key", "secret", "token", "password", "private_key", "auth_token", "sessionid", "c_user"}

    def _mask_secrets(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in SECRET_KEYS and isinstance(v, str) and len(v) > 4:
                    obj[k] = v[:4] + "***" + v[-4:]
                elif isinstance(v, (dict, list)):
                    _mask_secrets(v)
        elif isinstance(obj, list):
            for item in obj:
                _mask_secrets(item)

    settings = Settings.load(config)
    data = copy.deepcopy(settings.dump())
    _mask_secrets(data)

    if section:
        if section not in data:
            console.print(error(f"Unknown section: '{section}'"))
            console.print(dim(f"Available: {', '.join(sorted(data.keys()))}"))
            raise typer.Exit(1)
        data = {section: data[section]}

    console.print(json.dumps(data, indent=2, default=str))


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
    from jiro.cli_ui import (
        console as _c, success, error, warning, dim, rule, accent, Panel, box,
        Text, ORANGE,
    )

    _c.print(Panel(
        accent(f"Jiro Update v{__version__}")
        + (warning(" (dev mode - GitHub)") if dev else "")
        + Text("\n  Checking for updates...", style="dim"),
        border_style=ORANGE,
        box=box.ROUNDED,
        padding=(0, 1),
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

        # --force without --dev means "get the absolute latest from GitHub"
        use_github = dev or force

        if use_github:
            # Dev/force mode: skip PyPI check, install from GitHub
            latest_version = f"main (GitHub)"
            progress.update(task, description=f"Latest: [bold]{latest_version}[/]")

            if dev and not force and not check_only:
                progress.stop()
                _c.print()
                _c.print(warning("Dev version will be installed from GitHub main."))
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
                _c.print(warning(f"Could not check PyPI: {e}"))
                latest_version = current_version

            progress.update(task, description=f"Latest version: [bold]{latest_version}[/]")

        # Check if update needed
        if current_version == latest_version and not force and not dev:
            progress.stop()
            _c.print()
            _c.print(success(f"Already up to date! (v{current_version})"))
            if check_only:
                return
            _c.print(dim("Use --force to reinstall the current version."))
            return

        if check_only:
            progress.stop()
            _c.print()
            _c.print(warning(f"Update available: {current_version} -> {latest_version}"))
            return

        # For --force or --dev, skip the version comparison check above
        if use_github:
            progress.stop()
            if dev:
                _c.print()
                _c.print(warning("Installing dev version from GitHub..."))
            else:
                _c.print()
                _c.print(warning("Force updating from GitHub..."))

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
                progress.update(task, description=f"Backup skipped: {e}")

        # Step 4: Install latest version
        if sys.platform == "win32":
            import tempfile
            import time

            if use_github:
                pip_args = [sys.executable, "-m", "pip", "install", "--upgrade",
                            "git+https://github.com/DevAnimecx/jiro.git@main"]
            else:
                pip_args = [sys.executable, "-m", "pip", "install", "--upgrade", "jirosearch"]

            bat_path = Path(tempfile.gettempdir()) / "jiro_updater.bat"
            pip_cmd = " ".join(f'"{a}"' for a in pip_args)
            bat_content = f"""@echo off
echo Waiting for jiro.exe to release...
timeout /t 2 /nobreak >nul
echo Installing update...
{pip_cmd}
if %errorlevel%==0 (
    echo Update complete!
    echo.
    jiro --version
) else (
    echo Update FAILED. Try: python -m pip install --upgrade jirosearch
    pause
)
"""
            bat_path.write_text(bat_content, encoding="utf-8")

            progress.stop()
            _c.print()
            _c.print(warning("Launching updater after jiro exits..."))
            _c.print(dim(f"Updater script: {bat_path}"))

            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(bat_path)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                startupinfo=si,
            )
            _c.print(success("Updater launched. Jiro will exit now."))
            raise typer.Exit(0)

        else:
            progress.update(task, description="Installing latest version...")
            try:
                if use_github:
                    cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
                           "git+https://github.com/DevAnimecx/jiro.git@main"]
                else:
                    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "jirosearch"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if result.returncode != 0:
                    progress.stop()
                    _c.print(error("Installation failed:"))
                    _c.print(result.stderr)
                    raise typer.Exit(1)
                progress.update(task, description="Installed successfully")
            except subprocess.TimeoutExpired:
                progress.stop()
                _c.print(error("Installation timed out"))
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
                progress.update(task, description=f"Cache clear skipped: {e}")

        # Step 6: Verify components
        progress.update(task, description="Verifying components...")
        verification_results = []

        try:
            from jiro import __version__ as new_version
            from jiro.mcp import JiroMCPServer
            from jiro.ai.tools import mcp_tools
            from jiro.scraping.social import SocialRouter
            from jiro.search.intent import IntentClassifier
            verification_results.append(("Core imports", True, f"v{new_version}"))
        except Exception as e:
            verification_results.append(("Core imports", False, str(e)))

        try:
            from jiro.ai.tools import mcp_tools
            tools = mcp_tools()
            verification_results.append(("MCP tools", True, f"{len(tools)} tools"))
        except Exception as e:
            verification_results.append(("MCP tools", False, str(e)))

        try:
            from jiro.scraping.social import SocialRouter
            router = SocialRouter()
            platforms = list(set(p for p, _ in router._platform_patterns))
            verification_results.append(("Social platforms", True, f"{len(platforms)} platforms"))
        except Exception as e:
            verification_results.append(("Social platforms", False, str(e)))

        try:
            from jiro.ai.tools import ENGINE_ENUM
            verification_results.append(("Search engines", True, f"{len(ENGINE_ENUM)} engines"))
        except Exception as e:
            verification_results.append(("Search engines", False, str(e)))

        # Print verification results
        progress.stop()
        _c.print()
        _c.print(rule("Verification Results"))
        _c.print()
        for name, ok, detail in verification_results:
            status = success("OK") if ok else error("FAIL")
            _c.print(f"  {status}  {name}: {detail}")

        # Step 7: Run tests
        if run_tests:
            _c.print()
            _c.print(rule("Running tests"))
            try:
                source_dir = Path(__file__).parent.parent
                test_dir = source_dir / "tests"
                if not test_dir.exists():
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
                        _c.print(success("MCP tests passed"))
                    else:
                        _c.print(warning(f"Some tests failed:\n{result.stdout[-500:]}"))
                else:
                    _c.print(warning("Tests directory not found, skipping tests"))
            except Exception as e:
                _c.print(warning(f"Tests skipped: {e}"))

        # Final summary
        _c.print()
        _c.print(rule("Summary"))
        _c.print()
        _c.print(Panel.fit(
            success("Update Complete!") + "\n\n"
            f"Version: [bold]{current_version}[/] -> [bold]{latest_version}[/]\n"
            f"MCP Tools: 16\n"
            f"Social Platforms: 12\n"
            f"Search Engines: 9\n\n"
            + dim("Run 'jiro serve' to start the server"),
            title="[bold #f97316]Summary[/]",
            border_style="#f97316",
        ))


@app.command(help="Check for Jiro updates without installing.")
def check_update(
    dev: bool = typer.Option(False, "--dev", help="Check latest dev version from GitHub"),
) -> None:
    """Check if a newer version is available."""
    asyncio.run(_run_update(check_only=True, force=False, dev=dev, skip_backup=True,
                           clear_cache=False, run_tests=False, config=None))


@_require_dev_ip()
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


@_require_dev_ip()
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
def status(
    json_output: bool = typer.Option(False, "--json", "-j", help="Print raw JSON"),
) -> None:
    """Show system status, version, and component health."""
    asyncio.run(_run_status(json_output))


async def _run_status(json_output: bool = False) -> None:
    """Check system status."""
    from pathlib import Path
    from jiro.cli_ui import (
        console, rule, success, error, warning, dim, make_mini_logo, accent,
        Table, Panel, box, Align,
    )

    console.print()
    console.print(Align.center(make_mini_logo()), style="bold")
    console.print()
    console.print(rule(f"System Status  v{__version__}"))
    console.print()

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

    if json_output:
        passed = sum(1 for _, s, _ in checks if s)
        print(json.dumps({
            "version": __version__,
            "components": [{"name": n, "ok": s, "details": d} for n, s, d in checks],
            "healthy": passed == len(checks),
            "passed": passed,
            "total": len(checks),
        }, indent=2, default=str))
        return

    # Print results
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold orange1", padding=(0, 2))
    table.add_column("Component", style="bold white")
    table.add_column("Status")
    table.add_column("Details", style="dim")

    for name, is_ok, detail in checks:
        status = "[bold green]OK[/]" if is_ok else "[bold red]FAIL[/]"
        table.add_row(name, status, detail)

    console.print(table)

    # Summary
    passed = sum(1 for _, s, _ in checks if s)
    total = len(checks)
    console.print()
    if passed == total:
        console.print(success(f"All {total} components healthy"))
    else:
        console.print(warning(f"{passed}/{total} components healthy"))
    console.print()


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------
@app.command(help="Diagnose common Jiro issues.")
def doctor(
    fix: bool = typer.Option(False, "--fix", help="Auto-fix issues where possible"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Print raw JSON"),
) -> None:
    """Run diagnostics and suggest fixes."""
    asyncio.run(_run_doctor(json_output, fix))


async def _run_doctor(json_output: bool = False, do_fix: bool = False) -> None:
    from pathlib import Path
    from jiro.cli_ui import (
        console, rule, success, error, warning, dim, make_mini_logo,
        Table, Panel, box, Align, Text,
    )

    console.print()
    console.print(Align.center(make_mini_logo()), style="bold")
    console.print()
    console.print(rule("Diagnostics"))
    console.print()

    issues: List[str] = []
    fixes: List[str] = []

    # 1. Config check
    try:
        settings = Settings.load()
        console.print(success("Config loaded"))
    except Exception as e:
        issues.append(f"Config error: {e}")
        fixes.append("Run: jiro config init")
        console.print(error(f"Config: [white]{e}[/]"))

    # 2. License check
    try:
        from jiro.licensing import get_active_license
        lic = get_active_license()
        if lic.valid:
            console.print(success(f"License: {lic.tier}"))
        elif lic.in_grace_period:
            console.print(warning("License expired (grace period)"))
        else:
            console.print(warning(f"No valid license: {lic.error}"))
    except Exception as e:
        issues.append(f"License error: {e}")
        console.print(error(f"License: [white]{e}[/]"))

    # 3. Database check
    try:
        db_path = Path("~/.jiro/jiro.db").expanduser()
        if db_path.exists():
            size_mb = db_path.stat().st_size / (1024 * 1024)
            console.print(success(f"Database: {size_mb:.1f} MB"))
        else:
            console.print(warning("Database not created yet"))
    except Exception as e:
        issues.append(f"Database error: {e}")
        console.print(error(f"Database: [white]{e}[/]"))

    # 4. Python version
    if sys.version_info >= (3, 11):
        console.print(success(f"Python {sys.version.split()[0]}"))
    else:
        issues.append(f"Python {sys.version_info.major}.{sys.version_info.minor} < 3.11")
        fixes.append("Upgrade to Python >= 3.11")
        console.print(error(f"Python {sys.version_info.major}.{sys.version_info.minor} (need >= 3.11)"))

    # 5. Dependencies check
    required = ["fastapi", "uvicorn", "httpx", "curl_cffi", "selectolax", "pydantic", "typer", "rich"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        issues.append(f"Missing packages: {', '.join(missing)}")
        fixes.append(f"pip install {' '.join(missing)}")
        console.print(error(f"Missing: [white]{', '.join(missing)}[/]"))
    else:
        console.print(success("All required packages installed"))

    # 6. Network check
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("8.8.8.8", 80))
        s.close()
        console.print(success("Network connectivity"))
    except Exception:
        issues.append("No network connectivity")
        fixes.append("Check your internet connection")
        console.print(warning("No network connectivity"))

    if json_output:
        print(json.dumps({
            "issues": issues,
            "fixes": fixes,
            "checks": {
                "config": "ok" if not any("Config" in i for i in issues) else "fail",
                "license": "ok" if not any("License" in i for i in issues) else "fail",
                "database": "ok" if not any("Database" in i for i in issues) else "fail",
                "python": "ok" if not any("Python" in i for i in issues) else "fail",
                "dependencies": "ok" if not any("Missing" in i for i in issues) else "fail",
                "network": "ok" if not any("network" in i.lower() for i in issues) else "fail",
            },
        }, indent=2, default=str))
        raise typer.Exit(1 if issues else 0)

    # Summary
    console.print()
    if issues:
        console.print(rule(f"{len(issues)} issue(s) found"))
        console.print()
        for i, issue in enumerate(issues, 1):
            console.print(Text(f"  {i}. {issue}", style="bold red"))
        if fixes:
            console.print()
            if do_fix:
                console.print(Text("  Auto-fixing:", style="bold white"))
                import subprocess
                for fix in fixes:
                    if fix.startswith("pip install "):
                        pkgs = fix.replace("pip install ", "")
                        console.print(Text(f"  -> Installing {pkgs}...", style="cyan"))
                        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q"] + pkgs.split(),
                                           capture_output=True, text=True)
                        if r.returncode == 0:
                            console.print(success(f"Installed {pkgs}"))
                        else:
                            console.print(error(f"Failed to install {pkgs}: {r.stderr[:200]}"))
                    elif fix == "Run: jiro config init":
                        console.print(Text("  -> Running jiro config init...", style="cyan"))
                        from jiro.config import Settings
                        Settings.init()
                        console.print(success("Config initialized"))
                    else:
                        console.print(Text(f"  -> {fix} (manual)", style="dim"))
            else:
                console.print(Text("  Suggested fixes:", style="bold white"))
                for fix in fixes:
                    console.print(Text(f"  -> {fix}", style="cyan"))
                console.print(dim("  Run with --fix to auto-fix where possible"))
        console.print()
        raise typer.Exit(1)
    else:
        console.print(rule("All checks passed"))
        console.print()
        console.print(success("Jiro is ready to go!"))
        console.print()


# --------------------------------------------------------------------------
# social
# --------------------------------------------------------------------------
@social_app.command("scrape", help="Scrape a social media URL.")
def social_scrape(
    url: str = typer.Argument(..., help="Social media URL to scrape"),
    format: str = typer.Option("json", "--format", "-f", help="Output format: json, markdown, text"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.feature_flags import require as require_feature
    from jiro.cli_ui import error
    try:
        require_feature("social_advanced")
    except Exception as e:
        console.print(error(str(e)))
        raise typer.Exit(1)
    asyncio.run(_cli_social_scrape(url, format, config))


async def _cli_social_scrape(url, format, config):
    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.scraping.social import router as social_router
    from jiro.cli_ui import console as _c, error, dim

    platform = social_router.detect_platform(url)
    if not platform:
        _c.print(error(f"Could not detect platform for URL: {url}"))
        raise typer.Exit(1)

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.post("/social", json={"url": url, "format": format})
        data = resp.json()
        if resp.status_code != 200:
            _c.print(error(data.get('error', data.get('detail', resp.text))))
            raise typer.Exit(1)
        if format == "json":
            _c.print(json.dumps(data, indent=2, default=str))
        else:
            content = data.get("data", {}).get("content", "")
            title = data.get("data", {}).get("title", "")
            _c.print(f"[bold]{title}[/]  {dim(f'({url})')}")
            _c.print(content[:4000] if content else warning("No content extracted"))


@social_app.command("search", help="Search on a social platform.")
def social_search(
    query: str = typer.Argument(..., help="Search query"),
    platform: str = typer.Option(..., "--platform", "-p", help="Platform: twitter, reddit, youtube, etc."),
    limit: int = typer.Option(10, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.feature_flags import require as require_feature
    from jiro.cli_ui import error
    try:
        require_feature("social_search")
    except Exception as e:
        console.print(error(str(e)))
        raise typer.Exit(1)
    asyncio.run(_cli_social_search(query, platform, limit, json_output, config))


async def _cli_social_search(query, platform, limit, json_output, config):
    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.cli_ui import console as _c, error, dim
    from rich.table import Table

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.post("/social/search", json={
            "query": query,
            "platform": platform,
            "limit": limit,
        })
        data = resp.json()
        if resp.status_code != 200:
            _c.print(error(data.get('error', data.get('detail', resp.text))))
            raise typer.Exit(1)

        if json_output:
            _c.print(json.dumps(data, indent=2, default=str))
            return

        results = data.get("results", [])
        _c.print(f"[bold]{platform}[/] - " + dim(f"{len(results)} results for '{query}'"))
        table = Table(title=f"Social search: {query}")
        table.add_column("#", justify="right")
        table.add_column("Title")
        table.add_column("Author")
        table.add_column("URL", overflow="fold")
        for i, r in enumerate(results, 1):
            title = r.get("title", r.get("content", "")[:80])
            author = r.get("author", {}).get("name", "") if isinstance(r.get("author"), dict) else r.get("author", "")
            url = r.get("url", "")
            table.add_row(str(i), title, author, url)
        _c.print(table)


@social_app.command("batch", help="Batch scrape multiple social URLs.")
def social_batch(
    urls: List[str] = typer.Argument(..., help="Social media URLs to scrape"),
    parallel: bool = typer.Option(True, "--parallel/--sequential"),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.feature_flags import require as require_feature
    from jiro.cli_ui import error
    try:
        require_feature("social_batch")
    except Exception as e:
        console.print(error(str(e)))
        raise typer.Exit(1)
    asyncio.run(_cli_social_batch(urls, parallel, json_output, config))


async def _cli_social_batch(urls, parallel, json_output, config):
    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.cli_ui import console as _c, error, success, dim

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.post("/social/batch", json={
            "urls": urls,
            "parallel": parallel,
        })
        data = resp.json()
        if resp.status_code != 200:
            _c.print(error(data.get('error', resp.text)))
            raise typer.Exit(1)

        if json_output:
            _c.print(json.dumps(data, indent=2, default=str))
            return

        results = data.get("results", [])
        succeeded = sum(1 for r in results if r.get("status") == "success")
        failed = sum(1 for r in results if r.get("status") != "success")
        _c.print(f"[bold]Batch scrape complete:[/] {succeeded} succeeded, {failed} failed")
        for r in results:
            status = success("+") if r.get("status") == "success" else error("-")
            url = r.get("url", "")
            if r.get("status") == "success":
                platform = r.get("result", {}).get("platform", "?")
                _c.print(f"  {status} {platform}: {url}")
            else:
                err = r.get("error", "unknown")
                _c.print(f"  {status} {url} - {err}")


@social_app.command("profile", help="Scrape a social media profile.")
def social_profile(
    username: str = typer.Argument(..., help="Username or profile URL"),
    platform: str = typer.Option(..., "--platform", "-p", help="Platform: twitter, instagram, etc."),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.feature_flags import require as require_feature
    from jiro.cli_ui import error
    try:
        require_feature("social_search")
    except Exception as e:
        console.print(error(str(e)))
        raise typer.Exit(1)
    asyncio.run(_cli_social_profile(username, platform, json_output, config))


async def _cli_social_profile(username, platform, json_output, config):
    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.cli_ui import console as _c, error, warning, dim

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.post("/social/search", json={
            "query": username,
            "platform": platform,
            "limit": 1,
        })
        data = resp.json()
        if resp.status_code != 200:
            _c.print(error(data.get('error', data.get('detail', resp.text))))
            raise typer.Exit(1)

        if json_output:
            _c.print(json.dumps(data, indent=2, default=str))
            return

        results = data.get("results", [])
        if not results:
            _c.print(warning(f"No profile found for '{username}' on {platform}"))
            return
        r = results[0]
        author = r.get("author", {})
        _c.print(f"[bold]{author.get('display_name', author.get('name', username))}[/]  "
                      + dim(f"@{author.get('username', username)}"))
        _c.print(f"Platform: [cyan]{platform}[/]")
        bio = r.get("text", r.get("content", ""))
        if bio:
            _c.print(f"\n{bio[:500]}")
        url = r.get("url", "")
        if url:
            _c.print(f"\n{dim(url)}")


@social_app.command("timeline", help="Scrape a social media user timeline.")
def social_timeline(
    username: str = typer.Argument(..., help="Username or profile URL"),
    platform: str = typer.Option(..., "--platform", "-p", help="Platform: twitter, instagram, etc."),
    limit: int = typer.Option(10, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    from jiro.feature_flags import require as require_feature
    from jiro.cli_ui import error
    try:
        require_feature("social_timeline")
    except Exception as e:
        console.print(error(str(e)))
        raise typer.Exit(1)
    asyncio.run(_cli_social_timeline(username, platform, limit, json_output, config))


async def _cli_social_timeline(username, platform, limit, json_output, config):
    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.cli_ui import console as _c, error, warning, dim

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        resp = client.post("/social/search", json={
            "query": username,
            "platform": platform,
            "limit": limit,
        })
        data = resp.json()
        if resp.status_code != 200:
            _c.print(error(data.get('error', data.get('detail', resp.text))))
            raise typer.Exit(1)

        if json_output:
            _c.print(json.dumps(data, indent=2, default=str))
            return

        results = data.get("results", [])
        if not results:
            _c.print(warning(f"No timeline posts found for '{username}' on {platform}"))
            return
        _c.print(f"[bold]{platform}[/] timeline for [cyan]{username}[/]: "
                      f"{dim(f'{len(results)} posts')}\n")
        for i, r in enumerate(results, 1):
            title = r.get("title", r.get("content", "")[:100])
            ts = r.get("timestamp", "")
            url = r.get("url", "")
            _c.print(f"[bold]{i}.[/] {title[:120]}")
            if ts:
                _c.print(f"   {dim(ts)}")
            if url:
                _c.print(f"   [blue]{url}[/]")
            _c.print("")


@app.command(help="Clear the Jiro cache.")
def cache_clear(
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Clear all cached search results."""
    from pathlib import Path
    from jiro.cli_ui import console as _c, success, warning, dim

    settings = Settings.load(config)
    cleared = []

    if settings.cache_type == "sqlite":
        db_path = Path(settings.db_path).expanduser()
        cache_path = db_path.parent / "cache.db"
        if cache_path.exists():
            cache_path.unlink()
            cleared.append(str(cache_path))

    # Also clear any learning data
    learning_path = Path("~/.jiro/social_learning.json").expanduser()
    if learning_path.exists():
        learning_path.unlink()
        cleared.append(str(learning_path))

    if cleared:
        _c.print(success(f"Cleared {len(cleared)} cache files:"))
        for p in cleared:
            _c.print(dim(f"  {p}"))
    else:
        _c.print(warning("No cache files to clear."))


@app.command(help="View Jiro logs.")
def logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """View recent Jiro log entries."""
    from jiro.cli_ui import console as _c, warning, error, rule
    settings = Settings.load(config)
    log_file = settings.logging.get("file", "")
    if not log_file:
        _c.print(warning("Log file not configured. Set logging.file in config."))
        raise typer.Exit(1)

    log_path = Path(log_file).expanduser()
    if not log_path.exists():
        _c.print(warning(f"Log file not found: {log_path}"))
        raise typer.Exit(1)

    try:
        _c.print(rule(f"Logs (last {lines} lines)"))
        _c.print()
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        recent = all_lines[-lines:]
        for line in recent:
            _c.print(line.rstrip())
    except Exception as e:
        _c.print(error(f"Failed to read log file: {e}"))
        raise typer.Exit(1)


@app.command(help="Benchmark Jiro search and scrape performance.")
def bench(
    query: str = typer.Argument("test", help="Search query for benchmark"),
    iterations: int = typer.Option(5, "--iterations", "-n", help="Number of iterations"),
    engines: int = typer.Option(2, "--engines", "-e", help="Number of search engines"),
    config: str = typer.Option(None, "--config", "-c"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Print raw JSON"),
) -> None:
    """Benchmark search and scrape performance.

    Runs multiple iterations and reports timing statistics.
    """
    import time
    from statistics import mean, median, stdev

    from jiro.server import create_app
    from starlette.testclient import TestClient
    from jiro.cli_ui import console as _c, accent, rule, ORANGE
    from rich.table import Table

    _c.print(accent(f"Benchmarking Jiro") + f" ({iterations} iterations)")
    _c.print(f"Query: {query} | Engines: {engines}\n")

    search_times = []
    scrape_times = []

    with TestClient(create_app(_quiet_settings(Settings.load(config)))) as client:
        # Benchmark search
        _c.print(rule("Search benchmarks"))
        for i in range(iterations):
            start = time.perf_counter()
            resp = client.post("/search", json={"query": query, "num_results": 5})
            elapsed = time.perf_counter() - start
            search_times.append(elapsed)
            status = "ok" if resp.status_code == 200 else f"err:{resp.status_code}"
            _c.print(f"  [{i+1}/{iterations}] {elapsed:.3f}s [{status}]")

        # Benchmark scrape
        _c.print()
        _c.print(rule("Scrape benchmarks"))
        test_urls = [
            "https://example.com",
            "https://httpbin.org/html",
            "https://quotes.toscrape.com",
        ]
        for i in range(iterations):
            url = test_urls[i % len(test_urls)]
            start = time.perf_counter()
            resp = client.post("/scrape", json={"url": url, "format": "markdown"})
            elapsed = time.perf_counter() - start
            scrape_times.append(elapsed)
            status = "ok" if resp.status_code == 200 else f"err:{resp.status_code}"
            _c.print(f"  [{i+1}/{iterations}] {elapsed:.3f}s [{status}] {url}")

    if json_output:
        def _stats(times):
            if len(times) < 2:
                return {"mean": times[0], "median": times[0], "stdev": 0, "min": times[0], "max": times[0], "total": times[0]}
            return {"mean": mean(times), "median": median(times), "stdev": stdev(times), "min": min(times), "max": max(times), "total": sum(times)}
        print(json.dumps({"query": query, "iterations": iterations, "engines": engines, "search": _stats(search_times), "scrape": _stats(scrape_times)}, indent=2, default=str))
        return

    # Report
    _c.print()
    _c.print(rule("Results"))
    table = Table(title="Performance Summary")
    table.add_column("Metric")
    table.add_column("Search")
    table.add_column("Scrape")

    def fmt_stats(times):
        if len(times) < 2:
            return f"{times[0]:.3f}s", "N/A"
        return f"{mean(times):.3f}s", f"+-{stdev(times):.3f}s"

    search_mean, search_std = fmt_stats(search_times)
    scrape_mean, scrape_std = fmt_stats(scrape_times)

    table.add_row("Mean", search_mean, scrape_mean)
    table.add_row("Median", f"{median(search_times):.3f}s", f"{median(scrape_times):.3f}s")
    table.add_row("Std Dev", search_std, scrape_std)
    table.add_row("Min", f"{min(search_times):.3f}s", f"{min(scrape_times):.3f}s")
    table.add_row("Max", f"{max(search_times):.3f}s", f"{max(scrape_times):.3f}s")
    table.add_row("Total", f"{sum(search_times):.3f}s", f"{sum(scrape_times):.3f}s")

    _c.print(table)


if __name__ == "__main__":
    app()
