"""Config tests: defaults, env interpolation, overrides."""

from __future__ import annotations



from jiro.config import Settings, deep_merge, interpolate_env


def test_defaults():
    s = Settings()  # pure defaults, no file/env influence
    assert s.default_engine == "google"
    assert s.cache_type == "sqlite"
    assert s.timeout == 10
    assert s.retries == 3
    assert "google" in s.engines


def test_env_interpolation(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    raw = interpolate_env({"llm": {"api_key": "${OPENAI_API_KEY}"}})
    assert raw["llm"]["api_key"] == "sk-test-123"


def test_env_interpolation_missing_var():
    raw = interpolate_env({"x": "${DEFINITELY_NOT_SET_XYZ}"})
    assert raw["x"] == ""


def test_deep_merge():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    override = {"a": {"b": 9}}
    out = deep_merge(base, override)
    assert out == {"a": {"b": 9, "c": 2}, "d": 3}


def test_env_override_jiro_style(monkeypatch):
    monkeypatch.setenv("JIRO_SERVER__PORT", "9999")
    monkeypatch.setenv("JIRO_AUTH__ENABLED", "true")
    s = Settings.load()
    assert s.port == 9999
    assert s.auth_enabled is True


def test_config_file_loading(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 7000\nscraping:\n  default_engine: bing\n")
    s = Settings.load(str(cfg))
    assert s.port == 7000
    assert s.default_engine == "bing"
