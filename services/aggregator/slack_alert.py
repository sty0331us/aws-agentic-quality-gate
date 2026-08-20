"""Slack incoming-webhook alert when the quality gate fails (blocks merge)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import orjson
from eval_common.models import AggregateReport

logger = logging.getLogger("aqg.slack")


def _webhook_from_secret(secrets: Any, secret_arn: str | None) -> str | None:
    if not secret_arn:
        return None
    response = secrets.get_secret_value(SecretId=secret_arn)
    raw = response.get("SecretString") or ""
    try:
        parsed = orjson.loads(raw)
        return parsed.get("SLACK_WEBHOOK_URL") or parsed.get("webhook")
    except orjson.JSONDecodeError:
        return None


def notify_slack_failure(
    *,
    report: AggregateReport,
    markdown: str,
    secrets: Any,
    webhook_override: str | None,
    secret_arn: str | None,
) -> dict[str, Any]:
    if report.decision.passed:
        return {"notified": False, "reason": "gate_passed"}
    webhook = webhook_override or _webhook_from_secret(secrets, secret_arn)
    if not webhook:
        logger.warning("skipping Slack alert; webhook missing")
        return {"notified": False, "reason": "missing_webhook"}
    text = (
        f"❌ Agentic quality gate FAILED for `{report.github.repo or 'repo'}` "
        f"sha `{report.github.git_sha or '-'}` — overall score "
        f"{report.overall_score:.3f} < {report.thresholds.overall:.2f}. Merge is blocked.\n"
        + "\n".join(f"• {item}" for item in report.decision.failures[:8])
    )
    payload = {
        "text": text,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{markdown[:2500]}```"},
            },
        ],
    }
    with httpx.Client(timeout=10.0) as client:
        response = client.post(webhook, json=payload)
        response.raise_for_status()
    return {"notified": True}
