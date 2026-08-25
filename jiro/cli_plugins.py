"""Plugin management CLI commands."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console
from rich.table import Table

from jiro.scraping.engines import registry

# Ensure built-in engines are registered so the plugin CLI works standalone
# (the orchestrator builds the registry lazily; the CLI must do it eagerly).
try:
    from jiro.scraping.parsers import (  # noqa: F401
        amazon, baidu, bing, brave, duckduckgo, ebay, google, youtube, yandex,
    )
except Exception:  # pragma: no cover - defensive
    pass

console = Console()

plugin_app = typer.Typer(name="plugins", help="Manage search engine plugins")


@plugin_app.command("list")
def list_plugins(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """List all registered engine plugins."""
    engines = registry.names()
    metadata = registry.get_all_metadata()

    if json_output:
        result = []
        for name in engines:
            meta = metadata.get(name, {})
            result.append({
                "name": name,
                "version": meta.get("version", "1.0"),
                "author": meta.get("author", ""),
                "description": meta.get("description", ""),
                "types": meta.get("types", ["web"]),
                "homepage": meta.get("homepage", ""),
                "license": meta.get("license", "MIT"),
            })
        console.print(json.dumps(result, indent=2))
        return

    table = Table(title="Registered Engine Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Author", style="yellow")
    table.add_column("Types", style="blue")
    table.add_column("Description", style="white")

    for name in engines:
        meta = metadata.get(name, {})
        table.add_row(
            name,
            meta.get("version", "1.0"),
            meta.get("author", "Unknown"),
            ", ".join(meta.get("types", ["web"])),
            meta.get("description", "")[:60] + ("..." if len(meta.get("description", "")) > 60 else ""),
        )

    console.print(table)
    console.print(f"\nTotal: {len(engines)} engines")


@plugin_app.command("info")
def plugin_info(
    name: str = typer.Argument(..., help="Engine name"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Show detailed information about a plugin."""
    try:
        meta = registry.get_metadata(name)
        if not meta:
            console.print(f"[red]Engine '{name}' not found[/red]")
            raise typer.Exit(1)

        if json_output:
            console.print(json.dumps(meta, indent=2))
            return

        console.print(f"[bold cyan]{name}[/bold cyan]")
        console.print(f"  Version: {meta.get('version', '1.0')}")
        console.print(f"  Author: {meta.get('author', 'Unknown')}")
        console.print(f"  License: {meta.get('license', 'MIT')}")
        console.print(f"  Homepage: {meta.get('homepage', 'N/A')}")
        console.print(f"  Min Jiro Version: {meta.get('min_jiro_version', '0.1.0')}")
        console.print(f"  Types: {', '.join(meta.get('types', ['web']))}")
        console.print(f"  Description: {meta.get('description', 'No description')}")

        config_schema = meta.get("config_schema", {})
        if config_schema:
            console.print(f"\n  Config Schema: {json.dumps(config_schema, indent=4)}")

    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@plugin_app.command("discover")
