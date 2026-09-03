"""Multi-query search: break, parallelize, merge, deduplicate, rerank."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from jiro.config import Settings
from jiro.log import get_logger
from jiro.models import SearchRequest, SearchResponse, OrganicResult
from jiro.scraping.engines import SearchOrchestrator as EngineOrchestrator
from jiro.cache import CacheManager
from jiro.search.hybrid import HybridSearcher
from jiro.search.relevance import RelevanceScorer

log = get_logger("jiro.search.multiquery")


@dataclass
class MultiQueryRequest:
    """Request for multi-query search."""
    queries: List[str]
    merge: bool = True
    deduplicate: bool = True
    rerank: bool = True
    max_results: int = 20
    depth: str = "basic"
    engine: str = "auto"


class MultiQuerySearcher:
    """Orchestrates multi-query search."""
    
    def __init__(
        self,
        settings: Settings,
        engine_orchestrator: EngineOrchestrator,
        cache: CacheManager,
        hybrid_searcher: Optional[HybridSearcher] = None
    ) -> None:
        self.settings = settings
        self.engine_orchestrator = engine_orchestrator
        self.cache = cache
        self.hybrid_searcher = hybrid_searcher
        self.relevance_scorer = RelevanceScorer(settings)
    
    async def search(self, req: MultiQueryRequest) -> SearchResponse:
        """Execute multi-query search."""
        if not req.queries:
            return self._empty_response()
        
        # 1. Run each query in parallel
        query_results = await self._parallel_search(req)
        
        # 2. Merge and deduplicate
        if req.merge:
            merged = self._merge_results(query_results)
        else:
            merged = [r for qr in query_results for r in qr]
        
        if req.deduplicate:
            merged = self._deduplicate(merged)
        
        # 3. Rerank if requested
        if req.rerank and len(merged) > 1:
            # Use relevance scoring for reranking
            all_queries = " ".join(req.queries)
            merged = self._rerank_merged(all_queries, merged)
        
        # 4. Limit results
        merged = merged[:req.max_results]
        
        # 5. Build response
        return self._build_response(req, merged)
    
    async def _parallel_search(self, req: MultiQueryRequest) -> List[List[Dict[str, Any]]]:
        """Run all queries in parallel."""
        
        async def search_one(query: str) -> List[Dict[str, Any]]:
            if self.hybrid_searcher and req.depth in ("advanced", "deep"):
                search_req = SearchRequest(
                    q=query,
                    engine=req.engine,
                    num=req.max_results,
                    depth=req.depth,
                )
                result = await self.hybrid_searcher.search(search_req)
            else:
                search_req = SearchRequest(
                    q=query,
                    engine=req.engine,
                    num=req.max_results,
                )
                result = await self.engine_orchestrator.search(search_req)
            
            # Convert to dict format
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
                    "query": query,
                })
            return organic
        
        tasks = [search_one(q) for q in req.queries]
        return await asyncio.gather(*tasks)
    
    def _merge_results(self, query_results: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Merge results preserving query provenance."""
        merged = []
        for results in query_results:
            merged.extend(results)
        return merged
    
    def _deduplicate(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate by URL, preserving highest-ranked occurrence."""
        seen: Dict[str, Dict[str, Any]] = {}
        
        for result in results:
            url = result.get("link", "")
            if not url:
                continue
            
            if url not in seen:
                seen[url] = result
            else:
                # Merge query info
                existing_queries = seen[url].get("query", "")
                new_query = result.get("query", "")
                if new_query and new_query not in existing_queries:
                    seen[url]["query"] = f"{existing_queries}; {new_query}"
        
        return list(seen.values())
    
    def _rerank_merged(self, combined_query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rerank merged results by relevance to all queries."""
        if not results:
            return results
        
        scored = self.relevance_scorer.score_batch(combined_query, results)
        
        for i, result in enumerate(results):
            result["relevance"] = scored[i].to_dict()
        
        return sorted(results, key=lambda r: r.get("relevance", {}).get("relevance_score", 0), reverse=True)
    
    def _build_response(self, req: MultiQueryRequest, results: List[Dict[str, Any]]) -> SearchResponse:
        """Build SearchResponse from merged results."""
        organic_results = []
        for i, r in enumerate(results):
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
        
        return SearchResponse(
            search_metadata={
                "engine": "multi-query",
                "queries": req.queries,
                "status": "success",
                "total_time_taken": 0,  # Set by caller
                "cached": False,
                "search_mode": "multi-query",
            },
            search_information={"total_results": len(results)},
            organic_results=organic_results,
        )
    
    def _empty_response(self) -> SearchResponse:
        return SearchResponse(
            search_metadata={"engine": "multi-query", "status": "success"},
            search_information={"total_results": 0},
            organic_results=[],
        )


def generate_sub_queries(query: str, max_queries: int = 3) -> List[str]:
    """Generate sub-queries from a complex query (for agentic research)."""
    # Simple heuristic: split on comparison/question words
    import re
    
    # Common patterns that suggest sub-queries
    patterns = [
        (r"\b(vs|versus|compare|comparison)\b", lambda m: [m.string]),
        (r"\b(best|top|recommended)\b", lambda m: [f"{m.string} review", f"{m.string} comparison"]),
        (r"\b(how to|guide|tutorial)\b", lambda m: [f"{m.string} steps", f"{m.string} example"]),
        (r"\b(what is|define|definition)\b", lambda m: [f"{m.string} explained", f"{m.string} examples"]),
    ]
    
    sub_queries = [query]
    
    for pattern, generator in patterns:
        matches = list(re.finditer(pattern, query, re.IGNORECASE))
        if matches:
            for match in matches[:2]:
                sub_queries.extend(generator(match))
    
    # Deduplicate
    seen = set()
    unique = []
    for q in sub_queries:
        ql = q.lower().strip()
        if ql not in seen and len(ql) > 5:
            seen.add(ql)
            unique.append(q)
    
    return unique[:max_queries]