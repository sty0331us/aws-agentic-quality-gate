from __future__ import annotations

import pytest
from eval_common.aws import chunked


@pytest.mark.unit
def test_chunked_partitions_evenly() -> None:
    shards = chunked(list(range(10)), 4)
    assert shards == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]


@pytest.mark.unit
def test_chunked_rejects_invalid_size() -> None:
    with pytest.raises(ValueError):
        chunked([1], 0)
