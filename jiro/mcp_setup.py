"""MCP setup wizard for Jiro.

Auto-detects and configures Jiro as an MCP server for:
Claude Desktop, Cursor, OpenCode, Codex CLI, OpenClaw, Hermes,
Continue.dev, Zed, VS Code, and any MCP-compatible client.

Usage:
    jiro mcp setup --client claude
    jiro mcp setup --client cursor
    jiro mcp setup --client opencode
    jiro mcp setup --client codex
    jiro mcp setup --client openclaw
    jiro mcp setup --client hermes
    jiro mcp setup --auto   # detect and configure all found clients
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from jiro.log import get_logger

log = get_logger("jiro.mcp_setup")

# ── Client config definitions ─────────────────────────────────────────

JIRO_BIN = shutil.which("jiro") or sys.executable

CLIENTS: Dict[str, Dict[str, Any]] = {
    "claude": {
        "name": "Claude Desktop",
        "detect": {
            "darwin": "~/Library/Application Support/Claude/claude_desktop_config.json",
            "win32": os.path.expandvars(
                r"%APPDATA%\Claude\claude_desktop_config.json"
            ),
            "linux": "~/.config/Claude/claude_desktop_config.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {
            "command": JIRO_BIN,
            "args": ["mcp"],
            "env": {},
        },
        "launch_hint": "Restart Claude Desktop after setup.",
    },
    "cursor": {
        "name": "Cursor IDE",
        "detect": {
            "darwin": "~/.cursor/mcp.json",
            "win32": os.path.expandvars(r"%USERPROFILE%\.cursor\mcp.json"),
            "linux": "~/.cursor/mcp.json",
        },
        "write": True,
        "config_key": ("servers", "jiro"),
        "config": lambda: {
            "command": JIRO_BIN,
            "args": ["mcp"],
            "env": {},
        },
        "launch_hint": "Restart Cursor after setup.",
    },
    "opencode": {
        "name": "OpenCode",
        "detect": {
            "darwin": "~/.config/opencode/config.json",
            "win32": os.path.expandvars(r"%USERPROFILE%\.config\opencode\config.json"),
            "linux": "~/.config/opencode/config.json",
        },
        "write": True,
        "config_key": ("mcp", "servers", "jiro"),
        "config": lambda: {
            "command": JIRO_BIN,
            "args": ["mcp"],
            "transport": "stdio",
        },
        "launch_hint": "Restart OpenCode after setup.",
    },
    "codex": {
        "name": "OpenAI Codex CLI",
        "detect": {
            "darwin": "~/.codex/config.toml",
            "win32": os.path.expandvars(r"%USERPROFILE%\.codex\config.toml"),
            "linux": "~/.codex/config.toml",
        },
        "write": True,
        "config_key": ("mcp", "servers", "jiro"),
        "config": lambda: {
            "command": JIRO_BIN,
            "args": ["mcp"],
        },
        "launch_hint": "Restart Codex CLI after setup.",
        "format": "toml",
    },
    "openclaw": {
        "name": "OpenClaw",
        "detect": {
            "darwin": "~/.openclaw/config.json",
            "win32": os.path.expandvars(r"%USERPROFILE%\.openclaw\config.json"),
            "linux": "~/.openclaw/config.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {
            "command": JIRO_BIN,
            "args": ["mcp"],
        },
        "launch_hint": "Restart OpenClaw after setup.",
    },
    "hermes": {
        "name": "Hermes",
        "detect": {
            "darwin": "~/.hermes/config.json",
            "win32": os.path.expandvars(r"%USERPROFILE%\.hermes\config.json"),
            "linux": "~/.hermes/config.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {
            "command": JIRO_BIN,
            "args": ["mcp"],
        },
        "launch_hint": "Restart Hermes after setup.",
    },
    "continue": {
        "name": "Continue.dev",
        "detect": {
            "darwin": "~/.continue/config.json",
            "win32": os.path.expandvars(r"%USERPROFILE%\.continue\config.json"),
            "linux": "~/.continue/config.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {
            "command": JIRO_BIN,
            "args": ["mcp"],
        },
        "launch_hint": "Restart Continue.dev after setup.",
    },
    "zed": {
        "name": "Zed",
        "detect": {
            "darwin": "~/.config/zed/settings.json",
            "win32": os.path.expandvars(r"%APPDATA%\Zed\settings.json"),
            "linux": "~/.config/zed/settings.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {
            "command": JIRO_BIN,
            "args": ["mcp"],
        },
        "launch_hint": "Restart Zed after setup.",
    },
    "claude_code": {
        "name": "Claude Code",
        "kind": "cli",
        "bin": "claude",
        "write": False,
        "command_hint": "claude mcp add --scope user jiro -- jiro mcp",
        "uninstall_hint": "claude mcp remove jiro",
        "launch_hint": "Run the command above, then start Claude Code.",
    },
    "windsurf": {
        "name": "Windsurf",
        "detect": {
            "darwin": "~/.codeium/windsurf/mcp_config.json",
            "win32": os.path.expandvars(r"%USERPROFILE%\.codeium\windsurf\mcp_config.json"),
            "linux": "~/.codeium/windsurf/mcp_config.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {"command": JIRO_BIN, "args": ["mcp"], "env": {}},
        "launch_hint": "Restart Windsurf after setup.",
    },
    "cline": {
        "name": "Cline (VS Code)",
        "detect": {
            "darwin": "~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
            "win32": os.path.expandvars(r"%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json"),
            "linux": "~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {"command": JIRO_BIN, "args": ["mcp"], "disabled": False, "autoApprove": []},
        "launch_hint": "Reload VS Code, then enable Jiro in Cline.",
    },
    "roo": {
        "name": "Roo Code (VS Code)",
        "detect": {
            "darwin": "~/Library/Application Support/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json",
            "win32": os.path.expandvars(r"%APPDATA%\Code\User\globalStorage\rooveterinaryinc.roo-cline\settings\mcp_settings.json"),
            "linux": "~/.config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {"command": JIRO_BIN, "args": ["mcp"], "disabled": False, "autoApprove": []},
        "launch_hint": "Reload VS Code, then enable Jiro in Roo Code.",
    },
    "kilo": {
        "name": "Kilo Code (VS Code)",
        "detect": {
            "darwin": "~/Library/Application Support/Code/User/globalStorage/kilocode.kilo-code/settings/mcp_settings.json",
            "win32": os.path.expandvars(r"%APPDATA%\Code\User\globalStorage\kilocode.kilo-code\settings\mcp_settings.json"),
            "linux": "~/.config/Code/User/globalStorage/kilocode.kilo-code/settings/mcp_settings.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {"command": JIRO_BIN, "args": ["mcp"], "disabled": False, "autoApprove": []},
        "launch_hint": "Reload VS Code, then enable Jiro in Kilo Code.",
    },
    "gemini": {
        "name": "Gemini CLI",
        "detect": {
            "darwin": "~/.gemini/settings.json",
            "win32": os.path.expandvars(r"%USERPROFILE%\.gemini\settings.json"),
            "linux": "~/.gemini/settings.json",
        },
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {"command": JIRO_BIN, "args": ["mcp"], "env": {}},
        "launch_hint": "Restart Gemini CLI after setup.",
    },
    "goose": {
        "name": "Goose",
        "detect": {
            "darwin": "~/.config/goose/config.yaml",
            "win32": os.path.expandvars(r"%USERPROFILE%\.config\goose\config.yaml"),
            "linux": "~/.config/goose/config.yaml",
        },
        "write": True,
        "config_key": ("extensions", "jiro"),
        "config": lambda: {
            "enabled": True,
            "type": "stdio",
            "name": "jiro",
            "cmd": JIRO_BIN,
            "args": ["mcp"],
        },
        "launch_hint": "Restart Goose after setup.",
    },
    "vscode": {
        "name": "VS Code Copilot",
        "detect": {
            "darwin": ".vscode/mcp.json",
            "win32": ".vscode/mcp.json",
            "linux": ".vscode/mcp.json",
        },
        "write": True,
        "config_key": ("servers", "jiro"),
        "config": lambda: {"command": JIRO_BIN, "args": ["mcp"], "type": "stdio"},
        "launch_hint": "Reload the workspace, then enable Jiro in Copilot agent mode.",
    },
}


# ── Helpers ───────────────────────────────────────────────────────────

def _get_system() -> str:
    return sys.platform


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path)))


def _detect_client(client_id: str) -> Optional[Path]:
    client = CLIENTS.get(client_id)
    if not client:
        return None
    if client.get("kind") == "cli":
        binary = client.get("bin")
        found = shutil.which(binary) if binary else None
        return Path(found) if found else None
    sys_name = _get_system()
    raw = client.get("detect", {}).get(sys_name)
    if not raw:
        return None
    p = _expand(raw)
    return p if p.exists() else None


def _detect_all() -> List[str]:
    found = []
    for cid in CLIENTS:
        if _detect_client(cid):
            found.append(cid)
    return found


def _deep_set(d: Dict[str, Any], keys: tuple, value: Any) -> None:
    for k in keys[:-1]:
        if k not in d:
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


def _load_config(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix == ".toml":
            try:
                import tomllib
                return tomllib.loads(text)
            except ImportError:
                try:
                    import tomli
                    return tomli.loads(text)
                except ImportError:
                    log.warning("tomllib/tomli not available; treating as JSON")
