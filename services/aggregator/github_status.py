"""Post GitHub Check Runs and PR comments for the quality gate."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import orjson
from eval_common.models import AggregateReport
from eval_common.reporting import check_title

logger = logging.getLogger("aqg.github")
_API = "https://api.github.com"


def _token_from_secret(secrets: Any, secret_arn: str | None) -> str | None:
    if not secret_arn:
        return None
    response = secrets.get_secret_value(SecretId=secret_arn)
    raw = response.get("SecretString") or ""
    try:
        parsed = orjson.loads(raw)
        return parsed.get("GITHUB_TOKEN") or parsed.get("token")
    except orjson.JSONDecodeError:
        return raw or None


def publish_github_gate(
    *,
    report: AggregateReport,
    markdown: str,
    secrets: Any,
    token_override: str | None,
    secret_arn: str | None,
) -> dict[str, Any]:
    token = token_override or _token_from_secret(secrets, secret_arn)
    repo = report.github.repo
    sha = report.github.git_sha
    if not token or not repo or not sha:
        logger.warning("skipping GitHub publish; token/repo/sha missing")
        return {"published": False, "reason": "missing_credentials_or_context"}

    conclusion = "success" if report.decision.passed else "failure"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "aws-agentic-quality-gate",
    }
    check_body = {
        "name": "agentic-quality-gate",
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": check_title(report),
            "summary": markdown[:65535],
        },
    }
    with httpx.Client(timeout=20.0) as client:
        check = client.post(f"{_API}/repos/{repo}/check-runs", headers=headers, json=check_body)
        check.raise_for_status()
        check_json = check.json()
        comment_url = None
        if report.github.pr_number:
            comment = client.post(
                f"{_API}/repos/{repo}/issues/{report.github.pr_number}/comments",
                headers=headers,
                json={"body": markdown},
            )
            comment.raise_for_status()
            comment_url = comment.json().get("html_url")
    return {
        "published": True,
        "conclusion": conclusion,
        "check_run_id": check_json.get("id"),
        "check_run_url": check_json.get("html_url"),
        "comment_url": comment_url,
    }
