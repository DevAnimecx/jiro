"""Query graph executor tests (no network)."""

from __future__ import annotations

import asyncio
import time

import pytest

from jiro.graph.executor import (
    GraphContext,
    GraphExecutionError,
    GraphExecutor,
    GraphTimeoutError,
)
from jiro.graph.model import Budget, QueryGraph, QueryNode


class FakeCache:
    def __init__(self) -> None:
        self.store = {}

    async def get(self, key, ttl=None):
        return self.store.get(key)

    async def put(self, key, payload, *, engine="", kind="search", ttl=None):
        self.store[key] = payload


def _graph(nodes, *, deadline_ms=1000, max_requests=10) -> QueryGraph:
    return QueryGraph(
        graph_id="pending",
        intent="fact_lookup",
        nodes=tuple(nodes),
        budget=Budget(deadline_ms=deadline_ms, max_requests=max_requests),
    ).with_derived_id()


def _search_node(node_id: str, query: str, *, required: bool = True) -> QueryNode:
    return QueryNode(
        id=node_id,
        op="SEARCH",
        query_template=query,
        required=required,
        timeout_ms=800,
    )


def _merge_node(inputs) -> QueryNode:
    return QueryNode(id="m1", op="MERGE", inputs=tuple(inputs), timeout_ms=300)


def _packet_node(inputs) -> QueryNode:
    return QueryNode(id="c1", op="COMPRESS", inputs=tuple(inputs), timeout_ms=300)


def _context(cache=None) -> GraphContext:
    return GraphContext(settings=None, orchestrator=None, scraper=None, cache=cache)


@pytest.mark.asyncio
async def test_executor_runs_searches_in_parallel():
    calls = []

    async def search(node, inputs, ctx):
        calls.append(node.id)
        await asyncio.sleep(0.08)
        return {
            "kind": "search",
            "results": [{"link": f"https://{node.id}.example", "title": node.id, "snippet": "x"}],
            "metadata": {"engine": "fake"},
        }

    async def merge(node, inputs, ctx):
        results = [r for value in inputs.values() for r in value.get("results", [])]
        return {"kind": "merged", "results": results}

    async def compress(node, inputs, ctx):
        return {"kind": "packet", "used_tokens": 10}

    graph = _graph(
        [_search_node("s1", "a"), _search_node("s2", "b"), _merge_node(("s1", "s2")), _packet_node(("m1",))]
    )
    executor = GraphExecutor(
        {"SEARCH": search, "MERGE": merge, "COMPRESS": compress}
    )
    started = time.perf_counter()
    run = await executor.run(graph, _context())
    elapsed = time.perf_counter() - started

    assert run.status == "ok"
    assert sorted(calls) == ["s1", "s2"]
    assert elapsed < 0.15
    assert run.packet == {"kind": "packet", "used_tokens": 10}


@pytest.mark.asyncio
async def test_ready_node_starts_without_waiting_for_a_slow_sibling():
    """A node must start when its own parents finish, not when the level does.

    The executor used to run one topological level at a time and await the
    whole level, so a node waited on the slowest node of the previous level
    rather than on its actual inputs.

    Shape: ``fast -> m1 -> c1`` (the critical path, ~0.01s total) alongside a
    ``slow`` search (0.4s) that nothing downstream needs. A level barrier makes
    the run cost slow + critical path because m1 cannot start until slow ends;
    ready-driven dispatch finishes in the critical-path time while slow runs on.
    """
    order = []
    times = {}

    async def search(node, inputs, ctx):
        order.append(("start", node.id))
        times[f"start:{node.id}"] = time.perf_counter()
        await asyncio.sleep(0.4 if node.id == "slow" else 0.01)
        times[f"end:{node.id}"] = time.perf_counter()
        return {"kind": "search", "results": [{"link": f"https://{node.id}.x"}]}

    async def merge(node, inputs, ctx):
        order.append(("start", node.id))
        times["start:m1"] = time.perf_counter()
        return {"kind": "merged", "results": []}

    async def compress(node, inputs, ctx):
        order.append(("start", node.id))
        return {"kind": "packet", "used_tokens": 1}

    graph = _graph(
        [
            _search_node("fast", "a"),
            _search_node("slow", "b"),
            _merge_node(("fast",)),
            _packet_node(("m1",)),
        ],
        deadline_ms=5000,
    )
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge, "COMPRESS": compress})
    run = await executor.run(graph, _context())

    assert run.status == "ok"
    # m1 depends only on `fast`. Under a level barrier it could not start until
    # `slow` had finished; ready-driven dispatch starts it while slow runs.
    assert "start:m1" in times, "merge never ran"
    assert "end:slow" in times, "slow never finished"
    assert times["start:m1"] < times["end:slow"], (
        "merge waited for the unrelated slow sibling instead of its own input"
    )
    # It should start promptly after `fast` ends, not eventually.
    assert times["start:m1"] - times["end:fast"] < 0.1


