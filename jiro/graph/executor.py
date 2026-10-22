"""Wave scheduler for query graphs.

The executor owns deadlines, cancellation, request/byte budgets, single-flight
deduplication, node cache reads/writes, and gap reporting. Operators own only
the network work.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from jiro.errors import JiroError
from jiro.graph.model import QueryGraph, QueryNode, canonical_digest

Operator = Callable[[QueryNode, Dict[str, Any], "GraphContext"], Awaitable[Dict[str, Any]]]


class GraphError(JiroError):
    code = "graph_error"
    status_code = 502


class GraphExecutionError(GraphError):
    code = "graph_execution_failed"
    status_code = 502


class GraphTimeoutError(GraphError):
    code = "graph_timeout"
    status_code = 504


class GraphBudgetError(GraphError):
    code = "graph_budget_exceeded"
    status_code = 422


class NodeTimeoutError(GraphError):
    code = "node_timeout"
    status_code = 504


@dataclass
class RunBudget:
    max_requests: int
    max_bytes: int
    max_nodes: int
    requests_used: int = 0
    bytes_used: int = 0
    nodes_used: int = 0
    tokens_used: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def reserve_request(self) -> None:
        async with self.lock:
            if self.requests_used >= self.max_requests:
                raise GraphBudgetError(
                    f"request budget exhausted ({self.max_requests})",
                    details={"requests_used": self.requests_used},
                )
            self.requests_used += 1

    async def add_bytes(self, count: int) -> None:
        if count <= 0:
            return
        async with self.lock:
            self.bytes_used += count
            if self.bytes_used > self.max_bytes:
                raise GraphBudgetError(
                    f"byte budget exhausted ({self.max_bytes})",
                    details={"bytes_used": self.bytes_used},
                )

    async def try_count_request(self) -> bool:
        """Take one request from the budget if any remain.

        Unlike ``reserve_request`` this reports rather than raises, so an
        optional extra attempt (a hedge) is simply skipped when the budget is
        spent instead of failing the node that was already in flight.
        """
        async with self.lock:
            if self.requests_used >= self.max_requests:
                return False
            self.requests_used += 1
            return True

    async def count_node(self) -> None:
        async with self.lock:
            self.nodes_used += 1

    def to_dict(self) -> Dict[str, int]:
        return {
            "requests_used": self.requests_used,
            "bytes_used": self.bytes_used,
            "nodes_used": self.nodes_used,
            "tokens_used": self.tokens_used,
        }


@dataclass
class GraphContext:
    settings: Any
    orchestrator: Any
    scraper: Any
    cache: Any = None
    request_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeOutcome:
    node_id: str
    op: str
    status: str
    value: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    elapsed_ms: int = 0
    cached: bool = False

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "node_id": self.node_id,
            "op": self.op,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "cached": self.cached,
        }
        if self.error:
            payload["error"] = self.error
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


@dataclass
class GraphRun:
    graph_id: str
    intent: str
    status: str
    duration_ms: int
    outcomes: List[NodeOutcome]
    gaps: List[Dict[str, Any]]
    budget: RunBudget
    results: List[Dict[str, Any]] = field(default_factory=list)
    packet: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def timings(self) -> Dict[str, int]:
        return {o.node_id: o.elapsed_ms for o in self.outcomes}

    def engine_attribution(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for outcome in self.outcomes:
            if outcome.op != "SEARCH" or not outcome.value:
                continue
            metadata = outcome.value.get("metadata") or {}
            engine = str(metadata.get("engine") or "unknown")
            counts[engine] = counts.get(engine, 0) + 1
        return counts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "intent": self.intent,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "results": self.results,
            "packet": self.packet,
            "gaps": self.gaps,
            "timings": self.timings(),
            "engine_attribution": self.engine_attribution(),
            "budget": self.budget.to_dict(),
            "nodes": [o.to_dict() for o in self.outcomes],
            "error": self.error,
        }


class GraphExecutor:
    """Run a validated graph in deterministic waves."""

    def __init__(
        self,
        operators: Optional[Dict[str, Operator]] = None,
        *,
        cache_ttl: int = 900,
    ) -> None:
        self._operators = operators
        self.cache_ttl = cache_ttl
        self._inflight: Dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        # id(value) -> digest. Entries are held only while the run holds the
        # value itself, so the map cannot outlive what it keys.
        self._digest_memo: Dict[int, str] = {}

    def _registry(self) -> Dict[str, Operator]:
        if self._operators is not None:
            return self._operators
        from jiro.graph.operators import DEFAULT_OPERATORS

        return DEFAULT_OPERATORS

    async def run(self, graph: QueryGraph, context: GraphContext) -> GraphRun:
        start = time.perf_counter()
        budget = RunBudget(
            max_requests=graph.budget.max_requests,
            max_bytes=graph.budget.max_bytes,
            max_nodes=graph.budget.max_nodes,
        )
        outcomes: List[NodeOutcome] = []
        outputs: Dict[str, Dict[str, Any]] = {}
        gaps: List[Dict[str, Any]] = []

        run = GraphRun(
            graph_id=graph.graph_id,
            intent=graph.intent,
            status="running",
            duration_ms=0,
            outcomes=outcomes,
            gaps=gaps,
            budget=budget,
        )

        try:
            async with asyncio.timeout(graph.budget.deadline_ms / 1000):
                await self._dispatch(graph, context, outputs, budget,
                                     outcomes, gaps, run, start)

        except TimeoutError as exc:
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            run.status = "timeout"
            run.error = f"graph exceeded {graph.budget.deadline_ms} ms deadline"
            run.duration_ms = int((time.perf_counter() - start) * 1000)
            raise GraphTimeoutError(
                run.error, details={"run": run.to_dict()}
            ) from exc
        finally:
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            self._tasks.clear()
            self._inflight.clear()
            # `id()` is only unique among live objects; releasing the memo at
            # the end of a run stops a later run reusing an address from
            # inheriting a stale digest.
            self._digest_memo.clear()

        run.status = "partial" if gaps else "ok"
        run.duration_ms = int((time.perf_counter() - start) * 1000)
        self._finalize(run, outcomes)
        return run

    async def _dispatch(
        self,
        graph: QueryGraph,
        context: GraphContext,
        outputs: Dict[str, Dict[str, Any]],
        budget: RunBudget,
        outcomes: List[NodeOutcome],
        gaps: List[Dict[str, Any]],
        run: GraphRun,
        started_at: float,
    ) -> None:
        """Run nodes as soon as their own inputs are ready.

        The previous implementation ran one topological level at a time and
        awaited the whole level before starting the next, so a node waited on
        the slowest node of the previous level rather than on its own parents.
        For the planner's usual shape (two searches, then a merge and a fetch,
        then a compress) that meant three full stalls, and the compress always
        waited for the slow page fetch even when its other input was done.

        This keeps the same accounting, gap reporting and fail-required
        semantics; only the dispatch order changes.
        """
        nodes_by_id = {node.id: node for node in graph.nodes}
        # How many parents each node is still waiting on, and who waits on whom.
        pending = {
            node.id: sum(1 for p in node.inputs if p in nodes_by_id)
            for node in graph.nodes
        }
        children: Dict[str, List[str]] = {node.id: [] for node in graph.nodes}
        for node in graph.nodes:
            for parent in node.inputs:
                if parent in nodes_by_id:
                    children[parent].append(node.id)

        running: Dict[asyncio.Task, QueryNode] = {}
        done_ids: set[str] = set()
        failed_required: List[NodeOutcome] = []

        def ready() -> List[QueryNode]:
            return [
                nodes_by_id[nid]
                for nid, count in pending.items()
                if count == 0 and nid not in done_ids
            ]

        def spawn(node: QueryNode) -> None:
            task = asyncio.create_task(
                self._run_node(node, graph, context, outputs, budget)
            )
            running[task] = node
            self._tasks.add(task)

        for node in sorted(ready(), key=lambda n: n.id):
            spawn(node)

        while running:
            finished, _ = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )
            for task in finished:
                node = running.pop(task)
                self._tasks.discard(task)
                done_ids.add(node.id)

                try:
                    result = task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    result = NodeOutcome(
                        node_id=node.id,
                        op=node.op,
                        status="failed",
                        error=str(exc) or exc.__class__.__name__,
                        error_code=getattr(exc, "code", "operator_error"),
                    )

                outcomes.append(result)
                if result.status == "ok" and result.value is not None:
                    outputs[node.id] = result.value
                elif node.required:
                    failed_required.append(result)

                # Release children whose every parent has now settled.
                for child_id in children.get(node.id, []):
                    pending[child_id] -= 1
                    if pending[child_id] == 0 and child_id not in done_ids:
                        if failed_required:
                            # A required ancestor failed: the whole run is
                            # already lost, so do not start more work.
                            done_ids.add(child_id)
                            continue
                        spawn(nodes_by_id[child_id])

            if failed_required:
                for task in list(running):
                    task.cancel()
                await asyncio.gather(*running, return_exceptions=True)
                running.clear()

                first = failed_required[0]
                gaps.extend(
                    {
                        "node_id": o.node_id,
                        "op": o.op,
                        "required": True,
                        "error": o.error,
                        "error_code": o.error_code,
                    }
                    for o in failed_required
                )
                run.status = "failed"
                run.error = first.error
                run.duration_ms = int((time.perf_counter() - started_at) * 1000)
                raise GraphExecutionError(
                    f"required node {first.node_id!r} failed: {first.error}",
                    details={"run": run.to_dict()},
                )

            # Nodes made ready by this completion were spawned above. The loop
            # drains `running`; when it empties, every node has either settled
            # or been blocked by a failed required ancestor.
            for node in sorted(ready(), key=lambda n: n.id):
                spawn(node)

        for outcome in outcomes:
            if outcome.status != "ok":
                gap = {
                    "node_id": outcome.node_id,
                    "op": outcome.op,
                    "required": graph.node(outcome.node_id).required,
                    "error": outcome.error,
                    "error_code": outcome.error_code,
                }
                if gap not in gaps:
                    gaps.append(gap)

    def _finalize(self, run: GraphRun, outcomes: List[NodeOutcome]) -> None:
        packet = None
        merged: List[Dict[str, Any]] = []
        searches: List[Dict[str, Any]] = []
        for outcome in outcomes:
            if outcome.status != "ok" or not outcome.value:
                continue
            if outcome.op == "COMPRESS":
                packet = outcome.value
            elif outcome.op == "MERGE":
                merged = list(outcome.value.get("results") or [])
            elif outcome.op == "SEARCH":
                searches.extend(outcome.value.get("results") or [])
        run.packet = packet
        run.results = merged or searches
        if packet:
            run.budget.tokens_used = int(packet.get("used_tokens") or 0)

    async def _run_node(
        self,
        node: QueryNode,
        graph: QueryGraph,
        context: GraphContext,
        outputs: Dict[str, Dict[str, Any]],
        budget: RunBudget,
    ) -> NodeOutcome:
        started = time.perf_counter()
        await budget.count_node()
        inputs = {parent: outputs[parent] for parent in node.inputs if parent in outputs}
        missing = [
            parent
            for parent in node.inputs
            if parent not in outputs and graph.node(parent).required
        ]
        if missing:
            return NodeOutcome(
                node_id=node.id,
                op=node.op,
                status="failed",
                error=f"missing inputs: {missing}",
                error_code="missing_inputs",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )

        key = self._cache_key(node, graph, inputs)
        if context.cache is not None:
            try:
                hit = await context.cache.get(key, ttl=self.cache_ttl)
                if isinstance(hit, dict):
                    return NodeOutcome(
                        node_id=node.id,
                        op=node.op,
                        status="ok",
                        value=hit,
                        elapsed_ms=int((time.perf_counter() - started) * 1000),
                        cached=True,
                    )
            except Exception:
                # Cache is an optimization; never fail a graph because of it.
                pass

        existing = self._inflight.get(key)
        if existing is not None:
            value = await asyncio.shield(existing)
            return NodeOutcome(
                node_id=node.id,
                op=node.op,
                status="ok",
                value=value,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                cached=True,
            )

        task = asyncio.create_task(
            self._execute_operator(node, inputs, context, budget)
        )
        self._inflight[key] = task
        self._tasks.add(task)

        hedge_task: Optional[asyncio.Task] = None
        if node.hedge_after_ms > 0:
            hedge_task = await self._start_hedge(
                node, inputs, context, budget, task)
            if hedge_task is not None:
                task, loser = await self._first_success(task, hedge_task)
                if loser is not None:
                    loser.cancel()

        try:
            value = await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_code = getattr(exc, "code", exc.__class__.__name__)
            return NodeOutcome(
                node_id=node.id,
                op=node.op,
                status="failed",
                error=str(exc) or exc.__class__.__name__,
                error_code=error_code,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
        finally:
            self._inflight.pop(key, None)
            self._tasks.discard(task)

        if context.cache is not None:
            try:
                await context.cache.put(
                    key, value, engine=node.engine_policy[0] if node.engine_policy else "",
                    kind="graph", ttl=self.cache_ttl,
                )
            except Exception:
                pass

        return NodeOutcome(
            node_id=node.id,
            op=node.op,
            status="ok",
            value=value,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _start_hedge(
        self,
        node: QueryNode,
        inputs: Dict[str, Dict[str, Any]],
        context: GraphContext,
        budget: RunBudget,
        primary: asyncio.Task,
    ) -> Optional[asyncio.Task]:
        """Start a duplicate attempt if the primary is still running after the hedge delay.

        ``QueryNode.hedge_after_ms`` was declared and never read, so the
        hedging the field was designed for did not exist. A hedged attempt runs
        on a *different* engine (falling back to the primary engine when there
        is no alternative), so the duplicate is not simply a repeat of the same
        request against the same index.

        Returns None when the primary finished inside the delay, or when there
        is no spare engine or budget to hedge with.
        """
        try:
            await asyncio.wait_for(asyncio.shield(primary),
                                   timeout=node.hedge_after_ms / 1000)
            return None  # primary was fast enough
        except asyncio.TimeoutError:
            pass
        except Exception:
            return None  # primary already failed; let the normal path report it

        if node.op != "SEARCH":
            # Only a network leaf is worth duplicating; a MERGE/COMPRESS hedge
            # would just recompute the same CPU work.
            return None
        if not await budget.try_count_request():
            return None

        engines = [e for e in node.engine_policy if e and e != "auto"]
        alternate = next((e for e in engines if e != (engines[0] if engines else None)), None)
        hedge_node = node
        if alternate is not None:
            hedge_node = node.model_copy(update={"engine_policy": (alternate,)})

        hedge = asyncio.create_task(
            self._execute_operator(hedge_node, inputs, context, budget))
        self._tasks.add(hedge)
        return hedge

    @staticmethod
    async def _first_success(
        primary: asyncio.Task, hedge: asyncio.Task
    ) -> tuple[asyncio.Task, Optional[asyncio.Task]]:
        """Wait for whichever of two attempts succeeds first.

        The loser is returned (rather than cancelled here) so the caller can
        release it; if both fail the primary is returned so its error is the
        one reported.
        """
        done, _ = await asyncio.wait(
            {primary, hedge}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception() is None:
                return task, (hedge if task is primary else primary)
        # Nothing succeeded yet: report whichever finishes last.
        done2, _ = await asyncio.wait(
            {primary, hedge}, return_when=asyncio.ALL_COMPLETED)
        return primary, None

    async def _execute_operator(
        self,
        node: QueryNode,
        inputs: Dict[str, Dict[str, Any]],
        context: GraphContext,
        budget: RunBudget,
    ) -> Dict[str, Any]:
        operator = self._registry().get(node.op)
        if operator is None:
            raise GraphError(f"no operator registered for {node.op}", status_code=500)
        if node.op in ("SEARCH", "FETCH"):
            await budget.reserve_request()
        try:
            async with asyncio.timeout(node.timeout_ms / 1000):
                value = await operator(node, inputs, context)
        except TimeoutError as exc:
            raise NodeTimeoutError(
                f"node {node.id!r} exceeded {node.timeout_ms} ms"
            ) from exc
        if not isinstance(value, dict):
            raise GraphError(f"operator {node.op} returned {type(value).__name__}, expected dict")
        if node.op == "FETCH":
            pages = value.get("pages") or []
            size = sum(len(str(p.get("content") or "")) for p in pages)
            if not pages and value.get("content"):
                size = len(str(value["content"]))
            await budget.add_bytes(size)
        return value

    def _cache_key(
        self, node: QueryNode, graph: QueryGraph, inputs: Dict[str, Dict[str, Any]]
    ) -> str:
        input_digests = {
            node_id: self._digest_value(value)
            for node_id, value in sorted(inputs.items())
        }
        payload = {
            "schema": graph.graph_schema,
            "op": node.op,
            "node": node.model_dump(
                mode="json",
                # ``hedge_after_ms`` is a scheduling hint, not part of a node's
                # identity: two nodes differing only in when they hedge must
                # share a cache entry. ``timeout_ms`` likewise.
                exclude={"id", "timeout_ms", "hedge_after_ms"},
            ),
            "inputs": input_digests,
            "policy": {
                k: graph.policy.get(k)
                for k in ("engine", "num", "type", "safe", "location", "language", "max_tokens")
            },
        }
        return "graph:" + canonical_digest(payload)

    def _digest_value(self, value: Any) -> str:
        """SHA-256 of a node's output, memoised by object identity.

        A MERGE node digests every upstream search payload, and each of those
        payloads is large. Without the memo the same dict is serialised again
        for every downstream node that consumes it; with it, each value is
        hashed once per run.
        """
        key = id(value)
        cached = self._digest_memo.get(key)
        if cached is not None:
            return cached
        digest = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"),
                       default=str).encode("utf-8")
        ).hexdigest()
        self._digest_memo[key] = digest
        return digest
