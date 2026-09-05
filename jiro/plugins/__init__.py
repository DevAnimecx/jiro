"""Enhanced Plugin System for Jiro v0.2.

Supports 4 plugin types:
- Engine plugins (existing): Custom search engines
- Search plugins: Post-processing (rerankers, filters, enrichers)
- Datasource plugins: Specialized data sources (SEC, patents, clinical trials)
- Extractor plugins: Custom extraction recipes for specific sites
- Social plugins: Custom social media scrapers
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Type

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.plugins")


# ── Base Classes ──────────────────────────────────────────────────────────────


class PluginType:
    """Plugin type identifiers."""
    ENGINE = "engine"
    SEARCH = "search"
    DATASOURCE = "datasource"
    EXTRACTOR = "extractor"
    SOCIAL = "social"


@dataclass
class PluginMetadata:
    """Common plugin metadata."""
    name: str
    type: str
    version: str = "1.0"
    author: str = ""
    description: str = ""
    homepage: str = ""
    license: str = "MIT"
    min_jiro_version: str = "0.2.0"
    config_schema: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.config_schema is None:
            self.config_schema = {}


class BasePlugin(ABC):
    """Base class for all plugins."""
    
    # Plugin metadata (class attributes)
    name: str = ""
    type: str = ""
    version: str = "1.0"
    author: str = ""
    description: str = ""
    homepage: str = ""
    license: str = "MIT"
    min_jiro_version: str = "0.2.0"
    config_schema: Dict[str, Any] = {}
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config = settings.raw.get("plugins", {}).get(self.name, {})
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the plugin. Called once on load."""
        pass
    
    async def shutdown(self) -> None:
        """Shutdown the plugin. Called on unload."""
        pass
    
    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate plugin configuration. Return list of errors."""
        return []
    
    @classmethod
    def get_metadata(cls) -> Dict[str, Any]:
        """Get plugin metadata as dict."""
        return {
            "name": cls.name,
            "type": cls.type,
            "version": cls.version,
            "author": cls.author,
            "description": cls.description,
            "homepage": cls.homepage,
            "license": cls.license,
            "min_jiro_version": cls.min_jiro_version,
            "config_schema": cls.config_schema,
        }


# ── Engine Plugins (existing, enhanced) ──────────────────────────────────────

class BaseEnginePlugin(BasePlugin):
    """Base class for search engine plugins."""
    
    type = PluginType.ENGINE
    
    # Engine-specific attributes
    supported_types: List[str] = ["web"]
    rate_limit_rpm: int = 30
    requires_proxy: bool = False
    
    @abstractmethod
    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Execute search and return results."""
        pass
    
    @abstractmethod
    async def scrape(self, url: str) -> Dict[str, Any]:
        """Scrape a URL. Optional for some engines."""
        pass


# ── Search Plugins (NEW) ─────────────────────────────────────────────────────

