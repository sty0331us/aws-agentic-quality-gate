"""Amazon Bedrock LLM-as-a-Judge with explicit chain-of-thought traces."""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from botocore.config import Config
from eval_common.json_extract import JudgeParseError, clamp_score, extract_json_object
from eval_common.models import AgentTrace, GoldenCase, MetricName, MetricScore
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

logger = logging.getLogger("aqg.judge")

_RETRYABLE = (
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "InternalServerException",
    "ModelNotReadyException",
)

SYSTEM_PROMPT = """You are a strict evaluation judge for RAG and tool-calling agents.
Score only from the provided evidence. Never reward fluency over factual grounding.
Think step by step in chain_of_thought, then emit a single JSON object with:
{
  "score": <number 0.0-1.0>,
  "passed": <boolean>,
  "reasoning": <short verdict for humans>,
  "chain_of_thought": <your step-by-step analysis>,
  "claims": [{"claim": <string>, "supported": <boolean>, "evidence": <string>}]
}
Do not wrap the JSON in markdown. Do not add keys other than those listed.
"""

FAITHFULNESS_INSTRUCTIONS = """Metric: faithfulness (groundedness).
1. Extract atomic claims from ANSWER.
2. For each claim, decide if CONTEXT (retrieved passages) supports it.
3. score = supported_claims / total_claims. If there are no claims, score 0.
4. Penalize invented numbers, citations, or entities not in CONTEXT.
5. passed is true iff score >= {threshold}.
"""

RELEVANCE_INSTRUCTIONS = """Metric: answer_relevance.
1. Decide whether ANSWER addresses QUESTION.
2. Penalize missing constraints, off-topic content, or refusal when CONTEXT is sufficient.
3. If EXPECTED_ANSWER is present, use it as a rubric, not as the only acceptable wording.
4. score is 1.0 fully relevant, 0.0 unrelated. passed is true iff score >= {threshold}.
"""


class BedrockJudgeError(RuntimeError):
    pass


class RetryableBedrockError(BedrockJudgeError):
    pass


def _is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", name)
    return code in _RETRYABLE or "Throttl" in code or "Timeout" in code


class BedrockJudge:
    def __init__(
        self,
        *,
        model_id: str,
        region: str,
        client: Any | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(retries={"max_attempts": 1, "mode": "standard"}),
        )
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=1, max=20),
        retry=retry_if_exception_type(RetryableBedrockError),
    )
    def _converse(self, user_prompt: str) -> dict[str, Any]:
        try:
            response = self._client.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={
                    "maxTokens": self.max_tokens,
                    "temperature": self.temperature,
                },
            )
        except Exception as exc:
            if _is_retryable(exc):
                raise RetryableBedrockError(str(exc)) from exc
            raise BedrockJudgeError(str(exc)) from exc
        usage = response.get("usage") or {}
        self.last_usage = {
            "input_tokens": int(usage.get("inputTokens") or 0),
            "output_tokens": int(usage.get("outputTokens") or 0),
        }
        stop = response.get("stopReason")
        if stop not in {None, "end_turn", "stop_sequence"}:
            logger.warning("unexpected bedrock stopReason", extra={"stopReason": stop})
        texts = []
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if "text" in block:
                texts.append(block["text"])
        return {"text": "\n".join(texts), **self.last_usage}

    def _score(
        self,
        *,
        name: MetricName,
        instructions: str,
        question: str,
        answer: str,
        context: list[str],
        expected_answer: str | None,
        threshold: float,
    ) -> MetricScore:
        prompt = "\n".join(
            [
                instructions.format(threshold=threshold),
                f"QUESTION:\n{question}",
                f"ANSWER:\n{answer}",
                "CONTEXT:\n" + ("\n---\n".join(context) if context else "(none)"),
                f"EXPECTED_ANSWER:\n{expected_answer or '(none)'}",
            ]
        )
        raw = self._converse(prompt)
        try:
            parsed = extract_json_object(raw["text"])
        except JudgeParseError:
            logger.warning("judge JSON parse failed; defaulting to 0", extra={"metric": name})
            parsed = {
                "score": 0.0,
                "passed": False,
                "reasoning": "Judge response was not valid JSON.",
                "chain_of_thought": raw["text"][:2000],
                "claims": [],
            }
        score = clamp_score(parsed.get("score"))
        passed = bool(parsed.get("passed")) if "passed" in parsed else score >= threshold
        return MetricScore(
            name=name,
            score=score,
            passed=passed,
            reasoning=str(parsed.get("reasoning") or ""),
            chain_of_thought=str(parsed.get("chain_of_thought") or ""),
            details={"claims": parsed.get("claims") or [], "raw_keys": sorted(parsed.keys())},
            judge_model_id=self.model_id,
            input_tokens=int(raw["input_tokens"]),
            output_tokens=int(raw["output_tokens"]),
        )

    def faithfulness(self, case: GoldenCase, trace: AgentTrace, threshold: float) -> MetricScore:
        contexts = trace.retrieved_contexts or case.expected_contexts
        return self._score(
            name=MetricName.FAITHFULNESS,
            instructions=FAITHFULNESS_INSTRUCTIONS,
            question=case.query,
            answer=trace.answer,
            context=contexts,
            expected_answer=case.expected_answer,
            threshold=threshold,
        )

    def answer_relevance(self, case: GoldenCase, trace: AgentTrace, threshold: float) -> MetricScore:
        contexts = trace.retrieved_contexts or case.expected_contexts
        return self._score(
            name=MetricName.ANSWER_RELEVANCE,
            instructions=RELEVANCE_INSTRUCTIONS,
            question=case.query,
            answer=trace.answer,
            context=contexts,
            expected_answer=case.expected_answer,
            threshold=threshold,
        )


def dump_prompt_preview(case: GoldenCase) -> str:
    """Used in tests to lock prompt contracts without calling Bedrock."""
    return json.dumps({"id": case.id, "query": case.query, "system": SYSTEM_PROMPT[:40]})
