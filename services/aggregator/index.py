"""Aggregator Lambda: collect shard results, apply thresholds, gate the PR."""

from __future__ import annotations

import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from botocore.config import Config
from cloudwatch_metrics import publish_gate_metrics
from eval_common.aggregation import build_report
from eval_common.aws import get_json, list_json_keys, put_json_model, report_key
from eval_common.models import CaseResult, RunStatus
from eval_common.reporting import markdown_report
from eval_common.runs import get_run, load_manifest_s3, put_run
from github_status import publish_github_gate
from opensearch_sink import index_report
from slack_alert import notify_slack_failure

logger = Logger(service="aqg-aggregator")
tracer = Tracer(service="aqg-aggregator")
metrics = Metrics(namespace="AgenticQualityGate", service="aggregator")
_RETRY = Config(retries={"max_attempts": 8, "mode": "adaptive"})


def _clients() -> dict[str, Any]:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return {
        "s3": boto3.client("s3", region_name=region, config=_RETRY),
        "ddb": boto3.client("dynamodb", region_name=region, config=_RETRY),
        "secrets": boto3.client("secretsmanager", region_name=region, config=_RETRY),
        "cloudwatch": boto3.client("cloudwatch", region_name=region, config=_RETRY),
    }


def _load_results(s3: Any, bucket: str, eval_run_id: str) -> list[CaseResult]:
    keys = list_json_keys(s3, bucket, f"runs/{eval_run_id}/results/")
    results: list[CaseResult] = []
    for key in keys:
        payload = get_json(s3, bucket, key)
        if isinstance(payload, list):
            results.extend(CaseResult.model_validate(item) for item in payload)
        elif isinstance(payload, dict) and "metrics" in payload:
            results.append(CaseResult.model_validate(payload))
        elif isinstance(payload, dict) and "results" in payload:
            results.extend(CaseResult.model_validate(item) for item in payload["results"])
    # Last write wins if a shard was retried.
    by_case: dict[str, CaseResult] = {}
    for item in results:
        by_case[item.case_id] = item
    return list(by_case.values())


def _run_timed_out(manifest_created_at: str, timeout_seconds: int) -> bool:
    from datetime import UTC, datetime

    stamp = manifest_created_at.replace("Z", "+00:00")
    created = datetime.fromisoformat(stamp)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (datetime.now(UTC) - created).total_seconds() >= timeout_seconds


def _list_open_run_ids(ddb: Any, table: str) -> list[str]:
    run_ids: list[str] = []
    kwargs: dict[str, Any] = {
        "TableName": table,
        "ProjectionExpression": "eval_run_id, #s",
        "ExpressionAttributeNames": {"#s": "status"},
    }
    while True:
        page = ddb.scan(**kwargs)
        for item in page.get("Items", []):
            status = item.get("status", {}).get("S")
            if status in {RunStatus.PENDING.value, RunStatus.RUNNING.value, RunStatus.AGGREGATING.value}:
                run_ids.append(item["eval_run_id"]["S"])
        if not page.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return run_ids


