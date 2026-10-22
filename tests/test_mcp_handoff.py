"""MCP session_handoff: the structured brief passed between agents.

The handoff payload is defined in docs/v0.4.0/MASTER_PLAN.md §5.2 as goal,
evidence ids, open questions, contradictions, budget left, and next suggested
node. These tests pin the properties that make it useful to a receiving agent:
ids it can resolve, gaps it can act on, and no invented budget figures.
"""

from __future__ import annotations

import json

import pytest

from jiro.ai.tools import mcp_tools
from jiro.mcp import HANDOFF_ROLES, build_handoff_brief


def _run(**overrides):
    run = {
        "graph_id": "g_1",
        "status": "ok",
        "intent": "entity_profile",
        "duration_ms": 165,
        "budget": {
            "requests_used": 3,
            "bytes_used": 285332,
            "nodes_used": 9,
            "tokens_used": 846,
        },
        "engine_attribution": {"google": 2},
        "gaps": [],
        "packet": {
            "packet_id": "p1",
            "provenance_digest": "abc",
            "budget_tokens": 1200,
            "used_tokens": 846,
            "claims": [
                {"id": "c1", "text": "a", "source_ids": ["s2"]},
                {"id": "c2", "text": "b", "source_ids": ["s2", "s3"]},
            ],
            "sources": [{"id": "s2", "url": "https://x"}, {"id": "s3", "url": "https://y"}],
            "contradictions": [],
            "gaps": [],
        },
    }
    run.update(overrides)
    return run


def test_handoff_tool_is_registered():
    names = [t["name"] for t in mcp_tools()]
    assert "session_handoff" in names
    schema = next(t for t in mcp_tools() if t["name"] == "session_handoff")
    assert schema["inputSchema"]["required"] == ["query"]
    assert set(schema["inputSchema"]["properties"]["role"]["enum"]) == set(HANDOFF_ROLES)


def test_brief_carries_resolvable_ids():
    brief = build_handoff_brief(_run(), query="capital of France", role="verifier")
    assert brief["schema"] == "jiro.handoff/1"
    assert brief["goal"] == "capital of France"
    assert brief["role"] == "verifier"
    assert brief["graph_id"] == "g_1"
    assert brief["packet_id"] == "p1"
    assert brief["provenance_digest"] == "abc"
    assert brief["claim_ids"] == ["c1", "c2"]
    assert brief["source_ids"] == ["s2", "s3"]
    # Every id the receiver might resolve is present exactly once.
    assert brief["evidence_ids"] == ["c1", "c2", "s2", "s3"]


def test_brief_flags_claims_with_no_source():
    run = _run()
    run["packet"]["claims"] = [
        {"id": "c1", "text": "a", "source_ids": ["s2"]},
        {"id": "c2", "text": "b", "source_ids": []},
    ]
    brief = build_handoff_brief(run, query="q", role="writer")
    assert brief["unresolvable_claim_ids"] == ["c2"]


def test_brief_turns_gaps_into_open_questions():
    run = _run(gaps=[{"node_id": "n3", "query": "sub query", "reason": "timeout"}])
    brief = build_handoff_brief(run, query="q", role="planner")
    assert len(brief["open_questions"]) == 1
    q = brief["open_questions"][0]
    assert q["node_id"] == "n3"
    assert q["query"] == "sub query"
    assert q["reason"] == "timeout"


def test_brief_merges_run_and_packet_gaps():
    run = _run(gaps=[{"node_id": "n1", "reason": "engine down"}])
    run["packet"]["gaps"] = [{"node_id": "n2", "reason": "no evidence"}]
    brief = build_handoff_brief(run, query="q", role="verifier")
    assert {q["node_id"] for q in brief["open_questions"]} == {"n1", "n2"}


def test_brief_reports_unknown_remaining_requests_as_null():
    """A brief must not invent a budget figure it cannot derive.

    The run's budget dict reports what was *used*. How many requests remained
    is only knowable when the graph's declared ceiling was carried through, so
    when it was not this is null rather than a fabricated number.
    """
    brief = build_handoff_brief(_run(), query="q", role="writer")
    assert brief["budget_left"]["requests"] is None
    assert brief["budget_left"]["requests_used"] == 3
    assert brief["budget_left"]["tokens_budget"] == 1200


def test_brief_computes_remaining_requests_when_the_ceiling_is_known():
    run = _run(budget_spec={"max_requests": 10})
    brief = build_handoff_brief(run, query="q", role="writer")
    assert brief["budget_left"]["requests"] == 7


def test_next_node_is_suggested_per_role():
    assert build_handoff_brief(_run(), query="q", role="verifier")["next_suggested_node"] == "VERIFY"
    assert build_handoff_brief(_run(), query="q", role="writer")["next_suggested_node"] == "SYNTHESIZE"
    assert build_handoff_brief(_run(), query="q", role="retriever")["next_suggested_node"] == "FETCH"


def test_brief_survives_an_empty_packet():
    run = _run()
    run["packet"] = {}
    brief = build_handoff_brief(run, query="q", role="writer")
    assert brief["evidence_ids"] == []
    assert brief["claim_ids"] == []
    assert brief["contradictions"] == []
    assert brief["goal"] == "q"


@pytest.mark.asyncio
async def test_handoff_end_to_end_against_a_fake_graph_service():
    """The tool must return a brief, not the raw run."""
    from jiro.mcp import JiroMCPServer

    class _Service:
        async def run(self, query, **kwargs):
            assert kwargs["deadline_ms"] is None, "0 must mean 'derive it'"

            class _Run:
                def to_dict(self):
                    return _run()

            return _Run()

    server = JiroMCPServer.__new__(JiroMCPServer)

    async def _require_graph():
        return _Service()

    server._require_graph = _require_graph  # type: ignore[method-assign]

    from jiro.mcp import ProgressReporter

    result = await JiroMCPServer._tool_session_handoff(
        server,
        {"query": "capital of France", "role": "verifier", "deadline_ms": 0},
        ProgressReporter(),
    )
    assert result["content"], "no content returned"
    text = result["content"][0]["text"]
    # The text is "<summary>\n\n---\n\n<json>"; the tools return JSON in the
    # text content, so parse the payload back out of it.
    payload = json.loads(text.split("---\n\n", 1)[1])
    assert payload["schema"] == "jiro.handoff/1"
    assert payload["role"] == "verifier"
    assert payload["goal"] == "capital of France"
    assert payload["claim_ids"] == ["c1", "c2"]
    # The brief must be returned, not the raw run.
    assert "results" not in payload


@pytest.mark.asyncio
async def test_handoff_rejects_an_unknown_role():
    from jiro.mcp import MCPError, JiroMCPServer, ProgressReporter

    server = JiroMCPServer.__new__(JiroMCPServer)

    async def _require_graph():
        raise AssertionError("must validate the role before touching the service")

    server._require_graph = _require_graph  # type: ignore[method-assign]

    with pytest.raises(MCPError) as exc:
        await JiroMCPServer._tool_session_handoff(
            server, {"query": "q", "role": "emperor"}, ProgressReporter()
        )
    assert "role" in str(exc.value)
