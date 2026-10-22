"""MCP setup registry, merge, backup, and rollback tests."""

from __future__ import annotations

import json

from jiro import mcp_setup


def _client(path, name="Test Client"):
    return {
        "name": name,
        "detect": {"win32": str(path), "darwin": str(path), "linux": str(path)},
        "write": True,
        "config_key": ("mcpServers", "jiro"),
        "config": lambda: {"command": "jiro", "args": ["mcp"]},
        "launch_hint": "restart",
    }


def _patch(monkeypatch, tmp_path, client_id="cursor"):
    manifest = tmp_path / "setup-manifest.json"
    monkeypatch.setattr(mcp_setup, "_get_system", lambda: "win32")
    monkeypatch.setattr(mcp_setup, "_manifest_path", lambda: manifest)
    return manifest


def test_registry_has_16_clients_and_15_tier_a():
    clients = mcp_setup.list_supported_clients()
    assert len(clients) == 16
    assert len([c for c in clients if c["tier"] == "A"]) >= 15


def test_setup_merges_and_preserves_existing_keys(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "keep"}}}), encoding="utf-8")
    monkeypatch.setitem(mcp_setup.CLIENTS, "cursor", _client(path))

    result = mcp_setup.setup_client("cursor", force=True)
    assert result["ok"] is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other"]["command"] == "keep"
    assert data["mcpServers"]["jiro"]["args"] == ["mcp"]
    assert result["backup"]


def test_uninstall_restores_backup_byte_for_byte(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    path = tmp_path / "mcp.json"
    original = b'{"mcpServers": {"other": {"command": "keep"}}, "x": 1}\n'
    path.write_bytes(original)
    monkeypatch.setitem(mcp_setup.CLIENTS, "cursor", _client(path))

    assert mcp_setup.setup_client("cursor", force=True)["ok"] is True
    assert path.read_bytes() != original

    result = mcp_setup.uninstall_client("cursor")
    assert result["ok"] is True
    assert path.read_bytes() == original


def test_dry_run_does_not_write(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    path = tmp_path / "mcp.json"
    monkeypatch.setitem(mcp_setup.CLIENTS, "cursor", _client(path))

    result = mcp_setup.setup_client("cursor", dry_run=True)
    assert result["ok"] is True
    assert result.get("dry_run") is True
    assert not path.exists()


def test_goose_yaml_round_trip(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text("extensions:\n  other:\n    enabled: true\n", encoding="utf-8")
    client = _client(path, name="Goose")
    client["config_key"] = ("extensions", "jiro")
    client["config"] = lambda: {"enabled": True, "type": "stdio", "cmd": "jiro", "args": ["mcp"]}
    monkeypatch.setitem(mcp_setup.CLIENTS, "goose", client)

    assert mcp_setup.setup_client("goose", force=True)["ok"] is True
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["extensions"]["other"]["enabled"] is True
    assert data["extensions"]["jiro"]["cmd"] == "jiro"


def test_claude_code_is_guided_not_written(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    result = mcp_setup.setup_client("claude_code")
    assert result["ok"] is True
    assert result["guided"] is True
    assert "claude mcp add" in result["message"]


def test_default_runtime_config_does_not_warn_about_its_own_defaults():
    """The shipped runtime defaults must be internally coherent.

    ``lease_ttl_seconds`` must exceed ``max_attempt_timeout_seconds``: a lease
    can otherwise be reclaimed mid-attempt. The shipped settings had 30 against
    3600, so every process logged a warning about a configuration the user
    never chose, and ``MAX_LEASE_TTL_CEILING`` made no valid value available.
    """
    from jiro.config import Settings
    from jiro.runtime.config import RuntimeConfig

    runtime_section = Settings().get("runtime") or {}
    config = RuntimeConfig.from_settings(runtime_section)
    assert config.lease_ttl > config.max_attempt_timeout, (
        f"lease_ttl {config.lease_ttl} must exceed "
        f"max_attempt_timeout {config.max_attempt_timeout}"
    )
