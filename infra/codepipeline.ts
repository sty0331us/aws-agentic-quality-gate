import * as cdk from "aws-cdk-lib";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as codepipeline from "aws-cdk-lib/aws-codepipeline";
import * as cpactions from "aws-cdk-lib/aws-codepipeline-actions";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { GateConfig, resourceName } from "./config";

export interface EvalPipelineProps {
  readonly config: GateConfig;
  readonly dispatcher: lambda.IFunction;
  readonly datasetBucket: s3.IBucket;
}

/**
 * AWS CodePipeline trigger path (GitHub via CodeStar Connections).
 * Set CODESTAR_CONNECTION_ARN to enable; GitHub Actions remains the default webhook.
 */
export class EvalCodePipeline extends Construct {
  constructor(scope: Construct, id: string, props: EvalPipelineProps) {
    super(scope, id);
    const connectionArn = process.env.CODESTAR_CONNECTION_ARN;
    const project = new codebuild.PipelineProject(this, "DispatchBuild", {
      projectName: resourceName(props.config, "codebuild-dispatch"),
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
      },
      environmentVariables: {
        DISPATCHER_FUNCTION: { value: props.dispatcher.functionName },
        DATASET_BUCKET: { value: props.datasetBucket.bucketName },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: "0.2",
        phases: {
          build: {
            commands: [
              "COMMIT=${CODEBUILD_RESOLVED_SOURCE_VERSION}",
              "KEY=golden/${COMMIT}.json",
              "aws s3 cp datasets/golden_dataset_sample.json s3://${DATASET_BUCKET}/${KEY} || true",
              'PAYLOAD=$(jq -nc --arg bucket "$DATASET_BUCKET" --arg key "$KEY" --arg sha "$COMMIT" \'{dataset_bucket:$bucket,dataset_key:$key,git_sha:$sha,dataset_id:$sha,eval_mode:"candidate",eval_backend:"deepeval"}\')',
              "aws lambda invoke --function-name $DISPATCHER_FUNCTION --cli-binary-format raw-in-base64-out --payload \"$PAYLOAD\" out.json",
              "cat out.json",
            ],
          },
        },
      }),
    });
    props.dispatcher.grantInvoke(project);
    props.datasetBucket.grantReadWrite(project);

    if (!connectionArn) {
      new cdk.CfnOutput(this, "CodeBuildProject", { value: project.projectName });
      return;
    }

    const sourceOutput = new codepipeline.Artifact();
    new codepipeline.Pipeline(this, "Pipeline", {
      pipelineName: resourceName(props.config, "pipeline"),
      stages: [
        {
          stageName: "Source",
          actions: [
            new cpactions.CodeStarConnectionsSourceAction({
              actionName: "GitHub",
              owner: props.config.githubRepo.split("/")[0],
              repo: props.config.githubRepo.split("/")[1] ?? props.config.githubRepo,
              branch: "main",
              connectionArn,
              output: sourceOutput,
            }),
          ],
        },
        {
          stageName: "DispatchEval",
          actions: [
            new cpactions.CodeBuildAction({
              actionName: "TriggerDispatcher",
              project,
              input: sourceOutput,
            }),
          ],
        },
      ],
    });
  }
}