def discover_plugins(
    plugin_dirs: Optional[List[str]] = typer.Option(
        None, "--dir", "-d", help="Plugin directories to search"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Discover and load plugins from directories."""
    loaded = registry.discover_plugins(plugin_dirs)

    if json_output:
        console.print(json.dumps({"loaded": loaded}, indent=2))
        return

    if loaded:
        console.print(f"[green]Loaded {len(loaded)} plugins:[/green]")
        for name in loaded:
            console.print(f"  - {name}")
    else:
        console.print("[yellow]No plugins found[/yellow]")
        console.print("\nDefault search paths:")
        for path in registry._get_default_plugin_dirs():
            console.print(f"  - {path}")


@plugin_app.command("validate")
def validate_plugin(
    name: str = typer.Argument(..., help="Engine name"),
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Config JSON file"),
) -> None:
    """Validate a plugin's configuration."""
    try:
        engine_cls = registry.get(name)
        engine = engine_cls(None, None)  # type: ignore

        if config:
            import json
            with open(config) as f:
                cfg = json.load(f)
        else:
            cfg = {}

        errors = engine.validate_config(cfg)
        if errors:
            console.print(f"[red]Validation failed for {name}:[/red]")
            for err in errors:
                console.print(f"  - {err}")
            raise typer.Exit(1)
        else:
            console.print(f"[green]Configuration valid for {name}[/green]")

    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)


@plugin_app.command("create")
def create_plugin(
    name: str = typer.Argument(..., help="Engine name (lowercase, no spaces)"),
    output_dir: str = typer.Option(".", "--output", "-o", help="Output directory"),
    author: str = typer.Option("", "--author", "-a", help="Author name"),
) -> None:
    """Create a new engine plugin scaffold."""
    if not name.islower() or " " in name:
        console.print("[red]Engine name must be lowercase with no spaces[/red]")
        raise typer.Exit(1)

    template = f'''"""Engine plugin for {name} search.

Generated by Jiro plugin scaffold.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from selectolax.parser import HTMLParser, Node

from jiro.errors import EngineParseError
from jiro.models import OrganicResult, SearchRequest, SearchResponse
from jiro.scraping.engines import BaseEngine
from jiro.scraping.client import parse_html


class {name.capitalize()}Engine(BaseEngine):
    name = "{name}"
    types = ["web"]  # Add supported types: web, images, news, videos, shopping, places
    version = "1.0.0"
    author = "{author or 'Your Name'}"
    description = "{name.capitalize()} search engine parser"
    homepage = "https://github.com/yourusername/jiro-{name}-plugin"
    license = "MIT"
    min_jiro_version = "0.1.0"

    # Configuration schema for this engine
    config_schema = {{
        "type": "object",
        "properties": {{
            "custom_param": {{"type": "string", "description": "Example config parameter"}}
        }}
    }}

    SEARCH_URL = "https://{name}.com/search"

    # Add selectors for self-healing parsing
    RESULT_BLOCKS = [
        "div.result",
        "div.item",
    ]
    TITLE_SELECTORS = ["h3", "h2 a"]
    SNIPPET_SELECTORS = [".snippet", ".summary", "p"]

    async def search(self, req: SearchRequest) -> SearchResponse:
        params = {{
            "q": req.q,
            "num": str(min(req.num, 100)),
            "start": str(req.start),
        }}

        html, _ = await self.client.get(
            self.SEARCH_URL,
            engine=self.name,
            params=params,
        )
        tree = parse_html(html)

        blocks = self._first_many(tree, self.RESULT_BLOCKS)
        organic: List[OrganicResult] = []
        position = req.start

        for block in blocks:
            parsed = self._parse_result(block, position + 1)
            if parsed is not None and parsed.link and parsed.title:
                organic.append(parsed)
                position += 1
            if len(organic) >= req.num:
                break

        if not organic:
            raise EngineParseError(
                f"{{self.name}} returned no parseable results",
                details={{"engine": self.name, "page_bytes": len(html)}},
            )

        return SearchResponse(
            search_metadata=self.metadata(req, engine=self.name, cached=False, total_time=0.0),
            search_information={{"query_displayed": req.q, "organic_results_count": len(organic)}},
            organic_results=organic,
        )

    def _parse_result(self, block: Node, position: int) -> Optional[OrganicResult]:
        # Extract link
        link_el = None
        for sel in self.TITLE_SELECTORS:
            el = block.css_first(sel)
            if el is not None:
                link_el = el if el.tag == "a" else el.css_first("a")
                if link_el is not None:
                    break
        if link_el is None:
            return None

        raw_link = link_el.attributes.get("href") or ""
        link = self._clean_link(raw_link)
        if not link:
            return None

        # Extract title
        title = ""
        for sel in self.TITLE_SELECTORS:
            el = block.css_first(sel)
            if el is not None:
                title = el.text(strip=True)
                if title:
                    break

        # Extract snippet
        snippet = ""
        for sel in self.SNIPPET_SELECTORS:
            el = block.css_first(sel)
            if el is not None and el.text(strip=True):
                snippet = el.text(strip=True)
                break

        source = self._source(link)
        return OrganicResult(
            position=position,
            title=title,
            link=link,
            displayed_link=source or "",
            snippet=snippet,
            source=source,
        )

    @staticmethod
    def _first_many(tree: HTMLParser, selectors: List[str]) -> List[Node]:
        for sel in selectors:
            nodes = tree.css(sel)
            if nodes:
                return nodes
        return []

    @staticmethod
    def _clean_link(href: str) -> str:
        from urllib.parse import unquote, urlparse
        href = href.strip()
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return f"https://{name}.com{{href}}"
        return ""

    @staticmethod
    def _source(link: str) -> Optional[str]:
        from urllib.parse import urlparse
        try:
            return urlparse(link).netloc or None
        except Exception:
            return None


# Register this engine
from jiro.scraping.engines import registry  # noqa: E402
registry.register({name.capitalize()}Engine)
'''

    output_path = Path(output_dir) / f"{name}.py"
    if output_path.exists():
        console.print(f"[red]File {output_path} already exists[/red]")
        raise typer.Exit(1)

    output_path.write_text(template)
    console.print(f"[green]Created plugin scaffold at {output_path}[/green]")
    console.print(f"\nTo use this plugin:")
    console.print(f"  1. Edit {output_path} with your engine's parsing logic")
    console.print(f"  2. Place it in ~/.jiro/plugins/ or set JIRO_PLUGIN_PATH")
    console.print(f"  3. Run 'jiro plugins discover' to load it")