"""Jiro CLI — Shared UI components (Rich-powered)."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()

# ── Color Palette ──────────────────────────────────────────────────────
ACCENT = "#f97316"       # Orange (brand)
ACCENT2 = "#ea580c"      # Dark orange
GREEN = "#62c073"        # Success
RED = "#e53e5e"          # Error
YELLOW = "#eab308"       # Warning
CYAN = "#22d3ee"         # Info
DIM = "#6b7280"          # Muted text
SURFACE = "#1a1b23"      # Dark surface


def make_logo() -> Panel:
    """Create the Jiro CLI logo panel."""
    logo = Text()
    logo.append("  ██╗ █████╗ ██████╗ ██╗████████╗", style=ACCENT)
    logo.append("\n  ██║██╔══██╗██╔══██╗██║╚══██╔══╝", style=ACCENT)
    logo.append("\n  ██║███████║██████╔╝██║   ██║   ", style=ACCENT)
    logo.append("\n██╗██║██╔══██║██╔══██╗██║   ██║   ", style=ACCENT)
    logo.append("\n██║██║██║  ██║██████╔╝██║   ██║   ", style=ACCENT)
    logo.append("\n╚═╝╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝   ╚═╝   ", style=ACCENT)

    subtitle = Text()
    subtitle.append("  AI-native web search & scraping", style=f"dim {DIM}")
    subtitle.append("\n  v", style=f"dim {DIM}")
    subtitle.append("0.3.0", style=f"bold {ACCENT}")

    group = Group(logo, subtitle)
    return Panel(group, border_style=ACCENT, box=box.DOUBLE, padding=(0, 1))


def make_mini_logo() -> Text:
    """Small inline logo for headers."""
    t = Text()
    t.append("j", style=f"bold {ACCENT}")
    t.append("iro", style=f"bold white")
    return t


def success(msg: str) -> Text:
    """Styled success message."""
    t = Text()
    t.append("  \u2713 ", style=f"bold {GREEN}")
    t.append(msg, style="bold white")
    return t


def error(msg: str) -> Text:
    """Styled error message."""
    t = Text()
    t.append("  \u2717 ", style=f"bold {RED}")
    t.append(msg, style=f"bold {RED}")
    return t


def warning(msg: str) -> Text:
    """Styled warning message."""
    t = Text()
    t.append("  \u26a0 ", style=f"bold {YELLOW}")
    t.append(msg, style=f"bold {YELLOW}")
    return t


def info(msg: str) -> Text:
    """Styled info message."""
    t = Text()
    t.append("  \u2139 ", style=f"bold {CYAN}")
    t.append(msg, style="white")
    return t


def dim(msg: str) -> Text:
    """Dimmed text."""
    return Text(msg, style=f"dim {DIM}")


def accent(msg: str) -> Text:
    """Accent-colored text."""
    return Text(msg, style=f"bold {ACCENT}")


def rule(title: str = "") -> Rule:
    """Styled horizontal rule."""
    return Rule(Text(title, style=f"dim {DIM}"), style=ACCENT, align="left")


def step(num: int, total: int, label: str) -> Text:
    """Step indicator: [1/3] Label."""
    t = Text()
    t.append(f"  [{num}/{total}] ", style=f"bold {ACCENT}")
    t.append(label, style="bold white")
    return t


def kv_table(pairs: list[tuple[str, str]], title: str = "") -> Table:
    """Key-value table with styled headers."""
    t = Table(
        title=title if title else None,
        title_style=f"bold {ACCENT}",
        box=box.SIMPLE_HEAVY,
        show_header=False,
        padding=(0, 2),
        border_style=DIM,
    )
    t.add_column("Key", style=f"bold {CYAN}", width=16)
    t.add_column("Value", style="white")
    for k, v in pairs:
        t.add_row(k, v)
    return t


def badge(text: str, color: str = ACCENT) -> Text:
    """Inline badge/tag."""
    t = Text()
    t.append(f" {text} ", style=f"bold white on {color}")
    return t


def progress_bar(current: int, total: int, width: int = 20) -> Text:
    """Simple text progress bar."""
    filled = int(width * current / total) if total > 0 else 0
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    t = Text()
    t.append("[", style=f"dim {DIM}")
    t.append(bar[:filled], style=f"bold {GREEN}")
    t.append(bar[filled:], style=f"dim {DIM}")
    t.append("]", style=f"dim {DIM}")
    t.append(f" {current}/{total}", style=f"dim {DIM}")
    return t


def credits_display(used: int, included: int) -> Panel:
    """Beautiful credit balance display."""
    remaining = max(0, included - used)
    pct = int(used / included * 100) if included > 0 else 0

    bar_width = 30
    filled = int(bar_width * pct / 100)
    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)

    color = GREEN if pct < 60 else YELLOW if pct < 85 else RED

    lines = Text()
    lines.append("  \u26a1 ", style=f"bold {ACCENT}")
    lines.append(f"{remaining:,}", style=f"bold {color}")
    lines.append(f" / {included:,} credits remaining", style=f"dim {DIM}")
    lines.append("\n\n  ")

    bar_text = Text()
    bar_text.append("[", style=f"dim {DIM}")
    bar_text.append(bar[:filled], style=f"bold {color}")
    bar_text.append(bar[filled:], style=f"dim {DIM}")
    bar_text.append("]", style=f"dim {DIM}")
    bar_text.append(f"  {pct}% used", style=f"dim {DIM}")
    lines.append_text(bar_text)

    return Panel(
        lines,
        title=f"[bold {ACCENT}]Credits[/]",
        border_style=color,
        box=box.ROUNDED,
        padding=(0, 1),
    )


def account_card(
    email: str,
    name: str,
    plan: str,
    credits_used: int,
    credits_included: int,
    rate_limit_rpm: int,
    api_key: str,
) -> Panel:
    """Full account info card."""
    plan_color = GREEN if plan == "PRO" else CYAN if plan == "ENTERPRISE" else DIM

    rows = []
    rows.append(("Email", f"[white]{email}[/]"))
    rows.append(("Name", f"[white]{name}[/]"))
    rows.append(("Plan", f"[bold {plan_color}]{plan}[/]"))
    rows.append(("Rate Limit", f"[white]{rate_limit_rpm} RPM[/]"))
    masked_key = f"***{api_key[-4:]}" if len(api_key) > 4 else api_key
    rows.append(("API Key", f"[dim]{masked_key}[/]"))

    content = kv_table(rows)

    credits_panel = credits_display(credits_used, credits_included)

    return Panel(
        Group(content, credits_panel),
        title=f"[bold {ACCENT}]Cloud Account[/]",
        border_style=ACCENT,
        box=box.DOUBLE,
        padding=(1, 2),
    )


def search_result_card(
    query: str,
    results: list[dict[str, Any]],
    engine: str = "google",
    cached: bool = False,
    time_taken: float = 0.0,
) -> Panel:
    """Beautiful search results display."""
    # Header with metadata
    header = Text()
    header.append("  \U0001f50d ", style=f"bold {ACCENT}")
    header.append(f'"{query}"', style="bold white")
    header.append(f"  \u00b7  ", style=f"dim {DIM}")
    header.append(engine, style=f"bold {CYAN}")
    if cached:
        header.append("  (cached)", style=f"italic {GREEN}")
    header.append(f"  \u00b7  {time_taken:.2f}s", style=f"dim {DIM}")

    # Results table
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style=f"bold {ACCENT}",
        padding=(0, 1),
        border_style=DIM,
        expand=True,
    )
    table.add_column("#", justify="right", style=f"dim {DIM}", width=3)
    table.add_column("Title", style="bold white", ratio=3)
    table.add_column("Source", style=f"{CYAN}", ratio=2, overflow="fold")
    table.add_column("Snippet", style=f"dim white", ratio=3, overflow="fold")

    for r in results[:10]:
        pos = str(r.get("position", ""))
        title = r.get("title", "")[:80]
        source = r.get("source", "")
        snippet = (r.get("snippet") or "")[:120]
        table.add_row(pos, title, source, snippet)

    items = [header, dim(f"  {len(results)} results")]
    if results:
        items.append(table)

    return Panel(
        Group(*items),
        border_style=DIM,
        box=box.ROUNDED,
        padding=(0, 1),
    )


def scrape_result_card(
    title: str,
    url: str,
    content: str,
    max_chars: int = 3000,
) -> Panel:
    """Beautiful scrape result display."""
    header = Text()
    header.append("  \U0001f4c4 ", style=f"bold {ACCENT}")
    header.append(title[:60], style="bold white")
    header.append(f"\n  ", style="white")
    header.append(url[:70], style=f"{CYAN}")

    # Truncate content
    display_content = content[:max_chars]
    if len(content) > max_chars:
        display_content += f"\n\n... [{len(content) - max_chars} more chars]"

    return Panel(
        Group(header, Text(""), Text(display_content, style="white")),
        border_style=DIM,
        box=box.ROUNDED,
        padding=(0, 1),
    )


def device_auth_panel(
    user_code: str,
    verify_url: str,
    expires_in: int = 900,
) -> Panel:
    """Device authorization code display."""
    mins = expires_in // 60

    code_text = Text()
    code_text.append("\n    ", style="white")
    code_text.append(user_code, style=f"bold {ACCENT}")
    code_text.append("\n", style="white")

    lines = Text()
    lines.append("  1. Open ", style="white")
    lines.append(verify_url, style=f"underline {CYAN}")
    lines.append("\n  2. Enter the code above", style="white")
    lines.append(f"\n\n  Code expires in {mins} minutes", style=f"dim {DIM}")

    return Panel(
        Group(code_text, lines),
        title=f"[bold {ACCENT}]Device Authorization[/]",
        subtitle=f"[dim]{expires_in // 60}m {expires_in % 60}s remaining[/]",
        border_style=ACCENT,
        box=box.DOUBLE,
        padding=(1, 2),
    )


def spinner_text(msg: str) -> Text:
    """Text for use alongside Rich spinners."""
    return Text(f"  {msg}...", style=f"dim {DIM}")


def launch_animation() -> None:
    """Brief launch animation (non-blocking, fast)."""
    console.print()
    console.print(Align.center(make_mini_logo()), style="bold")
    console.print()
