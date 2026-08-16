import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { GateConfig, resourceName } from "./config";

export interface GitHubOidcProps {
  readonly config: GateConfig;
  readonly datasetBucket: s3.IBucket;
  readonly dispatcher: lambda.IFunction;
}

/**
 * GitHub Actions OIDC role. The eval pipeline assumes this role to upload
 * datasets and invoke the dispatcher — no long-lived AWS keys in GitHub.
 */
export class GitHubOidc extends Construct {
  public readonly role: iam.Role;

  constructor(scope: Construct, id: string, props: GitHubOidcProps) {
    super(scope, id);
    const repo = props.config.githubRepo;
    const existingProviderArn = this.node.tryGetContext("githubOidcProviderArn") as string | undefined;
    const provider = existingProviderArn
      ? iam.OpenIdConnectProvider.fromOpenIdConnectProviderArn(
          this,
          "Provider",
          existingProviderArn,
        )
      : new iam.OpenIdConnectProvider(this, "Provider", {
          url: "https://token.actions.githubusercontent.com",
          clientIds: ["sts.amazonaws.com"],
        });

    this.role = new iam.Role(this, "Role", {
      roleName: resourceName(props.config, "github-oidc"),
      description: "GitHub Actions OIDC role for the agentic quality gate",
      assumedBy: new iam.FederatedPrincipal(
        provider.openIdConnectProviderArn,
        {
          StringEquals: {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          },
          StringLike: {
            "token.actions.githubusercontent.com:sub": `repo:${repo}:*`,
          },
        },
        "sts:AssumeRoleWithWebIdentity",
      ),
      maxSessionDuration: cdk.Duration.hours(1),
    });

    props.datasetBucket.grantReadWrite(this.role, "golden/*");
    props.dispatcher.grantInvoke(this.role);
    this.role.addToPolicy(
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [props.datasetBucket.bucketArn],
      }),
    );

    new cdk.CfnOutput(this, "GitHubOidcRoleArn", { value: this.role.roleArn });
  }
}
