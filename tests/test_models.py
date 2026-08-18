from __future__ import annotations

import pytest
from eval_common.models import GoldenDataset


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
def test_dataset_requires_cases() -> None:
    with pytest.raises(ValueError, match="at least one"):
        GoldenDataset.model_validate({"cases": []})
