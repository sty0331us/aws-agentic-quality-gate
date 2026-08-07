"""Robust extraction of JSON objects from LLM completions."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


class JudgeParseError(ValueError):
    pass


def extract_json_object(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise JudgeParseError("empty judge response")
    stripped = text.strip()
    candidates = [stripped]
    fenced = _FENCE.findall(stripped)
    candidates.extend(item.strip() for item in fenced)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                parsed = json.loads(candidate.replace(",}", "}").replace(",]", "]"))
            except json.JSONDecodeError:
                continue
        if isinstance(parsed, dict):
            return parsed
    raise JudgeParseError("could not parse JSON object from judge response")


def clamp_score(value: Any, default: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, score))
