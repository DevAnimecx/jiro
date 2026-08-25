"""Semantic cache — embedding-based fuzzy result reuse (PRD Phase 4).

When ``cache.semantic: true`` and an embeddings provider is configured
(OpenAI ``text-embedding-3-small`` or Ollama), search queries are embedded
and compared by cosine similarity against previously cached queries. A query
within ``threshold`` of a cached one reuses that cached result instead of
scraping again — without any exact-match dependency.

If embeddings are unavailable, the semantic cache is transparently disabled
and exact caching still works.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional

import httpx

from jiro.config import Settings
from jiro.db import Database
from jiro.log import get_logger

log = get_logger("jiro.semantic")

EMBED_MODEL = "text-embedding-3-small"


class Embedder:
    """OpenAI-compatible embeddings (OpenAI / Ollama)."""

    def __init__(self, settings: Settings) -> None:
        cfg = settings.llm
        self.provider = (cfg.get("provider") or "openai").lower()
        self.api_key = (cfg.get("api_key") or "").strip()
        self.model = EMBED_MODEL
        self.base_url = (cfg.get("base_url") or "").strip()

    @property
    def ready(self) -> bool:
        return bool(self.api_key) or self.provider == "ollama"

    async def embed(self, text: str) -> Optional[List[float]]:
        if not self.ready:
            return None
        url, headers, payload = self._request(text)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
        except Exception as exc:
            log.warning("embedding failed, semantic cache disabled for this query",
                        extra={"error": str(exc)})
            return None

    def _request(self, text: str):
        if self.provider == "ollama":
            base = self.base_url or "http://localhost:11434"
            return (f"{base}/api/embeddings", {}, {"model": "nomic-embed-text", "prompt": text})
        base = self.base_url or "https://api.openai.com/v1"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return (f"{base}/embeddings", headers,
                {"model": self.model, "input": text})


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class SemanticCache:
    def __init__(self, settings: Settings, db: Optional[Database] = None, *,
                 cache: Any = None, threshold: float = 0.88,
                 index_ttl: int = 7 * 86400) -> None:
        self.settings = settings
        self.db = db
        self.cache = cache
        self.threshold = threshold
        self.index_ttl = index_ttl
        self.embedder = Embedder(settings)
        self._enabled = bool(settings.get("cache.semantic", False))

    @property
    def enabled(self) -> bool:
        return self._enabled and self.embedder.ready

    async def find(self, query: str) -> Optional[Dict[str, Any]]:
        """Return a cached search payload for a semantically similar query."""
        if not self.enabled or self.db is None:
            return None
        embedding = await self.embedder.embed(query)
        if not embedding:
            return None
        rows = await self.db.fetchall(
            "SELECT query, embedding, result_key, created_at FROM semantic_cache"
            " WHERE created_at > ? ORDER BY created_at DESC LIMIT 500",
            (time.time() - self.index_ttl,),
        )
        best, best_score = None, self.threshold
        for row in rows:
            try:
                stored = json.loads(row["embedding"])
            except Exception:
                continue
            score = cosine(embedding, stored)
            if score > best_score:
                best_score = score
                best = row
        if best is None or self.cache is None:
            return None
        payload = await self.cache.get(best["result_key"])
        if payload is None:
            return None
        log.info("semantic cache hit", extra={"query": query, "cached": best["query"],
                                              "score": round(best_score, 3)})
        return payload

    async def store(self, query: str, result_key: str) -> None:
        """Index a query + result for future fuzzy reuse."""
        if not self.enabled or self.db is None:
            return
        embedding = await self.embedder.embed(query)
        if not embedding:
            return
        await self.db.execute(
            "INSERT OR REPLACE INTO semantic_cache (query, embedding, result_key, created_at)"
            " VALUES (?, ?, ?, ?)",
            (query, json.dumps(embedding), result_key, time.time()),
        )
