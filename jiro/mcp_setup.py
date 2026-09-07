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

import json
import os
import platform
import shutil
import subprocess
import sys
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
}


# ── Helpers ───────────────────────────────────────────────────────────

def _get_system() -> str:
    return platform.system().lower()


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path)))


def _detect_client(client_id: str) -> Optional[Path]:
    client = CLIENTS.get(client_id)
    if not client:
        return None
    sys_name = _get_system()
    raw = client["detect"].get(sys_name)
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
        if path.suffix == ".toml":
            try:
                import tomllib
                return tomllib.loads(text)
            except ImportError:
                try:
                    import tomli
                    return tomli.loads(text)
                except ImportError:
                    log.warning("tomllib/tomli not available; treating as JSON")
        return json.loads(text)
    except Exception:
        return {}


def _save_config(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".toml":
        try:
            import tomllib
            import tomli_w
            path.write_text(tomli_w.dumps(data), encoding="utf-8")
            return
        except ImportError:
            pass
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def setup_client(client_id: str, force: bool = False) -> Dict[str, Any]:
    client = CLIENTS.get(client_id)
    if not client:
        return {"ok": False, "error": f"Unknown client: {client_id}"}

    sys_name = _get_system()
    raw_path = client["detect"].get(sys_name)
    if not raw_path:
        return {"ok": False, "error": f"Unsupported platform: {sys_name}"}

    config_path = _expand(raw_path)

    if not config_path.exists():
        if not client.get("write"):
            return {"ok": False, "error": f"Config not found: {config_path}", "path": str(config_path)}
        data: Dict[str, Any] = {}
    else:
        data = _load_config(config_path)

    config_value = client["config"]()
    keys = client["config_key"]

    if not force and keys[-1] in _deep_get(data, keys[:-1]):
        return {
            "ok": True,
            "skipped": True,
            "path": str(config_path),
            "message": f"Jiro already configured in {config_path}",
        }

    _deep_set(data, keys, config_value)
    _save_config(config_path, data)

    return {
        "ok": True,
        "path": str(config_path),
        "client": client["name"],
        "launch_hint": client.get("launch_hint", ""),
        "message": f"Configured {client['name']} at {config_path}",
    }


def _deep_get(d: Dict[str, Any], keys: tuple) -> Dict[str, Any]:
    for k in keys:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d


# ── Public API ────────────────────────────────────────────────────────

def list_supported_clients() -> List[Dict[str, str]]:
    return [{"id": cid, "name": CLIENTS[cid]["name"]} for cid in sorted(CLIENTS)]


def detect_configured() -> List[str]:
    return _detect_all()


def run_setup(client_id: str, force: bool = False) -> int:
    result = setup_client(client_id, force)
    if result.get("ok"):
        print(f"[OK] {result['message']}")
        if result.get("launch_hint"):
            print(f"       {result['launch_hint']}")
        return 0
    else:
        print(f"[FAIL] {result.get('error', 'Unknown error')}")
        if result.get("path"):
            print(f"       Config: {result['path']}")
        return 1


def run_auto_setup() -> int:
    found = _detect_all()
    if not found:
        print("[INFO] No supported MCP clients detected.")
        print("       Supported: " + ", ".join(sorted(CLIENTS.keys())))
        return 0

    print(f"[INFO] Detected {len(found)} client(s): {', '.join(found)}")
    exit_code = 0
    for cid in found:
        rc = run_setup(cid)
        if rc != 0:
            exit_code = rc
    return exit_code


# ── CLI integration ───────────────────────────────────────────────────

def _print_status() -> int:
    found = _detect_all()
    if found:
        print(f"Detected {len(found)} configured MCP client(s):")
        for cid in found:
            p = _detect_client(cid)
            print(f"  [OK] {CLIENTS[cid]['name']:20s} {p}")
    else:
        print("No MCP clients detected.")
    print()
    print("Supported clients:")
    for cid, meta in sorted(CLIENTS.items()):
        detected = "[detected]" if cid in found else ""
        print(f"  {cid:15s} {meta['name']} {detected}")
    return 0
