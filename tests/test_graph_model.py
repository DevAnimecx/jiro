"""Query graph contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jiro.graph.model import Budget, QueryGraph, QueryNode


def _graph(nodes, *, budget=None) -> QueryGraph:
    return QueryGraph(
        graph_id="pending",
        intent="fact_lookup",
        nodes=tuple(nodes),
        budget=budget or Budget(),
    ).with_derived_id()


def test_graph_derives_stable_id_and_keeps_schema_alias():
    graph = _graph(
        [
            QueryNode(id="s1", op="SEARCH", query_template="rust ownership"),
            QueryNode(id="c1", op="COMPRESS", inputs=("s1",)),
        ]
    )
    assert graph.graph_id.startswith("g_")
    assert graph.graph_schema == "jiro.graph/v1"
    assert graph.model_dump(mode="json", by_alias=True)["schema"] == "jiro.graph/v1"
    assert graph.graph_id == graph.with_derived_id().graph_id


def test_graph_rejects_unknown_inputs():
    with pytest.raises(ValidationError):
        _graph(
            [
                QueryNode(id="s1", op="SEARCH", query_template="x"),
                QueryNode(id="m1", op="MERGE", inputs=("missing",)),
            ]
        )


def test_graph_rejects_cycles():
    with pytest.raises(ValidationError):
        _graph(
            [
                QueryNode(id="m1", op="MERGE", inputs=("m2",)),
                QueryNode(id="m2", op="MERGE", inputs=("m1",)),
            ]
        )


def test_graph_rejects_depth_over_budget():
    with pytest.raises(ValidationError):
        _graph(
            [
                QueryNode(id="s1", op="SEARCH", query_template="x"),
                QueryNode(id="m1", op="MERGE", inputs=("s1",)),
                QueryNode(id="m2", op="MERGE", inputs=("m1",)),
                QueryNode(id="m3", op="MERGE", inputs=("m2",)),
            ],
            budget=Budget(max_depth=1),
        )


def test_graph_rejects_wave_wider_than_fanout():
    with pytest.raises(ValidationError):
        _graph(
            [
                QueryNode(id="s1", op="SEARCH", query_template="a"),
                QueryNode(id="s2", op="SEARCH", query_template="b"),
                QueryNode(id="s3", op="SEARCH", query_template="c"),
                QueryNode(id="m1", op="MERGE", inputs=("s1", "s2", "s3")),
            ],
            budget=Budget(max_fanout=2),
        )


def test_graph_rejects_node_count_over_budget():
    with pytest.raises(ValidationError):
        _graph(
            [
                QueryNode(id="s1", op="SEARCH", query_template="a"),
                QueryNode(id="m1", op="MERGE", inputs=("s1",)),
            ],
            budget=Budget(max_nodes=1),
        )


def test_search_node_must_be_root_and_have_query():
    with pytest.raises(ValidationError):
        QueryNode(id="s1", op="SEARCH", query_template="")
    with pytest.raises(ValidationError):
        QueryNode(id="s1", op="SEARCH", query_template="x", inputs=("s2",))
