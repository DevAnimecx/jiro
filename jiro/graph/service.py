"""Application service that connects planning, execution, and packets."""

from __future__ import annotations

from typing import Any, Optional

from jiro.errors import JiroError
from jiro.graph.executor import GraphContext, GraphExecutor, GraphRun
from jiro.graph.planner import GraphPlanner, PlanResult


class GraphDisabledError(JiroError):
    code = "graph_disabled"
    status_code = 404


class GraphService:
    def __init__(
        self,
        settings: Any,
        orchestrator: Any,
        scraper: Any,
        cache: Any = None,
    ) -> None:
        self.settings = settings
        self.orchestrator = orchestrator
        self.scraper = scraper
        self.cache = cache
        # Share the orchestrator's latency observations so the planner budgets
        # from measured search times. Absent (or empty), the planner falls back
        # to its static defaults.
        latency = getattr(orchestrator, "latency", None)
        self.planner = GraphPlanner(settings, latency=latency)
        self.executor = GraphExecutor(
            cache_ttl=int(settings.get("graph.cache_ttl_seconds", 900))
        )

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("graph.enabled", True))

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise GraphDisabledError("query graph is disabled by configuration")

    def plan(
        self,
        query: str,
        *,
        engine: str = "auto",
        num: int = 5,
        max_tokens: Optional[int] = None,
        deadline_ms: Optional[int] = None,
        max_nodes: int = 12,
        fetch_top: Optional[int] = None,
        intent: Optional[str] = None,
    ) -> PlanResult:
        self._require_enabled()
        max_tokens = self._resolve_max_tokens(max_tokens)
        deadline_ms = self._resolve_deadline(deadline_ms)
        fetch_top = self._resolve_fetch_top(fetch_top)
        return self.planner.plan(
            query,
            engine=engine,
            num=num,
            max_tokens=max_tokens,
            deadline_ms=deadline_ms,
            max_nodes=max_nodes,
            fetch_top=fetch_top,
            intent=intent,
        )

    async def run(
        self,
        query: str,
        *,
        engine: str = "auto",
        num: int = 5,
        max_tokens: Optional[int] = None,
        deadline_ms: Optional[int] = None,
        max_nodes: int = 12,
        fetch_top: Optional[int] = None,
        intent: Optional[str] = None,
    ) -> GraphRun:
        plan = self.plan(
            query,
            engine=engine,
            num=num,
            max_tokens=max_tokens,
            deadline_ms=deadline_ms,
            max_nodes=max_nodes,
            fetch_top=fetch_top,
            intent=intent,
        )
        context = GraphContext(
            settings=self.settings,
            orchestrator=self.orchestrator,
            scraper=self.scraper,
            cache=self.cache,
            extra={
                "query": query,
                "policy": plan.graph.policy,
                "max_tokens": plan.graph.budget.max_tokens,
                "fetch_top": plan.graph.policy.get("fetch_top", 0),
                "fetch_concurrency": int(
                    self.settings.get("graph.fetch_concurrency", 6)),
            },
        )
        return await self.executor.run(plan.graph, context)

    def _resolve_max_tokens(self, value: Optional[int]) -> int:
        if value is None:
            value = int(self.settings.get("graph.default_max_tokens", 1200))
        ceiling = int(self.settings.get("graph.max_tokens", 4000))
        if value < 64 or value > ceiling:
            raise ValueError(f"max_tokens must be between 64 and {ceiling}")
        return value

    def _resolve_deadline(self, value: Optional[int]) -> Optional[int]:
        """Return the caller's deadline, or None to let the planner derive one.

        A configured default of 0 (the shipped value) means "derive from
        observed latency", so an unset graph deadline is not forced to a
        constant that may be far below the real cost of a search.
        """
        ceiling = int(self.settings.get("graph.max_deadline_ms", 30000))
        if value is None:
            value = int(self.settings.get("graph.default_deadline_ms", 0))
            if value <= 0:
                return None
        if value < 100 or value > ceiling:
            raise ValueError(f"deadline_ms must be between 100 and {ceiling}")
        return value

    def _resolve_fetch_top(self, value: Optional[int]) -> int:
        if value is None:
            value = int(self.settings.get("graph.fetch_top", 0))
        if value < 0 or value > 10:
            raise ValueError("fetch_top must be between 0 and 10")
        return value
