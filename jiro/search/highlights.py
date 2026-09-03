"""Content highlights extraction for token-efficient result excerpts.

Extracts relevant snippets from search results, optimized for token efficiency.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.search.highlights")


class HighlightExtractor:
    """Extracts relevant highlights from search results."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.max_chars = settings.get("search.highlights.max_characters", 500)
        self.max_highlights_per_result = settings.get("search.highlights.max_per_result", 3)
    
    def extract(
        self,
        query: str,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Extract highlights for all results."""
        query_terms = self._extract_query_terms(query)
        
        for result in results:
            highlights = self._extract_highlights(query_terms, result)
            result["highlights"] = highlights
        
        return results
    
    def _extract_query_terms(self, query: str) -> List[str]:
        """Extract meaningful terms from query."""
        # Remove stop words
        stopwords = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
            "for", "of", "with", "by", "from", "as", "is", "was", "are",
            "were", "been", "be", "have", "has", "had", "do", "does", "did",
            "will", "would", "could", "should", "may", "might", "must",
            "what", "who", "where", "when", "why", "how", "which", "that",
            "this", "these", "those", "best", "top", "good", "great", "new",
            "latest", "2024", "2025", "2026", "guide", "tutorial", "howto",
        }
        
        terms = re.findall(r"[a-zA-Z0-9\-]{3,}", query.lower())
        return [t for t in terms if t not in stopwords]
    
    def _extract_highlights(
        self,
        query_terms: List[str],
        result: Dict[str, Any]
    ) -> List[str]:
        """Extract highlight snippets from a single result."""
        # Combine all text fields
        text_parts = []
        for field in ["title", "snippet", "displayed_link"]:
            val = result.get(field, "")
            if val:
                text_parts.append(val)
        
        full_text = " ".join(text_parts)
        
        if not full_text or not query_terms:
            return []
        
        # Split into sentences
        sentences = re.split(r"(?<=[.!?])\s+", full_text)
        
        # Score sentences by query term overlap
        scored = []
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 20:
                continue
            
            score = 0
            sent_lower = sent.lower()
            for term in query_terms:
                # Count occurrences
                score += sent_lower.count(term) * 2
                # Bonus for word boundary matches
                if re.search(rf"\b{re.escape(term)}\b", sent_lower):
                    score += 3
            
            if score > 0:
                scored.append((score, sent))
        
        # Sort by score and take top
        scored.sort(key=lambda x: x[0], reverse=True)
        highlights = [s for _, s in scored[:self.max_highlights_per_result]]
        
        # Truncate to max_chars total
        total_chars = 0
        final = []
        for h in highlights:
            if total_chars + len(h) > self.max_chars:
                # Truncate this highlight
                remaining = self.max_chars - total_chars
                if remaining > 50:
                    final.append(h[:remaining] + "...")
                break
            final.append(h)
            total_chars += len(h)
        
        return final


def extract_highlights_from_content(
    query: str,
    content: str,
    max_chars: int = 500,
    max_snippets: int = 3
) -> List[str]:
    """Extract highlights from full page content (for scraped pages)."""
    if not content or not query:
        return []
    
    query_terms = re.findall(r"[a-zA-Z0-9\-]{3,}", query.lower())
    stopwords = {"the", "and", "for", "best", "top", "what", "how", "why", "which"}
    query_terms = [t for t in query_terms if t not in stopwords]
    
    if not query_terms:
        return [content[:max_chars]]
    
    # Split into paragraphs
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    
    scored = []
    for para in paragraphs:
        if len(para) < 50:
            continue
        
        score = 0
        para_lower = para.lower()
        for term in query_terms:
            score += para_lower.count(term) * 2
            if re.search(rf"\b{re.escape(term)}\b", para_lower):
                score += 3
        
        if score > 0:
            scored.append((score, para))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    
    highlights = []
    total = 0
    for _, para in scored[:max_snippets]:
        if total + len(para) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                highlights.append(para[:remaining] + "...")
            break
        highlights.append(para)
        total += len(para)
    
    return highlights