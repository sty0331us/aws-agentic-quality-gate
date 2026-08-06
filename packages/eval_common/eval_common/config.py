"""Environment-driven settings. Lambdas and ECS inject the same names."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from eval_common.models import EvalBackend, EvalMode, RunThresholds


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    environment: str = Field(default="dev", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    dataset_bucket: str = Field(default="", alias="DATASET_BUCKET")
    results_bucket: str = Field(default="", alias="RESULTS_BUCKET")
    eval_queue_url: str = Field(default="", alias="EVAL_QUEUE_URL")
    results_queue_url: str = Field(default="", alias="RESULTS_QUEUE_URL")
    runs_table_name: str = Field(default="", alias="RUNS_TABLE_NAME")
    opensearch_endpoint: str = Field(default="", alias="OPENSEARCH_ENDPOINT")
    github_secret_arn: str = Field(default="", alias="GITHUB_SECRET_ARN")
    ecs_cluster_name: str = Field(default="", alias="ECS_CLUSTER_NAME")
    ecs_service_name: str = Field(default="", alias="ECS_SERVICE_NAME")

    eval_mode: EvalMode = Field(default=EvalMode.OFFLINE, alias="EVAL_MODE")
    eval_backend: EvalBackend = Field(default=EvalBackend.NATIVE, alias="EVAL_BACKEND")
    judge_model_id: str = Field(
        default="us.anthropic.claude-3-5-haiku-20241022-v1:0",
        alias="JUDGE_MODEL_ID",
    )
    bedrock_enabled: bool = Field(default=True, alias="BEDROCK_ENABLED")
    shard_size: int = Field(default=8, ge=1, le=100, alias="SHARD_SIZE")
    sqs_wait_seconds: int = Field(default=20, ge=0, le=20, alias="SQS_WAIT_SECONDS")
    max_cases_per_worker: int = Field(default=64, ge=1, alias="MAX_CASES_PER_WORKER")
    worker_idle_exit_seconds: int = Field(default=90, ge=10, alias="WORKER_IDLE_EXIT_SECONDS")
    run_timeout_seconds: int = Field(default=1800, ge=60, alias="RUN_TIMEOUT_SECONDS")

    agent_endpoint: str = Field(default="", alias="AGENT_ENDPOINT")
    agent_api_key: str = Field(default="", alias="AGENT_API_KEY")

    github_repo: str = Field(default="", alias="GITHUB_REPO")
    github_token: str = Field(default="", alias="GITHUB_TOKEN")
    github_app_id: str = Field(default="", alias="GITHUB_APP_ID")

    threshold_faithfulness: float = Field(default=0.70, alias="THRESHOLD_FAITHFULNESS")
    threshold_answer_relevance: float = Field(default=0.70, alias="THRESHOLD_ANSWER_RELEVANCE")
    threshold_tool_selection_precision: float = Field(default=0.80, alias="THRESHOLD_TOOL_SELECTION_PRECISION")
    threshold_min_pass_rate: float = Field(default=0.85, alias="THRESHOLD_MIN_PASS_RATE")
    threshold_max_error_rate: float = Field(default=0.05, alias="THRESHOLD_MAX_ERROR_RATE")

    # Approximate Bedrock on-demand prices (USD / 1M tokens). Override via env if needed.
    judge_input_usd_per_mtok: float = Field(default=0.80, alias="JUDGE_INPUT_USD_PER_MTOK")
    judge_output_usd_per_mtok: float = Field(default=4.00, alias="JUDGE_OUTPUT_USD_PER_MTOK")

    def thresholds(self) -> RunThresholds:
        return RunThresholds(
            faithfulness=self.threshold_faithfulness,
            answer_relevance=self.threshold_answer_relevance,
            tool_selection_precision=self.threshold_tool_selection_precision,
            min_pass_rate=self.threshold_min_pass_rate,
            max_error_rate=self.threshold_max_error_rate,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
