"""Shared contracts, settings, and helpers for the Agentic Quality Gate."""

from eval_common.config import Settings, get_settings
from eval_common.models import (
    AgentTrace,
    AggregateReport,
    CaseResult,
    DatasetManifest,
    EvalJobMessage,
    GoldenCase,
    GoldenDataset,
    MetricScore,
    RunThresholds,
    ToolCall,
)

__all__ = [
    "AggregateReport",
    "AgentTrace",
    "CaseResult",
    "DatasetManifest",
    "EvalJobMessage",
    "GoldenCase",
    "GoldenDataset",
    "MetricScore",
    "RunThresholds",
    "Settings",
    "ToolCall",
    "get_settings",
]

__version__ = "0.1.0"
