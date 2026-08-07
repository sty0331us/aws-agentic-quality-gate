"""Run-state helpers for the DynamoDB control plane."""

from __future__ import annotations

from typing import Any

from eval_common.aws import get_json, manifest_key, put_json_model
from eval_common.models import RunManifest, RunStatus, utc_now_iso


def manifest_to_item(manifest: RunManifest) -> dict[str, Any]:
    github = manifest.github
    return {
        "eval_run_id": {"S": manifest.eval_run_id},
        "status": {"S": manifest.status.value},
        "total_cases": {"N": str(manifest.total_cases)},
        "total_shards": {"N": str(manifest.total_shards)},
        "completed_shards": {"N": str(manifest.completed_shards)},
        "completed_cases": {"N": str(manifest.completed_cases)},
        "dataset_s3_uri": {"S": manifest.dataset_s3_uri},
        "judge_model_id": {"S": manifest.judge_model_id},
        "git_sha": {"S": github.git_sha or ""},
        "pr_number": {"N": str(github.pr_number or 0)},
        "repo": {"S": github.repo or ""},
        "created_at": {"S": manifest.created_at},
        "updated_at": {"S": manifest.updated_at},
        "payload": {"S": manifest.model_dump_json()},
    }


def item_to_manifest(item: dict[str, Any]) -> RunManifest:
    if "payload" not in item:
        raise ValueError("DynamoDB item missing payload")
    manifest = RunManifest.model_validate_json(item["payload"]["S"])
    if "completed_shards" in item:
        manifest.completed_shards = int(item["completed_shards"]["N"])
    if "completed_cases" in item:
        manifest.completed_cases = int(item["completed_cases"]["N"])
    if "status" in item and item["status"].get("S"):
        try:
            manifest.status = RunStatus(item["status"]["S"])
        except ValueError:
            pass
    return manifest


def put_run(ddb: Any, table: str, manifest: RunManifest, s3: Any, bucket: str) -> None:
    manifest.updated_at = utc_now_iso()
    ddb.put_item(TableName=table, Item=manifest_to_item(manifest))
    put_json_model(s3, bucket, manifest_key(manifest.eval_run_id), manifest)


def get_run(ddb: Any, table: str, eval_run_id: str) -> RunManifest | None:
    response = ddb.get_item(TableName=table, Key={"eval_run_id": {"S": eval_run_id}})
    item = response.get("Item")
    if not item:
        return None
    return item_to_manifest(item)


def increment_completed_shard(
    ddb: Any,
    table: str,
    eval_run_id: str,
    cases_in_shard: int,
) -> RunManifest:
    response = ddb.update_item(
        TableName=table,
        Key={"eval_run_id": {"S": eval_run_id}},
        UpdateExpression=("ADD completed_shards :one, completed_cases :cases SET updated_at = :now, #s = :running"),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":one": {"N": "1"},
            ":cases": {"N": str(cases_in_shard)},
            ":now": {"S": utc_now_iso()},
            ":running": {"S": RunStatus.RUNNING.value},
        },
        ReturnValues="ALL_NEW",
    )
    item = response["Attributes"]
    manifest = item_to_manifest(item)
    ddb.update_item(
        TableName=table,
        Key={"eval_run_id": {"S": eval_run_id}},
        UpdateExpression="SET payload = :payload",
        ExpressionAttributeValues={":payload": {"S": manifest.model_dump_json()}},
    )
    return manifest


def load_manifest_s3(s3: Any, bucket: str, eval_run_id: str) -> RunManifest:
    from eval_common.aws import manifest_key as key_for

    payload = get_json(s3, bucket, key_for(eval_run_id))
    return RunManifest.model_validate(payload)