def aggregate_run(event: dict[str, Any], context: Any) -> dict[str, Any]:
    payload = event.get("detail") if isinstance(event.get("detail"), dict) else event
    eval_run_id = payload.get("eval_run_id")
    force = bool(payload.get("force"))
    if not eval_run_id:
        raise ValueError("eval_run_id is required")

    results_bucket = os.environ["RESULTS_BUCKET"]
    table = os.environ["RUNS_TABLE_NAME"]
    timeout_seconds = int(os.environ.get("RUN_TIMEOUT_SECONDS", "1800"))
    clients = _clients()

    manifest = get_run(clients["ddb"], table, eval_run_id)
    if manifest is None:
        manifest = load_manifest_s3(clients["s3"], results_bucket, eval_run_id)

    if manifest.status in {RunStatus.PASS, RunStatus.FAIL} and not force:
        logger.info("run already gated", extra={"eval_run_id": eval_run_id, "status": manifest.status})
        return {"eval_run_id": eval_run_id, "status": manifest.status.value, "idempotent": True}

    results = _load_results(clients["s3"], results_bucket, eval_run_id)
    timed_out = _run_timed_out(manifest.created_at, timeout_seconds)
    complete = len(results) >= manifest.total_cases or manifest.completed_shards >= manifest.total_shards

    if not complete and not timed_out and not force:
        logger.info(
            "run incomplete; waiting",
            extra={
                "eval_run_id": eval_run_id,
                "completed_cases": len(results),
                "total_cases": manifest.total_cases,
            },
        )
        return {
            "eval_run_id": eval_run_id,
            "status": RunStatus.RUNNING.value,
            "completed_cases": len(results),
            "total_cases": manifest.total_cases,
        }

    manifest.status = RunStatus.AGGREGATING
    put_run(clients["ddb"], table, manifest, clients["s3"], results_bucket)

    report = build_report(
        eval_run_id=eval_run_id,
        results=results,
        total_cases=manifest.total_cases,
        thresholds=manifest.thresholds,
        github=manifest.github,
        judge_model_id=manifest.judge_model_id,
        timed_out=timed_out and not complete,
        input_usd_per_mtok=float(os.environ.get("JUDGE_INPUT_USD_PER_MTOK", "0.80")),
        output_usd_per_mtok=float(os.environ.get("JUDGE_OUTPUT_USD_PER_MTOK", "4.00")),
    )
    report_uri = put_json_model(clients["s3"], results_bucket, report_key(eval_run_id), report)
    report.s3_report_uri = report_uri
    put_json_model(clients["s3"], results_bucket, report_key(eval_run_id), report)

    md = markdown_report(report)
    clients["s3"].put_object(
        Bucket=results_bucket,
        Key=f"runs/{eval_run_id}/report.md",
        Body=md.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )

    github_result = publish_github_gate(
        report=report,
        markdown=md,
        secrets=clients["secrets"],
        token_override=os.environ.get("GITHUB_TOKEN") or None,
        secret_arn=os.environ.get("GITHUB_SECRET_ARN") or None,
    )
    slack_result: dict[str, Any] = {"notified": False}
    try:
        slack_result = notify_slack_failure(
            report=report,
            markdown=md,
            secrets=clients["secrets"],
            webhook_override=os.environ.get("SLACK_WEBHOOK_URL") or None,
            secret_arn=os.environ.get("SLACK_SECRET_ARN") or os.environ.get("GITHUB_SECRET_ARN") or None,
        )
    except Exception:
        logger.exception("slack alert failed")
    try:
        publish_gate_metrics(clients["cloudwatch"], report)
    except Exception:
        logger.exception("cloudwatch metrics failed")
    try:
        index_report(report, results)
    except Exception:
        logger.exception("opensearch index failed; gate decision is still authoritative")

    manifest.status = report.status
    put_run(clients["ddb"], table, manifest, clients["s3"], results_bucket)

    metrics.add_metric("RunsAggregated", MetricUnit.Count, 1)
    metrics.add_metric("GatePass" if report.decision.passed else "GateFail", MetricUnit.Count, 1)
    metrics.add_metric("PassRate", MetricUnit.Percent, report.pass_rate * 100)
    logger.info(
        "gated eval run",
        extra={
            "eval_run_id": eval_run_id,
            "status": report.status.value,
            "passed": report.decision.passed,
            "failures": report.decision.failures,
            "github": github_result,
            "slack": slack_result,
            "report_uri": report_uri,
            "overall_score": report.overall_score,
        },
    )
    return {
        "eval_run_id": eval_run_id,
        "status": report.status.value,
        "passed": report.decision.passed,
        "overall_score": report.overall_score,
        "failures": report.decision.failures,
        "report_uri": report_uri,
        "github": github_result,
        "slack": slack_result,
    }


@logger.inject_lambda_context(log_event=True)
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Route SQS shard completions, scheduled sweeps, and direct invokes."""
    if event.get("Records"):
        return shard_complete_handler(event, context)
    if event.get("sweep"):
        return sweep_handler(event, context)
    return aggregate_run(event, context)


def shard_complete_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS-triggered trampoline: a worker finished a shard; try to aggregate."""
    import orjson

    run_ids: set[str] = set()
    for record in event.get("Records", []):
        body = record.get("body") or "{}"
        parsed = orjson.loads(body)
        if "eval_run_id" in parsed:
            run_ids.add(parsed["eval_run_id"])
    results = [aggregate_run({"eval_run_id": run_id}, context) for run_id in sorted(run_ids)]
    return {"runs": results}


def sweep_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    table = os.environ["RUNS_TABLE_NAME"]
    run_ids = _list_open_run_ids(_clients()["ddb"], table)
    logger.info("sweeping open runs", extra={"count": len(run_ids)})
    return {
        "sweep": True,
        "runs": [aggregate_run({"eval_run_id": run_id}, context) for run_id in run_ids],
    }
