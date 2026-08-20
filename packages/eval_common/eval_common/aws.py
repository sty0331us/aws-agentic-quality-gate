"""Thin AWS helpers shared by dispatcher, worker, and aggregator."""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

import boto3
import orjson
from botocore.config import Config

from eval_common.config import Settings

_RETRY = Config(retries={"max_attempts": 8, "mode": "adaptive"})


@lru_cache(maxsize=8)
def client(service: str, region: str) -> Any:
    return boto3.client(service, region_name=region, config=_RETRY)


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def put_json(s3: Any, bucket: str, key: str, payload: Any) -> str:
    body = orjson.dumps(payload, default=str)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    return f"s3://{bucket}/{key}"


def put_json_model(s3: Any, bucket: str, key: str, model: Any) -> str:
    return put_json(s3, bucket, key, model.model_dump(mode="json"))


def get_json(s3: Any, bucket: str, key: str) -> Any:
    response = s3.get_object(Bucket=bucket, Key=key)
    return orjson.loads(response["Body"].read())


def get_json_uri(s3: Any, uri: str) -> Any:
    bucket, key = parse_s3_uri(uri)
    return get_json(s3, bucket, key)


def list_json_keys(s3: Any, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for item in page.get("Contents", []):
            if item["Key"].endswith(".json"):
                keys.append(item["Key"])
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    return keys


def send_fifo_json(
    sqs: Any,
    queue_url: str,
    payload: Any,
    *,
    group_id: str,
    dedup_id: str,
) -> None:
    body = payload if isinstance(payload, str) else orjson.dumps(payload, default=str).decode("utf-8")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=body,
        MessageGroupId=group_id,
        MessageDeduplicationId=dedup_id[:128],
    )


def chunked[T](items: list[T], size: int) -> list[list[T]]:
    if size < 1:
        raise ValueError("shard size must be >= 1")
    return [items[index : index + size] for index in range(0, len(items), size)]


def shard_prefix(eval_run_id: str) -> str:
    return f"runs/{eval_run_id}/shards/"


def results_prefix(eval_run_id: str) -> str:
    return f"runs/{eval_run_id}/results/"


def manifest_key(eval_run_id: str) -> str:
    return f"runs/{eval_run_id}/manifest.json"


def report_key(eval_run_id: str) -> str:
    return f"runs/{eval_run_id}/report.json"


def clients_from_settings(settings: Settings) -> dict[str, Any]:
    region = settings.aws_region
    return {
        "s3": client("s3", region),
        "sqs": client("sqs", region),
        "ddb": client("dynamodb", region),
        "ecs": client("ecs", region),
        "secrets": client("secretsmanager", region),
        "bedrock": client("bedrock-runtime", region),
    }
