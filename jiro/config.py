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
