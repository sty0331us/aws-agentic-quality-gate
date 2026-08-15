import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";
import { GateConfig, resourceName } from "./config";

export interface EvalStorageProps {
  readonly config: GateConfig;
}

export class EvalStorage extends Construct {
  public readonly datasetBucket: s3.Bucket;
  public readonly resultsBucket: s3.Bucket;
  public readonly runsTable: dynamodb.Table;
  public readonly githubSecret: secretsmanager.Secret;
  public readonly key: kms.Key;

  constructor(scope: Construct, id: string, props: EvalStorageProps) {
    super(scope, id);
    const { config } = props;

    this.key = new kms.Key(this, "Key", {
      enableKeyRotation: true,
      alias: resourceName(config, "cmk"),
      description: "Agentic quality gate encryption key",
    });

    const bucketProps: s3.BucketProps = {
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.key,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      lifecycleRules: [
        { abortIncompleteMultipartUploadAfter: cdk.Duration.days(7) },
        {
          transitions: [
            {
              storageClass: s3.StorageClass.INFREQUENT_ACCESS,
              transitionAfter: cdk.Duration.days(30),
            },
          ],
        },
      ],
      removalPolicy: config.envName === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: config.envName !== "prod",
      eventBridgeEnabled: true,
    };

    this.datasetBucket = new s3.Bucket(this, "Datasets", bucketProps);

    this.resultsBucket = new s3.Bucket(this, "Results", bucketProps);

    this.runsTable = new dynamodb.Table(this, "Runs", {
      tableName: resourceName(config, "runs"),
      partitionKey: { name: "eval_run_id", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecovery: true,
      removalPolicy: config.envName === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    this.githubSecret = new secretsmanager.Secret(this, "GitHubToken", {
      secretName: resourceName(config, "github"),
      description: "GitHub token (or {GITHUB_TOKEN:...}) used by the aggregator to post checks",
      encryptionKey: this.key,
    });
    this.githubSecret.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: "DenyInsecureTransport",
        effect: iam.Effect.DENY,
        principals: [new iam.AnyPrincipal()],
        actions: ["secretsmanager:*"],
        resources: ["*"],
        conditions: { Bool: { "aws:SecureTransport": "false" } },
      }),
    );

    new cdk.CfnOutput(this, "DatasetBucketName", { value: this.datasetBucket.bucketName });
    new cdk.CfnOutput(this, "ResultsBucketName", { value: this.resultsBucket.bucketName });
    new cdk.CfnOutput(this, "RunsTableName", { value: this.runsTable.tableName });
    new cdk.CfnOutput(this, "GitHubSecretArn", { value: this.githubSecret.secretArn });
  }
}
