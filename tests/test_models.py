from __future__ import annotations

import pytest
from eval_common.models import (
    DEFAULT_JUDGE_MODEL_ID,
    OVERALL_SCORE_THRESHOLD,
    EvalBackend,
    EvalJobMessage,
    EvalMode,
    GoldenDataset,
)


@pytest.mark.unit
def test_dataset_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        GoldenDataset.model_validate(
            {
                "cases": [
                    {"id": "a", "query": "q1"},
                    {"id": "a", "query": "q2"},
                ]
            }
        )


@pytest.mark.unit
def test_eval_job_defaults_match_architecture() -> None:
    job = EvalJobMessage(
        eval_run_id="eval-1",
        shard_id=2,
        shard_s3_uri="s3://b/k.json",
        case_ids=["c1"],
    )
    assert job.eval_mode is EvalMode.CANDIDATE
    assert job.eval_backend is EvalBackend.DEEPEVAL
    assert job.judge_model_id == DEFAULT_JUDGE_MODEL_ID
    assert job.judge_model_id == "us.anthropic.claude-sonnet-5"
    assert job.fifo_group_id() == "eval-1-0002"
    assert job.fifo_dedup_id() == "eval-1-shard-0002"
    assert OVERALL_SCORE_THRESHOLD == 0.85


@pytest.mark.unit
def test_dataset_requires_cases() -> None:
    with pytest.raises(ValueError, match="at least one"):
        GoldenDataset.model_validate({"cases": []})
