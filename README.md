# Agentic CI/CD Evaluation Engine

Automated **LLM-as-a-Judge** quality gate for Agentic RAG and tool-calling workflows. A developer PR (or prompt/RAG commit) triggers sharding over **Amazon SQS FIFO**, evaluation on **ECS Fargate Spot / AWS Batch**, and a merge decision of **overall score ≥ 0.85**.

![Enterprise AI Architecture: Automated Agentic CI/CD Evaluation Engine on AWS](docs/architecture.jpg)

## What is gated

The **deployment decision** is a single composite **overall score** (mean of faithfulness, answer relevance, and tool-selection precision):

| Outcome | Rule |
| --- | --- |
| ✅ SUCCESS (proceed) | `overall_score >= 0.85` |
| ❌ FAILED (block merge + Slack) | `overall_score < 0.85`, timeout, or no results |

Per-metric series are still published to CloudWatch and shown on the PR comment. The judge is **Claude Sonnet 5**; the agent under test is a Bedrock **candidate** model with an in-container RAG index and tool runner. DeepEval/Ragas is the default harness (native Bedrock CoT judge is the fallback).

## Repository layout

```text
.
├── .github/workflows/eval-pipeline.yml
├── infra/                      # AWS CDK (TypeScript)
│   ├── sqs.ts                  # SQS FIFO eval + results queues
│   ├── storage.ts              # S3, DynamoDB runs + manifests
│   ├── ecs_evaluator.ts        # ECS Fargate Spot fleet
│   ├── batch_evaluator.ts      # AWS Batch Fargate Spot
│   ├── lambdas.ts              # Dispatcher + aggregator
│   ├── dashboard.ts            # CloudWatch metric dashboard
│   ├── codepipeline.ts         # Optional CodePipeline webhook path
│   └── opensearch.ts           # OpenSearch Serverless audits
├── services/
│   ├── dispatcher/             # Lambda: S3 golden dataset → SQS shards
│   ├── worker/                 # ECS Fargate evaluation harness
│   │   ├── Dockerfile
│   │   ├── test_runner.py
│   │   ├── metrics/            # Faithfulness, answer relevance, tool precision
│   │   └── bedrock_judge.py    # LLM-as-a-Judge with CoT traces
│   └── aggregator/             # Lambda: metrics → Pass/Fail + GitHub Check
├── datasets/golden_dataset_sample.json
├── packages/eval_common/       # Shared pydantic contracts
├── Makefile
└── pyproject.toml
```

## Evaluation modes

- **candidate** (default): in-container **target agent under test** — Bedrock candidate model + RAG index + tool runner, then Claude Sonnet 5 judge.
- **offline**: score `recorded_trace` on each golden case (deterministic CI replay).
- **online**: workers POST `{case_id, query, metadata}` to `AGENT_ENDPOINT`.

Backends (`EVAL_BACKEND`, default `deepeval`):

- `deepeval` / `ragas` — DeepEval/Ragas harness in the evaluator container
- `native` — Bedrock Converse CoT judge
- `all` — adapters plus native

Compute (`COMPUTE_BACKEND`): `ecs` (Fargate Spot long-poll of SQS FIFO) or `batch` (AWS Batch Fargate Spot one-shot jobs). Both fleets are provisioned.

## Local dry-run (no AWS)

```bash
python3 -m venv .venv && source .venv/bin/activate
make install
make test
make eval-local
```

`make eval-local` uses a heuristic scorer so you can validate dataset shape and the gate math without Bedrock. Production scores always come from the Bedrock judge (or DeepEval/Ragas).

## Deploy

Prerequisites: Node 20+, AWS credentials, CDK bootstrap, Bedrock model access for **Claude Sonnet 5** (judge) and the candidate model, and a GitHub token (or GitHub App) stored in Secrets Manager.

```bash
cp .env.example .env
# set GITHUB_REPO=owner/name  (restricts the OIDC role)
make bootstrap
make deploy
```

After deploy, copy stack outputs into GitHub Actions **variables**:

