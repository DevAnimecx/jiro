"""Answer synthesis for search results.

Supports both extractive (zero-LLM-cost) and LLM-powered synthesis.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from jiro.ai.llm import LLM
from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.search.answer")


@dataclass
class AnswerResult:
    """Synthesized answer with citations."""
    answer: str
    citations: List[Dict[str, Any]]
    provider: str
    model: Optional[str]
    confidence: float
    mode: str  # "extractive" | "llm"


class AnswerSynthesizer:
    """Synthesizes answers from search results."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm = LLM(settings)
        self.max_sources = settings.get("search.answer.max_sources", 5)
        self.max_snippet_chars = settings.get("search.answer.max_snippet_chars", 600)
    
    async def synthesize(
        self,
        query: str,
        results: List[Dict[str, Any]],
        mode: str = "auto"  # "extractive" | "llm" | "auto"
    ) -> AnswerResult:
        """Synthesize answer from search results."""
        # Prepare sources (top N with content)
        sources = self._prepare_sources(results)
        
        if not sources:
            return AnswerResult(
                answer="No relevant sources found to answer the question.",
                citations=[],
                provider="none",
                model=None,
                confidence=0.0,
                mode=mode,
            )
        
        # Determine synthesis mode
        if mode == "auto":
            mode = "llm" if self.llm.available else "extractive"
        
        if mode == "llm" and self.llm.available:
            return await self._synthesize_llm(query, sources)
        else:
            return self._synthesize_extractive(query, sources)
    
    def _prepare_sources(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Prepare top sources for synthesis."""
        sources = []
        for r in results[:self.max_sources]:
            # Prefer scraped content, fall back to snippet
            content = r.get("content", "") or r.get("snippet", "")
            if content:
                sources.append({
                    "title": r.get("title", ""),
                    "url": r.get("link", ""),
                    "snippet": r.get("snippet", "")[:400],
                    "content": content[:3000],
                })
        return sources
    
    def _synthesize_extractive(
        self,
        query: str,
        sources: List[Dict[str, Any]]
    ) -> AnswerResult:
        """Extractive synthesis without LLM (zero cost, fast)."""
        # Build answer from most relevant snippets
        answer_parts = []
        citations = []
        
        for i, src in enumerate(sources, 1):
            snippet = src.get("snippet", "")
            content = src.get("content", "")
            
            # Use the longer of snippet or content excerpt
            text = content if len(content) > len(snippet) else snippet
            text = re.sub(r"\s+", " ", text).strip()
            
            if not text:
                continue
            
            # Truncate
            text = text[:self.max_snippet_chars]
            
            answer_parts.append(f"[{i}] {text}")
            citations.append({
                "title": src["title"],
                "url": src["url"],
                "snippet": src["snippet"][:200],
            })
        
        if not answer_parts:
            return AnswerResult(
                answer="Could not extract relevant information from sources.",
                citations=[],
                provider="extractive-fallback",
                model=None,
                confidence=0.2,
                mode="extractive",
            )
        
        answer = "\n\n".join(answer_parts)
        
        # Add a summary header
        if len(sources) > 1:
            answer = f"Based on {len(sources)} sources:\n\n{answer}"
        
        return AnswerResult(
            answer=answer,
            citations=citations,
            provider="extractive-fallback",
            model=None,
            confidence=0.7,
            mode="extractive",
        )
    
    async def _synthesize_llm(
        self,
        query: str,
        sources: List[Dict[str, Any]]
    ) -> AnswerResult:
        """LLM-powered synthesis with citations."""
        try:
            context = self._build_context(sources)
            
            system = (
                "You are Jiro, a precise research assistant. Answer the question "
                "using ONLY the web excerpts below. Use numbered citations like [1], [2] "
                "referring to the source list. Say when sources are insufficient. "
                "Be concise and factual."
            )
            
            user = (
                f"Question: {query}\n\n"
                f"Sources:\n{context}\n\n"
                f"Answer with citations [n]."
            )
            
            answer = await asyncio.wait_for(
                self.llm.complete([{"role": "user", "content": user}], system=system),
                timeout=30.0
            )
            
            citations = [
                {"title": s["title"], "url": s["url"], "snippet": s["snippet"][:200]}
                for s in sources
            ]
            
            return AnswerResult(
                answer=answer,
                citations=citations,
                provider=self.llm.provider_name,
                model=self.llm.model,
                confidence=0.9,
                mode="llm",
            )
        except Exception as exc:
            log.warning("LLM synthesis failed, falling back to extractive", extra={"error": str(exc)})
            return self._synthesize_extractive(query, sources)
    
    def _build_context(self, sources: List[Dict[str, Any]]) -> str:
        """Build context string for LLM."""
        blocks = []
        for i, src in enumerate(sources, 1):
            snippet = re.sub(r"\s+", " ", src.get("snippet", ""))[:400]
            content = re.sub(r"\s+", " ", src.get("content", ""))[:self.max_snippet_chars]
            blocks.append(
                f"[{i}] {src['title']}\n"
                f"URL: {src['url']}\n"
                f"Snippet: {snippet}\n"
                f"Excerpt: {content}"
            )
        return "\n\n".join(blocks)