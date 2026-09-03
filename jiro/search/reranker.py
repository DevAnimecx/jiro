"""Cross-encoder reranker for precision ranking.

Lazy-loads cross-encoder model (ms-marco-MiniLM-L-6-v2, ~80MB, CPU).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import numpy as np

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.search.reranker")

_RERANKER_MODEL: Optional[Any] = None
_RERANKER_MODEL_NAME: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_RERANKER_LOCK = asyncio.Lock()


async def get_reranker_model(model_name: Optional[str] = None) -> Any:
    """Get or create the cross-encoder reranker model (lazy-loaded)."""
    global _RERANKER_MODEL, _RERANKER_MODEL_NAME
    
    if model_name:
        _RERANKER_MODEL_NAME = model_name
    
    if _RERANKER_MODEL is not None:
        return _RERANKER_MODEL
    
    async with _RERANKER_LOCK:
        if _RERANKER_MODEL is not None:
            return _RERANKER_MODEL
        
        log.info("loading reranker model", extra={"model": _RERANKER_MODEL_NAME})
        
        def _load():
            try:
                from sentence_transformers import CrossEncoder
                model = CrossEncoder(_RERANKER_MODEL_NAME)
                return model
            except ImportError:
                log.warning("sentence-transformers not installed, reranking disabled")
                return None
            except Exception as exc:
                log.error("failed to load reranker model", extra={"error": str(exc)})
                return None
        
        loop = asyncio.get_running_loop()
        _RERANKER_MODEL = await loop.run_in_executor(None, _load)
        
        if _RERANKER_MODEL is not None:
            log.info("reranker model loaded", extra={"model": _RERANKER_MODEL_NAME})
        else:
            log.warning("reranker model unavailable")
        
        return _RERANKER_MODEL


async def rerank(
    query: str,
    results: List[Dict[str, Any]],
    model_name: Optional[str] = None,
    top_n: Optional[int] = None,
    batch_size: int = 32
) -> List[Dict[str, Any]]:
    """Rerank results using cross-encoder.
    
    Args:
        query: Search query
        results: List of result dicts with 'title' and 'snippet' keys
        model_name: Optional model override
        top_n: Number of top results to rerank (default: all)
        batch_size: Batch size for encoding
    
    Returns:
        Reranked results with 'rerank_score' added
    """
    model = await get_reranker_model(model_name)
    if model is None or not results:
        return results
    
    # Limit to top_n
    to_rerank = results[:top_n] if top_n else results
    
    def _rerank():
        pairs = [
            (query, f"{r.get('title', '')} {r.get('snippet', '')}".strip())
            for r in to_rerank
        ]
        scores = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
        return scores
    
    loop = asyncio.get_running_loop()
    scores = await loop.run_in_executor(None, _rerank)
    
    # Add scores and sort
    for i, result in enumerate(to_rerank):
        result["rerank_score"] = float(scores[i])
    
    # Sort by rerank score descending
    reranked = sorted(to_rerank, key=lambda r: r.get("rerank_score", 0.0), reverse=True)
    
    # Append remaining results (if top_n < len(results))
    if top_n and top_n < len(results):
        reranked.extend(results[top_n:])
    
    return reranked


class CrossEncoderReranker:
    """High-level cross-encoder reranker interface."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_name = settings.get("search.hybrid.rerank_model", _RERANKER_MODEL_NAME)
        self.top_n = settings.get("search.hybrid.rerank_top_n", 20)
        self._model: Optional[Any] = None
    
    async def _ensure_loaded(self) -> None:
        if self._model is None:
            self._model = await get_reranker_model(self.model_name)
    
    async def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_n: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        await self._ensure_loaded()
        if self._model is None:
            return results
        return await rerank(query, results, self.model_name, top_n or self.top_n)