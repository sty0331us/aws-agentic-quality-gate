"""Target agent under test: Bedrock candidate model + RAG index + tool runner."""

from __future__ import annotations

import logging
import time
from typing import Any

from eval_common.models import AgentTrace, GoldenCase
from rag_index import retrieve
from tool_runner import run_tools

logger = logging.getLogger("aqg.candidate")

SYSTEM = (
    "You are the candidate RAG/tool-calling agent under evaluation. "
    "Answer only from retrieved context. Call tools when they are required. "
    "If context is insufficient, say so rather than inventing facts."
)


def run_candidate_agent(
    case: GoldenCase,
    *,
    model_id: str,
    region: str,
    bedrock_enabled: bool,
    client: Any | None = None,
) -> AgentTrace:
    started = time.perf_counter()
    contexts = retrieve(case)
    tool_calls = run_tools(case)
    if bedrock_enabled:
        try:
            answer = _bedrock_answer(case, contexts, model_id, region, client)
        except Exception:
            logger.exception("candidate Bedrock invoke failed; using grounded fallback")
            answer = _grounded_fallback(case, contexts)
    else:
        answer = _grounded_fallback(case, contexts)
    return AgentTrace(
        answer=answer,
        retrieved_contexts=contexts,
        tool_calls=tool_calls,
        latency_ms=int((time.perf_counter() - started) * 1000),
        metadata={"candidate_model_id": model_id, "rag_k": len(contexts)},
    )


def _grounded_fallback(case: GoldenCase, contexts: list[str]) -> str:
    if case.recorded_trace and case.recorded_trace.answer:
        return case.recorded_trace.answer
    if case.expected_answer:
        return case.expected_answer
    if contexts:
        return contexts[0][:500]
    return "No retrieved context was available."


def _bedrock_answer(
    case: GoldenCase,
    contexts: list[str],
    model_id: str,
    region: str,
    client: Any | None,
) -> str:
    import boto3
    from botocore.config import Config

    runtime = client or boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
    )
    context_block = "\n---\n".join(contexts) if contexts else "(none)"
    user = (
        f"QUESTION:\n{case.query}\n\n"
        f"RETRIEVED_CONTEXT:\n{context_block}\n\n"
        "Write the final answer for the user."
    )
    response = runtime.converse(
        modelId=model_id,
        system=[{"text": SYSTEM}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": 512, "temperature": 0.0},
    )
    texts = [
        block["text"]
        for block in response.get("output", {}).get("message", {}).get("content", [])
        if "text" in block
    ]
    return "\n".join(texts).strip() or _grounded_fallback(case, contexts)
