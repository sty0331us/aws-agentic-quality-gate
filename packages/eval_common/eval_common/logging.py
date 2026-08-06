"""JSON-lines friendly structured logging for Lambda and ECS."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import orjson


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return orjson.dumps(payload).decode("utf-8")


def configure_logging(level: str | None = None) -> logging.Logger:
    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    root.setLevel(resolved)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger("aqg")


def log_extra(**fields: Any) -> dict[str, Any]:
    return {"extra": {"extra_fields": fields}}
