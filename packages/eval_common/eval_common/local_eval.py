"""Offline local evaluation of a golden dataset (no AWS required)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval_common.aggregation import build_report
from eval_common.models import (
    AgentTrace,
    CaseResult,
    GitHubContext,
    GoldenCase,
    GoldenDataset,
    MetricName,
    MetricScore,
    RunThresholds,
)
from eval_common.reporting import markdown_report
from eval_common.tool_metrics import tool_selection_score


def _overlap(expected: list[str], answer: str) -> float:
    if not expected:
        return 1.0 if answer.strip() else 0.0
    haystack = answer.lower()
    hits = sum(1 for ctx in expected if ctx.lower()[:80] in haystack or haystack[:80] in ctx.lower())
    if hits:
        return min(1.0, 0.6 + 0.4 * hits / max(len(expected), 1))
    tokens = set(haystack.split())
    ctx_tokens = set(" ".join(expected).lower().split())
    if not tokens or not ctx_tokens:
        return 0.0
    return round(len(tokens & ctx_tokens) / max(len(tokens), 1), 4)


def heuristic_faithfulness(case: GoldenCase, trace: AgentTrace) -> MetricScore:
    if case.expected_answer and trace.answer.strip() == case.expected_answer.strip():
        score = 1.0
    else:
        contexts = trace.retrieved_contexts or case.expected_contexts
        score = _overlap(contexts, trace.answer)
    return MetricScore(
        name=MetricName.FAITHFULNESS,
        score=score,
        passed=score >= 0.85,
        reasoning="Heuristic lexical overlap with retrieved/expected contexts (local backend).",
        chain_of_thought=("Used for dry-runs when Bedrock is disabled. Not a substitute for LLM-as-a-Judge."),
        details={"backend": "heuristic"},
    )


def heuristic_relevance(case: GoldenCase, trace: AgentTrace) -> MetricScore:
    if case.expected_answer and trace.answer.strip() == case.expected_answer.strip():
        score = 1.0
    else:
        query_tokens = set(case.query.lower().split())
        answer_tokens = set(trace.answer.lower().split())
        if not query_tokens or not answer_tokens:
            score = 0.0
        else:
            score = round(
                min(1.0, 2 * len(query_tokens & answer_tokens) / max(len(query_tokens), 1)),
                4,
            )
            if case.expected_answer:
                expected_tokens = set(case.expected_answer.lower().split())
                score = round(
                    max(score, len(answer_tokens & expected_tokens) / max(len(expected_tokens), 1)),
                    4,
                )
    return MetricScore(
        name=MetricName.ANSWER_RELEVANCE,
        score=score,
        passed=score >= 0.85,
        reasoning="Heuristic token overlap between query/expected answer and actual answer.",
        chain_of_thought="Local dry-run scorer.",
        details={"backend": "heuristic"},
    )


def evaluate_case(case: GoldenCase) -> CaseResult:
    trace = case.recorded_trace or AgentTrace(answer=case.expected_answer or "")
    metrics = [
        heuristic_faithfulness(case, trace),
        heuristic_relevance(case, trace),
        tool_selection_score(case, trace),
    ]
    return CaseResult(
        eval_run_id="local",
        case_id=case.id,
        shard_id=0,
        suite=case.suite,
        query=case.query,
        answer=trace.answer,
        metrics=metrics,
        judge_model_id="heuristic-local",
    )


def evaluate_dataset(dataset: GoldenDataset, thresholds: RunThresholds | None = None) -> tuple[list[CaseResult], str]:
    results = [evaluate_case(case) for case in dataset.cases]
    report = build_report(
        eval_run_id="local",
        results=results,
        total_cases=len(dataset.cases),
        thresholds=thresholds or RunThresholds(),
        github=GitHubContext(),
        judge_model_id="heuristic-local",
    )
    return results, markdown_report(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local dry-run of the agentic quality gate")
    parser.add_argument("dataset", type=Path, help="Path to golden dataset JSON")
    parser.add_argument("--json", action="store_true", help="Print aggregate JSON instead of markdown")
    args = parser.parse_args(argv)
    dataset = GoldenDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    results = [evaluate_case(case) for case in dataset.cases]
    report = build_report(
        eval_run_id="local",
        results=results,
        total_cases=len(dataset.cases),
        thresholds=RunThresholds(),
        github=GitHubContext(),
        judge_model_id="heuristic-local",
    )
    if args.json:
        json.dump(report.model_dump(mode="json"), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(markdown_report(report) + "\n")
    return 0 if report.decision.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
