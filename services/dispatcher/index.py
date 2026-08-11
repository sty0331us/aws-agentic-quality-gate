"""Dispatcher Lambda: shard a golden dataset from S3 onto the eval SQS queue."""

from __future__ import annotations

import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from botocore.config import Config
from eval_common.aws import chunked, put_json, put_json_model
from eval_common.models import (
    EvalBackend,
    EvalJobMessage,
    EvalMode,
    GitHubContext,
    GoldenDataset,
    RunManifest,
    RunStatus,
    RunThresholds,
    new_eval_run_id,
    utc_now_iso,
)
from eval_common.runs import put_run

logger = Logger(service="aqg-dispatcher")
tracer = Tracer(service="aqg-dispatcher")
metrics = Metrics(namespace="AgenticQualityGate", service="dispatcher")

_RETRY = Config(retries={"max_attempts": 8, "mode": "adaptive"})


def _clients() -> dict[str, Any]:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return {
        "s3": boto3.client("s3", region_name=region, config=_RETRY),
        "sqs": boto3.client("sqs", region_name=region, config=_RETRY),
        "ddb": boto3.client("dynamodb", region_name=region, config=_RETRY),
        "ecs": boto3.client("ecs", region_name=region, config=_RETRY),
    }


def _parse_s3_event(record: dict[str, Any]) -> tuple[str, str]:
    bucket = record["s3"]["bucket"]["name"]
    key = record["s3"]["object"]["key"]
    # S3 event keys are URL-encoded
    from urllib.parse import unquote_plus

    return bucket, unquote_plus(key)


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Accept S3 notifications, EventBridge, or a direct invoke payload."""
    from urllib.parse import unquote_plus

    if "Records" in event and event["Records"] and "s3" in event["Records"][0]:
        bucket, key = _parse_s3_event(event["Records"][0])
        return {"dataset_bucket": bucket, "dataset_key": key}
    detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
    nested_bucket = detail.get("bucket") if isinstance(detail.get("bucket"), dict) else None
    nested_object = detail.get("object") if isinstance(detail.get("object"), dict) else None
    if nested_bucket and nested_object and nested_bucket.get("name") and nested_object.get("key"):
        return {
            "dataset_bucket": nested_bucket["name"],
            "dataset_key": unquote_plus(str(nested_object["key"])),
        }
    merged = {**detail, **{k: v for k, v in event.items() if k != "detail"}}
    return merged


def _scale_out_workers(ecs: Any, desired: int) -> None:
    cluster = os.environ.get("ECS_CLUSTER_NAME")
    service = os.environ.get("ECS_SERVICE_NAME")
    if not cluster or not service or desired < 1:
        return
    try:
        described = ecs.describe_services(cluster=cluster, services=[service])
        services = described.get("services") or []
        current = int(services[0]["desiredCount"]) if services else 0
        if current >= desired:
            return
        ecs.update_service(cluster=cluster, service=service, desiredCount=desired)
        logger.info("scaled ECS service", extra={"desired": desired, "previous": current})
    except Exception:
        logger.exception("failed to scale ECS service; queue-depth autoscaling should still fire")


@logger.inject_lambda_context(log_event=True)
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    payload = _normalize_event(event)
    dataset_bucket = payload.get("dataset_bucket") or os.environ["DATASET_BUCKET"]
    dataset_key = payload.get("dataset_key") or payload.get("dataset_s3_key")
    if not dataset_key:
        raise ValueError("dataset_key is required")

    results_bucket = os.environ["RESULTS_BUCKET"]
    queue_url = os.environ["EVAL_QUEUE_URL"]
    table = os.environ["RUNS_TABLE_NAME"]
    shard_size = int(os.environ.get("SHARD_SIZE", "8"))
    judge_model_id = payload.get("judge_model_id") or os.environ.get(
        "JUDGE_MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    )
    eval_mode = EvalMode(payload.get("eval_mode") or os.environ.get("EVAL_MODE", "offline"))
    eval_backend = EvalBackend(payload.get("eval_backend") or os.environ.get("EVAL_BACKEND", "native"))

    clients = _clients()
    raw = clients["s3"].get_object(Bucket=dataset_bucket, Key=dataset_key)
    dataset = GoldenDataset.model_validate_json(raw["Body"].read())

    eval_run_id = payload.get("eval_run_id") or new_eval_run_id()
    github = GitHubContext(
        repo=payload.get("repo") or os.environ.get("GITHUB_REPO") or None,
        pr_number=payload.get("pr_number"),
        git_sha=payload.get("git_sha"),
        check_run_id=payload.get("check_run_id"),
    )
    thresholds = RunThresholds(
        faithfulness=float(os.environ.get("THRESHOLD_FAITHFULNESS", "0.70")),
        answer_relevance=float(os.environ.get("THRESHOLD_ANSWER_RELEVANCE", "0.70")),
        tool_selection_precision=float(os.environ.get("THRESHOLD_TOOL_SELECTION_PRECISION", "0.80")),
        min_pass_rate=float(os.environ.get("THRESHOLD_MIN_PASS_RATE", "0.85")),
        max_error_rate=float(os.environ.get("THRESHOLD_MAX_ERROR_RATE", "0.05")),
    )

    shards = chunked(dataset.cases, shard_size)
    dataset_uri = f"s3://{dataset_bucket}/{dataset_key}"
    manifest = RunManifest(
        eval_run_id=eval_run_id,
        status=RunStatus.RUNNING,
        dataset_s3_uri=dataset_uri,
        dataset_version=dataset.version,
        dataset_name=dataset.name,
        total_cases=len(dataset.cases),
        total_shards=len(shards),
        eval_mode=eval_mode,
        eval_backend=eval_backend,
        judge_model_id=judge_model_id,
        agent_endpoint=payload.get("agent_endpoint") or os.environ.get("AGENT_ENDPOINT") or None,
        github=github,
        thresholds=thresholds,
        created_at=utc_now_iso(),
    )
    put_run(clients["ddb"], table, manifest, clients["s3"], results_bucket)

    entries: list[dict[str, Any]] = []
    for shard_id, cases in enumerate(shards):
        shard_key = f"runs/{eval_run_id}/shards/{shard_id:04d}.json"
        put_json(
            clients["s3"],
            results_bucket,
            shard_key,
            {"cases": [case.model_dump(mode="json") for case in cases]},
        )
        message = EvalJobMessage(
            eval_run_id=eval_run_id,
            shard_id=shard_id,
            shard_s3_uri=f"s3://{results_bucket}/{shard_key}",
            case_ids=[case.id for case in cases],
            eval_mode=eval_mode,
            eval_backend=eval_backend,
            judge_model_id=judge_model_id,
            agent_endpoint=manifest.agent_endpoint,
            github=github,
        )
        entries.append(
            {
                "Id": f"{shard_id:04d}",
                "MessageBody": message.model_dump_json(),
            }
        )

    # SQS SendMessageBatch max is 10.
    for offset in range(0, len(entries), 10):
        batch = entries[offset : offset + 10]
        response = clients["sqs"].send_message_batch(QueueUrl=queue_url, Entries=batch)
        failed = response.get("Failed") or []
        if failed:
            raise RuntimeError(f"SQS batch send failed: {failed}")

    desired_workers = min(len(shards), int(os.environ.get("MAX_WORKERS", "20")))
    _scale_out_workers(clients["ecs"], desired_workers)

    metrics.add_metric("RunsDispatched", MetricUnit.Count, 1)
    metrics.add_metric("ShardsEnqueued", MetricUnit.Count, len(shards))
    metrics.add_metric("CasesEnqueued", MetricUnit.Count, len(dataset.cases))
    logger.info(
        "dispatched eval run",
        extra={
            "eval_run_id": eval_run_id,
            "shards": len(shards),
            "cases": len(dataset.cases),
            "dataset": dataset_uri,
        },
    )
    put_json_model(clients["s3"], results_bucket, f"runs/{eval_run_id}/dispatch.json", manifest)
    return {
        "eval_run_id": eval_run_id,
        "total_shards": len(shards),
        "total_cases": len(dataset.cases),
        "status": RunStatus.RUNNING.value,
    }
