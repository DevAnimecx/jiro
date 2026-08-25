"""Scraping engines: registry, base class and the fallback orchestrator."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Type

from jiro.config import Settings
from jiro.errors import EngineBlockedError, EngineError, EngineParseError
from jiro.models import SearchRequest, SearchResponse
from jiro.scraping.client import ScrapingClient
from jiro.robots import RobotsManager

# Optional type support table: engine -> list of supported search types.
ENGINE_TYPES: Dict[str, List[str]] = {
    "google": ["web", "images", "news", "videos", "shopping", "places"],
    "bing": ["web", "images", "news", "videos"],
    "duckduckgo": ["web", "images", "news"],
    "brave": ["web", "videos"],
    "youtube": ["videos"],
    "amazon": ["web", "shopping"],
    "ebay": ["web", "shopping"],
    "yandex": ["web"],
    "baidu": ["web"],
}

ENGINE_DESCRIPTIONS: Dict[str, str] = {
    "google": "Google web search (may require proxies on datacenter IPs)",
    "bing": "Bing web search — most reliable for direct HTTP scraping (web/images/news/videos)",
    "duckduckgo": "DuckDuckGo (html/lite endpoints + vqd JSON API)",
    "brave": "Brave Search (web, videos)",
    "youtube": "YouTube video search — extracts video metadata, duration, channel, views",
    "amazon": "Amazon product search — extracts price, rating, reviews, ASIN, Prime badge",
    "ebay": "eBay product search — extracts price, condition, seller, shipping info",
    "yandex": "Yandex search — Russian/CIS market, web results with domain info",
    "baidu": "Baidu search — Chinese market, web results with source domains",
}


class BaseEngine:
    """Interface every engine parser implements."""

    name: str = ""
    types: List[str] = ["web"]
    # Metadata for plugin marketplace
    version: str = "1.0"
    author: str = ""
    description: str = ""
    homepage: str = ""
    license: str = "MIT"
    min_jiro_version: str = "0.1.0"
    config_schema: Dict[str, Any] = {}  # JSON Schema for engine-specific config

    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def search(self, req: SearchRequest) -> SearchResponse:
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------
    def metadata(self, req: SearchRequest, *, engine: str, cached: bool,
                 total_time: float, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "engine": engine,
            "query": req.q,
            "type": req.type,
            "location": req.location,
            "language": req.language,
            "status": "success",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_time_taken": round(total_time, 3),
            "cached": cached,
            "parser_version": self.parser_version,
        }
        if extra:
            meta.update(extra)
        return meta

    parser_version: str = "1.0"

    @staticmethod
    def _num_results(req: SearchRequest) -> int:
        return min(req.num, 100)

    # Plugin lifecycle hooks
    async def on_load(self) -> None:
        """Called when engine is loaded. Override for initialization."""
        pass

    async def on_unload(self) -> None:
        """Called when engine is unloaded. Override for cleanup."""
        pass

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate engine-specific configuration. Return list of errors."""
        return []


