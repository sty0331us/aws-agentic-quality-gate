"""Typed contracts exchanged by dispatcher, workers, aggregator, and GitHub."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class EvalMode(StrEnum):
    OFFLINE = "offline"
    ONLINE = "online"


class EvalBackend(StrEnum):
    NATIVE = "native"
    DEEPEVAL = "deepeval"
    RAGAS = "ragas"
    ALL = "all"


class Suite(StrEnum):
    RAG = "rag"
    TOOL_CALLING = "tool_calling"
    HYBRID = "hybrid"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AGGREGATING = "aggregating"
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    TIMEOUT = "timeout"


class MetricName(StrEnum):
    FAITHFULNESS = "faithfulness"
    ANSWER_RELEVANCE = "answer_relevance"
    TOOL_SELECTION_PRECISION = "tool_selection_precision"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_eval_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"eval-{stamp}-{uuid4().hex[:8]}"


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str | None = None


class AgentTrace(BaseModel):
    """Observed agent behavior for a single case (recorded or live)."""

    answer: str
    retrieved_contexts: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    latency_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.tool_calls]


class GoldenCase(BaseModel):
    id: str
    suite: Suite = Suite.HYBRID
    query: str
    expected_answer: str | None = None
    expected_contexts: list[str] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    expected_tool_calls: list[ToolCall] = Field(default_factory=list)
    recorded_trace: AgentTrace | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("case id must be non-empty")
        return stripped


class GoldenDataset(BaseModel):
    version: str = "1.0"
    name: str = "golden"
    description: str = ""
    cases: list[GoldenCase]

    @field_validator("cases")
    @classmethod
    def unique_ids(cls, cases: list[GoldenCase]) -> list[GoldenCase]:
        ids = [case.id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("golden dataset contains duplicate case ids")
        if not cases:
            raise ValueError("golden dataset must contain at least one case")
        return cases


class RunThresholds(BaseModel):
    """Gate thresholds. Scores are in [0, 1]. A PR fails if any check trips."""

    faithfulness: float = Field(default=0.70, ge=0.0, le=1.0)
    answer_relevance: float = Field(default=0.70, ge=0.0, le=1.0)
    tool_selection_precision: float = Field(default=0.80, ge=0.0, le=1.0)
    min_pass_rate: float = Field(default=0.85, ge=0.0, le=1.0)
    max_error_rate: float = Field(default=0.05, ge=0.0, le=1.0)

    def as_metric_map(self) -> dict[str, float]:
        return {
            MetricName.FAITHFULNESS: self.faithfulness,
            MetricName.ANSWER_RELEVANCE: self.answer_relevance,
            MetricName.TOOL_SELECTION_PRECISION: self.tool_selection_precision,
        }


class GitHubContext(BaseModel):
    repo: str | None = None
    pr_number: int | None = None
    git_sha: str | None = None
    check_run_id: int | None = None
    installation_id: int | None = None


class EvalJobMessage(BaseModel):
    """SQS payload. Keep small; case bodies live in S3."""

    schema_version: str = "1.0"
    eval_run_id: str
    shard_id: int = Field(ge=0)
    shard_s3_uri: str
    case_ids: list[str]
    eval_mode: EvalMode = EvalMode.OFFLINE
    eval_backend: EvalBackend = EvalBackend.NATIVE
    judge_model_id: str
    agent_endpoint: str | None = None
    github: GitHubContext = Field(default_factory=GitHubContext)
    created_at: str = Field(default_factory=utc_now_iso)


class ClaimVerdict(BaseModel):
    claim: str
    supported: bool
    evidence: str | None = None


class MetricScore(BaseModel):
    name: MetricName | str
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    reasoning: str = ""
    chain_of_thought: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    judge_model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class CaseResult(BaseModel):
    eval_run_id: str
    case_id: str
    shard_id: int
    suite: Suite | str
    query: str
    answer: str = ""
    metrics: list[MetricScore] = Field(default_factory=list)
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    judge_model_id: str
    error: str | None = None
    evaluated_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def metric_map(self) -> dict[str, MetricScore]:
        return {str(metric.name): metric for metric in self.metrics}

    @property
    def case_passed(self) -> bool:
        if self.error:
            return False
        return bool(self.metrics) and all(metric.passed for metric in self.metrics)


class RunManifest(BaseModel):
    eval_run_id: str
    status: RunStatus = RunStatus.PENDING
    dataset_s3_uri: str
    dataset_version: str = "1.0"
    dataset_name: str = "golden"
    total_cases: int
    total_shards: int
    completed_shards: int = 0
    completed_cases: int = 0
    eval_mode: EvalMode = EvalMode.OFFLINE
    eval_backend: EvalBackend = EvalBackend.NATIVE
    judge_model_id: str
    agent_endpoint: str | None = None
    github: GitHubContext = Field(default_factory=GitHubContext)
    thresholds: RunThresholds = Field(default_factory=RunThresholds)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    expires_at_epoch: int | None = None


class MetricAggregate(BaseModel):
    name: str
    mean: float
    p50: float
    p95: float
    min: float
    max: float
    n: int
    pass_rate: float
    threshold: float
    gated: bool


class GateDecision(BaseModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)


class AggregateReport(BaseModel):
    eval_run_id: str
    status: RunStatus
    decision: GateDecision
    total_cases: int
    completed_cases: int
    passed_cases: int
    failed_cases: int
    error_cases: int
    pass_rate: float
    error_rate: float
    metrics: list[MetricAggregate] = Field(default_factory=list)
    thresholds: RunThresholds
    github: GitHubContext = Field(default_factory=GitHubContext)
    judge_model_id: str
    estimated_cost_usd: float = 0.0
    created_at: str = Field(default_factory=utc_now_iso)
    s3_report_uri: str | None = None

    def metric(self, name: str) -> MetricAggregate | None:
        return next((item for item in self.metrics if item.name == name), None)
