from __future__ import annotations

import pytest
from eval_common.aggregation import build_report
from eval_common.models import (
    CaseResult,
    GitHubContext,
    MetricName,
    MetricScore,
    RunStatus,
    RunThresholds,
)


def _result(case_id: str, scores: dict[str, float], error: str | None = None) -> CaseResult:
    metrics = [
        MetricScore(
            name=name,
            score=score,
            passed=score >= 0.7,
            reasoning="test",
        )
        for name, score in scores.items()
    ]
    return CaseResult(
        eval_run_id="eval-1",
        case_id=case_id,
        shard_id=0,
        suite="rag",
        query="q",
        answer="a",
        metrics=metrics,
        judge_model_id="test",
        error=error,
        input_tokens=1000,
        output_tokens=200,
    )


@pytest.mark.unit
def test_gate_passes_when_means_and_pass_rate_clear_thresholds() -> None:
    results = [
        _result(
            f"c{i}",
            {
                MetricName.FAITHFULNESS: 0.9,
                MetricName.ANSWER_RELEVANCE: 0.85,
                MetricName.TOOL_SELECTION_PRECISION: 1.0,
            },
        )
        for i in range(10)
    ]
    report = build_report(
        eval_run_id="eval-1",
        results=results,
        total_cases=10,
        thresholds=RunThresholds(),
        github=GitHubContext(),
        judge_model_id="test",
    )
    assert report.status is RunStatus.PASS
    assert report.decision.passed
    assert report.metric(MetricName.FAITHFULNESS).mean == 0.9
    assert report.overall_score >= 0.85


@pytest.mark.unit
def test_gate_fails_on_low_faithfulness() -> None:
    results = [
        _result(
            "c1",
            {
                MetricName.FAITHFULNESS: 0.2,
                MetricName.ANSWER_RELEVANCE: 0.9,
                MetricName.TOOL_SELECTION_PRECISION: 1.0,
            },
        )
    ]
    report = build_report(
        eval_run_id="eval-1",
        results=results,
        total_cases=1,
        thresholds=RunThresholds(),
        github=GitHubContext(),
        judge_model_id="test",
    )
    assert report.status is RunStatus.FAIL
    assert any("overall_score" in item for item in report.decision.failures)


@pytest.mark.unit
def test_gate_passes_at_overall_threshold() -> None:
    results = [
        _result(
            "c1",
            {
                MetricName.FAITHFULNESS: 0.85,
                MetricName.ANSWER_RELEVANCE: 0.85,
                MetricName.TOOL_SELECTION_PRECISION: 0.85,
            },
        )
    ]
    report = build_report(
        eval_run_id="eval-1",
        results=results,
        total_cases=1,
        thresholds=RunThresholds(),
        github=GitHubContext(),
        judge_model_id="test",
    )
    assert report.overall_score == 0.85
    assert report.status is RunStatus.PASS
    assert report.decision.passed


@pytest.mark.unit
def test_timeout_with_no_results() -> None:
    report = build_report(
        eval_run_id="eval-1",
        results=[],
        total_cases=4,
        thresholds=RunThresholds(),
        github=GitHubContext(),
        judge_model_id="test",
        timed_out=True,
    )
    assert report.status is RunStatus.TIMEOUT
    assert not report.decision.passed