@pytest.mark.asyncio
async def test_executor_cache_avoids_second_run_work():
    calls = []

    async def search(node, inputs, ctx):
        calls.append(node.id)
        return {"kind": "search", "results": [], "metadata": {"engine": "fake"}}

    async def merge(node, inputs, ctx):
        return {"kind": "merged", "results": []}

    graph = _graph([_search_node("s1", "same"), _merge_node(("s1",))])
    cache = FakeCache()
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})

    await executor.run(graph, _context(cache))
    await executor.run(graph, _context(cache))

    assert calls == ["s1"]


@pytest.mark.asyncio
async def test_executor_single_flight_for_identical_nodes():
    calls = []

    async def search(node, inputs, ctx):
        calls.append(node.id)
        await asyncio.sleep(0.02)
        return {"kind": "search", "results": [], "metadata": {"engine": "fake"}}

    async def merge(node, inputs, ctx):
        return {"kind": "merged", "results": []}

    graph = _graph(
        [
            _search_node("s1", "same"),
            _search_node("s2", "same"),
            _merge_node(("s1", "s2")),
        ]
    )
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})
    run = await executor.run(graph, _context())

    assert run.status == "ok"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_executor_optional_failure_becomes_gap():
    async def search(node, inputs, ctx):
        if node.id == "s2":
            raise RuntimeError("engine down")
        return {"kind": "search", "results": [{"link": "https://a.example"}], "metadata": {}}

    async def merge(node, inputs, ctx):
        results = [r for value in inputs.values() for r in value.get("results", [])]
        return {"kind": "merged", "results": results}

    graph = _graph(
        [
            _search_node("s1", "a"),
            _search_node("s2", "b", required=False),
            _merge_node(("s1", "s2")),
        ]
    )
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})
    run = await executor.run(graph, _context())

    assert run.status == "partial"
    assert any(gap["node_id"] == "s2" for gap in run.gaps)
    assert run.results


@pytest.mark.asyncio
async def test_executor_required_failure_raises_with_partial_run():
    async def search(node, inputs, ctx):
        raise RuntimeError("boom")

    async def merge(node, inputs, ctx):
        return {"kind": "merged", "results": []}

    graph = _graph([_search_node("s1", "a"), _merge_node(("s1",))])
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})

    with pytest.raises(GraphExecutionError) as exc:
        await executor.run(graph, _context())
    assert exc.value.details["run"]["status"] == "failed"


@pytest.mark.asyncio
async def test_executor_deadline_raises_timeout():
    async def search(node, inputs, ctx):
        await asyncio.sleep(0.3)
        return {"kind": "search", "results": [], "metadata": {}}

    async def merge(node, inputs, ctx):
        return {"kind": "merged", "results": []}

    graph = _graph(
        [_search_node("s1", "a"), _merge_node(("s1",))],
        deadline_ms=150,
    )
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})

    with pytest.raises(GraphTimeoutError):
        await executor.run(graph, _context())


@pytest.mark.asyncio
async def test_executor_node_timeout_marks_failure():
    async def search(node, inputs, ctx):
        await asyncio.sleep(0.2)
        return {"kind": "search", "results": [], "metadata": {}}

    async def merge(node, inputs, ctx):
        return {"kind": "merged", "results": []}

    node = QueryNode(id="s1", op="SEARCH", query_template="a", timeout_ms=50)
    graph = _graph([node, _merge_node(("s1",))], deadline_ms=1000)
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})

    with pytest.raises(GraphExecutionError):
        await executor.run(graph, _context())


