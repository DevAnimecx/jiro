"""Jiro CLI — Premium UI components (Rich-powered)."""

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

# ── Brand Palette ──────────────────────────────────────────────────────
ORANGE    = "#f97316"
PURPLE    = "#a78bfa"
ORANGE2   = "#ea580c"
GREEN     = "#62c073"
RED       = "#e53e5e"
YELLOW    = "#eab308"
CYAN      = "#22d3ee"
DIM       = "#6b7280"
SURFACE   = "#1a1b23"
DARK      = "#060608"


# ── Logo ───────────────────────────────────────────────────────────────

def _gradient_text(text: str, colors: list[str]) -> Text:
    """Apply per-character gradient coloring to text."""
    t = Text()
    n = len(text)
    for i, ch in enumerate(text):
        # Interpolate between colors
        idx = (i / max(n - 1, 1)) * (len(colors) - 1)
        c1 = colors[int(idx)]
        t.append(ch, style=f"bold {c1}")
    return t


def make_logo() -> Panel:
    """Premium Jiro CLI logo — orange-to-purple gradient, geometric block."""
    # The "j" glyph rendered as styled block art
    glyph_lines = [
        "  ╔══════════════════════════════════╗",
        "  ║  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ ║",
        "  ║  ░░  ▄▄▄▄▄  ▄▄▄▄▄▄▄  ▄▄▄▄▄  ░░ ║",
        "  ║  ░░  ██▀▀█  ██▀▀██  ██▀▀█  ░░ ║",
        "  ║  ░░  ██  █  ██  ██  ██  █  ░░ ║",
        "  ║  ░░  ██▄▄█  ██  ██  ██▄▄█  ░░ ║",
        "  ║  ░░  ▀▀▀▀▀  ▀▀  ▀▀  ▀▀▀▀▀  ░░ ║",
        "  ║  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ ║",
        "  ╚══════════════════════════════════╝",
    ]

    logo = Text()
    for line in glyph_lines:
        logo.append(line + "\n", style=f"bold {ORANGE}")

    subtitle = Text()
    subtitle.append("  AI-native web search & scraping", style=f"dim {DIM}")
    subtitle.append("\n  v", style=f"dim {DIM}")
    subtitle.append("0.3.0", style=f"bold {ORANGE}")

    group = Group(logo, subtitle)
    return Panel(group, border_style=ORANGE, box=box.DOUBLE_EDGE, padding=(0, 1))


def make_logo_compact() -> Panel:
    """Compact logo for non-fullscreen contexts."""
    logo = Text()
    logo.append("    ╭─────────────────────╮\n", style=f"dim {ORANGE}")
    logo.append("    │  ", style=f"dim {ORANGE}")
    logo.append("JIRO", style=f"bold {ORANGE}")
    logo.append("       │\n", style=f"dim {ORANGE}")
    logo.append("    │  ", style=f"dim {ORANGE}")
    logo.append("0.3.0", style=f"dim {DIM}")
    logo.append("     │\n", style=f"dim {ORANGE}")
    logo.append("    ╰─────────────────────╯", style=f"dim {ORANGE}")

    subtitle = Text()
    subtitle.append("\n  AI-native web search & scraping", style=f"dim {DIM}")

    return Panel(
        Group(logo, subtitle),
        border_style=ORANGE,
        box=box.ROUNDED,
        padding=(0, 1),
    )


def make_mini_logo() -> Text:
    """Small inline logo for headers."""
    t = Text()
    t.append("j", style=f"bold {ORANGE}")
    t.append("iro", style=f"bold white")
    return t


def make_brand_bar() -> Rule:
    """Brand-colored horizontal rule."""
    return Rule(style=ORANGE, align="center")


# ── Status Indicators ─────────────────────────────────────────────────

def success(msg: str) -> Text:
    t = Text()
    t.append("  ✓ ", style=f"bold {GREEN}")
    t.append(msg, style="bold white")
    return t


def error(msg: str) -> Text:
    t = Text()
    t.append("  ✗ ", style=f"bold {RED}")
    t.append(msg, style=f"bold {RED}")
    return t


def warning(msg: str) -> Text:
    t = Text()
    t.append("  ⚠ ", style=f"bold {YELLOW}")
    t.append(msg, style=f"bold {YELLOW}")
    return t


def info(msg: str) -> Text:
    t = Text()
    t.append("  ℹ ", style=f"bold {CYAN}")
    t.append(msg, style="white")
    return t


def dim(msg: str) -> Text:
    return Text(msg, style=f"dim {DIM}")


def accent(msg: str) -> Text:
    return Text(msg, style=f"bold {ORANGE}")


def rule(title: str = "") -> Rule:
    if title:
        return Rule(Text(f" {title} ", style=f"bold {ORANGE}"), style=ORANGE, align="left")
    return Rule(style=ORANGE, align="center")


def step(num: int, total: int, label: str) -> Text:
    t = Text()
    t.append(f"  [{num}/{total}] ", style=f"bold {ORANGE}")
    t.append(label, style="bold white")
    return t


