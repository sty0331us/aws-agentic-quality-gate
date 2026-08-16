import * as path from "node:path";
import * as cdk from "aws-cdk-lib";
import * as appscaling from "aws-cdk-lib/aws-applicationautoscaling";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { Construct } from "constructs";
import { GateConfig, resourceName } from "./config";

export interface EvaluatorServiceProps {
  readonly config: GateConfig;
  readonly vpc: ec2.IVpc;
  readonly evalQueue: sqs.IQueue;
  readonly resultsQueue: sqs.IQueue;
  readonly datasetBucket: s3.IBucket;
  readonly resultsBucket: s3.IBucket;
  readonly runsTable: dynamodb.ITable;
  readonly opensearchEndpoint: string;
  readonly envVars: Record<string, string>;
  readonly taskRole: iam.Role;
}

/**
 * Fargate Spot evaluation workers. Desired count starts at 0; the dispatcher
 * and a queue-depth step-scaling policy scale the service out. Workers exit
 * after an idle period so the service can return to zero.
 */
export class EvaluatorService extends Construct {
  public readonly cluster: ecs.Cluster;
  public readonly service: ecs.FargateService;
  public readonly taskDefinition: ecs.FargateTaskDefinition;

  constructor(scope: Construct, id: string, props: EvaluatorServiceProps) {
    super(scope, id);
    const { config } = props;

    this.cluster = new ecs.Cluster(this, "Cluster", {
      clusterName: resourceName(config, "cluster"),
      vpc: props.vpc,
      containerInsights: true,
      enableFargateCapacityProviders: true,
    });

    props.evalQueue.grantConsumeMessages(props.taskRole);
    props.resultsQueue.grantSendMessages(props.taskRole);
    props.datasetBucket.grantRead(props.taskRole);
    props.resultsBucket.grantReadWrite(props.taskRole);
    props.runsTable.grantReadWriteData(props.taskRole);
    props.taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "BedrockJudge",
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: ["*"],
      }),
    );

    this.taskDefinition = new ecs.FargateTaskDefinition(this, "TaskDef", {
      family: resourceName(config, "evaluator"),
      cpu: 1024,
      memoryLimitMiB: 2048,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
      taskRole: props.taskRole,
    });

    const image = new ecr_assets.DockerImageAsset(this, "WorkerImage", {
      directory: path.join(__dirname, ".."),
      file: "services/worker/Dockerfile",
      platform: ecr_assets.Platform.LINUX_AMD64,
      buildArgs: { INSTALL_EVAL_LIBS: process.env.INSTALL_EVAL_LIBS ?? "false" },
    });

    const logGroup = new logs.LogGroup(this, "WorkerLogs", {
      logGroupName: `/aqg/${config.envName}/worker`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.taskDefinition.addContainer("evaluator", {
      image: ecs.ContainerImage.fromDockerImageAsset(image),
      logging: ecs.LogDrivers.awsLogs({ logGroup, streamPrefix: "evaluator" }),
      environment: {
        ...props.envVars,
        EVAL_QUEUE_URL: props.evalQueue.queueUrl,
        RESULTS_QUEUE_URL: props.resultsQueue.queueUrl,
        OPENSEARCH_ENDPOINT: props.opensearchEndpoint,
        VISIBILITY_TIMEOUT: "900",
        WORKER_IDLE_EXIT_SECONDS: "90",
      },
      healthCheck: {
        command: ["CMD-SHELL", "python -c 'import bedrock_judge' || exit 1"],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(20),
      },
    });

    this.service = new ecs.FargateService(this, "Service", {
      serviceName: resourceName(config, "evaluator"),
      cluster: this.cluster,
      taskDefinition: this.taskDefinition,
      desiredCount: 0,
      assignPublicIp: true,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      capacityProviderStrategies: [
        { capacityProvider: "FARGATE_SPOT", weight: 4, base: 0 },
        { capacityProvider: "FARGATE", weight: 1, base: 0 },
      ],
      circuitBreaker: { rollback: true },
      minHealthyPercent: 0,
      maxHealthyPercent: 200,
      enableExecuteCommand: config.envName !== "prod",
    });

    const scaling = this.service.autoScaleTaskCount({
      minCapacity: 0,
      maxCapacity: config.maxWorkers,
    });
    scaling.scaleOnMetric("QueueDepth", {
      metric: props.evalQueue.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.minutes(1),
      }),
      scalingSteps: [
        { upper: 0, change: 0 },
        { lower: 1, change: +1 },
        { lower: 8, change: +4 },
        { lower: 32, change: +8 },
      ],
      adjustmentType: appscaling.AdjustmentType.CHANGE_IN_CAPACITY,
      cooldown: cdk.Duration.minutes(1),
    });

    new cdk.CfnOutput(this, "ClusterName", { value: this.cluster.clusterName });
    new cdk.CfnOutput(this, "ServiceName", { value: this.service.serviceName });
  }
}
