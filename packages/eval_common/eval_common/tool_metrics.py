"""Deterministic tool-selection precision / recall from traces."""

from __future__ import annotations

from eval_common.models import AgentTrace, GoldenCase, MetricName, MetricScore


def tool_selection_score(case: GoldenCase, trace: AgentTrace) -> MetricScore:
    expected = [name.strip() for name in case.expected_tools if name.strip()]
    actual = [name.strip() for name in trace.tool_names if name.strip()]
    expected_set = set(expected)
    actual_set = set(actual)

    if not expected and not actual:
        precision = 1.0
        recall = 1.0
        reasoning = "No tools expected and none were called."
    elif not actual:
        precision = 0.0
        recall = 0.0
        reasoning = f"Expected tools {sorted(expected_set)} but the agent called none."
    elif not expected:
        precision = 0.0
        recall = 1.0
        reasoning = f"No tools were expected but the agent called {sorted(actual_set)}."
    else:
        overlap = expected_set & actual_set
        precision = len(overlap) / len(actual_set)
        recall = len(overlap) / len(expected_set)
        extra = sorted(actual_set - expected_set)
        missing = sorted(expected_set - actual_set)
        bits = [f"overlap={sorted(overlap)}"]
        if extra:
            bits.append(f"unexpected={extra}")
        if missing:
            bits.append(f"missing={missing}")
        reasoning = "; ".join(bits)

    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    # Gate on precision: hallucinated tool calls are the usual production failure mode.
    score = round(precision, 4)
    threshold = 0.80
    return MetricScore(
        name=MetricName.TOOL_SELECTION_PRECISION,
        score=score,
        passed=score >= threshold,
        reasoning=reasoning,
        chain_of_thought=(
            "Tool selection is scored deterministically from the trace. "
            "precision = |expected ∩ actual| / |actual|; "
            f"recall={recall:.3f}; f1={f1:.3f}."
        ),
        details={
            "expected": expected,
            "actual": actual,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
    )
