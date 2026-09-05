"""Jiro Search API — local-first, AI-native web search & scraping.

A drop-in, self-hosted alternative to SerpAPI: scrape Google, Bing and
DuckDuckGo directly, cache results locally, and expose them to AI agents
via REST, function-calling schemas and MCP.
"""

__version__ = "0.2.11"  # Enterprise tier deep lock, free tier power-up, premium UI/UX
try:  # reflect the actually installed distribution version when available
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("jirosearch")
except Exception:  # pragma: no cover - importlib missing / not installed
    pass
__app_name__ = "jiro"
__tagline__ = "Local-first, AI-native search & scraping API"

# Integrity check on import (non-blocking in debug mode)
try:
    from jiro.integrity import verify_package_integrity
    _INTEGRITY_OK = verify_package_integrity()
    if not _INTEGRITY_OK:
        import logging
        logging.getLogger("jiro").warning("package integrity check failed on import")
except Exception:
    pass

# Lazy imports for heavy modules (avoid import overhead)
def __getattr__(name: str):
    if name == "search":
        from jiro import search as _search
        return _search
    if name == "HybridSearcher":
        from jiro.search.hybrid import HybridSearcher
        return HybridSearcher
    if name == "CrossEncoderReranker":
        from jiro.search.reranker import CrossEncoderReranker
        return CrossEncoderReranker
    if name == "EmbeddingModel":
        from jiro.search.embeddings import EmbeddingModel
        return EmbeddingModel
    if name == "RelevanceScorer":
        from jiro.search.relevance import RelevanceScorer
        return RelevanceScorer
    if name == "SearchFilter":
        from jiro.search.filters import SearchFilter
        return SearchFilter
    if name == "HighlightExtractor":
        from jiro.search.highlights import HighlightExtractor
        return HighlightExtractor
    if name == "AnswerSynthesizer":
        from jiro.search.answer import AnswerSynthesizer
        return AnswerSynthesizer
    if name == "MultiQuerySearcher":
        from jiro.search.multiquery import MultiQuerySearcher
        return MultiQuerySearcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "__version__",
    "__app_name__",
    "__tagline__",
    # Search modules (lazy-loaded)
    "search",
    "HybridSearcher",
    "CrossEncoderReranker",
    "EmbeddingModel",
    "RelevanceScorer",
    "SearchFilter",
    "HighlightExtractor",
    "AnswerSynthesizer",
    "MultiQuerySearcher",
]
