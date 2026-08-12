"""Tool-selection precision. Deterministic by default; LLM optional for arguments."""

from __future__ import annotations

from eval_common.models import AgentTrace, GoldenCase, MetricScore
from eval_common.tool_metrics import tool_selection_score


def score_tool_selection(case: GoldenCase, trace: AgentTrace) -> MetricScore:
    return tool_selection_score(case, trace)
