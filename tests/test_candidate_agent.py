from __future__ import annotations

from eval_common.models import AgentTrace, GoldenCase, Suite, ToolCall
from rag_index import retrieve
from tool_runner import run_tools


def test_retrieve_ranks_expected_contexts() -> None:
    case = GoldenCase(
        id="c1",
        suite=Suite.RAG,
        query="refund window annual plans",
        expected_contexts=[
            "Refund policy: annual subscriptions may be refunded within 14 days.",
            "Unrelated parking policy.",
        ],
    )
    hits = retrieve(case, k=1)
    assert hits
    assert "14 days" in hits[0]


def test_tool_runner_emits_expected_tools() -> None:
    case = GoldenCase(
        id="c2",
        suite=Suite.TOOL_CALLING,
        query="page payments",
        expected_tools=["lookup_oncall", "create_incident"],
        recorded_trace=AgentTrace(
            answer="ok",
            tool_calls=[
                ToolCall(name="lookup_oncall"),
                ToolCall(name="create_incident"),
            ],
        ),
    )
    calls = run_tools(case)
    assert [c.name for c in calls] == ["lookup_oncall", "create_incident"]
