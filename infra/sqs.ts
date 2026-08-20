import * as cdk from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cw_actions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as sns from "aws-cdk-lib/aws-sns";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { Construct } from "constructs";
import { GateConfig, resourceName } from "./config";

export interface EvalQueuesProps {
  readonly config: GateConfig;
  readonly alarmTopic?: sns.ITopic;
}

/**
 * FIFO evaluation job queue: message deduplication + per-shard concurrency buffer.
 */
export class EvalQueues extends Construct {
  public readonly evalQueue: sqs.Queue;
  public readonly evalDlq: sqs.Queue;
  public readonly resultsQueue: sqs.Queue;
  public readonly resultsDlq: sqs.Queue;

  constructor(scope: Construct, id: string, props: EvalQueuesProps) {
    super(scope, id);
    const { config } = props;

    this.evalDlq = new sqs.Queue(this, "EvalDlq", {
      queueName: `${resourceName(config, "eval-dlq")}.fifo`,
      fifo: true,
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
    });

    this.evalQueue = new sqs.Queue(this, "EvalQueue", {
      queueName: `${resourceName(config, "eval")}.fifo`,
      fifo: true,
      contentBasedDeduplication: false,
      deduplicationScope: sqs.DeduplicationScope.MESSAGE_GROUP,
      fifoThroughputLimit: sqs.FifoThroughputLimit.PER_MESSAGE_GROUP_ID,
      visibilityTimeout: cdk.Duration.minutes(15),
      retentionPeriod: cdk.Duration.days(4),
      receiveMessageWaitTime: cdk.Duration.seconds(20),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      deadLetterQueue: {
        queue: this.evalDlq,
        maxReceiveCount: 3,
      },
    });

    this.resultsDlq = new sqs.Queue(this, "ResultsDlq", {
      queueName: `${resourceName(config, "results-dlq")}.fifo`,
      fifo: true,
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
    });

    this.resultsQueue = new sqs.Queue(this, "ResultsQueue", {
      queueName: `${resourceName(config, "results")}.fifo`,
      fifo: true,
      contentBasedDeduplication: false,
      deduplicationScope: sqs.DeduplicationScope.MESSAGE_GROUP,
      fifoThroughputLimit: sqs.FifoThroughputLimit.PER_MESSAGE_GROUP_ID,
      visibilityTimeout: cdk.Duration.minutes(2),
      receiveMessageWaitTime: cdk.Duration.seconds(20),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      deadLetterQueue: {
        queue: this.resultsDlq,
        maxReceiveCount: 5,
      },
    });

    const dlqAlarm = new cloudwatch.Alarm(this, "EvalDlqAlarm", {
      metric: this.evalDlq.metricApproximateNumberOfMessagesVisible(),
      threshold: 1,
      evaluationPeriods: 1,
      alarmDescription: "Poison eval shards landed on the DLQ",
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    if (props.alarmTopic) {
      dlqAlarm.addAlarmAction(new cw_actions.SnsAction(props.alarmTopic));
    }

    new cdk.CfnOutput(this, "EvalQueueUrl", { value: this.evalQueue.queueUrl });
    new cdk.CfnOutput(this, "ResultsQueueUrl", { value: this.resultsQueue.queueUrl });
  }
}
