"""CloudWatch custom metrics: Faithfulness, Answer Relevance, Tool Precision."""

from __future__ import annotations

import logging
import os
from typing import Any

from eval_common.models import AggregateReport, MetricName

logger = logging.getLogger("aqg.cloudwatch")
NAMESPACE = "AgenticQualityGate"


def publish_gate_metrics(cloudwatch: Any, report: AggregateReport) -> None:
    dims = [
        {"Name": "Environment", "Value": os.environ.get("ENVIRONMENT", "dev")},
        {"Name": "GitSha", "Value": (report.github.git_sha or "unknown")[:40]},
    ]
    pairs: list[tuple[str, float]] = [
        ("OverallScore", report.overall_score),
        ("PassRate", report.pass_rate),
        ("ErrorRate", report.error_rate),
        ("GatePass", 1.0 if report.decision.passed else 0.0),
    ]
    name_map = {
        MetricName.FAITHFULNESS: "Faithfulness",
        MetricName.ANSWER_RELEVANCE: "AnswerRelevance",
        MetricName.TOOL_SELECTION_PRECISION: "ToolPrecision",
    }
    for metric in report.metrics:
        cloudwatch_name = name_map.get(str(metric.name), str(metric.name))
        pairs.append((cloudwatch_name, metric.mean))
    cloudwatch.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[
            {
                "MetricName": name,
                "Value": value,
                "Unit": "None",
                "Dimensions": dims,
            }
            for name, value in pairs
        ],
    )
    logger.info("published CloudWatch metrics", extra={"overall": report.overall_score})
