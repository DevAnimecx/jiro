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
        if suffix in (".yaml", ".yml"):
            import yaml
            return yaml.safe_load(text) or {}
        return json.loads(text)
    except Exception:
        return {}


def _save_config(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".toml":
        try:
            import tomli_w
            path.write_text(tomli_w.dumps(data), encoding="utf-8")
            return
        except ImportError:
            pass
    if suffix in (".yaml", ".yml"):
        try:
            import yaml
            path.write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            return
        except ImportError:
            pass
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _manifest_path() -> Path:
    return Path("~/.jiro/setup-manifest.json").expanduser()


def _load_manifest() -> List[Dict[str, Any]]:
    path = _manifest_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("entries") or [])
    except Exception:
        return []


def _save_manifest(entries: List[Dict[str, Any]]) -> None:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "entries": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_manifest(entry: Dict[str, Any]) -> None:
    entries = [
        item
        for item in _load_manifest()
        if not (item.get("client") == entry.get("client") and item.get("path") == entry.get("path"))
    ]
    entries.append(entry)
    _save_manifest(entries)


def setup_client(
    client_id: str,
    force: bool = False,
    remote: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Configure a client for local (stdio) or remote (HTTP) MCP."""
    client = CLIENTS.get(client_id)
    if not client:
        return {"ok": False, "error": f"Unknown client: {client_id}"}

    if client.get("kind") == "cli":
        command = client.get("command_hint", "")
        return {
            "ok": True,
            "guided": True,
            "client": client["name"],
            "path": "",
            "message": f"{client['name']} is configured by its CLI. Run: {command}",
            "launch_hint": client.get("launch_hint", ""),
        }

    sys_name = _get_system()
    raw_path = client.get("detect", {}).get(sys_name)
    if not raw_path:
        return {"ok": False, "error": f"Unsupported platform: {sys_name}"}

    config_path = _expand(raw_path).resolve()
    created = not config_path.exists()

    if created:
        if not client.get("write"):
            return {"ok": False, "error": f"Config not found: {config_path}", "path": str(config_path)}
        data: Dict[str, Any] = {}
    else:
        data = _load_config(config_path)

    if remote:
        # Load API key from cloud credentials
        from jiro.cloud_auth import load_cloud_credentials, JIRO_CLOUD_BASE
        creds = load_cloud_credentials()
        if not creds or not creds.api_key:
            return {
                "ok": False,
                "error": "Not signed in. Run 'jiro auth login' first to get an API key for remote MCP.",
            }
        api_key = creds.api_key
        config_value = {
            "url": f"{JIRO_CLOUD_BASE}/mcp",
            "headers": {
                "Authorization": f"Bearer {api_key}",
            },
        }
        hint = f"Remote MCP configured. Restart {client['name']} to use cloud backend."
    else:
        config_value = client["config"]()
        hint = client.get("launch_hint", "")

    keys = client["config_key"]

    if not force and keys[-1] in _deep_get(data, keys[:-1]):
        return {
            "ok": True,
            "skipped": True,
            "path": str(config_path),
            "message": f"Jiro already configured in {config_path}",
        }

    _deep_set(data, keys, config_value)

    mode = "remote (cloud)" if remote else "local (stdio)"
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "path": str(config_path),
            "client": client["name"],
            "mode": mode,
            "message": f"Would configure {client['name']} ({mode}) at {config_path}",
            "launch_hint": hint,
        }

    before_hash = _file_hash(config_path)
    backup: Optional[Path] = None
    if not created:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup = config_path.with_name(f"{config_path.name}.jiro-backup-{stamp}")
        try:
            shutil.copy2(config_path, backup)
        except Exception as exc:
            return {"ok": False, "error": f"Could not back up {config_path}: {exc}"}

    try:
        _save_config(config_path, data)
    except Exception as exc:
        if backup and backup.exists():
            shutil.copy2(backup, config_path)
        elif created and config_path.exists():
            config_path.unlink()
        return {"ok": False, "error": f"Could not write {config_path}: {exc}"}

    _record_manifest(
        {
            "client": client_id,
            "path": str(config_path),
            "backup": str(backup) if backup else None,
            "created": created,
            "before_sha256": before_hash,
            "after_sha256": _file_hash(config_path),
            "configured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
    )

    return {
        "ok": True,
        "path": str(config_path),
        "client": client["name"],
        "mode": mode,
        "backup": str(backup) if backup else None,
        "launch_hint": hint,
        "message": f"Configured {client['name']} ({mode}) at {config_path}",
    }


def _deep_get(d: Dict[str, Any], keys: tuple) -> Dict[str, Any]:
    for k in keys:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d


def _deep_del(d: Dict[str, Any], keys: tuple) -> bool:
    for k in keys[:-1]:
        if not isinstance(d.get(k), dict):
            return False
        d = d[k]
    return d.pop(keys[-1], None) is not None


def uninstall_client(client_id: str) -> Dict[str, Any]:
    client = CLIENTS.get(client_id)
    if not client:
        return {"ok": False, "error": f"Unknown client: {client_id}"}
    if client.get("kind") == "cli":
        return {
            "ok": True,
            "guided": True,
            "message": (
                f"Remove {client['name']} with: "
                f"{client.get('uninstall_hint', 'its own MCP remove command')}"
            ),
        }

    entries = _load_manifest()
    matching = [entry for entry in entries if entry.get("client") == client_id]
    if not matching:
        return {
            "ok": False,
            "error": f"No setup manifest entry for {client_id}; nothing to roll back.",
        }

    restored: List[str] = []
    remaining = [entry for entry in entries if entry.get("client") != client_id]
    for entry in reversed(matching):
        path = Path(entry.get("path") or "")
        if not path:
            continue
        backup = Path(entry["backup"]) if entry.get("backup") else None
        created = bool(entry.get("created"))
        if backup and backup.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, path)
            restored.append(str(path))
        elif created:
            if path.exists():
                path.unlink()
            restored.append(str(path))
        elif path.exists():
            data = _load_config(path)
            if _deep_del(data, client["config_key"]):
                _save_config(path, data)
            restored.append(str(path))

    _save_manifest(remaining)
    return {
        "ok": True,
        "client": client["name"],
        "restored": restored,
        "message": f"Rolled back {client['name']}: {', '.join(restored) or 'no files'}",
    }


# ── Public API ────────────────────────────────────────────────────────

def list_supported_clients() -> List[Dict[str, str]]:
    return [
        {
            "id": cid,
            "name": CLIENTS[cid]["name"],
            "tier": "B" if CLIENTS[cid].get("kind") == "cli" else "A",
        }
        for cid in sorted(CLIENTS)
    ]


def detect_configured() -> List[str]:
    return _detect_all()


def run_setup(
    client_id: str,
    force: bool = False,
    remote: bool = False,
    dry_run: bool = False,
) -> int:
    result = setup_client(client_id, force, remote=remote, dry_run=dry_run)
    if result.get("ok"):
        prefix = "[DRY]" if dry_run else ("[GUIDE]" if result.get("guided") else "[OK]")
        print(f"{prefix} {result['message']}")
        if result.get("backup"):
            print(f"       Backup: {result['backup']}")
        if result.get("launch_hint"):
            print(f"       {result['launch_hint']}")
        return 0
    print(f"[FAIL] {result.get('error', 'Unknown error')}")
    if result.get("path"):
        print(f"       Config: {result['path']}")
    return 1


def run_auto_setup(dry_run: bool = False, json_output: bool = False) -> int:
    found = _detect_all()
    if json_output:
        results = [setup_client(cid, dry_run=dry_run) for cid in found]
        print(json.dumps({"detected": found, "results": results}, indent=2))
        return 0 if all(result.get("ok") for result in results) else 1
    if not found:
        print("[INFO] No supported MCP clients detected.")
        print("       Supported: " + ", ".join(sorted(CLIENTS.keys())))
        return 0

    print(f"[INFO] Detected {len(found)} client(s): {', '.join(found)}")
    exit_code = 0
    for cid in found:
        rc = run_setup(cid, dry_run=dry_run)
        if rc != 0:
            exit_code = rc
    return exit_code


def run_uninstall(client_id: str) -> int:
    result = uninstall_client(client_id)
    if result.get("ok"):
        print(f"[OK] {result['message']}")
        return 0
    print(f"[FAIL] {result.get('error', 'Unknown error')}")
    return 1


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
