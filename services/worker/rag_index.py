"""Candidate RAG index. Backed by case corpora now; swap for a Bedrock Knowledge Base later."""

from __future__ import annotations

import math
import re

from eval_common.models import GoldenCase

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def retrieve(case: GoldenCase, *, k: int = 4) -> list[str]:
    """Return ranked passages for the query from the candidate RAG corpus."""
    corpus = list(case.expected_contexts)
    if case.recorded_trace:
        corpus.extend(case.recorded_trace.retrieved_contexts)
    # Dedup while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for passage in corpus:
        key = passage.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    if not unique:
        return []
    query_tokens = _tokens(case.query)
    if not query_tokens:
        return unique[:k]

    def score(passage: str) -> float:
        tokens = _tokens(passage)
        if not tokens:
            return 0.0
        overlap = len(query_tokens & tokens)
        return overlap / math.sqrt(len(tokens))

    ranked = sorted(unique, key=score, reverse=True)
    return ranked[:k]
