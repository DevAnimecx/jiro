"""Search filters: domain, date, category filtering."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.search.filters")


@dataclass
class FilterConfig:
    """Configuration for search filters."""
    include_domains: Optional[List[str]] = None
    exclude_domains: Optional[List[str]] = None
    bias_domains: Optional[Dict[str, float]] = None
    time_range: Optional[str] = None  # day, week, month, year
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None    # YYYY-MM-DD
    category: Optional[str] = None    # publication, financial_report, people, shopping, github, news


# Category to engine mapping
CATEGORY_ENGINES = {
    "publication": ["google_scholar", "arxiv", "pubmed", "google"],
    "financial_report": ["sec", "google", "bing"],
    "people": ["linkedin", "google", "bing"],
    "shopping": ["google", "amazon", "ebay", "bing"],
    "github": ["github", "google", "bing"],
    "news": ["google", "bing", "brave", "duckduckgo"],
    "code": ["github", "google", "bing"],
    "academic": ["google_scholar", "arxiv", "pubmed", "google"],
    "social": ["twitter", "reddit", "linkedin", "facebook"],
    "video": ["youtube", "google", "bing", "brave"],
}


# Domain categories for category filtering
DOMAIN_CATEGORIES = {
    "publication": {
        "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "scholar.google.com",
        "ieee.org", "acm.org", "springer.com", "elsevier.com",
        "nature.com", "science.org", "plos.org", "biorxiv.org",
    },
    "financial_report": {
        "sec.gov", "investor.", "ir.", "finance.yahoo.com",
        "bloomberg.com", "reuters.com", "wsj.com", "ft.com",
    },
    "people": {
        "linkedin.com", "twitter.com", "x.com", "github.com",
        "scholar.google.com", "orcid.org",
    },
    "shopping": {
        "amazon.com", "ebay.com", "walmart.com", "target.com",
        "bestbuy.com", "newegg.com", "etsy.com", "shopify.com",
    },
    "github": {
        "github.com", "gitlab.com", "bitbucket.org", "github.io",
    },
    "news": {
        "reuters.com", "apnews.com", "bbc.com", "nytimes.com",
        "wsj.com", "ft.com", "economist.com", "cnn.com",
        "theguardian.com", "washingtonpost.com", "npr.org",
    },
    "code": {
        "github.com", "gitlab.com", "stackoverflow.com", "npmjs.com",
        "pypi.org", "crates.io", "packagist.org", "rubygems.org",
    },
}


class SearchFilter:
    """Applies filters to search results."""
    
    def __init__(self, config: FilterConfig, settings: Settings) -> None:
        self.config = config
        self.settings = settings
        self._normalize_config()
    
    def _normalize_config(self) -> None:
        """Normalize domain lists."""
        for attr in ["include_domains", "exclude_domains"]:
            val = getattr(self.config, attr)
            if val:
                setattr(self.config, attr, [d.lower().replace("www.", "") for d in val])
        
        if self.config.bias_domains:
            normalized = {}
            for domain, boost in self.config.bias_domains.items():
                normalized[domain.lower().replace("www.", "")] = boost
            self.config.bias_domains = normalized
    
    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL."""
        try:
            return urlparse(url).netloc.lower().replace("www.", "")
        except Exception:
            return ""
    
    def _domain_matches(self, url: str, domains: List[str]) -> bool:
        """Check if URL domain matches any in list."""
        domain = self._extract_domain(url)
        for d in domains:
            if d in domain:
                return True
        return False
    
    def _check_date_range(self, date_str: Optional[str]) -> bool:
        """Check if date falls within configured range."""
        if not date_str:
            return True  # No date = include
        
        try:
            # Parse date
            for fmt in [
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%d",
                "%b %d, %Y",
                "%d %b %Y",
                "%B %d, %Y",
            ]:
                try:
                    parsed = time.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue
            else:
                return True  # Can't parse = include
            
            result_time = time.mktime(parsed)
            
            # Check time_range (relative)
            if self.config.time_range:
                now = time.time()
                age_days = (now - result_time) / 86400
                
                if self.config.time_range == "day" and age_days > 1:
                    return False
                elif self.config.time_range == "week" and age_days > 7:
                    return False
                elif self.config.time_range == "month" and age_days > 30:
                    return False
                elif self.config.time_range == "year" and age_days > 365:
                    return False
            
            # Check absolute date range
            if self.config.start_date:
                start = time.strptime(self.config.start_date, "%Y-%m-%d")
                if result_time < time.mktime(start):
                    return False
            
            if self.config.end_date:
                end = time.strptime(self.config.end_date, "%Y-%m-%d")
                if result_time > time.mktime(end) + 86400:  # End of day
                    return False
            
            return True
        except Exception:
            return True  # On error, include
    
    def _check_category(self, result: Dict[str, Any]) -> bool:
        """Check if result matches category."""
        if not self.config.category:
            return True
        
        category = self.config.category.lower()
        
        # Check domain category
        url = result.get("link", "")
        domain = self._extract_domain(url)
        
        if category in DOMAIN_CATEGORIES:
            for cat_domain in DOMAIN_CATEGORIES[category]:
                if cat_domain in domain:
                    return True
        
        # Check snippet/title for category keywords
        text = f"{result.get('title', '')} {result.get('snippet', '')}".lower()
        
        category_keywords = {
            "publication": ["paper", "study", "research", "journal", "doi", "arxiv", "pubmed"],
            "financial_report": ["earnings", "revenue", "quarterly", "annual report", "sec filing", "10-k", "10-q"],
            "people": ["profile", "biography", "linkedin", "experience", "education"],
            "shopping": ["price", "buy", "shop", "product", "review", "rating", "$"],
            "github": ["repository", "repo", "commit", "pull request", "issue", "github"],
            "news": ["breaking", "latest", "news", "report", "announced", "today"],
        }
        
        if category in category_keywords:
            for kw in category_keywords[category]:
                if kw in text:
                    return True
        
        return False
    
    def filter(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply all filters to results."""
        filtered = []
        
        for result in results:
            url = result.get("link", "")
            
            # Include domains filter
            if self.config.include_domains:
                if not self._domain_matches(url, self.config.include_domains):
                    continue
            
            # Exclude domains filter
            if self.config.exclude_domains:
                if self._domain_matches(url, self.config.exclude_domains):
                    continue
            
            # Date range filter
            if not self._check_date_range(result.get("date")):
                continue
            
            # Category filter
            if not self._check_category(result):
                continue
            
            # Apply domain bias (boost score)
            if self.config.bias_domains:
                domain = self._extract_domain(url)
                for bias_domain, boost in self.config.bias_domains.items():
                    if bias_domain in domain:
                        # Boost the relevance score if present
                        if "relevance" in result and "relevance_score" in result["relevance"]:
                            result["relevance"]["relevance_score"] = min(
                                1.0, result["relevance"]["relevance_score"] * boost
                            )
                        break
            
            filtered.append(result)
        
        return filtered


def get_engines_for_category(category: str, settings: Settings) -> List[str]:
    """Get recommended engines for a category."""
    category = category.lower()
    engines = CATEGORY_ENGINES.get(category, [])
    
    # Filter to only configured engines
    configured = set(settings.engines)
    return [e for e in engines if e in configured]


def build_filter_config(req: Any) -> FilterConfig:
    """Build FilterConfig from request object."""
    return FilterConfig(
        include_domains=getattr(req, "include_domains", None),
        exclude_domains=getattr(req, "exclude_domains", None),
        bias_domains=getattr(req, "bias_domains", None),
        time_range=getattr(req, "time_range", None),
        start_date=getattr(req, "start_date", None),
        end_date=getattr(req, "end_date", None),
        category=getattr(req, "category", None),
    )