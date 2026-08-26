"""Jiro Search API — local-first, AI-native web search & scraping.

A drop-in, self-hosted alternative to SerpAPI: scrape Google, Bing and
DuckDuckGo directly, cache results locally, and expose them to AI agents
via REST, function-calling schemas and MCP.
"""

__version__ = "0.1.2"
try:  # reflect the actually installed distribution version when available
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("jirosearch")
except Exception:  # pragma: no cover - importlib missing / not installed
    pass
__app_name__ = "jiro"
__tagline__ = "Local-first, AI-native search & scraping API"
