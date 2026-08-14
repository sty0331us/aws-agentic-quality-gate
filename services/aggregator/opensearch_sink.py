"""Best-effort OpenSearch indexing of reports and per-case scores."""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import boto3
import orjson
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from eval_common.models import AggregateReport, CaseResult

logger = logging.getLogger("aqg.opensearch")


def _signed_request(method: str, url: str, body: bytes, region: str) -> None:
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("no AWS credentials available for OpenSearch signing")
    frozen = credentials.get_frozen_credentials()
    request = AWSRequest(method=method, url=url, data=body, headers={"Content-Type": "application/json"})
    SigV4Auth(frozen, "es", region).add_auth(request)
    prepared = request.prepare()
    import httpx

    with httpx.Client(timeout=10.0) as client:
        response = client.request(method, url, headers=dict(prepared.headers), content=body)
        response.raise_for_status()


def index_report(report: AggregateReport, results: list[CaseResult]) -> None:
    endpoint = os.environ.get("OPENSEARCH_ENDPOINT")
    if not endpoint:
        logger.info("OPENSEARCH_ENDPOINT unset; skipping index")
        return
    region = os.environ.get("AWS_REGION") or "us-east-1"
    base = endpoint.rstrip("/")
    parsed = urlparse(base if base.startswith("http") else f"https://{base}")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    report_body = orjson.dumps(report.model_dump(mode="json"))
    _signed_request(
        "PUT",
        f"{origin}/eval-reports/_doc/{report.eval_run_id}",
        report_body,
        region,
    )
    for result in results:
        _signed_request(
            "PUT",
            f"{origin}/eval-cases/_doc/{report.eval_run_id}:{result.case_id}",
            orjson.dumps(result.model_dump(mode="json")),
            region,
        )
    logger.info("indexed report and %s cases", len(results))
