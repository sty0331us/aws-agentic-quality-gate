"""Dispatcher Lambda: pull golden set from S3/DynamoDB, shard onto SQS FIFO."""

from __future__ import annotations

import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from botocore.config import Config
from eval_common.aws import chunked, put_json, put_json_model
from eval_common.manifests import get_manifest, put_manifest
from eval_common.models import (
    DEFAULT_CANDIDATE_MODEL_ID,
    DEFAULT_JUDGE_MODEL_ID,
    DatasetManifest,
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
        "batch": boto3.client("batch", region_name=region, config=_RETRY),
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


def _load_dataset(clients: dict[str, Any], payload: dict[str, Any]) -> tuple[GoldenDataset, str, str]:
    """Pull the golden set from DynamoDB (manifest) or S3."""
    from eval_common.aws import parse_s3_uri

    manifests_table = os.environ.get("MANIFESTS_TABLE_NAME", "")
    dataset_id = payload.get("dataset_id") or payload.get("dataset_manifest_id")
    if dataset_id and manifests_table:
        stored = get_manifest(clients["ddb"], manifests_table, dataset_id)
        if stored:
            bucket, key = parse_s3_uri(stored.s3_uri)
            raw = clients["s3"].get_object(Bucket=bucket, Key=key)
            return GoldenDataset.model_validate_json(raw["Body"].read()), stored.s3_uri, stored.dataset_id

    dataset_bucket = payload.get("dataset_bucket") or os.environ["DATASET_BUCKET"]
    dataset_key = payload.get("dataset_key") or payload.get("dataset_s3_key")
    if not dataset_key:
        raise ValueError("dataset_key or dataset_id is required")
    raw = clients["s3"].get_object(Bucket=dataset_bucket, Key=dataset_key)
    dataset = GoldenDataset.model_validate_json(raw["Body"].read())
    uri = f"s3://{dataset_bucket}/{dataset_key}"
    manifest_id = dataset_id or payload.get("git_sha") or dataset.name
    put_manifest(
        clients["ddb"],
        manifests_table,
        DatasetManifest(
            dataset_id=str(manifest_id),
            git_sha=payload.get("git_sha"),
            s3_uri=uri,
            version=dataset.version,
            name=dataset.name,
            case_count=len(dataset.cases),
        ),
    )
    return dataset, uri, str(manifest_id)


def _submit_batch_jobs(batch: Any, eval_run_id: str, shard_count: int) -> None:
    """Launch Fargate Spot Batch workers that consume the SQS FIFO eval queue."""
    queue = os.environ.get("BATCH_JOB_QUEUE")
    definition = os.environ.get("BATCH_JOB_DEFINITION")
    if not queue or not definition or shard_count < 1:
        return
    for shard_id in range(shard_count):
        batch.submit_job(
            jobName=f"{eval_run_id}-shard-{shard_id:04d}"[:128],
            jobQueue=queue,
            jobDefinition=definition,
        )


def _fifo_entries(messages: list[EvalJobMessage]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for message in messages:
        entry: dict[str, Any] = {
            "Id": f"{message.shard_id:04d}",
            "MessageBody": message.model_dump_json(),
        }
        entry["MessageGroupId"] = message.fifo_group_id()
        entry["MessageDeduplicationId"] = message.fifo_dedup_id()
        entries.append(entry)
    return entries


@logger.inject_lambda_context(log_event=True)
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    payload = _normalize_event(event)
    results_bucket = os.environ["RESULTS_BUCKET"]
    queue_url = os.environ["EVAL_QUEUE_URL"]
    table = os.environ["RUNS_TABLE_NAME"]
    shard_size = int(os.environ.get("SHARD_SIZE", "8"))
    judge_model_id = payload.get("judge_model_id") or os.environ.get("JUDGE_MODEL_ID", DEFAULT_JUDGE_MODEL_ID)
    candidate_model_id = payload.get("candidate_model_id") or os.environ.get(
        "CANDIDATE_MODEL_ID", DEFAULT_CANDIDATE_MODEL_ID
    )
    eval_mode = EvalMode(payload.get("eval_mode") or os.environ.get("EVAL_MODE", "candidate"))
    eval_backend = EvalBackend(payload.get("eval_backend") or os.environ.get("EVAL_BACKEND", "deepeval"))

    clients = _clients()
    dataset, dataset_uri, manifest_id = _load_dataset(clients, payload)

    eval_run_id = payload.get("eval_run_id") or new_eval_run_id()
    github = GitHubContext(
        repo=payload.get("repo") or os.environ.get("GITHUB_REPO") or None,
        pr_number=payload.get("pr_number"),
        git_sha=payload.get("git_sha"),
        check_run_id=payload.get("check_run_id"),
    )
    thresholds = RunThresholds(
        overall=float(os.environ.get("THRESHOLD_OVERALL", "0.85")),
        faithfulness=float(os.environ.get("THRESHOLD_FAITHFULNESS", "0.85")),
        answer_relevance=float(os.environ.get("THRESHOLD_ANSWER_RELEVANCE", "0.85")),
        tool_selection_precision=float(os.environ.get("THRESHOLD_TOOL_SELECTION_PRECISION", "0.85")),
        min_pass_rate=float(os.environ.get("THRESHOLD_MIN_PASS_RATE", "0.85")),
        max_error_rate=float(os.environ.get("THRESHOLD_MAX_ERROR_RATE", "0.05")),
    )

    shards = chunked(dataset.cases, shard_size)
    run = RunManifest(
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
        candidate_model_id=candidate_model_id,
        dataset_manifest_id=manifest_id,
        agent_endpoint=payload.get("agent_endpoint") or os.environ.get("AGENT_ENDPOINT") or None,
        github=github,
        thresholds=thresholds,
        created_at=utc_now_iso(),
    )
    put_run(clients["ddb"], table, run, clients["s3"], results_bucket)

    jobs: list[EvalJobMessage] = []
    for shard_id, cases in enumerate(shards):
        shard_key = f"runs/{eval_run_id}/shards/{shard_id:04d}.json"
        put_json(
            clients["s3"],
            results_bucket,
            shard_key,
            {"cases": [case.model_dump(mode="json") for case in cases]},
        )
        jobs.append(
            EvalJobMessage(
                eval_run_id=eval_run_id,
                shard_id=shard_id,
                shard_s3_uri=f"s3://{results_bucket}/{shard_key}",
                case_ids=[case.id for case in cases],
                eval_mode=eval_mode,
                eval_backend=eval_backend,
                judge_model_id=judge_model_id,
                candidate_model_id=candidate_model_id,
                dataset_manifest_id=manifest_id,
                agent_endpoint=run.agent_endpoint,
                github=github,
            )
        )

    # Layer 1 always fans out through SQS FIFO (dedup + concurrency buffer).
    entries = _fifo_entries(jobs)
    for offset in range(0, len(entries), 10):
        chunk = entries[offset : offset + 10]
        response = clients["sqs"].send_message_batch(QueueUrl=queue_url, Entries=chunk)
        failed = response.get("Failed") or []
        if failed:
            raise RuntimeError(f"SQS batch send failed: {failed}")
    backend = os.environ.get("COMPUTE_BACKEND", "ecs")
    if backend != "batch":
        desired_workers = min(len(shards), int(os.environ.get("MAX_WORKERS", "20")))
        _scale_out_workers(clients["ecs"], desired_workers)
    if backend in {"batch", "both"}:
        _submit_batch_jobs(clients["batch"], eval_run_id, len(jobs))

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
            "commit": github.git_sha,
            "manifest_id": manifest_id,
        },
    )
    put_json_model(clients["s3"], results_bucket, f"runs/{eval_run_id}/dispatch.json", run)
    return {
        "eval_run_id": eval_run_id,
        "total_shards": len(shards),
        "total_cases": len(dataset.cases),
        "status": RunStatus.RUNNING.value,
        "dataset_manifest_id": manifest_id,
        "git_sha": github.git_sha,
    }

