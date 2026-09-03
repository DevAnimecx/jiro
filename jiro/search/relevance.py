"""Relevance scoring for search results.

Computes a 0.0-1.0 relevance score with breakdown:
- keyword_match: TF-IDF style keyword overlap
- semantic_similarity: Embedding-based similarity
- source_authority: Domain authority heuristic
- freshness: Recency boost
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import numpy as np

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.search.relevance")

# Known high-authority domains (can be extended)
HIGH_AUTHORITY_DOMAINS = {
    "wikipedia.org": 1.0,
    "github.com": 0.95,
    "stackoverflow.com": 0.95,
    "arxiv.org": 0.95,
    "pubmed.ncbi.nlm.nih.gov": 0.95,
    "scholar.google.com": 0.95,
    "nature.com": 0.9,
    "science.org": 0.9,
    "ieee.org": 0.9,
    "acm.org": 0.9,
    "mit.edu": 0.9,
    "stanford.edu": 0.9,
    "berkeley.edu": 0.9,
    "cmu.edu": 0.9,
    "google.com": 0.85,
    "microsoft.com": 0.85,
    "amazon.com": 0.8,
    "reuters.com": 0.9,
    "apnews.com": 0.9,
    "bbc.com": 0.9,
    "nytimes.com": 0.9,
    "wsj.com": 0.9,
    "ft.com": 0.9,
    "economist.com": 0.9,
}

# Domain categories for category-specific boosting
DOMAIN_CATEGORIES = {
    "academic": {"arxiv.org", "pubmed.ncbi.nlm.nih.gov", "scholar.google.com", "ieee.org", "acm.org"},
    "code": {"github.com", "gitlab.com", "bitbucket.org", "stackoverflow.com"},
    "news": {"reuters.com", "apnews.com", "bbc.com", "nytimes.com", "wsj.com", "ft.com", "economist.com"},
    "shopping": {"amazon.com", "ebay.com", "walmart.com", "target.com", "bestbuy.com"},
    "reference": {"wikipedia.org", "britannica.com", "wikidata.org"},
}


@dataclass
class RelevanceBreakdown:
    """Breakdown of relevance score components."""
    keyword_match: float
    semantic_similarity: float
    source_authority: float
    freshness: float
    
    def to_dict(self) -> Dict[str, float]:
        return {
            "keyword_match": round(self.keyword_match, 3),
            "semantic_similarity": round(self.semantic_similarity, 3),
            "source_authority": round(self.source_authority, 3),
            "freshness": round(self.freshness, 3),
        }


@dataclass
class RelevanceScore:
    """Complete relevance score with breakdown."""
    score: float
    breakdown: RelevanceBreakdown
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "relevance_score": round(self.score, 3),
            "score_breakdown": self.breakdown.to_dict(),
        }


class RelevanceScorer:
    """Computes relevance scores for search results."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bias_domains: Dict[str, float] = settings.get("scraping.bias_domains", {}) or {}
    
    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL."""
        try:
            return urlparse(url).netloc.lower().replace("www.", "")
        except Exception:
            return ""
    
    def _keyword_match_score(self, query: str, result: Dict[str, Any]) -> float:
        """Compute keyword overlap score (0-1)."""
        query_words = set(
            w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query.lower())
            if w.lower() not in {
                "the", "and", "for", "best", "top", "what", "how", "why",
                "which", "with", "from", "this", "that", "are", "was",
                "has", "can", "how", "you", "your", "our", "their",
                "will", "about", "into", "been", "was", "can"
            }
        )
        
        if not query_words:
            return 0.5
        
        # Combine title + snippet + displayed_link
        text = " ".join([
            result.get("title", ""),
            result.get("snippet", ""),
            result.get("displayed_link", ""),
        ]).lower()
        
        text_words = set(re.findall(r"[a-zA-Z]{3,}", text))
        
        if not text_words:
            return 0.0
        
        # Jaccard similarity
        intersection = query_words & text_words
        union = query_words | text_words
        
        return len(intersection) / len(union) if union else 0.0
    
    def _source_authority_score(self, url: str) -> float:
        """Compute source authority score (0-1)."""
        domain = self._extract_domain(url)
        
        # Check bias domains first (user-configured boost)
        for bias_domain, boost in self.bias_domains.items():
            if bias_domain in domain:
                return min(1.0, boost)
        
        # Check known high-authority domains
        for auth_domain, score in HIGH_AUTHORITY_DOMAINS.items():
            if auth_domain in domain:
                return score
        
        # Default for unknown domains
        return 0.5
    
    def _freshness_score(self, result: Dict[str, Any]) -> float:
        """Compute freshness score based on date (0-1)."""
        date_str = result.get("date")
        if not date_str:
            return 0.5  # Unknown date = neutral
        
        try:
            # Parse various date formats
            for fmt in [
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%d",
                "%b %d, %Y",
                "%d %b %Y",
            ]:
                try:
                    parsed = time.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue
            else:
                return 0.5
            
            result_time = time.mktime(parsed)
            now = time.time()
            age_days = (now - result_time) / 86400
            
            # Exponential decay: 1.0 at 0 days, 0.5 at ~30 days, ~0.1 at 1 year
            if age_days <= 1:
                return 1.0
            elif age_days <= 7:
                return 0.9
            elif age_days <= 30:
                return 0.7
            elif age_days <= 90:
                return 0.5
            elif age_days <= 365:
                return 0.3
            else:
                return 0.1
        except Exception:
            return 0.5
    
    def score(
        self,
        query: str,
        result: Dict[str, Any],
        semantic_similarity: float = 0.0
    ) -> RelevanceScore:
        """Compute full relevance score with breakdown."""
        keyword_match = self._keyword_match_score(query, result)
        source_authority = self._source_authority_score(result.get("link", ""))
        freshness = self._freshness_score(result)
        
        # Weighted combination
        weights = {
            "keyword_match": 0.35,
            "semantic_similarity": 0.35,
            "source_authority": 0.15,
            "freshness": 0.15,
        }
        
        score = (
            weights["keyword_match"] * keyword_match +
            weights["semantic_similarity"] * semantic_similarity +
            weights["source_authority"] * source_authority +
            weights["freshness"] * freshness
        )
        
        breakdown = RelevanceBreakdown(
            keyword_match=keyword_match,
            semantic_similarity=semantic_similarity,
            source_authority=source_authority,
            freshness=freshness,
        )
        
        return RelevanceScore(score=min(1.0, max(0.0, score)), breakdown=breakdown)
    
    def score_batch(
        self,
        query: str,
        results: List[Dict[str, Any]],
        semantic_scores: Optional[List[float]] = None
    ) -> List[RelevanceScore]:
        """Score multiple results efficiently."""
        if semantic_scores is None:
            semantic_scores = [0.0] * len(results)
        
        return [
            self.score(query, result, semantic_scores[i])
            for i, result in enumerate(results)
        ]


def get_domain_category(domain: str) -> Optional[str]:
    """Get category for a domain if known."""
    domain = domain.lower().replace("www.", "")
    for category, domains in DOMAIN_CATEGORIES.items():
        for d in domains:
            if d in domain:
                return category
    return None