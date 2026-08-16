#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { AgenticQualityGateStack } from "../lib/eval-engine-stack";

const app = new cdk.App();
const account = process.env.CDK_DEFAULT_ACCOUNT;
const region = process.env.CDK_DEFAULT_REGION ?? process.env.AWS_REGION ?? "us-east-1";

new AgenticQualityGateStack(app, "AgenticQualityGate", {
  env: { account, region },
  description: "Automated Agentic CI/CD Evaluation Engine (SQS + Fargate Spot + Bedrock LLM-as-a-Judge)",
});
