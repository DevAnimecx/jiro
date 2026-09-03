"""Search intelligence module for Jiro v0.2.

Provides hybrid search, relevance scoring, filters, highlights, answer synthesis,
multi-query search capabilities, and structured output extraction.
"""

from __future__ import annotations

# Import main classes for external use
from jiro.search.hybrid import HybridSearcher
from jiro.search.reranker import CrossEncoderReranker, rerank
from jiro.search.embeddings import EmbeddingModel, embed_texts, semantic_similarity
from jiro.search.relevance import RelevanceScorer, RelevanceScore, RelevanceBreakdown
from jiro.search.filters import SearchFilter, FilterConfig, get_engines_for_category
from jiro.search.highlights import HighlightExtractor, extract_highlights_from_content
from jiro.search.answer import AnswerSynthesizer, AnswerResult
from jiro.search.multiquery import MultiQuerySearcher, MultiQueryRequest, generate_sub_queries
from jiro.search.structured import StructuredExtractor, ExtractionResult, SchemaValidator, extract_structured

__all__ = [
    # Hybrid search
    "HybridSearcher",
    # Reranker
    "CrossEncoderReranker",
    "rerank",
    # Embeddings
    "EmbeddingModel",
    "embed_texts",
    "semantic_similarity",
    # Relevance
    "RelevanceScorer",
    "RelevanceScore",
    "RelevanceBreakdown",
    # Filters
    "SearchFilter",
    "FilterConfig",
    "get_engines_for_category",
    # Highlights
    "HighlightExtractor",
    "extract_highlights_from_content",
    # Answer
    "AnswerSynthesizer",
    "AnswerResult",
    # Multi-query
    "MultiQuerySearcher",
    "MultiQueryRequest",
    "generate_sub_queries",
    # Structured extraction
    "StructuredExtractor",
    "ExtractionResult",
    "SchemaValidator",
    "extract_structured",
]