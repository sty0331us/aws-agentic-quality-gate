from __future__ import annotations

import pytest
from eval_common.models import AgentTrace, GoldenCase, Suite, ToolCall
from eval_common.tool_metrics import tool_selection_score


def _case(expected_tools: list[str]) -> GoldenCase:
    return GoldenCase(
        id="case-1",
        suite=Suite.TOOL_CALLING,
        query="q",
        expected_tools=expected_tools,
    )


@pytest.mark.unit
def test_precision_penalizes_unexpected_tools() -> None:
    trace = AgentTrace(
        answer="ok",
        tool_calls=[ToolCall(name="search_docs"), ToolCall(name="delete_bucket")],
    )
    metric = tool_selection_score(_case(["search_docs"]), trace)
    assert metric.score == 0.5
    assert metric.details["recall"] == 1.0
    assert not metric.passed


@pytest.mark.unit
def test_perfect_tool_match() -> None:
    trace = AgentTrace(
        answer="ok",
        tool_calls=[ToolCall(name="create_ticket"), ToolCall(name="search_docs")],
    )
    metric = tool_selection_score(_case(["search_docs", "create_ticket"]), trace)
    assert metric.score == 1.0
    assert metric.passed


@pytest.mark.unit
def test_no_tools_expected_or_called() -> None:
    metric = tool_selection_score(_case([]), AgentTrace(answer="ok"))
    assert metric.score == 1.0
