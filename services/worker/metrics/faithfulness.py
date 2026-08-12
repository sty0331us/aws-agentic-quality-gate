"""Faithfulness (groundedness) via LLM-as-a-Judge or DeepEval/Ragas adapters."""

from __future__ import annotations

from bedrock_judge import BedrockJudge
from eval_common.local_eval import heuristic_faithfulness
from eval_common.models import AgentTrace, EvalBackend, GoldenCase, MetricScore, RunThresholds


def score_faithfulness(
    *,
    case: GoldenCase,
    trace: AgentTrace,
    judge: BedrockJudge | None,
    backend: EvalBackend,
    thresholds: RunThresholds,
    bedrock_enabled: bool,
) -> MetricScore:
    if backend in {EvalBackend.DEEPEVAL, EvalBackend.ALL}:
        from metrics.deepeval_adapter import deepeval_faithfulness

        metric = deepeval_faithfulness(case, trace, thresholds.faithfulness)
        if metric is not None:
            if backend is EvalBackend.DEEPEVAL:
                return metric
    if backend in {EvalBackend.RAGAS, EvalBackend.ALL}:
        from metrics.ragas_adapter import ragas_faithfulness

        metric = ragas_faithfulness(case, trace)
        if metric is not None and backend is EvalBackend.RAGAS:
            return metric
    if judge is not None and bedrock_enabled:
        return judge.faithfulness(case, trace, thresholds.faithfulness)
    return heuristic_faithfulness(case, trace)
