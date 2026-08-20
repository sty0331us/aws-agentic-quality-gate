"""Optional Ragas backend. Returns None if the library is not installed."""

from __future__ import annotations

import logging

from eval_common.models import AgentTrace, GoldenCase, MetricName, MetricScore

logger = logging.getLogger("aqg.ragas")


def _row(case: GoldenCase, trace: AgentTrace) -> dict[str, object]:
    return {
        "question": case.query,
        "answer": trace.answer,
        "contexts": trace.retrieved_contexts or case.expected_contexts,
        "ground_truth": case.expected_answer or "",
    }


def _score(name: MetricName, value: float) -> MetricScore:
    bounded = max(0.0, min(1.0, float(value)))
    return MetricScore(
        name=name,
        score=round(bounded, 4),
        passed=bounded >= 0.85,
        reasoning="Scored by Ragas.",
        chain_of_thought="Ragas metric over the (question, answer, contexts) triple.",
        details={"backend": "ragas"},
    )


def _evaluate(case: GoldenCase, trace: AgentTrace, metric_name: str) -> float | None:
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, faithfulness
    except ImportError:
        logger.warning("ragas not installed; skipping adapter")
        return None
    metrics = [faithfulness if metric_name == "faithfulness" else answer_relevancy]
    dataset = Dataset.from_list([_row(case, trace)])
    result = evaluate(dataset, metrics=metrics)
    frame = result.to_pandas()
    column = "faithfulness" if metric_name == "faithfulness" else "answer_relevancy"
    if column not in frame.columns:
        return None
    return float(frame.iloc[0][column])


def ragas_faithfulness(case: GoldenCase, trace: AgentTrace) -> MetricScore | None:
    value = _evaluate(case, trace, "faithfulness")
    if value is None:
        return None
    return _score(MetricName.FAITHFULNESS, value)


def ragas_answer_relevance(case: GoldenCase, trace: AgentTrace) -> MetricScore | None:
    value = _evaluate(case, trace, "answer_relevancy")
    if value is None:
        return None
    return _score(MetricName.ANSWER_RELEVANCE, value)
