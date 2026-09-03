"""Hybrid search orchestrator: keyword + semantic + RRF + rerank.

Combines multiple search engines, deduplicates, applies semantic similarity,
reciprocal rank fusion, and cross-encoder reranking.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from jiro.config import Settings
from jiro.log import get_logger
from jiro.models import SearchRequest, SearchResponse, OrganicResult
from jiro.scraping.engines import SearchOrchestrator as EngineOrchestrator
from jiro.cache import CacheManager
from jiro.search.embeddings import EmbeddingModel
from jiro.search.reranker import CrossEncoderReranker
from jiro.search.relevance import RelevanceScorer

log = get_logger("jiro.search.hybrid")


@dataclass
class SearchStageResult:
    """Results from a single search stage."""
    engine: str
    results: List[Dict[str, Any]]
    latency_ms: float
    cached: bool
    error: Optional[str] = None


class HybridSearcher:
    """Orchestrates hybrid search across multiple engines with semantic reranking."""
    
    def __init__(
        self,
        settings: Settings,
        engine_orchestrator: EngineOrchestrator,
        cache: CacheManager,
        semantic_cache: Any = None
    ) -> None:
        self.settings = settings
        self.engine_orchestrator = engine_orchestrator
        self.cache = cache
        self.semantic_cache = semantic_cache
        
        # Hybrid search config
        self.enabled = settings.get("search.hybrid.enabled", True)
        self.embedding_model = EmbeddingModel(settings)
        self.reranker = CrossEncoderReranker(settings)
        self.rrf_k = settings.get("search.hybrid.rrf_k", 60)
        self.rerank_top_n = settings.get("search.hybrid.rerank_top_n", 20)
        self.semantic_weight = settings.get("search.hybrid.semantic_weight", 0.4)
        self.keyword_weight = settings.get("search.hybrid.keyword_weight", 0.6)
        self.relevance_scorer = RelevanceScorer(settings)
    
    async def search(self, req: SearchRequest) -> SearchResponse:
        """Execute hybrid search."""
        if not self.enabled:
            # Fall back to single-engine search
            return await self.engine_orchestrator.search(req)
        
        started = time.perf_counter()
        
        # 1. Determine engines to use based on depth
        engines = self._get_engines_for_depth(req)
        
        # 2. Parallel keyword search across engines
        stage_results = await self._parallel_search(req, engines)
        
        # 3. Deduplicate and merge
        merged_results = self._deduplicate_merge(stage_results)
        
        if not merged_results:
            return self._empty_response(req, started)
        
        # 4. Semantic similarity scoring
        semantic_scores = await self._semantic_score(req.q, merged_results)
        
        # 5. Apply relevance scoring (keyword + semantic + authority + freshness)
        scored_results = self._apply_relevance_scoring(req.q, merged_results, semantic_scores)
        
        # 6. Reciprocal Rank Fusion
        rrf_results = self._rrf_fusion(scored_results, stage_results)
        
        # 7. Cross-encoder rerank top-N
        if self.reranker and len(rrf_results) > 1:
            reranked = await self.reranker.rerank(req.q, rrf_results, self.rerank_top_n)
        else:
            reranked = rrf_results
        
        # 8. Build final response
        return self._build_response(req, reranked, started, engines, stage_results)
    
    def _get_engines_for_depth(self, req: SearchRequest) -> List[str]:
        """Get engines based on search depth."""
        depth = getattr(req, "depth", "basic")
        
        depth_config = {
            "instant": 1,
            "fast": 1,
            "basic": 1,
            "advanced": 3,
            "deep": 5,
        }
        
        num_engines = depth_config.get(depth, 1)
        
        # Use fallback order
        fallback = self.settings.fallback_order
        available = [e for e in fallback if e in self.settings.engines]
        
        return available[:num_engines]
    
    async def _parallel_search(
        self,
        req: SearchRequest,
        engines: List[str]
    ) -> List[SearchStageResult]:
        """Search across multiple engines in parallel."""
        
        async def search_engine(engine: str) -> SearchStageResult:
            engine_started = time.perf_counter()
            try:
                # Create request for this engine
                engine_req = SearchRequest(
                    q=req.q,
                    engine=engine,
                    type=req.type,
                    location=req.location,
                    language=req.language,
                    num=req.num,
                    start=req.start,
                    safe=req.safe,
                    time_range=req.time_range,
                    device=req.device,
                    gl=req.gl,
                    hl=req.hl,
                    fresh=req.fresh,
                )
                
                result = await self.engine_orchestrator.search(engine_req)
                elapsed = (time.perf_counter() - engine_started) * 1000
                
                organic = []
                for r in result.organic_results:
                    organic.append({
                        "title": r.title,
                        "link": r.link,
                        "displayed_link": r.displayed_link,
                        "snippet": r.snippet,
                        "date": r.date,
                        "source": r.source,
                        "sitelinks": r.sitelinks,
                        "rich_snippet": r.rich_snippet,
                        "thumbnail": r.thumbnail,
                        "engine": engine,
                    })
                
                return SearchStageResult(
                    engine=engine,
                    results=organic,
                    latency_ms=elapsed,
                    cached=result.search_metadata.get("cached", False),
                )
            except Exception as exc:
                elapsed = (time.perf_counter() - engine_started) * 1000
                log.warning("engine search failed", extra={"engine": engine, "error": str(exc)})
                return SearchStageResult(
                    engine=engine,
                    results=[],
                    latency_ms=elapsed,
                    cached=False,
                    error=str(exc),
                )
        
        tasks = [search_engine(eng) for eng in engines]
        return await asyncio.gather(*tasks)
    
    def _deduplicate_merge(self, stage_results: List[SearchStageResult]) -> List[Dict[str, Any]]:
        """Deduplicate results across engines by URL."""
        seen: Set[str] = set()
        merged: List[Dict[str, Any]] = []
        
        for stage in stage_results:
            if stage.error:
                continue
            for result in stage.results:
                url = result.get("link", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                merged.append(result)
        
        return merged
    
    async def _semantic_score(
        self,
        query: str,
        results: List[Dict[str, Any]]
    ) -> List[float]:
        """Compute semantic similarity scores."""
        if not results:
            return []
        
        # Check semantic cache first
        if self.semantic_cache:
            cached = await self.semantic_cache.find_similar(query, results)
            if cached is not None:
                return cached
        
        # Compute embeddings
        texts = [
            f"{r.get('title', '')} {r.get('snippet', '')}".strip()
            for r in results
        ]
        
        try:
            scores = await self.embedding_model.score(query, results)
            # Cache for future
            if self.semantic_cache:
                await self.semantic_cache.store_scores(query, results, scores)
            return scores
        except Exception as exc:
            log.warning("semantic scoring failed", extra={"error": str(exc)})
            return [0.0] * len(results)
    
    def _apply_relevance_scoring(
        self,
        query: str,
        results: List[Dict[str, Any]],
        semantic_scores: List[float]
    ) -> List[Dict[str, Any]]:
        """Apply full relevance scoring with breakdown."""
        scored = self.relevance_scorer.score_batch(query, results, semantic_scores)
        
        for i, result in enumerate(results):
            result["relevance"] = scored[i].to_dict()
        
        return results
    
    def _rrf_fusion(
        self,
        results: List[Dict[str, Any]],
        stage_results: List[SearchStageResult]
    ) -> List[Dict[str, Any]]:
        """Reciprocal Rank Fusion across engines."""
        # Build rank maps per engine
        engine_ranks: Dict[str, Dict[str, int]] = {}
        for stage in stage_results:
            if stage.error:
                continue
            ranks = {}
            for rank, result in enumerate(stage.results):
                url = result.get("link", "")
                if url:
                    ranks[url] = rank + 1  # 1-indexed
            engine_ranks[stage.engine] = ranks
        
        # Compute RRF score for each result
        for result in results:
            url = result.get("link", "")
            rrf_score = 0.0
            for engine, ranks in engine_ranks.items():
                if url in ranks:
                    rrf_score += 1.0 / (self.rrf_k + ranks[url])
            result["rrf_score"] = rrf_score
        
        # Sort by RRF score
        return sorted(results, key=lambda r: r.get("rrf_score", 0.0), reverse=True)
    
    def _build_response(
        self,
        req: SearchRequest,
        results: List[Dict[str, Any]],
        started: float,
        engines: List[str],
        stage_results: List[SearchStageResult]
    ) -> SearchResponse:
        """Build final SearchResponse."""
        # Convert to OrganicResult models
        organic_results = []
        for i, r in enumerate(results[:req.num]):
            organic_results.append(OrganicResult(
                position=i + 1,
                title=r.get("title", ""),
                link=r.get("link", ""),
                displayed_link=r.get("displayed_link", ""),
                snippet=r.get("snippet", ""),
                date=r.get("date"),
                source=r.get("source"),
                sitelinks=r.get("sitelinks", []),
                rich_snippet=r.get("rich_snippet", {}),
                thumbnail=r.get("thumbnail"),
            ))
        
        total_time = (time.perf_counter() - started) * 1000
        
        search_metadata = {
            "id": f"hybrid-{int(started * 1000)}",
            "engine": "hybrid",
            "query": req.q,
            "type": req.type,
            "location": req.location,
            "language": req.language,
            "status": "success",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_time_taken": round(total_time, 3),
            "cached": False,
            "engines_used": engines,
            "engine_details": [
                {
                    "engine": sr.engine,
                    "results": len(sr.results),
                    "latency_ms": round(sr.latency_ms, 1),
                    "cached": sr.cached,
                    "error": sr.error,
                }
                for sr in stage_results
            ],
            "search_mode": "hybrid",
        }
        
        return SearchResponse(
            search_metadata=search_metadata,
            search_information={"total_results": len(results)},
            organic_results=organic_results,
        )
    
    def _empty_response(self, req: SearchRequest, started: float) -> SearchResponse:
        """Return empty response."""
        total_time = (time.perf_counter() - started) * 1000
        return SearchResponse(
            search_metadata={
                "engine": "hybrid",
                "query": req.q,
                "status": "success",
                "total_time_taken": round(total_time, 3),
                "cached": False,
                "engines_used": [],
            },
            search_information={"total_results": 0},
            organic_results=[],
        )