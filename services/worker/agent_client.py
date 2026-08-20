"""Resolve the agent-under-test trace: recorded, HTTP, or in-process Bedrock candidate."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from eval_common.models import AgentTrace, EvalMode, GoldenCase, ToolCall

logger = logging.getLogger("aqg.agent")


class AgentClientError(RuntimeError):
    pass


def resolve_trace(
    case: GoldenCase,
    *,
    mode: EvalMode,
    endpoint: str | None,
    api_key: str | None,
    candidate_model_id: str,
    region: str,
    bedrock_enabled: bool,
    bedrock_client: Any | None = None,
) -> AgentTrace:
    if mode is EvalMode.OFFLINE:
        if case.recorded_trace is None:
            raise AgentClientError(f"case {case.id} has no recorded_trace for offline eval")
        return case.recorded_trace
    if mode is EvalMode.CANDIDATE:
        from candidate_agent import run_candidate_agent

        return run_candidate_agent(
            case,
            model_id=candidate_model_id,
            region=region,
            bedrock_enabled=bedrock_enabled,
            client=bedrock_client,
        )
    if not endpoint:
        raise AgentClientError("AGENT_ENDPOINT required for online eval")
    return invoke_agent(case, endpoint=endpoint, api_key=api_key)


def invoke_agent(case: GoldenCase, *, endpoint: str, api_key: str | None) -> AgentTrace:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "case_id": case.id,
        "query": case.query,
        "metadata": case.metadata,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(endpoint, headers=headers, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AgentClientError(f"agent HTTP {exc.response.status_code}") from exc
        body: dict[str, Any] = response.json()
    tool_calls = [
        ToolCall.model_validate(item) if not isinstance(item, ToolCall) else item
        for item in body.get("tool_calls") or []
    ]
    return AgentTrace(
        answer=str(body.get("answer") or body.get("output") or ""),
        retrieved_contexts=list(body.get("retrieved_contexts") or body.get("contexts") or []),
        tool_calls=tool_calls,
        latency_ms=int(body.get("latency_ms") or 0),
        metadata=dict(body.get("metadata") or {}),
    )