@pytest.mark.asyncio
async def test_executor_request_budget_is_enforced():
    async def search(node, inputs, ctx):
        return {"kind": "search", "results": [], "metadata": {}}

    async def merge(node, inputs, ctx):
        return {"kind": "merged", "results": []}

    graph = _graph(
        [_search_node("s1", "a"), _search_node("s2", "b"), _merge_node(("s1", "s2"))],
        max_requests=1,
    )
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})

    with pytest.raises(GraphExecutionError) as exc:
        await executor.run(graph, _context())
    detail = exc.value.details["run"]
    assert any(
        node.get("error_code") == "graph_budget_exceeded" for node in detail["nodes"]
    )


@pytest.mark.asyncio
async def test_hedge_starts_a_second_attempt_when_the_first_is_slow():
    """`hedge_after_ms` must actually cause a duplicate attempt.

    The field was declared on QueryNode, excluded from the cache key and read
    by nothing, so hedging did not exist. A slow primary should now trigger a
    second attempt and the first success should win.
    """
    calls = []

    async def search(node, inputs, ctx):
        engine = node.engine_policy[0]
        calls.append(engine)
        await asyncio.sleep(0.30 if engine == "slow" else 0.02)
        return {"kind": "search", "results": [{"link": "https://x.example"}]}

    async def merge(node, inputs, ctx):
        return {"kind": "merged", "results": []}

    graph = _graph(
        [
            QueryNode(
                id="s1", op="SEARCH", query_template="q",
                engine_policy=("slow", "fast"), timeout_ms=5000,
                hedge_after_ms=50,
            ),
            _merge_node(("s1",)),
        ],
        deadline_ms=10000,
    )
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})
    started = time.perf_counter()
    run = await executor.run(graph, _context())
    elapsed = time.perf_counter() - started

    assert run.status == "ok"
    # Both engines were tried: the hedge fired.
    assert "slow" in calls and "fast" in calls, f"no hedge happened: {calls}"
    # The fast hedge won, so the run is not bounded by the slow attempt.
    assert elapsed < 0.28, f"hedge did not take over: {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_hedge_does_not_fire_when_primary_is_fast():
    """A prompt primary must not trigger a wasted duplicate request."""
    calls = []

    async def search(node, inputs, ctx):
        calls.append(node.engine_policy[0])
        await asyncio.sleep(0.01)
        return {"kind": "search", "results": [{"link": "https://x.example"}]}

    async def merge(node, inputs, ctx):
        return {"kind": "merged", "results": []}

    graph = _graph(
        [
            QueryNode(
                id="s1", op="SEARCH", query_template="q",
                engine_policy=("fast", "other"), timeout_ms=5000,
                hedge_after_ms=200,
            ),
            _merge_node(("s1",)),
        ],
        deadline_ms=10000,
    )
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})
    run = await executor.run(graph, _context())

    assert run.status == "ok"
    assert calls == ["fast"], f"hedged unnecessarily: {calls}"


@pytest.mark.asyncio
async def test_hedge_respects_the_request_budget():
    """Hedging must not run when the request budget is already spent."""
    calls = []

    async def search(node, inputs, ctx):
        calls.append(node.engine_policy[0])
        await asyncio.sleep(0.2)
        return {"kind": "search", "results": [{"link": "https://x.example"}]}

    async def merge(node, inputs, ctx):
        return {"kind": "merged", "results": []}

    graph = _graph(
        [
            QueryNode(
                id="s1", op="SEARCH", query_template="q",
                engine_policy=("slow", "fast"), timeout_ms=5000,
                hedge_after_ms=20,
            ),
            _merge_node(("s1",)),
        ],
        deadline_ms=10000,
        max_requests=1,  # no headroom for a second request
    )
    executor = GraphExecutor({"SEARCH": search, "MERGE": merge})
    run = await executor.run(graph, _context())

    assert run.status == "ok"
    assert calls == ["slow"], f"hedged past the request budget: {calls}"
