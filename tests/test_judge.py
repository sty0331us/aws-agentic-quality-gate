from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from bedrock_judge import SYSTEM_PROMPT, BedrockJudge
from eval_common.models import AgentTrace, GoldenCase, MetricName


@pytest.mark.unit
def test_judge_parses_converse_json() -> None:
    client = MagicMock()
    client.converse.return_value = {
        "output": {
            "message": {
                "content": [
                    {
                        "text": (
                            '{"score": 0.91, "passed": true, "reasoning": "grounded",'
                            ' "chain_of_thought": "step 1", "claims": []}'
                        )
                    }
                ]
            }
        },
        "usage": {"inputTokens": 120, "outputTokens": 40},
        "stopReason": "end_turn",
    }
    judge = BedrockJudge(model_id="test-model", region="us-east-1", client=client)
    case = GoldenCase(id="c1", query="What is the SLO?", expected_contexts=["250ms p99"])
    trace = AgentTrace(answer="250ms p99 in us-east-1", retrieved_contexts=["250ms p99"])
    metric = judge.faithfulness(case, trace, threshold=0.7)
    assert metric.name is MetricName.FAITHFULNESS
    assert metric.score == 0.91
    assert metric.passed
    assert metric.input_tokens == 120
    assert "strict evaluation judge" in SYSTEM_PROMPT
    client.converse.assert_called_once()
