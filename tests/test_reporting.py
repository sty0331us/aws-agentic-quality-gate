from __future__ import annotations

from eval_common.aggregation import build_report
from eval_common.models import (
    CaseResult,
    GitHubContext,
    MetricName,
    MetricScore,
    RunThresholds,
)
from eval_common.reporting import markdown_report


def test_markdown_includes_gate_table() -> None:
    result = CaseResult(
        eval_run_id="eval-9",
        case_id="c1",
        shard_id=0,
        suite="rag",
        query="q",
        answer="a",
        metrics=[
            MetricScore(name=MetricName.FAITHFULNESS, score=0.8, passed=True),
            MetricScore(name=MetricName.ANSWER_RELEVANCE, score=0.8, passed=True),
            MetricScore(name=MetricName.TOOL_SELECTION_PRECISION, score=1.0, passed=True),
        ],
        judge_model_id="haiku",
    )
    report = build_report(
        eval_run_id="eval-9",
        results=[result],
        total_cases=1,
        thresholds=RunThresholds(),
        github=GitHubContext(repo="acme/agent", pr_number=1, git_sha="deadbeef"),
        judge_model_id="haiku",
    )
    md = markdown_report(report)
    assert "faithfulness" in md
    assert "Overall score" in md
    assert "PASS" in md or "FAIL" in md
