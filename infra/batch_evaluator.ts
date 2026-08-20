import * as cdk from "aws-cdk-lib";
import * as batch from "aws-cdk-lib/aws-batch";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import { Construct } from "constructs";
import { GateConfig, resourceName } from "./config";

export interface BatchEvaluatorProps {
  readonly config: GateConfig;
  readonly vpc: ec2.IVpc;
  readonly image: ecr_assets.DockerImageAsset;
  readonly taskRole: iam.IRole;
  readonly envVars: Record<string, string>;
}

/**
 * AWS Batch Fargate Spot compute for one-shot shard evaluation (EVAL_JOB_JSON).
 */
export class BatchEvaluator extends Construct {
  public readonly jobQueue: batch.CfnJobQueue;
  public readonly jobDefinition: batch.CfnJobDefinition;

  constructor(scope: Construct, id: string, props: BatchEvaluatorProps) {
    super(scope, id);
    const { config } = props;

    const execRole = new iam.Role(this, "BatchExecRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AmazonECSTaskExecutionRolePolicy"),
      ],
    });

    const compute = new batch.CfnComputeEnvironment(this, "Compute", {
      computeEnvironmentName: resourceName(config, "batch"),
      type: "MANAGED",
      state: "ENABLED",
      computeResources: {
        type: "FARGATE_SPOT",
        maxvCpus: Math.max(config.maxWorkers * 2, 16),
        subnets: props.vpc.publicSubnets.map((s) => s.subnetId),
        securityGroupIds: [
          new ec2.SecurityGroup(this, "BatchSg", {
            vpc: props.vpc,
            allowAllOutbound: true,
            description: "AWS Batch Fargate Spot evaluators",
          }).securityGroupId,
        ],
      },
    });

    this.jobQueue = new batch.CfnJobQueue(this, "Queue", {
      jobQueueName: resourceName(config, "batch-queue"),
      priority: 1,
      state: "ENABLED",
      computeEnvironmentOrder: [{ order: 1, computeEnvironment: compute.ref }],
    });

    const logGroup = new logs.LogGroup(this, "BatchLogs", {
      logGroupName: `/aqg/${config.envName}/batch`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.jobDefinition = new batch.CfnJobDefinition(this, "JobDef", {
      jobDefinitionName: resourceName(config, "batch-job"),
      type: "container",
      platformCapabilities: ["FARGATE"],
      containerProperties: {
        image: props.image.imageUri,
        jobRoleArn: props.taskRole.roleArn,
        executionRoleArn: execRole.roleArn,
        resourceRequirements: [
          { type: "VCPU", value: "1" },
          { type: "MEMORY", value: "2048" },
        ],
        fargatePlatformConfiguration: { platformVersion: "LATEST" },
        runtimePlatform: {
          operatingSystemFamily: "LINUX",
          cpuArchitecture: "X86_64",
        },
        environment: Object.entries(props.envVars).map(([name, value]) => ({ name, value })),
        logConfiguration: {
          logDriver: "awslogs",
          options: {
            "awslogs-group": logGroup.logGroupName,
            "awslogs-region": cdk.Stack.of(this).region,
            "awslogs-stream-prefix": "batch",
          },
        },
        networkConfiguration: { assignPublicIp: "ENABLED" },
      },
      retryStrategy: { attempts: 2 },
      timeout: { attemptDurationSeconds: 900 },
    });

    new cdk.CfnOutput(this, "BatchJobQueue", { value: this.jobQueue.ref });
    new cdk.CfnOutput(this, "BatchJobDefinition", { value: this.jobDefinition.ref });
  }
}
