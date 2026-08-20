"""ECS Fargate evaluation harness. Long-polls SQS, scores shards, writes results."""

from __future__ import annotations

import os
import signal
import sys
import time
import traceback
from typing import Any

import boto3
import orjson
from agent_client import resolve_trace
from bedrock_judge import BedrockJudge
from botocore.config import Config
from eval_common.aws import get_json_uri, parse_s3_uri, put_json
from eval_common.config import Settings, get_settings
from eval_common.logging import configure_logging
from eval_common.models import (
    CaseResult,
    EvalJobMessage,
    GoldenCase,
    RunThresholds,
    utc_now_iso,
)
from eval_common.runs import increment_completed_shard
from metrics.answer_relevance import score_answer_relevance
from metrics.faithfulness import score_faithfulness
from metrics.tool_selection import score_tool_selection

_STOP = False
_RETRY = Config(retries={"max_attempts": 8, "mode": "adaptive"})


def _handle_signal(signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True
    logging_once = sys.stderr
    logging_once.write(f"received signal {signum}; draining current shard\n")


def _clients(region: str) -> dict[str, Any]:
    return {
        "s3": boto3.client("s3", region_name=region, config=_RETRY),
        "sqs": boto3.client("sqs", region_name=region, config=_RETRY),
        "ddb": boto3.client("dynamodb", region_name=region, config=_RETRY),
    }


def evaluate_case(
    *,
    job: EvalJobMessage,
    case: GoldenCase,
    settings: Settings,
    judge: BedrockJudge | None,
    thresholds: RunThresholds,
) -> CaseResult:
    started = time.perf_counter()
    try:
        trace = resolve_trace(
            case,
            mode=job.eval_mode,
            endpoint=job.agent_endpoint or settings.agent_endpoint or None,
            api_key=settings.agent_api_key or None,
            candidate_model_id=job.candidate_model_id or settings.candidate_model_id,
            region=settings.aws_region,
            bedrock_enabled=settings.bedrock_enabled,
        )
        metrics = [
            score_faithfulness(
                case=case,
                trace=trace,
                judge=judge,
                backend=job.eval_backend,
                thresholds=thresholds,
                bedrock_enabled=settings.bedrock_enabled,
            ),
            score_answer_relevance(
                case=case,
                trace=trace,
                judge=judge,
                backend=job.eval_backend,
                thresholds=thresholds,
                bedrock_enabled=settings.bedrock_enabled,
            ),
            score_tool_selection(case, trace),
        ]
        input_tokens = sum(item.input_tokens for item in metrics)
        output_tokens = sum(item.output_tokens for item in metrics)
        return CaseResult(
            eval_run_id=job.eval_run_id,
            case_id=case.id,
            shard_id=job.shard_id,
            suite=case.suite,
            query=case.query,
            answer=trace.answer,
            metrics=metrics,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            judge_model_id=job.judge_model_id,
            candidate_model_id=job.candidate_model_id,
            tool_call_logs=list(trace.tool_calls),
            retrieved_contexts=list(trace.retrieved_contexts),
            evaluated_at=utc_now_iso(),
        )
    except Exception as exc:
        return CaseResult(
            eval_run_id=job.eval_run_id,
            case_id=case.id,
            shard_id=job.shard_id,
            suite=case.suite,
            query=case.query,
            answer="",
            metrics=[],
            latency_ms=int((time.perf_counter() - started) * 1000),
            judge_model_id=job.judge_model_id,
            error=f"{type(exc).__name__}: {exc}",
            metadata={"traceback": traceback.format_exc()[-2000:]},
            evaluated_at=utc_now_iso(),
        )


def process_message(
    *,
    body: str,
    settings: Settings,
    clients: dict[str, Any],
    judge: BedrockJudge | None,
    log: Any,
) -> None:
    job = EvalJobMessage.model_validate_json(body)
    job_judge = judge
    if settings.bedrock_enabled and (judge is None or judge.model_id != job.judge_model_id):
        job_judge = BedrockJudge(model_id=job.judge_model_id, region=settings.aws_region)
    shard = get_json_uri(clients["s3"], job.shard_s3_uri)
    cases = [GoldenCase.model_validate(item) for item in shard["cases"]]
    thresholds = settings.thresholds()
    results = [
        evaluate_case(job=job, case=case, settings=settings, judge=job_judge, thresholds=thresholds) for case in cases
    ]
    results_bucket, _ = parse_s3_uri(job.shard_s3_uri)
    result_key = f"runs/{job.eval_run_id}/results/shard-{job.shard_id:04d}.json"
    put_json(
        clients["s3"],
        results_bucket,
        result_key,
        {"results": [item.model_dump(mode="json") for item in results]},
    )
    if settings.results_queue_url:
        body = {
            "eval_run_id": job.eval_run_id,
            "shard_id": job.shard_id,
            "case_ids": job.case_ids,
            "result_s3_uri": f"s3://{results_bucket}/{result_key}",
        }
        send_kwargs: dict[str, Any] = {
            "QueueUrl": settings.results_queue_url,
            "MessageBody": orjson.dumps(body).decode("utf-8"),
        }
        if ".fifo" in settings.results_queue_url:
            send_kwargs["MessageGroupId"] = job.fifo_group_id()
            send_kwargs["MessageDeduplicationId"] = f"{job.fifo_dedup_id()}-result"
        clients["sqs"].send_message(**send_kwargs)
    if settings.runs_table_name:
        try:
            increment_completed_shard(
                clients["ddb"],
                settings.runs_table_name,
                job.eval_run_id,
                len(results),
            )
        except Exception:
            log.exception("failed to increment shard counter", extra={"eval_run_id": job.eval_run_id})
    log.info(
        "shard complete",
        extra={
            "eval_run_id": job.eval_run_id,
            "shard_id": job.shard_id,
            "cases": len(results),
            "errors": sum(1 for item in results if item.error),
        },
    )


def run_forever(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    log = configure_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    clients = _clients(settings.aws_region)
    judge = None
    if settings.bedrock_enabled:
        judge = BedrockJudge(model_id=settings.judge_model_id, region=settings.aws_region)

    one_shot = os.environ.get("EVAL_JOB_JSON")
    if one_shot:
        log.info("batch one-shot shard")
        process_message(
            body=os.environ["EVAL_JOB_JSON"],
            settings=settings,
            clients=clients,
            judge=judge,
            log=log,
        )
        return

    if not settings.eval_queue_url:
        raise SystemExit("EVAL_QUEUE_URL is required")
    idle_started = time.monotonic()
    log.info("worker started", extra={"queue": settings.eval_queue_url, "backend": settings.eval_backend})
    while not _STOP:
        response = clients["sqs"].receive_message(
            QueueUrl=settings.eval_queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=settings.sqs_wait_seconds,
            VisibilityTimeout=int(os.environ.get("VISIBILITY_TIMEOUT", "900")),
        )
        messages = response.get("Messages") or []
        if not messages:
            if time.monotonic() - idle_started >= settings.worker_idle_exit_seconds:
                log.info("idle timeout; exiting so Fargate can scale to zero")
                return
            continue
        idle_started = time.monotonic()
        message = messages[0]
        try:
            process_message(
                body=message["Body"],
                settings=settings,
                clients=clients,
                judge=judge,
                log=log,
            )
            clients["sqs"].delete_message(
                QueueUrl=settings.eval_queue_url,
                ReceiptHandle=message["ReceiptHandle"],
            )
        except Exception:
            log.exception("shard failed; leaving message for retry/DLQ")
            # Do not delete. Visibility timeout will retry; redrive policy sends to DLQ.


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
