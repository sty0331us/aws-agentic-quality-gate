from __future__ import annotations

import json
from pathlib import Path

import boto3
import pytest
from eval_common.models import GoldenDataset
from moto import mock_aws


@pytest.fixture()
def aws_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("DATASET_BUCKET", "aqg-datasets")
    monkeypatch.setenv("RESULTS_BUCKET", "aqg-results")
    monkeypatch.setenv("EVAL_QUEUE_URL", "placeholder")
    monkeypatch.setenv("RUNS_TABLE_NAME", "aqg-runs")
    monkeypatch.setenv("MANIFESTS_TABLE_NAME", "aqg-manifests")
    monkeypatch.setenv("SHARD_SIZE", "3")
    monkeypatch.setenv("JUDGE_MODEL_ID", "test-model")
    return {
        "dataset": "aqg-datasets",
        "results": "aqg-results",
        "table": "aqg-runs",
    }


@pytest.mark.integration
@mock_aws
def test_dispatcher_shards_and_enqueues(aws_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    region = "us-east-1"
    s3 = boto3.client("s3", region_name=region)
    sqs = boto3.client("sqs", region_name=region)
    ddb = boto3.client("dynamodb", region_name=region)
    s3.create_bucket(Bucket=aws_env["dataset"])
    s3.create_bucket(Bucket=aws_env["results"])
    queue = sqs.create_queue(
        QueueName="eval.fifo",
        Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"},
    )
    monkeypatch.setenv("EVAL_QUEUE_URL", queue["QueueUrl"])
    ddb.create_table(
        TableName=aws_env["table"],
        KeySchema=[{"AttributeName": "eval_run_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "eval_run_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="aqg-manifests",
        KeySchema=[{"AttributeName": "dataset_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "dataset_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    dataset_path = Path(__file__).resolve().parents[1] / "datasets" / "golden_dataset_sample.json"
    s3.put_object(Bucket=aws_env["dataset"], Key="golden/sample.json", Body=dataset_path.read_bytes())

    from index import handler

    result = handler(
        {
            "dataset_bucket": aws_env["dataset"],
            "dataset_key": "golden/sample.json",
            "git_sha": "abc123",
            "pr_number": 42,
            "repo": "acme/agent",
        },
        type(
            "Ctx",
            (),
            {
                "function_name": "dispatcher",
                "memory_limit_in_mb": 512,
                "invoked_function_arn": "arn:aws:lambda:us-east-1:123456789012:function:dispatcher",
                "aws_request_id": "test-request",
            },
        )(),
    )
    assert result["total_cases"] == len(GoldenDataset.model_validate_json(dataset_path.read_text()).cases)
    assert result["total_shards"] >= 1
    assert result["dataset_manifest_id"]
    assert result["git_sha"] == "abc123"
    messages = sqs.receive_message(QueueUrl=queue["QueueUrl"], MaxNumberOfMessages=10, WaitTimeSeconds=1)
    assert "Messages" in messages
    body = json.loads(messages["Messages"][0]["Body"])
    assert body["eval_run_id"] == result["eval_run_id"]
    assert body["case_ids"]
    assert "MessageGroupId" in messages["Messages"][0] or body["shard_id"] >= 0
