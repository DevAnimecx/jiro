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
from jiro.log import get_logger
from jiro.errors import EngineBlockedError, EngineError
from jiro.models import SearchRequest, SearchResponse
from jiro.scraping.client import ScrapingClient
from jiro.scraping.latency import default_recorder
from jiro.robots import RobotsManager

log = get_logger("jiro.engines")

# Search status values. ``status`` describes fidelity, not transport: a search
# can return HTTP 200 with real results and still be "partial" because the
# engine that served them is not the one the caller asked for.
STATUS_SUCCESS = "success"   # served by the requested engine
STATUS_PARTIAL = "partial"   # served by a substitute engine
STATUS_ERROR = "error"       # no engine could serve the query

def _record_substitution(meta: Dict[str, Any], requested_engine: str) -> None:
    """Stamp what was asked for, what actually served, and how honest the result is.

    ``engine`` is the engine that returned the results; ``engine_requested`` is
    what the caller asked for. When those differ the search is ``partial``: the
    results are real, but they come from a different index than requested, and
    the caller must be able to tell. Keeping this in one place stops the live,
    cached and semantic-cache paths from drifting apart.
    """
    served = meta.get("engine")
    substituted = served != requested_engine
    meta["engine_requested"] = requested_engine
    meta["engine_substituted"] = substituted
    meta["status"] = STATUS_PARTIAL if substituted else STATUS_SUCCESS


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
    # True only when the upstream request actually accepts a page offset.
    # Most engines return a single fixed SERP page and merely number results
    # with ``req.start``, so paging them just re-fetches the same rows.
    supports_pagination: bool = False
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
        # Rolling observations of how long each engine actually takes, so the
        # graph planner can budget from measurement rather than a constant.
        self.latency = default_recorder()
        # Settled engine-race tasks, so a shutdown can cancel them.
        self._race_tasks: Set[asyncio.Task] = set()
        
        # Lazy import hybrid searcher
        self._hybrid_searcher = None
    
    @property
    def hybrid_searcher(self):
        if self._hybrid_searcher is None:
            from jiro.search.hybrid import HybridSearcher
            self._hybrid_searcher = HybridSearcher(
                self.settings, self, self.cache, self.semantic
            )
        return self._hybrid_searcher

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
        
        # Handle hybrid mode
        if getattr(req, "mode", "auto") == "hybrid":
            return await self.hybrid_searcher.search(req)
        
        engines = self.available_engines(req.engine)
        requested_engine = req.engine
        cache_key = self.cache.make_key(
            "search", req.engine, req.q, req.type, req.num, req.start,
            req.location, req.language, req.safe, req.time_range, req.device,
            req.gl, req.hl, req.mode, getattr(req, "depth", "basic"),
        )

        if not fresh:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                cached["search_metadata"]["cached"] = True
                _record_substitution(cached["search_metadata"], requested_engine)
                return SearchResponse(**cached)
            # semantic (fuzzy) cache hit
            if self.semantic is not None:
                fuzzy = await self.semantic.find(req.q)
                if fuzzy is not None:
                    fuzzy["search_metadata"]["cached"] = True
                    fuzzy["search_metadata"]["semantic_cache"] = True
                    _record_substitution(fuzzy["search_metadata"], requested_engine)
                    return SearchResponse(**fuzzy)

        errors: List[Dict[str, Any]] = []
        ua = self.settings.robots_txt.get("user_agent", "JiroBot/1.0")

        # Racing: query engines concurrently instead of one at a time, so a
        # slow or rate-limited engine no longer serialises the whole request.
        # Preference order still decides which result wins.
        if self._should_race(req, engines):
            winner = await self._race_engines(engines, req, ua=ua, errors=errors)
            if winner is None:
                raise EngineError(
                    "all engines failed for this query",
                    status_code=502,
                    details={"attempted": engines, "errors": errors},
                )
            return await self._finish_result(
                winner, requested_engine, errors, req, cache_key)

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
                self.latency.record(engine_name, elapsed * 1000)
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
                # Always record what was asked for so a caller can never be
                # misled into thinking a substituted engine was the requested
                # one. ``engine`` is the engine that actually served results.
                _record_substitution(result.search_metadata, requested_engine)
                if errors:
                    result.search_metadata["skipped_engines"] = errors

                # Engines return one SERP page (~10 rows) regardless of ``num``.
                # Walk further pages on the same engine so a request for 50
                # results is not silently served as 3.
                await self._paginate(engine_name, engine_cls, req, result)

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

    def _should_race(self, req: SearchRequest, engines: List[str]) -> bool:
        """Whether to query engines concurrently for this request.

        Off by default: racing issues more upstream requests and can return a
        different engine than a sequential walk would, so opting in should be
        explicit. Skipped when there is nothing to race or when a caller pinned
        a single engine.
        """
        if len(engines) < 2:
            return False
        try:
            if not self.settings.get("search.race_engines", False):
                return False
        except Exception:
            return False
        # An exact engine request still races only its own fallback chain, which
        # is what `engines` already is (requested first, then fallbacks).
        return True

    async def _finish_result(
        self, winner: Dict[str, Any], requested_engine: str,
        errors: List[Dict[str, Any]], req: SearchRequest, cache_key: str,
    ) -> SearchResponse:
        """Stamp attribution, paginate and cache a successful engine result."""
        engine_name = winner["engine"]
        engine_cls = winner["engine_cls"]
        result = winner["result"]

        if engine_name != requested_engine:
            result.search_metadata["fallback_engine"] = engine_name
        # Always record what was asked for so a caller can never be misled into
        # thinking a substituted engine was the requested one.
        _record_substitution(result.search_metadata, requested_engine)
        if errors:
            result.search_metadata["skipped_engines"] = errors

        await self._paginate(engine_name, engine_cls, req, result)

        await self.cache.put(cache_key, result.model_dump(), engine=engine_name)
        if self.semantic is not None:
            await self.semantic.store(req.q, cache_key)
        return result

    async def _attempt_engine(
        self, engine_name: str, req: SearchRequest,
        *, ua: str,
    ) -> Dict[str, Any]:
        """One engine attempt, returning either a result or a recorded error.

        Extracted from the fallback loop so the same attempt can be run
        sequentially (the default, which prefers the requested engine) or
        concurrently with other engines when racing is enabled.
        """
        if self.robots is not None:
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
                allowed = await self.robots.check_fetch(engine_name, search_url, ua)
                if not allowed:
                    if self.settings.robots_txt.get("strict_mode", False):
                        return {"error": {"engine": engine_name,
                                          "error": "disallowed by robots.txt",
                                          "error_code": "robots_txt_disallowed"}}
                    log.warning("robots.txt disallows search path, continuing anyway",
                                extra={"engine": engine_name, "path": path})

            delay = self.robots.get_crawl_delay(engine_name, ua)
            if delay:
                await asyncio.sleep(delay)

        try:
            engine_cls = self.registry.get(engine_name)
            engine = engine_cls(self.client, self.settings)
            if req.type not in engine.types:
                return {"error": {"engine": engine_name,
                                  "error": f"type '{req.type}' not supported by this engine"}}

            started = time.perf_counter()
            result = await engine.search(req)
            elapsed = time.perf_counter() - started
            self.latency.record(engine_name, elapsed * 1000)
            result.search_metadata["engine"] = engine_name
            result.search_metadata["total_time_taken"] = round(elapsed, 3)

            if not self._is_relevant(req.q, result):
                return {
                    "error": {
                        "engine": engine_name,
                        "error": "results not relevant to query (possible bot detection)",
                        "error_code": "irrelevant_results",
                    }
                }
            return {"engine": engine_name, "engine_cls": engine_cls, "result": result}
        except EngineBlockedError as exc:
            return {"error": {"engine": engine_name, "error": exc.message,
                              "error_code": exc.code}}
        except EngineError as exc:
            return {"error": {"engine": engine_name, "error": exc.message,
                              "error_code": exc.code}}
        except Exception as exc:
            return {"error": {"engine": engine_name, "error": str(exc),
                              "error_code": type(exc).__name__}}

    async def _race_engines(
        self, engines: List[str], req: SearchRequest, *, ua: str,
        errors: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Try engines concurrently; return the best *relevant* result.

        The preference order is preserved: if the first engine succeeds it wins
        even when a later one finished sooner, because the caller asked for that
        index. Racing only removes the wait for an engine that is slow, rate
        limited or blocked. A winning engine that is not the first is still
        reported through ``_record_substitution`` by the caller.
        """
        tasks = {
            name: asyncio.create_task(
                self._attempt_engine(name, req, ua=ua))
            for name in engines
        }
        self._race_tasks.update(tasks.values())
        try:
            # Wait for every attempt to settle. Racing is about not serialising
            # the *latency*; correctness still needs all outcomes so the skipped
            # engines can be reported honestly.
            done = await asyncio.gather(*tasks.values(), return_exceptions=True)
        finally:
            self._race_tasks.difference_update(tasks.values())

        winners: Dict[str, Dict[str, Any]] = {}
        for name, outcome in zip(tasks, done):
            if isinstance(outcome, BaseException):
                errors.append({"engine": name, "error": str(outcome),
                               "error_code": type(outcome).__name__})
            elif "error" in outcome:
                errors.append(outcome["error"])
            else:
                winners[name] = outcome

        if not winners:
            return None
        # Preference order decides among the engines that actually succeeded.
        for name in engines:
            if name in winners:
                return winners[name]
        return None

    async def _paginate(self, engine_name: str, engine_cls: Type[BaseEngine],
                        req: SearchRequest, result: SearchResponse) -> None:
        """Top up ``result`` from later SERP pages of the *same* engine.

        Each engine returns roughly one page of results, so ``num`` above that
        page size used to be ignored. This walks successive pages (dedup by
        link) until ``num`` is reached, the engine stops returning new rows, or
        the page budget is used up. Never triggers a fallback.
        """
        max_pages = self.settings.get("search.pagination.max_pages", 5)
        have = len(result.organic_results)
        if have >= req.num or max_pages <= 0 or req.start > 0:
            return
        # Pagination is only meaningful for engines that expose a page offset.
        if req.type not in ("web", "images", "news", "videos"):
            return
        if not getattr(engine_cls, "supports_pagination", False):
            # Disclose the shortfall rather than silently serving a short page.
            result.search_metadata["requested_num"] = req.num
            result.search_metadata["truncated"] = True
            result.search_metadata["pagination_supported"] = False
            return

        seen = {r.link for r in result.organic_results if r.link}
        # Offset step. A short first page means the engine serves smaller pages
        # than assumed, so step by what was actually returned rather than a
        # fixed 10: stepping by more than the engine's page size silently skips
        # results, which is how a request for 9 came back with 6.
        page_size = have if have > 0 else 10

        # Page offsets are independent, so fetch them concurrently instead of
        # walking pages one at a time: a request for 50 results used to spend
        # up to five sequential round-trips. Order is preserved when merging
        # (results are appended in page order, not completion order), so the
        # caller sees the same ranking as the sequential walk produced.
        #
        # Only as many pages as could still be useful are requested: the
        # shortfall divided by the page size, bounded by max_pages. Fetching
        # every allowed page unconditionally would spend upstream requests on
        # pages that cannot fit in the result anyway.
        shortfall = req.num - have
        pages_needed = min(max_pages, max(1, -(-shortfall // page_size)))
        offsets = [req.start + page_size * (n + 1) for n in range(pages_needed)]

        async def _page(offset: int) -> List[Any]:
            page_req = req.model_copy(update={"start": offset})
            try:
                page = await engine_cls(self.client, self.settings).search(page_req)
            except Exception as exc:
                log.debug("pagination page failed",
                          extra={"engine": engine_name, "start": offset,
                                 "error": str(exc)})
                return []
            return list(page.organic_results)

        responses = await asyncio.gather(*(_page(o) for o in offsets))
        fetched_pages = sum(1 for batch in responses if batch)

        for batch in responses:
            for item in batch:
                if len(result.organic_results) >= req.num:
                    break
                if item.link and item.link in seen:
                    continue
                if item.link:
                    seen.add(item.link)
                result.organic_results.append(item)

        if len(result.organic_results) > req.num:
            result.organic_results = result.organic_results[:req.num]

        total = len(result.organic_results)
        result.search_information["organic_results_count"] = total
        if total < req.num:
            # Be explicit rather than letting the caller assume a short page
            # is the complete answer.
            result.search_metadata["requested_num"] = req.num
            result.search_metadata["truncated"] = True
            result.search_metadata["pages_fetched"] = 1 + fetched_pages
            result.search_metadata["pagination_supported"] = True

    async def engines_info(self) -> List[Dict[str, Any]]:
        info = self.registry.info()
        for item in info:
            item["default"] = item["name"] == self.settings.default_engine
        return info

    async def probe_engine(self, name: str, *, timeout: float = 15.0,
                           query: str = "jiro") -> Dict[str, Any]:
        """Live reachability probe for a single engine (no fallback chain).

        A real request is issued so rate limits, bot walls and parse
        regressions show up as failures instead of reporting every engine as
        healthy merely because its module imported.
        """
        started = time.perf_counter()
        try:
            engine_cls = self.registry.get(name)
            engine = engine_cls(self.client, self.settings)
            req = SearchRequest(q=query, engine=name, num=1, type="web")
            result = await asyncio.wait_for(engine.search(req), timeout=timeout)
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            return {
                "ok": True,
                "reachable": True,
                "results": len(result.organic_results),
                "latency_ms": elapsed,
            }
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "reachable": False,
                "error": f"probe timed out after {timeout}s",
                "error_code": "engine_timeout",
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        except EngineBlockedError as exc:
            return {
                "ok": False,
                "reachable": False,
                "error": exc.message,
                "error_code": exc.code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        except EngineError as exc:
            return {
                "ok": False,
                "reachable": False,
                "error": exc.message,
                "error_code": exc.code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        except Exception as exc:
            return {
                "ok": False,
                "reachable": False,
                "error": str(exc),
                "error_code": type(exc).__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }

    async def health_check(self, live: bool = True,
                           timeout: float = 15.0) -> Dict[str, Dict[str, Any]]:
        """Check health of all registered engines.

        With ``live=True`` (default) each engine is probed with a real request
        so blocked engines (HTTP 429, CAPTCHA walls) are reported honestly.
        With ``live=False`` only instantiation is verified.
        """
        names = self.registry.names()
        if not live:
            health: Dict[str, Dict[str, Any]] = {}
            for name in names:
                try:
                    # NOTE: BaseEngine takes (client, settings) — passing them
                    # swapped silently skipped every real check.
                    engine_cls = self.registry.get(name)
                    engine_cls(self.client, self.settings)
                    health[name] = {"ok": True, "mode": "instantiation"}
                except Exception as exc:
                    health[name] = {"ok": False, "mode": "instantiation",
                                    "error": str(exc)}
            return health

        probes = await asyncio.gather(
            *[self.probe_engine(name, timeout=timeout) for name in names],
            return_exceptions=True,
        )
        health = {}
        for name, probe in zip(names, probes):
            if isinstance(probe, BaseException):
                health[name] = {"ok": False, "reachable": False,
                                "error": str(probe),
                                "error_code": type(probe).__name__}
            else:
                health[name] = {**probe, "mode": "live"}
        return health

    # ------------------------------------------------------------------
    # v0.2.13: Parallel search with smart engine selection
    # ------------------------------------------------------------------

    def _select_engines_for_query(self, query: str, requested: str = "auto") -> List[str]:
        """Smart engine selection based on query characteristics.

        v0.2.13: Analyzes the query to pick the best engines in parallel,
        rather than trying them sequentially in a fallback chain.
        """
        query_lower = query.lower()
        query_words = set(w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query_lower))

        # Detect query type
        is_product = any(w in query_words for w in {"buy", "price", "cost", "product", "shop", "cheapest", "deal"})
        is_news = any(w in query_words for w in {"news", "latest", "today", "recent", "update"})
        is_video = any(w in query_words for w in {"video", "youtube", "tutorial", "how.to", "demo"})
        is_reddit = "reddit.com" in query_lower
        is_github = "github.com" in query_lower or "repo" in query_words

        engines = self.available_engines(requested)
        selected: List[str] = []

        if requested != "auto":
            # User requested a specific engine - use it
            return engines[:3]

        # Select top 3 engines based on query type
        if is_product:
            # Amazon/eBay for product queries
            preferred = ["amazon", "ebay", "google"]
        elif is_video or "youtube" in query_lower:
            preferred = ["youtube", "google", "bing"]
        elif is_reddit:
            preferred = ["bing", "google", "duckduckgo"]
        elif is_github:
            preferred = ["google", "bing", "duckduckgo"]
        elif is_news:
            preferred = ["google", "bing", "duckduckgo"]
        else:
            # Default: Google + 2 fallbacks (skip Google if no proxy)
            preferred = ["google", "bing", "duckduckgo"]

        for eng in preferred:
            if eng in engines and eng not in selected:
                selected.append(eng)
                if len(selected) >= 3:
                    break

        # Fill with remaining engines
        for eng in engines:
            if eng not in selected:
                selected.append(eng)
                if len(selected) >= 3:
                    break

        return selected

    async def search_parallel(self, req: SearchRequest, *, fresh: bool = False,
                               num_engines: int = 3) -> SearchResponse:
        """Search with multiple engines in parallel and merge results.

        v0.2.13: Query top N engines simultaneously instead of sequentially.
        Returns deduplicated, merged results from all engines.
        """
        if getattr(req, "mode", "auto") == "hybrid":
            return await self.hybrid_searcher.search(req)

        engines = self._select_engines_for_query(req.q, req.engine)
        engines = engines[:num_engines]

        cache_key = self.cache.make_key(
            "search_parallel", req.engine, req.q, req.type, req.num, req.start,
            req.location, req.language, req.safe, req.time_range, req.device,
            req.gl, req.hl, req.mode, getattr(req, "depth", "basic"),
        )

        if not fresh:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                cached["search_metadata"]["cached"] = True
                return SearchResponse(**cached)
            if self.semantic is not None:
                fuzzy = await self.semantic.find(req.q)
                if fuzzy is not None:
                    fuzzy["search_metadata"]["cached"] = True
                    fuzzy["search_metadata"]["semantic_cache"] = True
                    return SearchResponse(**fuzzy)

        # Query engines in parallel
        async def _search_one(engine_name: str):
            try:
                engine_cls = self.registry.get(engine_name)
                engine = engine_cls(self.client, self.settings)
                if req.type not in engine.types:
                    return None, {"engine": engine_name,
                                  "error": f"type '{req.type}' not supported"}
                started = time.perf_counter()
                result = await engine.search(req)
                elapsed = time.perf_counter() - started
                result.search_metadata["engine"] = engine_name
                result.search_metadata["total_time_taken"] = round(elapsed, 3)
                return result, None
            except Exception as exc:
                return None, {"engine": engine_name, "error": str(exc)}

        tasks = [asyncio.create_task(_search_one(eng)) for eng in engines]
        results = await asyncio.gather(*tasks)

        # Merge successful results
        successful = [(r, e) for r, e in results if r is not None]
        errors = [e for _, e in results if e is not None]

        if not successful:
            raise EngineError(
                "all engines failed for this query",
                status_code=502,
                details={"attempted": engines, "errors": errors},
            )

        # Take results from the best engine (fastest + relevant)
        best_result = successful[0][0]
        if len(successful) > 1:
            # Merge results from multiple engines (dedup by URL)
            seen_urls = set()
            merged_results = []
            for result, _ in successful:
                for item in result.organic_results or []:
                    url = item.get("link", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        merged_results.append(item)
                    if len(merged_results) >= req.num:
                        break
                if len(merged_results) >= req.num:
                    break

            # Use the best engine's metadata but with merged results
            best_result.organic_results = merged_results[:req.num]
            if errors:
                best_result.search_metadata["skipped_engines"] = errors

            best_result.search_metadata["engines_queried"] = [e["engine"] for r, e in successful]
            best_result.search_metadata["parallel_search"] = True
            best_result.search_metadata["engines_queried"] = engines

        # Cache the result
        await self.cache.put(cache_key, best_result.model_dump(), engine=best_result.search_metadata.get("engine", "unknown"))
        if self.semantic is not None:
            await self.semantic.store(req.q, cache_key)

        return best_result

    async def stream_search(self, query: str, engine: str = "google", num: int = 10,
                            location: str = "us", language: str = "en"):
        """Stream search results as they arrive from the engine.

        Used by WebSocket endpoint for real-time result delivery.
        Yields individual result dicts.
        """
        from jiro.models import SearchRequest
        req = SearchRequest(
            q=query, engine=engine, num=min(num, 100),
            location=location, language=language,
        )

        engines = self._select_engines_for_query(query, engine)
        engine_order = engines[:3]

        for engine_name in engine_order:
            try:
                engine_cls = self.registry.get(engine_name)
                e = engine_cls(self.client, self.settings)
                if req.type not in e.types:
                    continue
                started = time.perf_counter()
                result = await e.search(req)
                elapsed = time.perf_counter() - started
                result.search_metadata["engine"] = engine_name
                result.search_metadata["total_time_taken"] = round(elapsed, 3)

                for item in result.organic_results or []:
                    yield {
                        "engine": engine_name,
                        "position": item.get("position", 0),
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "source": item.get("source", ""),
                    }
                    await asyncio.sleep(0)  # Allow yielding

            except Exception as exc:
                log.error("engine streaming failed", extra={
                    "engine": engine_name, "error": str(exc)
                })
                continue
