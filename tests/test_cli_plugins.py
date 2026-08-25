"""Tests for the engine plugin CLI (ecosystem surface)."""

from __future__ import annotations

import json

import click
import pytest

from jiro.cli_plugins import list_plugins, plugin_info, validate_plugin, create_plugin
from jiro.config import Settings
from tests.integration_utils import TEST_CONFIG


@pytest.fixture(autouse=True)
def _quiet_settings():
    # Plugin commands read global Settings; ensure a stable config.
    Settings(raw=TEST_CONFIG.copy())


class TestPluginList:
    def test_list_json(self, capsys):
        list_plugins(json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        names = {e["name"] for e in data}
        assert "google" in names
        assert "duckduckgo" in names

    def test_list_table(self, capsys):
        list_plugins(json_output=False)
        out = capsys.readouterr().out
        assert "google" in out


class TestPluginInfo:
    def test_info_known_engine(self, capsys):
        plugin_info("google", json_output=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["name"] == "google"

    def test_info_unknown_engine_exits(self, capsys):
        with pytest.raises(click.exceptions.Exit):
            plugin_info("does-not-exist", json_output=True)


class TestPluginValidate:
    def test_validate_known_engine_no_config(self, capsys):
        # google's base config schema should accept an empty config
        validate_plugin("google", config=None)
        out = capsys.readouterr().out
        assert "valid" in out.lower()

    def test_validate_unknown_engine_exits(self, capsys):
        with pytest.raises(click.exceptions.Exit):
            validate_plugin("nope", config=None)


class TestPluginCreate:
    def test_create_scaffold(self, tmp_path, capsys):
        out_dir = str(tmp_path)
        create_plugin("myengine", output_dir=out_dir, author="Tester")
        created = list(tmp_path.glob("*.py"))
        assert created, "expected a scaffolded plugin file"
        out = capsys.readouterr().out
        assert "myengine" in out

    def test_create_rejects_bad_name(self, tmp_path):
        with pytest.raises(click.exceptions.Exit):
            create_plugin("Bad Name", output_dir=str(tmp_path), author="Tester")
