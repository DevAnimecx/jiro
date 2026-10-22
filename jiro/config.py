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
        "insecure": False,        # allow binding to 0.0.0.0 with auth disabled (DANGER)
        "cors": {
            "enabled": False,
            "origins": [],        # SECURITY: Empty by default - add allowed origins explicitly
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
        "stealth": {
            "enabled": True,            # Enable TLS/JA3 fingerprint rotation
            "delay_between_requests": True,  # Simulate human delays
            "referer_chain_simulation": True,  # Simulate natural browsing path
            "rotate_profile_per_request": True,  # Rotate curl-cffi impersonation per request
        },
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
        "hybrid": {
            "enabled": True,
            "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
            "rerank_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "rrf_k": 60,
            "rerank_top_n": 20,
            "semantic_weight": 0.4,
            "keyword_weight": 0.6,
        },
        "depths": {
            "instant": {"engines": 1, "scrape": False, "rerank": False, "credits": 1},
            "fast": {"engines": 1, "scrape": False, "rerank": False, "credits": 1},
            "basic": {"engines": 1, "scrape": "snippet", "rerank": False, "credits": 1},
            "advanced": {"engines": 3, "scrape": 5, "rerank": True, "credits": 2},
            "deep": {"engines": 5, "scrape": 10, "rerank": True, "semantic": True, "credits": 5},
        },
        "filters": {
            "default_time_range": None,
            "default_max_results": 10,
            "safe_search": True,
        },
        "bias_domains": {},
        "answer": {
            "max_sources": 5,
            "max_snippet_chars": 600,
        },
        "highlights": {
            "max_characters": 500,
            "max_per_result": 3,
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
    "graph": {
        # Budgeted query graph (v0.4.0 Phase 1). Additive: disabling it
        # leaves every 0.3.x endpoint unchanged.
        "enabled": True,
        # Whole-graph deadline. 0 = derive it from observed engine latency. A
        # fixed 2500 ms was below the cost of one real web search, so every
        # plan timed out before it could run.
        "default_deadline_ms": 0,
        "max_deadline_ms": 60000,
        # Cold-start per-search timeout, used until latency is observed.
        # Measured on a working connection: a single engine search takes
        # 4.6-15s depending on whether the first engine answers or has to be
        # substituted, so this has to clear the slow end, not the average.
        "search_timeout_ms": 20000,
        "default_max_tokens": 1200,
        "max_tokens": 4000,
        "fetch_top": 0,              # optional page fetch per search graph (0-10)
        "fetch_concurrency": 6,      # parallel page fetches per FETCH node
        "cache_ttl_seconds": 900,
        "max_concurrency": 6,
    },
    "params": {
        # Live Parameter Layer (v0.4.0 Phase 2). File-backed, local-first.
        "root": "~/.jiro/params",
        "default_max_tokens": 1200,
    },
    "db": {
        "type": "sqlite",           # sqlite | postgresql
        "path": "~/.jiro/jiro.db",
        "url": "",                  # SECURITY: Required for postgresql - no default credentials
        "pool_size": 10,
    },
    "runtime": {
        # Durable task runtime (jiro/runtime). See docs/runtime.md.
        "enabled": True,
        "db_path": "",              # empty = reuse db.path (one SQLite writer per process)
        "max_concurrency": 4,       # worker count == max simultaneous handler executions
        "poll_interval_seconds": 0.5,
        # Leases are renewed on a heartbeat while a handler runs, so this
        # mostly bounds how long a task waits after a hard kill before
        # recovery. It must still exceed max_attempt_timeout_seconds: an
        # attempt that blocks the event loop long enough to miss heartbeats
        # can otherwise be reclaimed and re-executed while it is running.
        "lease_ttl_seconds": 3900,
        "default_attempt_timeout_seconds": 60,
        "max_attempt_timeout_seconds": 3600,
        "default_max_attempts": 3,
        "max_max_attempts": 10,
        "retry_backoff_base_seconds": 1.0,
        "retry_backoff_max_seconds": 60.0,
        "retry_jitter_ratio": 0.2,  # fraction of each delay randomised
        "shutdown_grace_seconds": 15,
        "max_result_bytes": 262144,
        "event_retention_days": 7,  # 0 disables event pruning
        # Re-reconcile durable state on this interval. Startup recovery alone
        # leaves a task stranded in `running` if its owner died but this
        # process kept running: the lease is still live at startup and only
        # becomes reclaimable once it lapses. 0 disables the sweep.
        "reconcile_interval_seconds": 30,
    },
    "memory": {
        # Cognitive memory (jiro/memory). See docs/memory.md.
        "enabled": True,
        # Working memory: bounded, disposable scratch context per scope.
        "working_capacity": 50,        # entries per scope; deterministic eviction
        "working_ttl_seconds": 3600,   # 0 disables expiry
        # Retention windows in days. 0 keeps the type indefinitely.
        "episodic_retention_days": 90,
        "semantic_retention_days": 0,
        "procedural_retention_days": 0,
        "max_content_bytes": 65536,
        "max_memories_per_scope": 2000,
        # Retrieval bounds. Every result is bounded; nothing is unbounded.
        "default_retrieval_limit": 10,
        "max_retrieval_limit": 100,
        "context_budget_chars": 2000,
        "max_context_memories": 20,
        "recency_half_life_seconds": 604800,  # 7 days
        "retrieval": {
            # Documented ranking weights. No hidden multipliers.
            "weights": {
                "text": 0.55,
                "recency": 0.15,
                "importance": 0.10,
                "verification": 0.12,
                "scope": 0.08,
            },
        },
        "embedding": {
            # Opt-in. The core memory system works fully without it, using
            # deterministic lexical ranking.
            "enabled": False,
            "model": "",            # empty = the project's configured model
            "dimensions": 384,
            "fail_open": True,      # provider failure falls back to lexical
        },
        "outbox": {
            # Durable deferred writes, so a task result and its episode cannot
            # be lost to a crash between two commits.
            "enabled": True,
            "drain_interval_seconds": 5.0,
            "max_attempts": 5,
            "batch_size": 50,
        },
        "auto_episodes": True,        # terminal tasks produce episodic records
        "promotion_min_evaluations": 2,  # gates procedural promotion
    },
    "llm": {
        "provider": "openai",       # openai | anthropic | gemini | openrouter | ollama
        "api_key": "",
        "model": "gpt-4o-mini",
        "base_url": "",
        "temperature": 0.2,
        "max_tokens": 1024,
    },
    "auth": {
        "enabled": True,            # SECURITY: Enabled by default for safety
        "jwt_secret": "",           # SECURITY: Must be set when auth enabled
        "jwt_ttl_minutes": 720,
        "rate_limit_rpm": 60,
        "jwt_algorithm": "HS256",   # HS256 (shared secret) or RS256 (asymmetric keys)
        "jwt_key_id": "jiro-default",
        "jwt_private_key": "",
        "jwt_public_key": "",
        "api_key_header": "X-API-Key",  # SECURITY: Header-only auth
        "allow_query_param": False,      # SECURITY: Never accept API keys in query strings
        "oidc": {},                 # OIDC provider configs (see auth_oidc.py)
        "rbac": {
            "enabled": True,        # Enable fine-grained RBAC
            "default_role": "editor",  # Default role for new API keys
        },
        "session": {
            "enabled": True,        # Enable JWT session revocation
            "cleanup_interval_seconds": 3600,
        },
    },
    "agent": {
        "max_steps": 5,
        "max_sources": 8,
        "llm_provider": "",
        "llm_model": "",
        "max_snippets_per_source": 3,
        "deadline_seconds": 0,      # 0 = no hard deadline; recommended 60-120 for /ai/agent
        "content_budget_chars": 200000,  # cap on accumulated scraped content per run
    },
    "social": {
        "twitter": {"auth_token": "", "backend": "auto", "nitter_url": "https://nitter.net"},
        "instagram": {"sessionid": ""},
        "facebook": {"sessionid": "", "c_user": ""},
        "tiktok": {"use_browser": True},
    },
    "dashboard": {
        "enabled": True,
    },
    "logging": {"level": "info", "file": ""},
    "privacy": {"log_queries": False, "log_payloads": False},
    "audit": {"enabled": True, "log_file": "~/.jiro/audit.log", "buffer_size": 100,
              "flush_interval_seconds": 5},
    "security": {
        "encryption_enabled": False,  # Enable encryption at rest
        "encryption_key": "",         # 32+ byte key for AES-256-GCM
        "encryption_algorithm": "aes-256-gcm",
        "encrypted_fields": {         # Fields to encrypt in database
            "api_keys": ["key_hash"],
            "usage": ["query"],
            "oidc_identities": ["email"],
        },
    },
    "compliance": {
        "dsar_enabled": True,         # GDPR data subject access requests
        "retention_days": 365,        # Data retention period
        "auto_purge_enabled": False,  # Automatic cleanup of old data
    },
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


def _plugin_engine_names() -> set:
    """Engine names provided by plugin modules under jiro/plugins/engine.

    These ship with the package but are not part of the built-in parser
    registry, so a config naming one of them (arxiv, github, ...) is valid.
    Failure here is non-fatal: an unreadable plugins directory just means the
    caller falls back to the built-in registry.
    """
    names = set()
    try:
        from pathlib import Path
        plugin_dir = Path(__file__).parent / "plugins" / "engine"
        if plugin_dir.is_dir():
            names = {p.stem for p in plugin_dir.glob("*.py") if not p.name.startswith("_")}
    except Exception:
        pass
    return names


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
    def server_insecure(self) -> bool:
        return bool(self.raw["server"].get("insecure", False))

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
        return self._validated_engines(
            self.raw["scraping"].get("engines", ["google", "bing", "duckduckgo"])
        )

    @property
    def fallback_order(self) -> List[str]:
        return self._validated_engines(
            self.raw["scraping"].get("fallback_order", ["google", "bing", "duckduckgo"])
        )

    @staticmethod
    def _validated_engines(configured: List[str]) -> List[str]:
        """Drop engines named in config that no parser can serve.

        Config is user-editable and is not checked against the engine registry,
        so a stale or invented name (``mock`` is a common one in the wild would
        be advertised as available and then fail every request with a 422). Warn
        once and keep only engines that can actually serve.

        Built-in parsers are not the whole story: engines may also come from
        plugin modules under ``jiro/plugins/engine`` (arxiv, github, ...), which
        ``_build_registry`` does not load. Those names are legitimate, so accept
        them too.
        """
        try:
            from jiro.scraping.engines import _build_registry
            known = set(_build_registry().names())
            known |= _plugin_engine_names()
        except Exception:
            # Registry unavailable (import cycle, partial install): trust config.
            return list(configured)

        valid, unknown = [], []
        for name in configured:
            (valid if name in known else unknown).append(name)
        if unknown:
            from jiro.log import get_logger
            get_logger("jiro.config").warning(
                "ignoring unregistered engines in config",
                extra={"unknown": unknown, "available": sorted(known)},
            )
        return valid or ["google"]

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

    # -- graph --------------------------------------------------------------
    @property
    def graph(self) -> Dict[str, Any]:
        return self.raw.get("graph", {})

    @property
    def graph_enabled(self) -> bool:
        return bool(self.graph.get("enabled", True))

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

    @property
    def jwt_algorithm(self) -> str:
        return str(self.raw["auth"].get("jwt_algorithm", "HS256"))

    @property
    def jwt_key_id(self) -> str:
        return str(self.raw["auth"].get("jwt_key_id", "jiro-default"))

    # -- agent ---------------------------------------------------------------
    @property
    def agent(self) -> Dict[str, Any]:
        return self.raw.get("agent", {})

    @property
    def agent_deadline_seconds(self) -> float:
        return float(self.agent.get("deadline_seconds", 0) or 0)

    @property
    def agent_content_budget_chars(self) -> int:
        return int(self.agent.get("content_budget_chars", 200000) or 200000)

    # -- search hybrid ---------------------------------------------------------
    @property
    def search_hybrid(self) -> Dict[str, Any]:
        return self.raw["scraping"].get("hybrid", {})

    @property
    def search_depths(self) -> Dict[str, Any]:
        return self.raw["scraping"].get("depths", {})

    @property
    def search_filters(self) -> Dict[str, Any]:
        return self.raw["scraping"].get("filters", {})

    @property
    def search_answer(self) -> Dict[str, Any]:
        return self.raw["scraping"].get("answer", {})

    @property
    def search_highlights(self) -> Dict[str, Any]:
        return self.raw["scraping"].get("highlights", {})

    # -- social ---------------------------------------------------------------
    @property
    def social(self) -> Dict[str, Any]:
        return self.raw.get("social", {})

    # -- dashboard ------------------------------------------------------------
    @property
    def dashboard_enabled(self) -> bool:
        return bool(self.raw.get("dashboard", {}).get("enabled", True))

    # -- db -------------------------------------------------------------------
    @property
    def db_type(self) -> str:
        return self.raw.get("db", {}).get("type", "sqlite")

    @property
    def db_url(self) -> str:
        return self.raw.get("db", {}).get("url", "")

    @property
    def db_pool_size(self) -> int:
        return int(self.raw.get("db", {}).get("pool_size", 10))

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
