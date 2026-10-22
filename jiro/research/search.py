"""Search adapter for the research engine.

Wraps the project's existing ``SearchOrchestrator`` (from ``jiro.scraping.engines``)
with the bounds the research loop needs:

* Budget accounting: every call consumes queries and sources from the
  ``ResearchRecord`` budget, clamped at zero.
* Deduplication: normalised queries and URLs are never issued/fetched twice.
* Provider selection: choose the engine or fallback set that matches the query
  class (news, academic, technical, general).
* Persistence: every issued query and discovered source is written to the
  repository so a resumed run does not repeat work.

The orchestrator itself is untouched — this is a thin deterministic layer that
makes its output durable and budget-aware.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from jiro.log import get_logger
from jiro.models import OrganicResult, SearchRequest
from jiro.research.config import ResearchConfig
from jiro.research.errors import ResearchBudgetExceeded
from jiro.research.models import PlannedQuery, ResearchPlan
from jiro.research.repository import ResearchRepository
from jiro.research.urls import normalize_query, normalize_url, stable_id
from jiro.scraping.engines import SearchOrchestrator

log = get_logger("jiro.research.search")


@dataclass(frozen=True)
class SearchOutcome:
    """Result of a single search call."""

    query: PlannedQuery
    results: List[OrganicResult]
    engines_used: Tuple[str, ...]
    latency_ms: float
    error: Optional[str] = None


class ResearchSearchAdapter:
    """Budget-aware, deduplicated search over SearchOrchestrator."""

    def __init__(
        self,
        orchestrator: SearchOrchestrator,
        repository: ResearchRepository,
        config: ResearchConfig,
    ) -> None:
        self._orchestrator = orchestrator
        self._repo = repository
        self._config = config
        self._issued_queries: Set[str] = set()
        self._seen_urls: Set[str] = set()

    async def warmup(self, research_id: str, owner_key_id: Optional[str] = None) -> None:
        """Load already-issued queries and seen URLs from the repository."""
        issued = await self._repo.issued_queries(research_id, owner_key_id=owner_key_id)
        self._issued_queries = {q["normalized_query"] for q in issued}
        # Also load already-discovered source URLs to avoid re-discovery
        sources = await self._repo.list_sources(research_id, owner_key_id=owner_key_id)
        self._seen_urls = {normalize_url(s.normalized_url) for s in sources}

    async def execute_plan(
        self,
        research_id: str,
        plan: ResearchPlan,
        *,
        owner_key_id: Optional[str] = None,
        iteration: int = 0,
    ) -> List[SearchOutcome]:
        """Execute all queries in the plan, respecting budget and deduplication.

        Returns a list of outcomes in the same order as the plan's queries. A
        query that was already issued or would exceed the budget is skipped (its
        outcome has ``error="budget_exhausted"`` or ``error="duplicate"``).
        """
        rec = await self._repo.get(research_id, owner_key_id=owner_key_id)
        if rec is None:
            raise ResearchBudgetExceeded("research not found", details={"research_id": research_id})

        budget = rec.budget_remaining
        if budget["queries"] <= 0:
            log.warning("research %s query budget exhausted", research_id)
            return []

        outcomes: List[SearchOutcome] = []
        for query in plan.queries:
            if len(outcomes) >= budget["queries"]:
                # Budget reached — record as skipped and stop
                qid = stable_id("qry", research_id, normalize_query(query.text))
                await self._repo.record_query(
                    research_id, qid, query.text, normalized_query=normalize_query(query.text),
                    owner_key_id=owner_key_id, subquestion_id=query.subquestion_id,
                    iteration=iteration, engine_requested=query.engine,
                    status="skipped", error="budget_exhausted",
                )
                outcomes.append(SearchOutcome(
                    query=query, results=[], engines_used=(),
                    latency_ms=0.0, error="budget_exhausted"))
                break

            normalized = normalize_query(query.text)
            if normalized in self._issued_queries:
                qid = stable_id("qry", research_id, normalized)
                await self._repo.record_query(
                    research_id, qid, query.text, normalized_query=normalized,
                    owner_key_id=owner_key_id, subquestion_id=query.subquestion_id,
                    iteration=iteration, engine_requested=query.engine,
                    status="skipped", error="duplicate",
                )
                outcomes.append(SearchOutcome(
                    query=query, results=[], engines_used=(),
                    latency_ms=0.0, error="duplicate"))
                continue

            # Execute the search
            qid = stable_id("qry", research_id, normalized)
            start = time.perf_counter()
            try:
                req = SearchRequest(
                    q=query.text,
                    engine=query.engine or "auto",
                    type=query.type or "web",
                    num=min(self._config.max_results_per_query, budget["sources"]),
                )
                response = await self._orchestrator.search(req)
                results = response.organic_results
                engines = tuple(r.source or "unknown" for r in results)
                latency_ms = (time.perf_counter() - start) * 1000.0

                # Filter out already-seen URLs
                fresh_results = []
                for r in results:
                    norm = normalize_url(r.link)
                    if norm not in self._seen_urls:
                        fresh_results.append(r)
                        self._seen_urls.add(norm)

                await self._repo.record_query(
                    research_id, qid, query.text, normalized_query=normalized,
                    owner_key_id=owner_key_id, subquestion_id=query.subquestion_id,
                    iteration=iteration, engine_requested=query.engine,
                    engines_used=engines, result_count=len(fresh_results),
                    status="ok", error=None,
                )
                self._issued_queries.add(normalized)

                # Persist discovered sources in one transaction. Previously
                # this awaited add_source per result — a read, a write and a
                # second read each — so a twelve-hit query issued three dozen
                # serialized operations behind the single writer lock.
                await self._repo.add_sources(
                    research_id,
                    [r.link for r in fresh_results],
                    owner_key_id=owner_key_id,
                    title_by_url={r.link: r.title for r in fresh_results},
                    discovery_queries=[normalized],
                    discovery_engines=[query.engine or "default"],
                    discovered_by=[query.subquestion_id or ""],
                    start_rank=1,
                )

                # Consume budget
                await self._repo.consume(
                    research_id, queries=1, sources=len(fresh_results),
                    owner_key_id=owner_key_id)

                outcomes.append(SearchOutcome(
                    query=query, results=fresh_results, engines_used=engines,
                    latency_ms=latency_ms, error=None))

            except Exception as exc:  # pragma: no cover - network errors
                latency_ms = (time.perf_counter() - start) * 1000.0
                log.warning("search failed for %s: %s", query.text, exc)
                await self._repo.record_query(
                    research_id, qid, query.text, normalized_query=normalized,
                    owner_key_id=owner_key_id, subquestion_id=query.subquestion_id,
                    iteration=iteration, engine_requested=query.engine,
                    status="error", error=str(exc)[:500],
                )
                self._issued_queries.add(normalized)
                outcomes.append(SearchOutcome(
                    query=query, results=[], engines_used=(),
                    latency_ms=latency_ms, error=str(exc)))

        return outcomes

    def stats(self) -> Dict[str, int]:
        return {
            "issued_queries": len(self._issued_queries),
            "seen_urls": len(self._seen_urls),
        }


__all__ = ["ResearchSearchAdapter", "SearchOutcome"]