# ── Tables & Panels ───────────────────────────────────────────────────

def kv_table(pairs: list[tuple[str, str]], title: str = "") -> Table:
    t = Table(
        title=title if title else None,
        title_style=f"bold {ORANGE}",
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


def badge(text: str, color: str = ORANGE) -> Text:
    t = Text()
    t.append(f" {text} ", style=f"bold white on {color}")
    return t


# ── Credit Display ────────────────────────────────────────────────────

def credits_display(used: int, included: int) -> Panel:
    remaining = max(0, included - used)
    pct = int(used / included * 100) if included > 0 else 0

    bar_width = 30
    filled = int(bar_width * pct / 100)
    bar = "█" * filled + "░" * (bar_width - filled)

    color = GREEN if pct < 60 else YELLOW if pct < 85 else RED

    lines = Text()
    lines.append("  ⚡ ", style=f"bold {ORANGE}")
    lines.append(f"{remaining:,}", style=f"bold {color}")
    lines.append(f" / {included:,} credits remaining", style=f"dim {DIM}")
    lines.append("\n\n  ")

    bar_text = Text()
    bar_text.append("╭", style=f"dim {DIM}")
    bar_text.append("─" * filled, style=f"bold {color}")
    bar_text.append("─" * (bar_width - filled), style=f"dim {DIM}")
    bar_text.append("╮", style=f"dim {DIM}")
    bar_text.append(f"  {pct}% used", style=f"dim {DIM}")
    lines.append_text(bar_text)

    return Panel(
        lines,
        title=f"[bold {ORANGE}]Credits[/]",
        border_style=color,
        box=box.ROUNDED,
        padding=(0, 1),
    )


# ── Account Card ──────────────────────────────────────────────────────

def account_card(
    email: str,
    name: str,
    plan: str,
    credits_used: int,
    credits_included: int,
    rate_limit_rpm: int,
    api_key: str,
) -> Panel:
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
        title=f"[bold {ORANGE}]Cloud Account[/]",
        border_style=ORANGE,
        box=box.DOUBLE_EDGE,
        padding=(1, 2),
    )


# ── Search Results ────────────────────────────────────────────────────

def search_result_card(
    query: str,
    results: list[dict[str, Any]],
    engine: str = "google",
    cached: bool = False,
    time_taken: float = 0.0,
) -> Panel:
    header = Text()
    header.append("  🔍 ", style=f"bold {ORANGE}")
    header.append(f'"{query}"', style="bold white")
    header.append(f"  ·  ", style=f"dim {DIM}")
    header.append(engine, style=f"bold {CYAN}")
    if cached:
        header.append("  (cached)", style=f"italic {GREEN}")
    header.append(f"  ·  {time_taken:.2f}s", style=f"dim {DIM}")

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style=f"bold {ORANGE}",
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


# ── Scrape Result ─────────────────────────────────────────────────────

def scrape_result_card(
    title: str,
    url: str,
    content: str,
    max_chars: int = 3000,
) -> Panel:
    header = Text()
    header.append("  📄 ", style=f"bold {ORANGE}")
    header.append(title[:60], style="bold white")
    header.append(f"\n  ", style="white")
    header.append(url[:70], style=f"{CYAN}")

    display_content = content[:max_chars]
    if len(content) > max_chars:
        display_content += f"\n\n... [{len(content) - max_chars} more chars]"

    return Panel(
        Group(header, Text(""), Text(display_content, style="white")),
        border_style=DIM,
        box=box.ROUNDED,
        padding=(0, 1),
    )


# ── Device Auth Panel ─────────────────────────────────────────────────

def device_auth_panel(
    user_code: str,
    verify_url: str,
    expires_in: int = 900,
) -> Panel:
    mins = expires_in // 60

    code_text = Text()
    code_text.append("\n    ", style="white")
    # Render each char of the code with gradient colors
    for i, ch in enumerate(user_code):
        color = ORANGE if i < len(user_code) // 2 else PURPLE
        code_text.append(ch, style=f"bold {color}")
    code_text.append("\n", style="white")

    lines = Text()
    lines.append("  1. Open ", style="white")
    lines.append(verify_url, style=f"underline {CYAN}")
    lines.append("\n  2. Enter the code above", style="white")
    lines.append(f"\n\n  Code expires in {mins} minutes", style=f"dim {DIM}")

    return Panel(
        Group(code_text, lines),
        title=f"[bold {ORANGE}]Device Authorization[/]",
        subtitle=f"[dim]{expires_in // 60}m {expires_in % 60}s remaining[/]",
        border_style=ORANGE,
        box=box.DOUBLE_EDGE,
        padding=(1, 2),
    )


# ── Helpers ───────────────────────────────────────────────────────────

def spinner_text(msg: str) -> Text:
    return Text(f"  {msg}...", style=f"dim {DIM}")


def launch_animation() -> None:
    console.print()
    console.print(Align.center(make_mini_logo()), style="bold")
    console.print()
