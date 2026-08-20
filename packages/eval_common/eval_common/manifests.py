"""DynamoDB dataset-manifest store (commit hash → golden set pointer)."""

from __future__ import annotations

from typing import Any

from eval_common.models import DatasetManifest, utc_now_iso


def put_manifest(ddb: Any, table: str, manifest: DatasetManifest) -> None:
    if not table:
        return
    ddb.put_item(
        TableName=table,
        Item={
            "dataset_id": {"S": manifest.dataset_id},
            "git_sha": {"S": manifest.git_sha or ""},
            "s3_uri": {"S": manifest.s3_uri},
            "version": {"S": manifest.version},
            "name": {"S": manifest.name},
            "case_count": {"N": str(manifest.case_count)},
            "created_at": {"S": manifest.created_at or utc_now_iso()},
            "payload": {"S": manifest.model_dump_json()},
        },
    )


def get_manifest(ddb: Any, table: str, dataset_id: str) -> DatasetManifest | None:
    if not table or not dataset_id:
        return None
    response = ddb.get_item(TableName=table, Key={"dataset_id": {"S": dataset_id}})
    item = response.get("Item")
    if not item:
        return None
    if "payload" in item:
        return DatasetManifest.model_validate_json(item["payload"]["S"])
    return DatasetManifest(
        dataset_id=item["dataset_id"]["S"],
        git_sha=item.get("git_sha", {}).get("S") or None,
        s3_uri=item["s3_uri"]["S"],
        version=item.get("version", {}).get("S") or "1.0",
        name=item.get("name", {}).get("S") or "golden",
        case_count=int(item.get("case_count", {}).get("N") or 0),
    )