| Variable | Output |
| --- | --- |
| `AQG_ROLE_ARN` | `GitHubOidcRoleArn` |
| `AQG_DATASET_BUCKET` | `DatasetBucketName` |
| `AQG_RESULTS_BUCKET` | `ResultsBucketName` |
| `AQG_DISPATCHER_FUNCTION` | `DispatcherName` |
| `AWS_REGION` | e.g. `us-east-1` |

Put a GitHub token **and** Slack incoming webhook in the generated secret as `{"GITHUB_TOKEN":"...","SLACK_WEBHOOK_URL":"..."}`. Mark **agentic-quality-gate** as a required status check. Optional: `CODESTAR_CONNECTION_ARN` enables the CodePipeline GitHub source path.

If the account already has a GitHub OIDC provider:

```bash
npx cdk deploy -c githubOidcProviderArn=arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com
```

### Worker image

The evaluator image (`services/worker/Dockerfile`) bakes **DeepEval + Ragas** in by default (`INSTALL_EVAL_LIBS=true`) so containers run the spec harness.

## Runtime path

1. GitHub Actions / CodePipeline uploads the golden set to `s3://$bucket/golden/<commit-sha>.json` and invokes the dispatcher with **commit hash + dataset manifest id**.
2. Dispatcher pulls the dataset from **DynamoDB (manifest) or S3**, writes the manifest, shards cases, and always enqueues **SQS FIFO** messages (`MessageGroupId` + `MessageDeduplicationId`). ECS desired count is bumped, or AWS Batch Fargate Spot jobs are submitted when `COMPUTE_BACKEND=batch`.
3. Parallel evaluator containers (DeepEval/Ragas) run the **target agent** (Bedrock candidate + RAG index + tool runner) and the **Claude Sonnet 5** judge. Tool-call logs and retrieved contexts go to S3 with the scores.
4. Completing a shard notifies the results FIFO; the aggregator also sweeps every two minutes (`RUN_TIMEOUT_SECONDS`, default 30m).
5. Aggregator computes **overall_score** (mean of Faithfulness, Answer Relevance, Tool Precision), publishes CloudWatch, indexes OpenSearch Serverless (`eval-audit`, `eval-cases`) with per-sample CoT, sets the GitHub Check, and **Slack-alerts on failure**. Merge proceeds only when `overall_score >= 0.85`.

## Golden dataset schema

```json
{
  "version": "1.0",
  "name": "golden",
  "cases": [
    {
      "id": "rag-001",
      "suite": "rag",
      "query": "…",
      "expected_answer": "…",
      "expected_contexts": ["…"],
      "expected_tools": ["search_docs"],
      "recorded_trace": {
        "answer": "…",
        "retrieved_contexts": ["…"],
        "tool_calls": [{ "name": "search_docs", "arguments": {} }]
      }
    }
  ]
}
```

`suite` is `rag` | `tool_calling` | `hybrid`. Case ids must be unique.

## Security and operations

- S3 buckets: TLS only, KMS CMK, Block Public Access, versioning, IA transition.
- SQS: SSE, `enforceSSL`, DLQ + alarm.
- Lambdas: ARM64, X-Ray, Powertools metrics under `AgenticQualityGate`.
- Workers: non-root uid 10001, Fargate Spot with on-demand fallback (`weight 4 / 1`), scale 0–`MAX_WORKERS`.
- IAM: task role is limited to the eval queue, result buckets, DynamoDB, and `bedrock:InvokeModel`. GitHub uses OIDC (no static keys).
- OpenSearch Serverless collection is IAM-auth; data-access policy lists dispatcher/aggregator/worker roles.

Tune thresholds with `THRESHOLD_*` on the stack (see `.env.example`). Judge token spend is estimated on the report (`JUDGE_*_USD_PER_MTOK`).

## Development

```bash
make fmt
make lint
make test
```

Python 3.12, Ruff, pytest, moto. CDK app lives in `infra/` (`npx cdk synth`).
