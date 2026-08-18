from __future__ import annotations

from eval_common.models import AgentTrace, GoldenCase, Suite, ToolCall


def make_case(**overrides: object) -> GoldenCase:
    base = dict(
        id="case-1",
        suite=Suite.HYBRID,
        query="What is the refund window?",
        expected_answer="14 days for annual plans.",
        expected_contexts=["annual subscriptions may be refunded within 14 days"],
        expected_tools=["search_docs"],
        recorded_trace=AgentTrace(
            answer="14 days for annual plans.",
            retrieved_contexts=["annual subscriptions may be refunded within 14 days"],
            tool_calls=[ToolCall(name="search_docs", arguments={"q": "refund"})],
        ),
    )
    base.update(overrides)
    return GoldenCase.model_validate(base)
