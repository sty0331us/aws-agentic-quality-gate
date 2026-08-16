import { execSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaEventSources from "aws-cdk-lib/aws-lambda-event-sources";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { Construct } from "constructs";
import { GateConfig, resourceName } from "./config";

export interface EvalLambdasProps {
  readonly config: GateConfig;
  readonly datasetBucket: s3.IBucket;
  readonly resultsBucket: s3.IBucket;
  readonly evalQueue: sqs.IQueue;
  readonly resultsQueue: sqs.IQueue;
  readonly runsTable: dynamodb.ITable;
  readonly githubSecret: secretsmanager.ISecret;
  readonly opensearchEndpoint: string;
  readonly dispatcherRole: iam.Role;
  readonly aggregatorRole: iam.Role;
  readonly ecsClusterName: string;
  readonly ecsServiceName: string;
  readonly envVars: Record<string, string>;
}

function pythonCode(service: "dispatcher" | "aggregator"): lambda.Code {
  const repoRoot = path.join(__dirname, "..");
  const serviceDir = path.join(repoRoot, "services", service);
  const commonDir = path.join(repoRoot, "packages", "eval_common", "eval_common");
  return lambda.Code.fromAsset(serviceDir, {
    bundling: {
      image: lambda.Runtime.PYTHON_3_12.bundlingImage,
      local: {
        tryBundle(outputDir: string): boolean {
          try {
            execSync(`python3 -m pip install -r "${serviceDir}/requirements.txt" -t "${outputDir}"`, {
              stdio: "inherit",
            });
            for (const entry of fs.readdirSync(serviceDir)) {
              if (entry === "requirements.txt") continue;
              fs.cpSync(path.join(serviceDir, entry), path.join(outputDir, entry), {
                recursive: true,
              });
            }
            fs.cpSync(commonDir, path.join(outputDir, "eval_common"), { recursive: true });
            return true;
          } catch {
            return false;
          }
        },
      },
      command: [
        "bash",
        "-c",
        [
          "pip install -r requirements.txt -t /asset-output",
          "cp -R . /asset-output",
          "python - <<'PY'",
          "import shutil, os",
          "src='/common/eval_common'",
          "dst='/asset-output/eval_common'",
          "shutil.copytree(src, dst, dirs_exist_ok=True) if os.path.isdir(src) else None",
          "PY",
        ].join(" && "),
      ],
      volumes: [{ hostPath: path.join(repoRoot, "packages", "eval_common"), containerPath: "/common" }],
    },
  });
}

export class EvalLambdas extends Construct {
  public readonly dispatcher: lambda.Function;
  public readonly aggregator: lambda.Function;

  constructor(scope: Construct, id: string, props: EvalLambdasProps) {
    super(scope, id);
    const { config } = props;

    const sharedEnv = {
      ...props.envVars,
      DATASET_BUCKET: props.datasetBucket.bucketName,
      RESULTS_BUCKET: props.resultsBucket.bucketName,
      EVAL_QUEUE_URL: props.evalQueue.queueUrl,
      RESULTS_QUEUE_URL: props.resultsQueue.queueUrl,
      RUNS_TABLE_NAME: props.runsTable.tableName,
      GITHUB_SECRET_ARN: props.githubSecret.secretArn,
      OPENSEARCH_ENDPOINT: props.opensearchEndpoint,
      ECS_CLUSTER_NAME: props.ecsClusterName,
      ECS_SERVICE_NAME: props.ecsServiceName,
      POWERTOOLS_SERVICE_NAME: "aqg",
      POWERTOOLS_METRICS_NAMESPACE: "AgenticQualityGate",
    };

    this.dispatcher = new lambda.Function(this, "Dispatcher", {
      functionName: resourceName(config, "dispatcher"),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.handler",
      code: pythonCode("dispatcher"),
      timeout: cdk.Duration.minutes(2),
      memorySize: 512,
      role: props.dispatcherRole,
      architecture: lambda.Architecture.ARM_64,
      environment: sharedEnv,
      tracing: lambda.Tracing.ACTIVE,
      logGroup: new logs.LogGroup(this, "DispatcherLogs", {
        logGroupName: `/aqg/${config.envName}/dispatcher`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    this.aggregator = new lambda.Function(this, "Aggregator", {
      functionName: resourceName(config, "aggregator"),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.handler",
      code: pythonCode("aggregator"),
      timeout: cdk.Duration.minutes(2),
      memorySize: 512,
      role: props.aggregatorRole,
      architecture: lambda.Architecture.ARM_64,
      environment: sharedEnv,
      tracing: lambda.Tracing.ACTIVE,
      logGroup: new logs.LogGroup(this, "AggregatorLogs", {
        logGroupName: `/aqg/${config.envName}/aggregator`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    new events.Rule(this, "GoldenUploaded", {
      ruleName: resourceName(config, "golden-uploaded"),
      description: "Dispatch an eval run when a golden dataset lands in S3",
      eventPattern: {
        source: ["aws.s3"],
        detailType: ["Object Created"],
        detail: {
          bucket: { name: [props.datasetBucket.bucketName] },
          object: { key: [{ prefix: "golden/" }] },
        },
      },
    }).addTarget(new targets.LambdaFunction(this.dispatcher));

    this.aggregator.addEventSource(
      new lambdaEventSources.SqsEventSource(props.resultsQueue, {
        batchSize: 10,
        maxBatchingWindow: cdk.Duration.seconds(20),
        reportBatchItemFailures: true,
      }),
    );

    new events.Rule(this, "SweepIncompleteRuns", {
      ruleName: resourceName(config, "agg-sweep"),
      schedule: events.Schedule.rate(cdk.Duration.minutes(2)),
      description: "Sweep for timed-out eval runs (workers invoke aggregator on each shard too)",
      enabled: true,
    }).addTarget(
      new targets.LambdaFunction(this.aggregator, {
        event: events.RuleTargetInput.fromObject({ sweep: true }),
      }),
    );

    new cdk.CfnOutput(this, "DispatcherName", { value: this.dispatcher.functionName });
    new cdk.CfnOutput(this, "AggregatorName", { value: this.aggregator.functionName });
    new cdk.CfnOutput(this, "DispatcherArn", { value: this.dispatcher.functionArn });
  }
}

export function grantLambdaPermissions(props: {
  dispatcherRole: iam.Role;
  aggregatorRole: iam.Role;
  datasetBucket: s3.IBucket;
  resultsBucket: s3.IBucket;
  evalQueue: sqs.IQueue;
  resultsQueue: sqs.IQueue;
  runsTable: dynamodb.ITable;
  githubSecret: secretsmanager.ISecret;
  ecsClusterName: string;
  ecsServiceName: string;
}): void {
  const { dispatcherRole, aggregatorRole } = props;
  props.datasetBucket.grantRead(dispatcherRole);
  props.resultsBucket.grantReadWrite(dispatcherRole);
  props.resultsBucket.grantReadWrite(aggregatorRole);
  props.evalQueue.grantSendMessages(dispatcherRole);
  props.resultsQueue.grantConsumeMessages(aggregatorRole);
  props.runsTable.grantReadWriteData(dispatcherRole);
  props.runsTable.grantReadWriteData(aggregatorRole);
  props.githubSecret.grantRead(aggregatorRole);

  dispatcherRole.addToPolicy(
    new iam.PolicyStatement({
      actions: ["ecs:DescribeServices", "ecs:UpdateService"],
      resources: ["*"],
    }),
  );
  aggregatorRole.addToPolicy(
    new iam.PolicyStatement({
      actions: ["aoss:APIAccessAll"],
      resources: ["*"],
    }),
  );
}
