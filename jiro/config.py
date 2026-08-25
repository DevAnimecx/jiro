"""Configuration management.

Sources, in increasing priority:

1. Built-in defaults (match the PRD's `config.yaml`).
2. YAML file at ``$JIRO_CONFIG`` or ``~/.jiro/config.yaml`` (if it exists).
3. Environment variables ``JIRO_*`` (e.g. ``JIRO_SERVER__PORT``).

Secrets can be interpolated from the environment inside YAML using the
``${VAR}`` syntax (e.g. ``api_key: ${OPENAI_API_KEY}``). Missing variables
resolve to an empty string.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
DEFAULT_CONFIG_PATH = Path("~/.jiro/config.yaml").expanduser()

DEFAULT_CONFIG: Dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8000,
        "workers": 1,
        "cors": {
            "enabled": False,
            "origins": ["*"],
            "allow_credentials": False,
            "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["*"],
        },
    },
    "scraping": {
        "default_engine": "google",
        "engines": ["google", "bing", "brave", "duckduckgo", "youtube", "amazon",
                     "ebay", "yandex", "baidu"],
        "fallback_order": ["google", "bing", "brave", "duckduckgo", "youtube", "amazon",
                            "ebay", "yandex", "baidu"],
        "timeout": 10,
        "retries": 3,
        "user_agent_rotation": True,
        "max_results": 20,
        "proxy": {
            "enabled": False,
            "url": "",
            "provider": "",
            "api_key": "",
            "health_check": False,
            "rotation_strategy": "round_robin",  # round_robin | random | least_failures
        },
        "captcha": {"enabled": False, "provider": "capsolver", "api_key": ""},
        "browser_fallback": False,
        "robots_txt": {
            "enabled": True,
            "strict_mode": False,  # If True, refuse to scrape disallowed paths
            "user_agent": "JiroBot/1.0 (+https://github.com/DevAnimecx/jiro)",
            "cache_ttl_seconds": 3600,
        },
        "rate_limits_per_engine": {
            "google": {"rpm": 10, "burst": 2},
            "bing": {"rpm": 30, "burst": 5},
            "duckduckgo": {"rpm": 60, "burst": 10},
            "brave": {"rpm": 30, "burst": 5},
            "youtube": {"rpm": 20, "burst": 3},
            "amazon": {"rpm": 10, "burst": 2},
            "ebay": {"rpm": 15, "burst": 3},
            "yandex": {"rpm": 20, "burst": 3},
            "baidu": {"rpm": 10, "burst": 2},
        },
        "request_validation": {
            "max_query_length": 500,
            "max_batch_size": 10,
            "max_num_results": 100,
            "allowed_engines": ["google", "bing", "brave", "duckduckgo", "youtube",
                                "amazon", "ebay", "yandex", "baidu", "auto"],
            "allowed_types": ["web", "images", "news", "videos", "shopping", "places"],
        },
    },
    "cache": {
        "type": "sqlite",           # sqlite | memory | redis
        "path": "~/.jiro/cache.db",
        "url": "redis://localhost:6379/0",
        "ttl_seconds": 3600,
        "max_size_mb": 1024,
        "semantic": False,          # semantic cache (needs embeddings via llm key)
    },
    "db": {"path": "~/.jiro/jiro.db"},
    "llm": {
        "provider": "openai",       # openai | anthropic | gemini | openrouter | ollama
        "api_key": "",
        "model": "gpt-4o-mini",
        "base_url": "",
        "temperature": 0.2,
        "max_tokens": 1024,
    },
    "auth": {
        "enabled": False,           # False → open access for local use; enable for teams
        "jwt_secret": "",
        "jwt_ttl_minutes": 720,
        "rate_limit_rpm": 60,
    },
    "agent": {
        "max_steps": 5,
        "max_sources": 8,
        "llm_provider": "",
        "llm_model": "",
        "max_snippets_per_source": 3,
    },
    "logging": {"level": "info", "file": ""},
    "privacy": {"log_queries": False, "log_payloads": False},
    "audit": {"enabled": True, "log_file": "~/.jiro/audit.log", "buffer_size": 100,
              "flush_interval_seconds": 5},
}


def interpolate_env(value: Any) -> Any:
    """Recursively replace ``${VAR}`` in strings with the environment value."""
    if isinstance(value, str):
        def _sub(match: "re.Match[str]") -> str:
            return os.environ.get(match.group(1), "")
        return ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_env(v) for v in value]
    return value


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _env_override(raw: Dict[str, Any], prefix: str = "JIRO") -> Dict[str, Any]:
    """Apply JIRO_A__B__C=value overrides into the nested dict."""
    out = copy.deepcopy(raw)
    for key, value in os.environ.items():
        if not key.startswith(prefix + "_"):
            continue
        parts = key[len(prefix) + 1:].lower().split("__")
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _coerce_env(value)
    return out


def _coerce_env(value: str) -> Any:
    low = value.lower()
    if low in ("true", "yes", "1"):
        return True
    if low in ("false", "no", "0"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    return value


@dataclass
class Settings:
    """Typed access to the merged configuration."""

    raw: Dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_CONFIG))

    # -- server -----------------------------------------------------------
    @property
    def host(self) -> str:
        return self.raw["server"]["host"]

    @property
    def port(self) -> int:
        return int(self.raw["server"]["port"])

    @property
    def workers(self) -> int:
        return int(self.raw["server"].get("workers", 1))

    @property
    def cors(self) -> Dict[str, Any]:
        return self.raw["server"].get("cors", {})

    @property
    def cors_enabled(self) -> bool:
        return bool(self.cors.get("enabled", False))

    @property
    def cors_origins(self) -> List[str]:
        return self.cors.get("origins", ["*"])

    @property
    def cors_allow_credentials(self) -> bool:
        return bool(self.cors.get("allow_credentials", False))

    # -- scraping ----------------------------------------------------------
    @property
    def default_engine(self) -> str:
        return self.raw["scraping"].get("default_engine", "google")

    @property
    def engines(self) -> List[str]:
        return self.raw["scraping"].get("engines", ["google", "bing", "duckduckgo"])

    @property
    def fallback_order(self) -> List[str]:
        return self.raw["scraping"].get(
            "fallback_order", ["google", "bing", "duckduckgo"]
        )

    @property
    def timeout(self) -> float:
        return float(self.raw["scraping"].get("timeout", 10))

    @property
    def retries(self) -> int:
        return int(self.raw["scraping"].get("retries", 3))

    @property
    def user_agent_rotation(self) -> bool:
        return bool(self.raw["scraping"].get("user_agent_rotation", True))

    @property
    def max_results(self) -> int:
        return int(self.raw["scraping"].get("max_results", 20))

    @property
    def proxy(self) -> Dict[str, Any]:
        return self.raw["scraping"].get("proxy", {})

    @property
    def captcha(self) -> Dict[str, Any]:
        return self.raw["scraping"].get("captcha", {})

    @property
    def robots_txt(self) -> Dict[str, Any]:
        return self.raw["scraping"].get("robots_txt", {"enabled": True, "strict_mode": False,
                                                        "user_agent": "JiroBot/1.0",
                                                        "cache_ttl_seconds": 3600})

    @property
    def rate_limits_per_engine(self) -> Dict[str, Dict[str, int]]:
        return self.raw["scraping"].get("rate_limits_per_engine", {})

    @property
    def request_validation(self) -> Dict[str, Any]:
        return self.raw["scraping"].get("request_validation", {})

    # -- cache --------------------------------------------------------------
    @property
    def cache_type(self) -> str:
        return self.raw["cache"].get("type", "sqlite")

    @property
    def cache_path(self) -> str:
        return str(Path(self.raw["cache"].get("path", "~/.jiro/cache.db")).expanduser())

    @property
    def cache_ttl(self) -> int:
        return int(self.raw["cache"].get("ttl_seconds", 3600))

    @property
    def db_path(self) -> str:
        return str(Path(self.raw.get("db", {}).get("path", "~/.jiro/jiro.db")).expanduser())

    # -- llm ----------------------------------------------------------------
    @property
    def llm(self) -> Dict[str, Any]:
        return self.raw.get("llm", {})

    # -- auth ----------------------------------------------------------------
    @property
    def auth_enabled(self) -> bool:
        return bool(self.raw["auth"].get("enabled", False))

    @property
    def jwt_secret(self) -> str:
        return self.raw["auth"].get("jwt_secret", "")

    @property
    def jwt_ttl_minutes(self) -> int:
        return int(self.raw["auth"].get("jwt_ttl_minutes", 720))

    @property
    def rate_limit_rpm(self) -> int:
        return int(self.raw["auth"].get("rate_limit_rpm", 60))

    # -- agent ---------------------------------------------------------------
    @property
    def agent(self) -> Dict[str, Any]:
        return self.raw.get("agent", {})

    @property
    def logging(self) -> Dict[str, Any]:
        return self.raw.get("logging", {})

    @property
    def privacy(self) -> Dict[str, Any]:
        return self.raw.get("privacy", {})

    @property
    def audit(self) -> Dict[str, Any]:
        return self.raw.get("audit", {"enabled": True, "log_file": "~/.jiro/audit.log",
                                       "buffer_size": 100, "flush_interval_seconds": 5})

    def log_queries(self) -> bool:
        return bool(self.privacy.get("log_queries", False))

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def dump(self) -> Dict[str, Any]:
        return copy.deepcopy(self.raw)

    @classmethod
    def load(cls, path: Optional[str] = None, *, create_default: bool = False) -> "Settings":
        config_path = None
        if path:
            config_path = Path(path).expanduser()
        elif os.environ.get("JIRO_CONFIG"):
            config_path = Path(os.environ["JIRO_CONFIG"]).expanduser()
        else:
            default = DEFAULT_CONFIG_PATH
            if default.exists():
                config_path = default

        raw = copy.deepcopy(DEFAULT_CONFIG)
        if config_path and config_path.exists():
            with open(config_path, "r", encoding="utf-8") as fh:
                user_config = yaml.safe_load(fh) or {}
            raw = deep_merge(raw, user_config)
        elif create_default:
            if config_path is None:
                config_path = DEFAULT_CONFIG_PATH
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(DEFAULT_CONFIG, fh, sort_keys=False)

        raw = interpolate_env(raw)
        raw = _env_override(raw)
        return cls(raw=raw)
