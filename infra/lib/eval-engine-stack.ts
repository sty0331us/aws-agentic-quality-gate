import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as sns from "aws-cdk-lib/aws-sns";
import { Construct } from "constructs";
import { loadConfig } from "../config";
import { EvaluatorService } from "../ecs_evaluator";
import { GitHubOidc } from "../github_oidc";
import { EvalLambdas, grantLambdaPermissions } from "../lambdas";
import { EvalOpenSearch } from "../opensearch";
import { EvalQueues } from "../sqs";
import { EvalStorage } from "../storage";

export class AgenticQualityGateStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const config = loadConfig();

    const vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: "public", subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
      ],
    });

    const alarmTopic = new sns.Topic(this, "Alarms", {
      topicName: `aqg-${config.envName}-alarms`,
      displayName: "Agentic quality gate alarms",
    });

    const storage = new EvalStorage(this, "Storage", { config });
    const queues = new EvalQueues(this, "Queues", { config, alarmTopic });

    const dispatcherRole = new iam.Role(this, "DispatcherRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"),
        iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXRayDaemonWriteAccess"),
      ],
    });
    const aggregatorRole = new iam.Role(this, "AggregatorRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"),
        iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXRayDaemonWriteAccess"),
      ],
    });
    const workerRole = new iam.Role(this, "WorkerTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
    });

    const search = new EvalOpenSearch(this, "Search", {
      config,
      principalArns: [dispatcherRole.roleArn, aggregatorRole.roleArn, workerRole.roleArn],
    });
    search.grantAccess(aggregatorRole);
    search.grantAccess(workerRole);

    const sharedEnv = {
      ENVIRONMENT: config.envName,
      JUDGE_MODEL_ID: config.judgeModelId,
      SHARD_SIZE: String(config.shardSize),
      MAX_WORKERS: String(config.maxWorkers),
      GITHUB_REPO: config.githubRepo,
      THRESHOLD_FAITHFULNESS: config.thresholds.faithfulness,
      THRESHOLD_ANSWER_RELEVANCE: config.thresholds.answerRelevance,
      THRESHOLD_TOOL_SELECTION_PRECISION: config.thresholds.toolSelectionPrecision,
      THRESHOLD_MIN_PASS_RATE: config.thresholds.minPassRate,
      THRESHOLD_MAX_ERROR_RATE: config.thresholds.maxErrorRate,
      LOG_LEVEL: "INFO",
    };

    const evaluator = new EvaluatorService(this, "Evaluator", {
      config,
      vpc,
      evalQueue: queues.evalQueue,
      resultsQueue: queues.resultsQueue,
      datasetBucket: storage.datasetBucket,
      resultsBucket: storage.resultsBucket,
      runsTable: storage.runsTable,
      opensearchEndpoint: search.endpoint,
      envVars: sharedEnv,
      taskRole: workerRole,
    });

    grantLambdaPermissions({
      dispatcherRole,
      aggregatorRole,
      datasetBucket: storage.datasetBucket,
      resultsBucket: storage.resultsBucket,
      evalQueue: queues.evalQueue,
      resultsQueue: queues.resultsQueue,
      runsTable: storage.runsTable,
      githubSecret: storage.githubSecret,
      ecsClusterName: evaluator.cluster.clusterName,
      ecsServiceName: evaluator.service.serviceName,
    });

    const lambdas = new EvalLambdas(this, "Lambdas", {
      config,
      datasetBucket: storage.datasetBucket,
      resultsBucket: storage.resultsBucket,
      evalQueue: queues.evalQueue,
      resultsQueue: queues.resultsQueue,
      runsTable: storage.runsTable,
      githubSecret: storage.githubSecret,
      opensearchEndpoint: search.endpoint,
      dispatcherRole,
      aggregatorRole,
      ecsClusterName: evaluator.cluster.clusterName,
      ecsServiceName: evaluator.service.serviceName,
      envVars: sharedEnv,
    });

    new GitHubOidc(this, "GitHubOidc", {
      config,
      datasetBucket: storage.datasetBucket,
      dispatcher: lambdas.dispatcher,
    });

    storage.key.grantEncryptDecrypt(dispatcherRole);
    storage.key.grantEncryptDecrypt(aggregatorRole);
    storage.key.grantEncryptDecrypt(workerRole);

    cdk.Tags.of(this).add("Project", "agentic-quality-gate");
    cdk.Tags.of(this).add("Environment", config.envName);
  }
}