class BaseSearchPlugin(BasePlugin):
    """Base class for search post-processing plugins.
    
    These plugins modify/transform search results after they're fetched.
    Examples: rerankers, filters, enrichers, deduplicators.
    """
    
    type = PluginType.SEARCH
    
    # Processing priority (lower runs first)
    priority: int = 50
    
    @abstractmethod
    async def process(
        self,
        query: str,
        results: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Process search results. Return modified results."""
        pass


# ── Datasource Plugins (NEW) ─────────────────────────────────────────────────

class BaseDatasourcePlugin(BasePlugin):
    """Base class for specialized data source plugins.
    
    These plugins provide access to specific data sources that aren't
    general web search engines.
    Examples: SEC filings, patent databases, clinical trials, real estate listings.
    """
    
    type = PluginType.DATASOURCE
    
    # Datasource categories
    category: str = "general"  # e.g., "financial", "legal", "medical", "academic"
    
    @abstractmethod
    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Search this datasource."""
        pass
    
    @abstractmethod
    async def get(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific record by ID."""
        pass


# ── Extractor Plugins (NEW) ──────────────────────────────────────────────────

class BaseExtractorPlugin(BasePlugin):
    """Base class for custom extraction plugins.
    
    These plugins provide site-specific extraction logic.
    """
    
    type = PluginType.EXTRACTOR
    
    # URL patterns this extractor handles
    url_patterns: List[str] = []
    
    @abstractmethod
    async def extract(self, url: str, html: str, schema: Optional[Dict] = None) -> Dict[str, Any]:
        """Extract structured data from HTML."""
        pass
    
    def can_handle(self, url: str) -> bool:
        """Check if this extractor can handle the URL."""
        if not self.url_patterns:
            return False
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in self.url_patterns)


# ── Social Plugins (NEW) ─────────────────────────────────────────────────────

class BaseSocialPlugin(BasePlugin):
    """Base class for social media scraper plugins.
    
    These plugins add support for new social platforms.
    """
    
    type = PluginType.SOCIAL
    
    # Platform identifier
    platform: str = ""
    
    # URL patterns
    url_patterns: List[str] = []
    
    # Supported actions
    supported_actions: List[str] = ["post", "profile", "search"]
    
    # Rate limits
    rate_limit_rpm: int = 30
    requires_auth: bool = False
    
    @abstractmethod
    async def scrape_post(self, url: str) -> Dict[str, Any]:
        """Scrape a single post."""
        pass
    
    @abstractmethod
    async def scrape_profile(self, username: str) -> Dict[str, Any]:
        """Scrape a user profile."""
        pass
    
    async def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        """Search the platform (optional)."""
        raise NotImplementedError(f"{self.platform} search not implemented")


# ── Plugin Registries ────────────────────────────────────────────────────────


class PluginRegistry:
    """Registry for plugins of a specific type."""
    
    def __init__(self, plugin_type: str) -> None:
        self.plugin_type = plugin_type
        self._plugins: Dict[str, Type[BasePlugin]] = {}
        self._instances: Dict[str, BasePlugin] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._plugin_paths: Set[str] = set()
    
    def register(self, cls: Type[BasePlugin]) -> Type[BasePlugin]:
        """Register a plugin class."""
        if not cls.name:
            raise ValueError(f"{self.plugin_type} plugin class must define 'name'")
        if cls.type != self.plugin_type:
            raise ValueError(f"Plugin type mismatch: expected {self.plugin_type}, got {cls.type}")
        
        self._plugins[cls.name] = cls
        self._metadata[cls.name] = cls.get_metadata()
        log.info(f"Registered {self.plugin_type} plugin: {cls.name}")
        return cls
    
    def unregister(self, name: str) -> bool:
        """Unregister a plugin."""
        if name in self._plugins:
            del self._plugins[name]
            del self._metadata[name]
            if name in self._instances:
                del self._instances[name]
            return True
        return False
    
    def get(self, name: str) -> Type[BasePlugin]:
        """Get plugin class by name."""
        if name not in self._plugins:
            raise ValueError(
                f"unknown {self.plugin_type} plugin '{name}' "
                f"(available: {sorted(self._plugins)})"
            )
        return self._plugins[name]
    
    def get_instance(self, name: str, settings: Settings) -> BasePlugin:
        """Get or create plugin instance."""
        if name not in self._instances:
            cls = self.get(name)
            self._instances[name] = cls(settings)
        return self._instances[name]
    
    def names(self) -> List[str]:
        return sorted(self._plugins)
    
    def metadata(self) -> List[Dict[str, Any]]:
        return [self._metadata[name] for name in sorted(self._metadata)]
    
    def discover_plugins(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        """Discover and load plugins from directories."""
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
                    module_name = f"jiro.plugins.{self.plugin_type}.{py_file.stem}"
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)
                        loaded.append(py_file.stem)
                except Exception as exc:
                    log.warning(f"Failed to load plugin {py_file}: {exc}")
        
        return loaded
    
    def _get_default_plugin_dirs(self) -> List[str]:
        dirs = []
        
        # User config directory
        user_config = os.environ.get("JIRO_CONFIG")
        if user_config:
            dirs.append(str(Path(user_config).parent / "plugins" / self.plugin_type))
        else:
            dirs.append(str(Path.home() / ".jiro" / "plugins" / self.plugin_type))
        
        # Project plugins directory
        dirs.append(str(Path(__file__).parent / self.plugin_type))
        
        # JIRO_PLUGIN_PATH environment variable
        plugin_path = os.environ.get("JIRO_PLUGIN_PATH")
        if plugin_path:
            for p in plugin_path.split(os.pathsep):
                dirs.append(str(Path(p) / self.plugin_type))
        
        return dirs


# Global registries for each plugin type
engine_registry = PluginRegistry(PluginType.ENGINE)
search_plugin_registry = PluginRegistry(PluginType.SEARCH)
datasource_registry = PluginRegistry(PluginType.DATASOURCE)
extractor_registry = PluginRegistry(PluginType.EXTRACTOR)
social_plugin_registry = PluginRegistry(PluginType.SOCIAL)

# Legacy engine registry alias (for backward compatibility)
from jiro.scraping.engines import registry as legacy_engine_registry
legacy_engine_registry = legacy_engine_registry


# ── Unified Plugin Manager ───────────────────────────────────────────────────


class PluginManager:
    """Manages all plugin types."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registries = {
            PluginType.ENGINE: engine_registry,
            PluginType.SEARCH: search_plugin_registry,
            PluginType.DATASOURCE: datasource_registry,
            PluginType.EXTRACTOR: extractor_registry,
            PluginType.SOCIAL: social_plugin_registry,
        }
        self._initialized: Set[str] = set()
    
    def get_registry(self, plugin_type: str) -> PluginRegistry:
        """Get registry for plugin type."""
        return self.registries[plugin_type]
    
    def load_all(self) -> Dict[str, List[str]]:
        """Load all plugins from all registries."""
        results = {}
        for ptype, registry in self.registries.items():
            loaded = registry.discover_plugins()
            results[ptype] = loaded
        return results
    
    async def initialize_all(self) -> None:
        """Initialize all loaded plugins."""
        for ptype, registry in self.registries.items():
            for name in registry.names():
                if name not in self._initialized:
                    instance = registry.get_instance(name, self.settings)
                    await instance.initialize()
                    self._initialized.add(name)
    
    async def shutdown_all(self) -> None:
        """Shutdown all initialized plugins."""
        for ptype, registry in self.registries.items():
            for name, instance in list(registry._instances.items()):
                await instance.shutdown()
                del registry._instances[name]
        self._initialized.clear()
    
    def get_all_metadata(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get metadata for all plugins across all types."""
        return {
            ptype: registry.metadata()
            for ptype, registry in self.registries.items()
        }


# Convenience function
def get_plugin_manager(settings: Settings) -> PluginManager:
    """Get global plugin manager instance."""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager(settings)
    return _plugin_manager

_plugin_manager: Optional[PluginManager] = None