"""Search plugin examples - post-processing plugins for search results."""

from __future__ import annotations

from typing import Any, Dict, List

from jiro.plugins import BaseSearchPlugin, search_plugin_registry
from jiro.config import Settings


class RerankerPlugin(BaseSearchPlugin):
    """Reranks search results using a custom algorithm."""
    
    name = "custom_reranker"
    type = "search"
    version = "1.0"
    author = "Jiro Team"
    description = "Custom reranker for search results"
    priority = 10  # Run early
    
    async def process(
        self,
        query: str,
        results: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Apply custom reranking logic."""
        # Example: boost results from preferred domains
        preferred_domains = self.config.get("preferred_domains", {})
        
        for result in results:
            url = result.get("link", "")
            for domain, boost in preferred_domains.items():
                if domain in url:
                    score = result.get("score", 1.0)
                    result["score"] = score * boost
                    break
        
        # Re-sort by score
        return sorted(results, key=lambda r: r.get("score", 0), reverse=True)


class DeduplicatorPlugin(BaseSearchPlugin):
    """Removes duplicate results based on URL similarity."""
    
    name = "deduplicator"
    type = "search"
    version = "1.0"
    author = "Jiro Team"
    description = "Deduplicates search results by URL"
    priority = 5  # Run first
    
    async def process(
        self,
        query: str,
        results: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Remove duplicate URLs."""
        seen = set()
        unique = []
        
        for result in results:
            url = result.get("link", "").rstrip("/")
            if url and url not in seen:
                seen.add(url)
                unique.append(result)
        
        return unique


class DomainFilterPlugin(BaseSearchPlugin):
    """Filters results by domain include/exclude lists."""
    
    name = "domain_filter"
    type = "search"
    version = "1.0"
    author = "Jiro Team"
    description = "Filters results by domain include/exclude lists"
    priority = 15
    
    async def process(
        self,
        query: str,
        results: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Filter by domain lists."""
        include_domains = set(self.config.get("include_domains", []))
        exclude_domains = set(self.config.get("exclude_domains", []))
        
        filtered = []
        for result in results:
            url = result.get("link", "")
            if not url:
                filtered.append(result)
                continue
            
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower().replace("www.", "")
            
            # Check exclude first
            if exclude_domains and any(ex in domain for ex in exclude_domains):
                continue
            
            # Check include
            if include_domains and not any(inc in domain for inc in include_domains):
                continue
            
            filtered.append(result)
        
        return filtered


class FreshnessBoostPlugin(BaseSearchPlugin):
    """Boosts recent results."""
    
    name = "freshness_boost"
    type = "search"
    version = "1.0"
    author = "Jiro Team"
    description = "Boosts recent results based on date"
    priority = 20
    
    async def process(
        self,
        query: str,
        results: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Boost recent results."""
        import time
        
        now = time.time()
        decay_days = self.config.get("decay_days", 365)
        
        for result in results:
            date_str = result.get("date", "")
            if not date_str:
                continue
            
            try:
                # Try parsing various date formats
                for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%b %d, %Y"]:
                    try:
                        import time as t
                        parsed = t.strptime(date_str, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    continue
                
                result_time = time.mktime(parsed)
                age_days = (now - result_time) / 86400
                
                if age_days <= 1:
                    boost = 1.5
                elif age_days <= 7:
                    boost = 1.3
                elif age_days <= 30:
                    boost = 1.1
                elif age_days <= decay_days:
                    boost = 1.0
                else:
                    boost = 0.8
                
                score = result.get("score", 1.0)
                result["score"] = score * boost
                
            except Exception:
                pass
        
        return sorted(results, key=lambda r: r.get("score", 0), reverse=True)


class SourceAuthorityPlugin(BaseSearchPlugin):
    """Boosts results from authoritative sources."""
    
    name = "source_authority"
    type = "search"
    version = "1.0"
    author = "Jiro Team"
    description = "Boosts results from authoritative domains"
    priority = 25
    
    # Known high-authority domains
    AUTHORITY_DOMAINS = {
        "wikipedia.org": 1.5,
        "github.com": 1.4,
        "stackoverflow.com": 1.4,
        "arxiv.org": 1.4,
        "pubmed.ncbi.nlm.nih.gov": 1.4,
        "scholar.google.com": 1.4,
        "nature.com": 1.3,
        "science.org": 1.3,
        "ieee.org": 1.3,
        "acm.org": 1.3,
        "reuters.com": 1.3,
        "apnews.com": 1.3,
        "bbc.com": 1.2,
        "nytimes.com": 1.2,
        "wsj.com": 1.2,
    }
    
    async def process(
        self,
        query: str,
        results: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Boost authoritative sources."""
        custom_domains = self.config.get("custom_domains", {})
        authority = {**self.AUTHORITY_DOMAINS, **custom_domains}
        
        for result in results:
            url = result.get("link", "")
            if not url:
                continue
            
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower().replace("www.", "")
            
            for auth_domain, boost in authority.items():
                if auth_domain in domain:
                    score = result.get("score", 1.0)
                    result["score"] = score * boost
                    break
        
        return sorted(results, key=lambda r: r.get("score", 0), reverse=True)


class SnippetEnricherPlugin(BaseSearchPlugin):
    """Enriches results with additional snippet information."""
    
    name = "snippet_enricher"
    type = "search"
    version = "1.0"
    author = "Jiro Team"
    description = "Enriches snippets with additional context"
    priority = 30
    
    async def process(
        self,
        query: str,
        results: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Enrich snippets with query term highlights."""
        query_terms = [t.lower() for t in query.split() if len(t) > 2]
        
        for result in results:
            snippet = result.get("snippet", "")
            if not snippet:
                continue
            
            # Highlight query terms
            for term in query_terms:
                import re
                pattern = re.compile(re.escape(term), re.IGNORECASE)
                snippet = pattern.sub(f"**{term}**", snippet)
            
            result["enriched_snippet"] = snippet
        
        return results


# Register all search plugins
from jiro.plugins import search_plugin_registry
search_plugin_registry.register(RerankerPlugin)
search_plugin_registry.register(DeduplicatorPlugin)
search_plugin_registry.register(DomainFilterPlugin)
search_plugin_registry.register(FreshnessBoostPlugin)
search_plugin_registry.register(SourceAuthorityPlugin)
search_plugin_registry.register(SnippetEnricherPlugin)