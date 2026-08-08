"""Render GitHub Check summaries and PR comments from an aggregate report."""

from __future__ import annotations

from eval_common.models import AggregateReport, MetricAggregate


def _status_emoji(passed: bool) -> str:
    return "✅" if passed else "❌"


def _metric_row(metric: MetricAggregate) -> str:
    flag = _status_emoji(not metric.gated and metric.n > 0)
    return (
        f"| `{metric.name}` | {metric.mean:.3f} | {metric.p50:.3f} | {metric.p95:.3f} "
        f"| {metric.threshold:.2f} | {metric.pass_rate:.0%} | {flag} |"
    )


def markdown_report(report: AggregateReport) -> str:
    headline = "PASS" if report.decision.passed else "FAIL"
    lines = [
        f"## Agentic Quality Gate — **{headline}**",
        "",
        f"- Run: `{report.eval_run_id}`",
        f"- Judge: `{report.judge_model_id}`",
        f"- Cases: **{report.passed_cases}/{report.completed_cases}** passed "
        f"({report.pass_rate:.0%} pass rate, {report.error_rate:.0%} errors)",
        f"- Estimated Bedrock cost: **${report.estimated_cost_usd:.4f}**",
        "",
        "| Metric | Mean | p50 | p95 | Threshold | Pass rate | Gate |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(_metric_row(metric) for metric in report.metrics)
    if report.decision.failures:
        lines.extend(["", "### Gate failures", ""])
        lines.extend(f"- {item}" for item in report.decision.failures)
    lines.extend(
        [
            "",
            "<sub>Automated by the Agentic CI/CD Evaluation Engine "
            "(DeepEval/Ragas-compatible metrics, Amazon Bedrock LLM-as-a-Judge).</sub>",
        ]
    )
    return "\n".join(lines)


def check_title(report: AggregateReport) -> str:
    verb = "passed" if report.decision.passed else "failed"
    return f"Agentic quality gate {verb} ({report.pass_rate:.0%} cases, run {report.eval_run_id})"
