from __future__ import annotations

from pathlib import Path

import pytest
from eval_common.local_eval import evaluate_case, main
from eval_common.models import GoldenDataset


@pytest.mark.unit
def test_sample_dataset_loads() -> None:
    path = Path(__file__).resolve().parents[1] / "datasets" / "golden_dataset_sample.json"
    dataset = GoldenDataset.model_validate_json(path.read_text(encoding="utf-8"))
    assert len(dataset.cases) >= 8
    results = [evaluate_case(case) for case in dataset.cases]
    assert all(result.metrics for result in results)
    assert all(not result.error for result in results)


@pytest.mark.unit
def test_local_cli_exits_zero_on_sample(capsys: pytest.CaptureFixture[str]) -> None:
    path = Path(__file__).resolve().parents[1] / "datasets" / "golden_dataset_sample.json"
    code = main([str(path)])
    captured = capsys.readouterr()
    assert "Agentic Quality Gate" in captured.out
    assert code == 0
