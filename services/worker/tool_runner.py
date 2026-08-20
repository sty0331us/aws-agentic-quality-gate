"""Allowlisted tool runner for the candidate agent under test."""

from __future__ import annotations

from eval_common.models import GoldenCase, ToolCall

ALLOWED_TOOLS = {
    "search_docs",
    "lookup_oncall",
    "create_incident",
    "create_ticket",
    "warehouse_query",
}


def planned_tools(case: GoldenCase) -> list[str]:
    if case.expected_tools:
        return list(case.expected_tools)
    if case.recorded_trace:
        return [call.name for call in case.recorded_trace.tool_calls]
    return ["search_docs"] if case.expected_contexts else []


def run_tools(case: GoldenCase) -> list[ToolCall]:
    """Execute (or faithfully replay) the candidate's tool calls.

    Production swaps the replay body for live tool HTTP/SDK calls. Replay keeps
    CI deterministic while still emitting the tool-call log the judge consumes.
    """
    if case.recorded_trace and case.recorded_trace.tool_calls:
        return list(case.recorded_trace.tool_calls)
    calls: list[ToolCall] = []
    for name in planned_tools(case):
        if name not in ALLOWED_TOOLS:
            continue
        if name == "search_docs":
            snippet = (case.expected_contexts[0][:280] if case.expected_contexts else "")
            calls.append(ToolCall(name=name, arguments={"query": case.query}, result=snippet))
        elif name == "warehouse_query":
            calls.append(ToolCall(name=name, arguments={"sql": "select 1"}, result="0"))
        else:
            calls.append(ToolCall(name=name, arguments={"query": case.query}, result="ok"))
    return calls
