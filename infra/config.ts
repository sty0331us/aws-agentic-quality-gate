export interface GateConfig {
  readonly prefix: string;
  readonly envName: string;
  readonly maxWorkers: number;
  readonly shardSize: number;
  readonly judgeModelId: string;
  readonly githubRepo: string;
  readonly thresholds: {
    readonly faithfulness: string;
    readonly answerRelevance: string;
    readonly toolSelectionPrecision: string;
    readonly minPassRate: string;
    readonly maxErrorRate: string;
  };
}

export function loadConfig(): GateConfig {
  return {
    prefix: process.env.AQG_PREFIX ?? "aqg",
    envName: process.env.ENVIRONMENT ?? "dev",
    maxWorkers: Number(process.env.MAX_WORKERS ?? "20"),
    shardSize: Number(process.env.SHARD_SIZE ?? "8"),
    judgeModelId:
      process.env.JUDGE_MODEL_ID ?? "us.anthropic.claude-3-5-haiku-20241022-v1:0",
    githubRepo: process.env.GITHUB_REPO ?? "org/aws-agentic-quality-gate",
    thresholds: {
      faithfulness: process.env.THRESHOLD_FAITHFULNESS ?? "0.70",
      answerRelevance: process.env.THRESHOLD_ANSWER_RELEVANCE ?? "0.70",
      toolSelectionPrecision: process.env.THRESHOLD_TOOL_SELECTION_PRECISION ?? "0.80",
      minPassRate: process.env.THRESHOLD_MIN_PASS_RATE ?? "0.85",
      maxErrorRate: process.env.THRESHOLD_MAX_ERROR_RATE ?? "0.05",
    },
  };
}

export function resourceName(config: GateConfig, name: string): string {
  return `${config.prefix}-${config.envName}-${name}`;
}
