"""Query graph planner tests."""

from __future__ import annotations

import pytest

from jiro.graph.planner import GraphPlanner


def test_planner_is_deterministic():
    planner = GraphPlanner()
    a = planner.plan("what is rust ownership", fetch_top=2)
    b = planner.plan("what is rust ownership", fetch_top=2)
    assert a.graph.graph_id == b.graph.graph_id
    assert a.graph.canonical_json() == b.graph.canonical_json()


def test_planner_builds_search_merge_compress():
    result = GraphPlanner().plan("what is rust ownership")
    ops = [node.op for node in result.graph.nodes]
    assert ops.count("SEARCH") == 2
    assert ops[-2:] == ["MERGE", "COMPRESS"]
    assert result.graph.budget.max_tokens == 1200


def test_planner_adds_optional_fetch_when_requested():
    result = GraphPlanner().plan("what is rust ownership", fetch_top=2)
    fetch = [node for node in result.graph.nodes if node.op == "FETCH"]
    assert len(fetch) == 1
    assert fetch[0].required is False
    assert fetch[0].inputs == ("s1", "s2")
    compress = next(node for node in result.graph.nodes if node.op == "COMPRESS")
    assert compress.inputs == ("m1", "f1")


def test_planner_detects_scrape_url():
    result = GraphPlanner().plan("https://example.com/article")
    assert result.graph.intent == "scrape"
    assert [node.op for node in result.graph.nodes] == ["FETCH", "COMPRESS"]


def test_planner_detects_news_and_sets_policy():
    result = GraphPlanner().plan("latest AI regulation news")
    assert result.graph.intent == "news_check"
    assert result.graph.policy["type"] == "news"


def test_planner_rejects_invalid_budget_inputs():
    planner = GraphPlanner()
    with pytest.raises(ValueError):
        planner.plan("x", num=0)
    with pytest.raises(ValueError):
        planner.plan("x", fetch_top=11)
    with pytest.raises(ValueError):
        planner.plan("x", max_nodes=1)