class EngineRegistry:
    """Maps engine names to parser classes (plugin point)."""

    def __init__(self) -> None:
        self._engines: Dict[str, Type[BaseEngine]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._plugin_paths: Set[str] = set()

    def register(self, cls: Type[BaseEngine]) -> Type[BaseEngine]:
        if not cls.name:
            raise ValueError("engine class must define `name`")
        self._engines[cls.name] = cls
        self._metadata[cls.name] = {
            "name": cls.name,
            "version": getattr(cls, "version", "1.0"),
            "author": getattr(cls, "author", ""),
            "description": getattr(cls, "description", ""),
            "homepage": getattr(cls, "homepage", ""),
            "license": getattr(cls, "license", "MIT"),
            "min_jiro_version": getattr(cls, "min_jiro_version", "0.1.0"),
            "config_schema": getattr(cls, "config_schema", {}),
            "types": getattr(cls, "types", ["web"]),
        }
        return cls

    def unregister(self, name: str) -> bool:
        """Unregister an engine."""
        if name in self._engines:
            del self._engines[name]
            del self._metadata[name]
            return True
        return False

    def get(self, name: str) -> Type[BaseEngine]:
        if name not in self._engines:
            raise EngineError(
                f"unknown engine '{name}'",
                status_code=422,
                details={"available": sorted(self._engines)},
            )
        return self._engines[name]

    def names(self) -> List[str]:
        return sorted(self._engines)

    def info(self) -> List[Dict[str, Any]]:
        return [
            {"name": name, "types": ENGINE_TYPES.get(name, ["web"]),
             "description": ENGINE_DESCRIPTIONS.get(name, "")}
            for name in sorted(self._engines)
        ]

    def get_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        """Get full plugin metadata."""
        return self._metadata.get(name)

    def get_all_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Get metadata for all registered engines."""
        return self._metadata.copy()

    def discover_plugins(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        """Discover and load engine plugins from directories."""
        loaded = []
        dirs = plugin_dirs or self._get_default_plugin_dirs()

        for plugin_dir in dirs:
            if plugin_dir in self._plugin_paths:
                continue
            self._plugin_paths.add(plugin_dir)

            path = Path(plugin_dir)
            if not path.exists():
                continue

            for py_file in path.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                try:
                    module_name = f"jiro.plugins.{py_file.stem}"
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)
                        loaded.append(py_file.stem)
                except Exception as exc:
                    # Log but don't fail - plugin loading is best-effort
                    print(f"Warning: Failed to load plugin {py_file}: {exc}")

        return loaded

    def _get_default_plugin_dirs(self) -> List[str]:
        """Get default plugin discovery directories."""
        dirs = []

        # User config directory
        user_config = os.environ.get("JIRO_CONFIG")
        if user_config:
            dirs.append(str(Path(user_config).parent / "plugins"))
        else:
            dirs.append(str(Path.home() / ".jiro" / "plugins"))

        # Project plugins directory
        dirs.append(str(Path(__file__).parent.parent / "plugins"))

        # JIRO_PLUGIN_PATH environment variable
        plugin_path = os.environ.get("JIRO_PLUGIN_PATH")
        if plugin_path:
            dirs.extend(plugin_path.split(os.pathsep))

        return dirs


# populated by jiro.scraping.parsers registration at import time
registry = EngineRegistry()


def _build_registry() -> EngineRegistry:
    from jiro.scraping.parsers import (  # noqa: F401
        amazon, baidu, bing, brave, duckduckgo, ebay, google, youtube, yandex,
    )

    return registry


class SearchOrchestrator:
    """Runs a search through the fallback chain with caching."""

    def __init__(self, settings: Settings, client: ScrapingClient,
                 cache: Any, semantic: Any = None) -> None:
        self.settings = settings
        self.client = client
        self.cache = cache
        self.semantic = semantic
        self.registry = _build_registry()
        self.robots = RobotsManager(settings, cache) if settings.robots_txt.get("enabled", True) else None

    @staticmethod
    def _is_relevant(query: str, result: SearchResponse) -> bool:
        """Quick relevance check: do result titles/snippets share query words?"""
        if not result.organic_results:
            return False
        query_words = set(
            w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query.lower())
            if w.lower() not in {"the", "and", "for", "best", "top", "what",
                                  "how", "why", "which", "with", "from",
                                  "this", "that", "are", "was", "has", "can"}
        )
        if not query_words:
            return True  # can't filter, assume relevant
        combined = " ".join(
            (r.title or "") + " " + (r.snippet or "")
            for r in result.organic_results[:5]
        ).lower()
        matches = sum(1 for w in query_words if w in combined)
        return matches >= max(1, len(query_words) // 2)

    def available_engines(self, requested: str) -> List[str]:
        if requested == "auto":
            order = list(self.settings.fallback_order)
        elif requested in self.registry.names():
            # Try the requested engine first, then fall back gracefully (PRD §7.3).
            order = [requested] + [
                e for e in self.settings.fallback_order if e != requested
            ]
        else:
            raise EngineError(
                f"unknown engine '{requested}'",
                status_code=422,
                details={"available": self.registry.names()},
            )
        configured = set(self.settings.engines)
        order = [e for e in order if e in configured or e == requested]
        if self.settings.engines:
            return order or [self.settings.engines[0]]
        return order or ["google"]

    async def search(self, req: SearchRequest, *, fresh: bool = False) -> SearchResponse:
        """Search with engine fallback. Returns the first successful engine's result."""
        engines = self.available_engines(req.engine)
        cache_key = self.cache.make_key(
            "search", req.engine, req.q, req.type, req.num, req.start,
            req.location, req.language, req.safe, req.time_range, req.device,
            req.gl, req.hl,
        )

        if not fresh:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                cached["search_metadata"]["cached"] = True
                return SearchResponse(**cached)
            # semantic (fuzzy) cache hit
            if self.semantic is not None:
                fuzzy = await self.semantic.find(req.q)
                if fuzzy is not None:
                    fuzzy["search_metadata"]["cached"] = True
                    fuzzy["search_metadata"]["semantic_cache"] = True
                    return SearchResponse(**fuzzy)

        errors: List[Dict[str, Any]] = []
        for engine_name in engines:
            # Check robots.txt compliance before attempting
            if self.robots is not None:
                # Get the search URL that will be used
                from jiro.robots import ENGINE_SEARCH_PATHS
                base_urls = {
                    "google": "https://www.google.com",
                    "bing": "https://www.bing.com",
                    "duckduckgo": "https://duckduckgo.com",
                    "brave": "https://search.brave.com",
                    "youtube": "https://www.youtube.com",
                    "amazon": "https://www.amazon.com",
                    "ebay": "https://www.ebay.com",
                    "yandex": "https://yandex.com",
                    "baidu": "https://www.baidu.com",
                }
                base = base_urls.get(engine_name)
                if base:
                    path = ENGINE_SEARCH_PATHS.get(engine_name, "/")
                    search_url = base + path
                    ua = self.settings.robots_txt.get("user_agent", "JiroBot/1.0")
                    allowed = await self.robots.check_fetch(engine_name, search_url, ua)
                    if not allowed:
                        strict = self.settings.robots_txt.get("strict_mode", False)
                        if strict:
                            errors.append({"engine": engine_name,
                                           "error": "disallowed by robots.txt",
                                           "error_code": "robots_txt_disallowed"})
                            continue
                        # In non-strict mode, log warning but continue
                        from jiro.log import get_logger
                        log = get_logger("jiro.orchestrator")
                        log.warning("robots.txt disallows search path, continuing anyway",
                                   extra={"engine": engine_name, "path": path})

                # Respect crawl-delay
                delay = self.robots.get_crawl_delay(engine_name, ua)
                if delay:
                    await asyncio.sleep(delay)

            try:
                engine_cls = self.registry.get(engine_name)
                engine = engine_cls(self.client, self.settings)
                if req.type not in engine.types:
                    errors.append({"engine": engine_name,
                                   "error": f"type '{req.type}' not supported by this engine"})
                    continue
                started = time.perf_counter()
                result = await engine.search(req)
                elapsed = time.perf_counter() - started
                result.search_metadata["engine"] = engine_name
                result.search_metadata["total_time_taken"] = round(elapsed, 3)

                # Check result relevance — if results are clearly irrelevant
                # (e.g. bot detection returned wrong content), try next engine.
                if not self._is_relevant(req.q, result) and len(engines) > 1:
                    errors.append({"engine": engine_name,
                                   "error": "results not relevant to query (possible bot detection)",
                                   "error_code": "irrelevant_results"})
                    # Brief pause before trying next engine to avoid rate-limit patterns.
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                    continue

                if engine_name != engines[0]:
                    result.search_metadata["fallback_engine"] = engine_name
                if errors:
                    result.search_metadata["skipped_engines"] = errors
                await self.cache.put(cache_key, result.model_dump(), engine=engine_name)
                if self.semantic is not None:
                    await self.semantic.store(req.q, cache_key)
                return result
            except EngineBlockedError as exc:
                errors.append({"engine": engine_name, "error": exc.message,
                               "error_code": exc.code})
                continue
            except EngineError as exc:
                errors.append({"engine": engine_name, "error": exc.message,
                               "error_code": exc.code})
                continue

        raise EngineError(
            "all engines failed for this query",
            status_code=502,
            details={"attempted": engines, "errors": errors},
        )

    async def engines_info(self) -> List[Dict[str, Any]]:
        info = self.registry.info()
        for item in info:
            item["default"] = item["name"] == self.settings.default_engine
        return info
