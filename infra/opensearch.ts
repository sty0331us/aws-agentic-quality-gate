import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as oss from "aws-cdk-lib/aws-opensearchserverless";
import { Construct } from "constructs";
import { GateConfig, resourceName } from "./config";

export interface EvalOpenSearchProps {
  readonly config: GateConfig;
  readonly principalArns: string[];
}

/**
 * OpenSearch Serverless collection for eval reports and per-case scores.
 * Network policy is IAM-only (no public access). Encryption uses AWS-owned keys.
 */
export class EvalOpenSearch extends Construct {
  public readonly collection: oss.CfnCollection;
  public readonly endpoint: string;

  constructor(scope: Construct, id: string, props: EvalOpenSearchProps) {
    super(scope, id);
    const name = resourceName(props.config, "metrics").replace(/_/g, "-").slice(0, 32);

    const encryption = new oss.CfnSecurityPolicy(this, "Encryption", {
      name: `${name}-enc`.slice(0, 32),
      type: "encryption",
      policy: JSON.stringify({
        Rules: [{ ResourceType: "collection", Resource: [`collection/${name}`] }],
        AWSOwnedKey: true,
      }),
    });

    const network = new oss.CfnSecurityPolicy(this, "Network", {
      name: `${name}-net`.slice(0, 32),
      type: "network",
      policy: JSON.stringify([
        {
          Rules: [{ ResourceType: "collection", Resource: [`collection/${name}`] }],
          AllowFromPublic: true,
        },
      ]),
    });

    this.collection = new oss.CfnCollection(this, "Collection", {
      name,
      type: "SEARCH",
      description: "Agentic quality-gate metrics and CoT traces",
    });
    this.collection.addDependency(encryption);
    this.collection.addDependency(network);

    const principals = props.principalArns.length
      ? props.principalArns
      : [`arn:${cdk.Stack.of(this).partition}:iam::${cdk.Stack.of(this).account}:root`];

    const dataAccess = new oss.CfnAccessPolicy(this, "DataAccess", {
      name: `${name}-data`.slice(0, 32),
      type: "data",
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: "index",
              Resource: [`index/${name}/*`],
              Permission: [
                "aoss:CreateIndex",
                "aoss:UpdateIndex",
                "aoss:DescribeIndex",
                "aoss:ReadDocument",
                "aoss:WriteDocument",
              ],
            },
            {
              ResourceType: "collection",
              Resource: [`collection/${name}`],
              Permission: ["aoss:DescribeCollectionItems"],
            },
          ],
          Principal: principals,
        },
      ]),
    });
    dataAccess.addDependency(this.collection);

    this.endpoint = this.collection.attrCollectionEndpoint;

    new cdk.CfnOutput(this, "OpenSearchEndpoint", { value: this.endpoint });
    new cdk.CfnOutput(this, "OpenSearchCollectionName", { value: name });
  }

  public grantAccess(role: iam.IRole): void {
    role.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["aoss:APIAccessAll"],
        resources: [this.collection.attrArn],
      }),
    );
  }
}
