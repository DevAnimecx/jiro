"""Deterministic, intent-based query graph planning.

Rules first. The planner is synchronous, side-effect free, and never calls a
model. An LLM planner can be added behind a flag later; it must produce the
same validated contract or be rejected.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from jiro.graph.model import Budget, QueryGraph, QueryNode

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_COMPARE_RE = re.compile(r"\b(vs\.?|versus|compare|comparison|difference between)\b", re.I)
_HOWTO_RE = re.compile(r"\b(how to|tutorial|guide|steps to|setup|install)\b", re.I)
_NEWS_RE = re.compile(r"\b(news|latest|today|breaking|current|2025|2026)\b", re.I)
_SOCIAL_RE = re.compile(r"\b(reddit|twitter|x\.com|linkedin|hacker news|sentiment|social)\b", re.I)

_MAX_QUERY = 500

# Node timeout bounds. A SEARCH node has to outlive a real web search, which is
# seconds, not milliseconds: the previous 1400 ms ceiling made every plan fail
# by construction on any network slower than a LAN. These are the outer rails;
# the actual value is derived from observed latency when we have it.
_SEARCH_FLOOR_MS = 1_500
_SEARCH_CEILING_MS = 30_000
_CPU_NODE_MS = {"MERGE": 250, "COMPRESS": 500, "EXTRACT": 200,
                "VERIFY": 300, "SYNTHESIZE": 2_000}

# Static per-op latency used when nothing has been measured yet. Kept as the
# cold-start fallback for _estimate; replaced by observations once available.
_STATIC_OP_MS = {
    "SEARCH": 2_500,
    "FETCH": 3_000,
    "MERGE": 15,
    "EXTRACT": 30,
    "VERIFY": 50,
    "COMPRESS": 40,
    "SYNTHESIZE": 300,
}


class PlanEstimate(BaseModel):
    estimated_latency_ms: int
    estimated_requests: int
    estimated_tokens: int
    cache_hit_probability: float = Field(0.0, ge=0.0, le=1.0)
    nodes: int
    waves: int


class Alternative(BaseModel):
    name: str
    estimated_latency_ms: int
    estimated_requests: int
    tradeoff: str


class PlanResult(BaseModel):
    graph: QueryGraph
    estimate: PlanEstimate
    rationale: str
    alternatives: List[Alternative] = Field(default_factory=list)


def _clip(text: str, limit: int = _MAX_QUERY) -> str:
    return " ".join(text.split())[:limit].strip()


class GraphPlanner:
    """Turn a natural-language goal into a validated graph."""

    def __init__(self, settings: Any = None, latency: Any = None) -> None:
        self.settings = settings
        # Rolling per-engine latency observations. Optional: without it the
        # planner falls back to static defaults, which is the old behaviour.
        self._latency = latency

    def _measured_search_ms(self, engine: str) -> Optional[float]:
        """Observed p95 search latency for an engine, if enough samples exist."""
        if self._latency is None:
            return None
        try:
            return self._latency.percentile(engine, 95)
        except Exception:
            # Telemetry is an optimization, never a dependency.
            return None

    def search_timeout_ms(self, engine: str, deadline_ms: int) -> int:
        """Timeout for one SEARCH node.

        Derived from what this engine has actually been taking, falling back to
        the static default. The goal is a timeout that clears the real tail
        rather than one that is comfortably below the median.
        """
        if self._latency is not None:
            try:
                return self._latency.suggest_timeout_ms(
                    engine, floor_ms=_SEARCH_FLOOR_MS,
                    ceiling_ms=min(_SEARCH_CEILING_MS, max(_SEARCH_FLOOR_MS, deadline_ms)),
                    default_ms=self._default_search_ms(engine),
                )
            except Exception:
                pass
        return self._default_search_ms(engine)

    def _default_search_ms(self, engine: str) -> int:
        """Cold-start search timeout, from config with a sane floor."""
        configured = None
        if self.settings is not None:
            try:
                graph_cfg = self.settings.graph
                configured = graph_cfg.get("search_timeout_ms")
            except Exception:
                configured = None
        base = int(configured) if configured else _STATIC_OP_MS["SEARCH"]
        return max(_SEARCH_FLOOR_MS, min(_SEARCH_CEILING_MS, base))

    def default_deadline_ms(self, engine: str = "auto") -> int:
        """Whole-graph deadline for a plan that has not been given one.

        Must clear the sum of the critical path, not one node: the previous
        fixed 2500 ms was below a single real search, so every plan timed out.
        Config wins when set; otherwise budget from measured latency.
        """
        ceiling = 15_000
        if self.settings is not None:
            try:
                ceiling = int(self.settings.graph.get("max_deadline_ms", ceiling))
            except Exception:
                pass

        per_search = (self.search_timeout_ms(engine, ceiling)
                      if engine != "auto" else self._default_search_ms(engine))
        # Two search waves plus the CPU tail, matching the shapes the planner
        # emits below. Deliberately generous: a deadline that is too tight
        # turns a slow-but-valid answer into a failed run.
        budget = per_search * 2 + sum(_CPU_NODE_MS.values())
        return int(min(ceiling, max(per_search + 1_000, budget)))

    def plan(
        self,
        query: str,
        *,
        engine: str = "auto",
        num: int = 5,
        max_tokens: int = 1200,
        deadline_ms: Optional[int] = None,
        max_nodes: int = 12,
        fetch_top: int = 0,
        intent: Optional[str] = None,
    ) -> PlanResult:
        query = _clip(query)
        if deadline_ms is None:
            deadline_ms = self.default_deadline_ms(engine)
        if not query:
            raise ValueError("query must not be empty")
        if num < 1 or num > 10:
            raise ValueError("num must be between 1 and 10")
        if fetch_top < 0 or fetch_top > 10:
            raise ValueError("fetch_top must be between 0 and 10")

        resolved_intent, rationale = (
            (intent, "intent supplied by caller")
            if intent
            else self._classify(query)
        )

        if resolved_intent == "scrape":
            nodes = self._scrape_nodes(query, deadline_ms)
            policy = self._policy(query, engine, num, max_tokens, fetch_top)
        else:
            nodes = self._search_nodes(
                query,
                resolved_intent,
                engine=engine,
                num=num,
                fetch_top=fetch_top,
                deadline_ms=deadline_ms,
            )
            policy = self._policy(query, engine, num, max_tokens, fetch_top)

        budget = Budget(
            deadline_ms=deadline_ms,
            max_nodes=max_nodes,
            max_tokens=max_tokens,
            max_requests=max(4, len(nodes) + fetch_top * 2),
        )
        graph = QueryGraph(
            graph_id="pending",
            intent=resolved_intent,
            nodes=tuple(nodes),
            budget=budget,
            policy=policy,
        ).with_derived_id()

        estimate = self._estimate(graph, fetch_top, engine)
        return PlanResult(
            graph=graph,
            estimate=estimate,
            rationale=rationale,
            alternatives=self._alternatives(query, resolved_intent, estimate),
        )

    # -- intent ---------------------------------------------------------
    def _classify(self, query: str) -> Tuple[str, str]:
        if _URL_RE.search(query):
            return "scrape", "query contains a URL; plan a direct fetch"

        if self.settings is not None:
            try:
                from jiro.search.intent import IntentType, classify_intent

                result = classify_intent(query, self.settings)
                intent = result.intent
                if intent == IntentType.SCRAPE:
                    return "scrape", "intent classifier found a URL"
                if str(intent.value).startswith("social_"):
                    return "social_signal", f"intent classifier: {intent.value}"
                if intent in (IntentType.NEWS_SEARCH, IntentType.TRENDING, IntentType.MONITOR_SETUP):
                    return "news_check", f"intent classifier: {intent.value}"
            except Exception:
                # The classifier is an optimization, never a dependency.
                pass

        if _COMPARE_RE.search(query):
            return "comparison", "comparison wording detected"
        if _HOWTO_RE.search(query):
            return "howto", "procedural wording detected"
        if _NEWS_RE.search(query):
            return "news_check", "freshness wording detected"
        if _SOCIAL_RE.search(query):
            return "social_signal", "social wording detected"
        if re.search(r"\b(who is|what is|profile|company|ceo|founder)\b", query, re.I):
            return "entity_profile", "entity wording detected"
        return "fact_lookup", "default factual plan"

    # -- templates ------------------------------------------------------
    def _search_nodes(
        self,
        query: str,
        intent: str,
        *,
        engine: str,
        num: int,
        fetch_top: int,
        deadline_ms: int,
    ) -> List[QueryNode]:
        variants = self._variants(query, intent)
        nodes: List[QueryNode] = []
        search_ids: List[str] = []
        # The search nodes are the leaves, so their ceiling is the whole graph
        # deadline minus what the CPU tail needs. There is deliberately no fixed
        # cap here: capping below real search latency is what made every plan
        # fail, and the deadline above is already the bound.
        cpu_tail = sum(_CPU_NODE_MS[op] for op in ("MERGE", "COMPRESS"))
        fetch_reserve = 0
        if fetch_top > 0:
            cpu_tail += _CPU_NODE_MS["EXTRACT"]
            fetch_reserve = _STATIC_OP_MS["FETCH"]
        search_budget = max(_SEARCH_FLOOR_MS, deadline_ms - cpu_tail - fetch_reserve)
        per_search = min(self.search_timeout_ms(engine, deadline_ms), search_budget)

        for index, variant in enumerate(variants, start=1):
            node_id = f"s{index}"
            search_ids.append(node_id)
            nodes.append(
                QueryNode(
                    id=node_id,
                    op="SEARCH",
                    engine_policy=(engine,),
                    query_template=variant,
                    timeout_ms=max(_SEARCH_FLOOR_MS, per_search),
                    evidence="SNIPPET",
                )
            )

        nodes.append(
            QueryNode(
                id="m1",
                op="MERGE",
                inputs=tuple(search_ids),
                timeout_ms=250,
                evidence="MULTI_SOURCE",
            )
        )

        compress_inputs = ["m1"]
        if fetch_top > 0:
            # Same reasoning as SEARCH: FETCH runs concurrently with the search
            # nodes, so it may use the whole leaf budget rather than a fixed cap.
            nodes.append(
                QueryNode(
                    id="f1",
                    op="FETCH",
                    inputs=tuple(search_ids),
                    required=False,
                    timeout_ms=max(1_000, min(self._fetch_timeout_ms(deadline_ms),
                                              search_budget)),
                    evidence="PAGE",
                )
            )
            compress_inputs.append("f1")

        nodes.append(
            QueryNode(
                id="c1",
                op="COMPRESS",
                inputs=tuple(compress_inputs),
                timeout_ms=500,
                evidence="PAGE",
            )
        )
        return nodes

    def _fetch_timeout_ms(self, deadline_ms: int) -> int:
        """Timeout for one FETCH node, from observed scrape latency when known."""
        default = _STATIC_OP_MS["FETCH"]
        if self._latency is not None:
            try:
                return self._latency.suggest_timeout_ms(
                    "fetch", floor_ms=1_000, ceiling_ms=_SEARCH_CEILING_MS,
                    default_ms=default,
                )
            except Exception:
                pass
        return default

    def _scrape_nodes(self, query: str, deadline_ms: int) -> List[QueryNode]:
        url = _URL_RE.search(query)
        target = url.group(0) if url else query
        return [
            QueryNode(
                id="f1",
                op="FETCH",
                query_template=target,
                timeout_ms=max(1_000, min(self._fetch_timeout_ms(deadline_ms),
                                          deadline_ms - _CPU_NODE_MS["COMPRESS"])),
                evidence="PAGE",
            ),
            QueryNode(
                id="c1",
                op="COMPRESS",
                inputs=("f1",),
                timeout_ms=500,
                evidence="PAGE",
            ),
        ]

    @staticmethod
    def _variants(query: str, intent: str) -> List[str]:
        base = _clip(query, 300)
        if intent == "news_check":
            return [base, _clip(f"{base} latest news", 300)]
        if intent == "comparison":
            return [base, _clip(f"{base} comparison", 300)]
        if intent == "entity_profile":
            return [base, _clip(f"{base} official profile", 300)]
        if intent == "howto":
            return [base, _clip(f"{base} step by step", 300)]
        if intent == "social_signal":
            return [_clip(f"{base} reddit", 300), _clip(f"{base} twitter", 300)]
        return [base, _clip(f"{base} evidence", 300)]

    def _policy(
        self, query: str, engine: str, num: int, max_tokens: int, fetch_top: int
    ) -> Dict[str, Any]:
        return {
            "query": query,
            "engine": engine,
            "num": num,
            "type": "news" if _NEWS_RE.search(query) else "web",
            "fetch_top": fetch_top,
            "max_tokens": max_tokens,
            "safe": "off",
            "location": "us",
            "language": "en",
        }

    # -- estimation -----------------------------------------------------
    def _estimate(self, graph: QueryGraph, fetch_top: int,
                  engine: str = "auto") -> PlanEstimate:
        """Cost model over the graph's critical path.

        Uses observed latency when available so the estimate reflects this
        machine and network rather than a fixed table. The static values remain
        the cold-start fallback.
        """
        levels = graph.levels()
        measured_search = self._measured_search_ms(engine)
        measured_fetch = self._measured_search_ms("fetch")

        per_level: Dict[int, int] = {}
        for node in graph.nodes:
            if node.op == "SEARCH":
                cost = int(measured_search) if measured_search else _STATIC_OP_MS["SEARCH"]
            elif node.op == "FETCH":
                cost = int(measured_fetch) if measured_fetch else _STATIC_OP_MS["FETCH"]
            else:
                cost = _STATIC_OP_MS[node.op]
            per_level[levels[node.id]] = max(per_level.get(levels[node.id], 0), cost)

        latency = sum(per_level.values()) + 50  # merge/serialize overhead
        requests = sum(1 for n in graph.nodes if n.op == "SEARCH")
        requests += max(1, fetch_top) if any(n.op == "FETCH" for n in graph.nodes) else 0
        return PlanEstimate(
            estimated_latency_ms=latency,
            estimated_requests=requests,
            estimated_tokens=graph.budget.max_tokens,
            cache_hit_probability=0.0,
            nodes=len(graph.nodes),
            waves=max(levels.values()) + 1,
        )

    @staticmethod
    def _alternatives(
        query: str, intent: str, estimate: PlanEstimate
    ) -> List[Alternative]:
        single = max(250, estimate.estimated_latency_ms // 2)
        return [
            Alternative(
                name="single_search",
                estimated_latency_ms=single,
                estimated_requests=1,
                tradeoff="faster, but no cross-source corroboration",
            )
        ]
