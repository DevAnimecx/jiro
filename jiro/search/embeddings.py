"""Embedding model wrapper for semantic search.

Lazy-loads sentence-transformers model to avoid import overhead.
CPU-only, ~80MB model (all-MiniLM-L6-v2).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import numpy as np

from jiro.config import Settings
from jiro.log import get_logger

log = get_logger("jiro.search.embeddings")

# Global model cache (singleton per process)
_EMBEDDING_MODEL: Optional[Any] = None
_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
_MODEL_LOCK = asyncio.Lock()


async def get_embedding_model(model_name: Optional[str] = None) -> Any:
    """Get or create the embedding model (lazy-loaded)."""
    global _EMBEDDING_MODEL, _MODEL_NAME
    
    if model_name:
        _MODEL_NAME = model_name
    
    if _EMBEDDING_MODEL is not None:
        return _EMBEDDING_MODEL
    
    async with _MODEL_LOCK:
        if _EMBEDDING_MODEL is not None:
            return _EMBEDDING_MODEL
        
        log.info("loading embedding model", extra={"model": _MODEL_NAME})
        
        def _load():
            try:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(_MODEL_NAME)
                model.eval()
                return model
            except ImportError:
                log.warning("sentence-transformers not installed, semantic search disabled")
                return None
            except Exception as exc:
                log.error("failed to load embedding model", extra={"error": str(exc)})
                return None
        
        loop = asyncio.get_running_loop()
        _EMBEDDING_MODEL = await loop.run_in_executor(None, _load)
        
        if _EMBEDDING_MODEL is not None:
            log.info("embedding model loaded", extra={"model": _MODEL_NAME})
        else:
            log.warning("embedding model unavailable")
        
        return _EMBEDDING_MODEL


async def embed_texts(texts: List[str], model_name: Optional[str] = None) -> np.ndarray:
    """Embed a list of texts into vectors."""
    model = await get_embedding_model(model_name)
    if model is None:
        return np.zeros((len(texts), 384), dtype=np.float32)
    
    def _encode():
        return model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    
    loop = asyncio.get_running_loop()
    embeddings = await loop.run_in_executor(None, _encode)
    return embeddings.astype(np.float32)


async def embed_query(query: str, model_name: Optional[str] = None) -> np.ndarray:
    """Embed a single query."""
    result = await embed_texts([query], model_name)
    return result[0]


async def semantic_similarity(
    query: str,
    texts: List[str],
    model_name: Optional[str] = None
) -> List[float]:
    """Compute cosine similarity between query and texts."""
    model = await get_embedding_model(model_name)
    if model is None:
        return [0.0] * len(texts)
    
    def _compute():
        query_emb = model.encode([query], convert_to_numpy=True, show_progress_bar=False)
        text_embs = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        
        query_norm = query_emb / (np.linalg.norm(query_emb, axis=1, keepdims=True) + 1e-8)
        text_norms = text_embs / (np.linalg.norm(text_embs, axis=1, keepdims=True) + 1e-8)
        
        similarities = np.dot(text_norms, query_norm.T).flatten()
        return similarities.tolist()
    
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _compute)


def clear_model_cache() -> None:
    """Clear the cached model (for testing or memory pressure)."""
    global _EMBEDDING_MODEL
    _EMBEDDING_MODEL = None


class EmbeddingModel:
    """High-level embedding model interface."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_name = settings.get("search.hybrid.embedding_model", _MODEL_NAME)
        self._model: Optional[Any] = None
    
    async def _ensure_loaded(self) -> None:
        if self._model is None:
            self._model = await get_embedding_model(self.model_name)
    
    async def embed(self, texts: List[str]) -> np.ndarray:
        await self._ensure_loaded()
        if self._model is None:
            return np.zeros((len(texts), 384), dtype=np.float32)
        
        def _encode():
            return self._model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _encode)
    
    async def score(self, query: str, results: List[Dict[str, Any]]) -> List[float]:
        """Score results by semantic similarity to query."""
        await self._ensure_loaded()
        if self._model is None or not results:
            return [0.0] * len(results)
        
        texts = [
            f"{r.get('title', '')} {r.get('snippet', '')}".strip()
            for r in results
        ]
        return await semantic_similarity(query, texts, self.model_name)