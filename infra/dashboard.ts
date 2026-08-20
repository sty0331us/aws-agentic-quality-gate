import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import { Construct } from "constructs";
import { GateConfig, resourceName } from "./config";

/** CloudWatch dashboard for Faithfulness, Answer Relevance, and Tool Precision. */
export class EvalDashboard extends Construct {
  constructor(scope: Construct, id: string, props: { config: GateConfig }) {
    super(scope, id);
    const ns = "AgenticQualityGate";
    const dashboard = new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: resourceName(props.config, "metrics"),
    });
    const names = ["OverallScore", "Faithfulness", "AnswerRelevance", "ToolPrecision"];
    dashboard.addWidgets(
      ...names.map(
        (metricName) =>
          new cloudwatch.GraphWidget({
            title: metricName,
            left: [
              new cloudwatch.Metric({
                namespace: ns,
                metricName,
                statistic: "Average",
              }),
            ],
          }),
      ),
    );
  }
}
