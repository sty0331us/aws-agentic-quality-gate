"""Optional DeepEval backend. Returns None if the library is not installed."""

from __future__ import annotations

import logging

from eval_common.models import AgentTrace, GoldenCase, MetricName, MetricScore

logger = logging.getLogger("aqg.deepeval")


def _to_metric(name: MetricName, score: float, threshold: float, reasoning: str) -> MetricScore:
    bounded = max(0.0, min(1.0, float(score)))
    return MetricScore(
        name=name,
        score=round(bounded, 4),
        passed=bounded >= threshold,
        reasoning=reasoning,
        chain_of_thought="Scored by DeepEval with an Amazon Bedrock judge model.",
        details={"backend": "deepeval"},
    )


def _test_case(case: GoldenCase, trace: AgentTrace):
    from deepeval.test_case import LLMTestCase

    return LLMTestCase(
        input=case.query,
        actual_output=trace.answer,
        expected_output=case.expected_answer,
        retrieval_context=trace.retrieved_contexts or case.expected_contexts,
    )


def deepeval_faithfulness(case: GoldenCase, trace: AgentTrace, threshold: float) -> MetricScore | None:
    try:
        from deepeval.metrics import FaithfulnessMetric
    except ImportError:
        logger.warning("deepeval not installed; skipping adapter")
        return None
    metric = FaithfulnessMetric(threshold=threshold, include_reason=True)
    metric.measure(_test_case(case, trace))
    return _to_metric(
        MetricName.FAITHFULNESS,
        float(metric.score or 0.0),
        threshold,
        getattr(metric, "reason", "") or "",
    )


def deepeval_answer_relevance(case: GoldenCase, trace: AgentTrace, threshold: float) -> MetricScore | None:
    try:
        from deepeval.metrics import AnswerRelevancyMetric
    except ImportError:
        logger.warning("deepeval not installed; skipping adapter")
        return None
    metric = AnswerRelevancyMetric(threshold=threshold, include_reason=True)
    metric.measure(_test_case(case, trace))
    return _to_metric(
        MetricName.ANSWER_RELEVANCE,
        float(metric.score or 0.0),
        threshold,
        getattr(metric, "reason", "") or "",
    )
