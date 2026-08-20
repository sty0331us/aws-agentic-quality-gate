"""Deterministic aggregation and PR gate decision."""

from __future__ import annotations

import math
from statistics import mean, median

from eval_common.models import (
    AggregateReport,
    CaseResult,
    GateDecision,
    GitHubContext,
    MetricAggregate,
    MetricName,
    RunStatus,
    RunThresholds,
    utc_now_iso,
)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[low]
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def _scores(results: list[CaseResult], name: str) -> list[float]:
    values: list[float] = []
    for result in results:
        metric = result.metric_map.get(name)
        if metric is not None:
            values.append(metric.score)
    return values


def aggregate_metric(
    results: list[CaseResult],
    name: str,
    threshold: float,
) -> MetricAggregate:
    values = _scores(results, name)
    if not values:
        return MetricAggregate(
            name=name,
            mean=0.0,
            p50=0.0,
            p95=0.0,
            min=0.0,
            max=0.0,
            n=0,
            pass_rate=0.0,
            threshold=threshold,
            gated=True,
        )
    ordered = sorted(values)
    passed = sum(1 for value in values if value >= threshold)
    return MetricAggregate(
        name=name,
        mean=round(mean(values), 4),
        p50=round(median(values), 4),
        p95=round(_percentile(ordered, 0.95), 4),
        min=round(ordered[0], 4),
        max=round(ordered[-1], 4),
        n=len(values),
        pass_rate=round(passed / len(values), 4),
        threshold=threshold,
        gated=mean(values) < threshold,
    )


def decide_gate(
    *,
    overall_score: float,
    metrics: list[MetricAggregate],
    pass_rate: float,
    error_rate: float,
    thresholds: RunThresholds,
    completed_cases: int,
    total_cases: int,
    timed_out: bool,
) -> tuple[RunStatus, GateDecision]:
    failures: list[str] = []
    if timed_out:
        failures.append(f"run timed out before all shards finished ({completed_cases}/{total_cases} cases)")
    if completed_cases == 0:
        failures.append("no case results were produced")
    if error_rate > thresholds.max_error_rate:
        failures.append(f"error_rate {error_rate:.2%} exceeds max {thresholds.max_error_rate:.2%}")
    # Spec: Score >= 0.85 → SUCCESS; Score < 0.85 → FAILED (block merge).
    if overall_score < thresholds.overall:
        failures.append(
            f"overall_score {overall_score:.3f} is below threshold {thresholds.overall:.3f} "
            "(faithfulness, answer_relevance, tool_selection_precision mean)"
        )
    if timed_out and completed_cases == 0:
        return RunStatus.TIMEOUT, GateDecision(passed=False, failures=failures)
    if failures:
        return RunStatus.FAIL, GateDecision(passed=False, failures=failures)
    return RunStatus.PASS, GateDecision(passed=True, failures=[])


def estimate_cost_usd(
    results: list[CaseResult],
    input_usd_per_mtok: float,
    output_usd_per_mtok: float,
) -> float:
    input_tokens = sum(item.input_tokens for item in results)
    output_tokens = sum(item.output_tokens for item in results)
    cost = (input_tokens / 1_000_000) * input_usd_per_mtok + (output_tokens / 1_000_000) * output_usd_per_mtok
    return round(cost, 6)


def build_report(
    *,
    eval_run_id: str,
    results: list[CaseResult],
    total_cases: int,
    thresholds: RunThresholds,
    github: GitHubContext,
    judge_model_id: str,
    timed_out: bool = False,
    input_usd_per_mtok: float = 0.80,
    output_usd_per_mtok: float = 4.00,
) -> AggregateReport:
    error_cases = sum(1 for item in results if item.error)
    passed_cases = sum(1 for item in results if item.case_passed)
    failed_cases = sum(1 for item in results if not item.case_passed and not item.error)
    completed = len(results)
    pass_rate = passed_cases / completed if completed else 0.0
    error_rate = error_cases / completed if completed else 1.0 if total_cases else 0.0

    metric_names = [
        MetricName.FAITHFULNESS,
        MetricName.ANSWER_RELEVANCE,
        MetricName.TOOL_SELECTION_PRECISION,
    ]
    threshold_map = thresholds.as_metric_map()
    metrics = [aggregate_metric(results, name, threshold_map[name]) for name in metric_names]
    scored = [item.mean for item in metrics if item.n > 0]
    overall_score = round(mean(scored), 4) if scored else 0.0
    status, decision = decide_gate(
        overall_score=overall_score,
        metrics=metrics,
        pass_rate=pass_rate,
        error_rate=error_rate,
        thresholds=thresholds,
        completed_cases=completed,
        total_cases=total_cases,
        timed_out=timed_out,
    )
    return AggregateReport(
        eval_run_id=eval_run_id,
        status=status,
        decision=decision,
        overall_score=overall_score,
        total_cases=total_cases,
        completed_cases=completed,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        error_cases=error_cases,
        pass_rate=round(pass_rate, 4),
        error_rate=round(error_rate, 4),
        metrics=metrics,
        thresholds=thresholds,
        github=github,
        judge_model_id=judge_model_id,
        estimated_cost_usd=estimate_cost_usd(results, input_usd_per_mtok, output_usd_per_mtok),
        created_at=utc_now_iso(),
    )
