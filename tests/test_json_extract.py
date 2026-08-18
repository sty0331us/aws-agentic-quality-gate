from __future__ import annotations

import pytest
from eval_common.json_extract import JudgeParseError, extract_json_object


@pytest.mark.unit
def test_extracts_fenced_json() -> None:
    text = """thinking...
```json
{"score": 0.8, "passed": true}
```
"""
    assert extract_json_object(text)["score"] == 0.8


@pytest.mark.unit
def test_extracts_raw_object() -> None:
    assert extract_json_object('prefix {"score": 1} suffix')["score"] == 1


@pytest.mark.unit
def test_empty_raises() -> None:
    with pytest.raises(JudgeParseError):
        extract_json_object("   ")
