"""
The morning brief into the ops channel.

"Real-time communication during launches" is a job-description bullet. In
practice it means: the people who can act find out where they already work,
ranked, with a link — not in a report they open on a good day.

Posts via an incoming webhook (simplest, no scopes to argue about). The message
is deliberately short: verdict, who is holding it, the three worst things, and
anything past its SLA. Anyone wanting detail clicks through to Linear.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .readiness import Check, verdict as readiness_verdict
from .report import summary as findings_summary
from .rules import BLOCKING, Finding


class SlackError(RuntimeError):
    pass


def compose(
    findings: list[Finding],
    checks: list[Check],
    breaches: list = (),
    issue_urls: dict[str, str] | None = None,
    run_date: str = "",
) -> str:
    issue_urls = issue_urls or {}
    rv = readiness_verdict(checks)
    fs = findings_summary(findings)
    go = "GO" if rv["go"] else "NO-GO"

    lines = [
        f"*Launch readiness — {go}*{f'  ·  {run_date}' if run_date else ''}",
        f"{rv['ready']}/{rv['checks']} checks ready · {rv['holding']} blocking not met · "
        f"{rv['unanswered']} unanswered",
        f"Catalogue: {fs['findings']} findings, {fs['blocking']} blocking · "
        f"${fs['revenue_at_risk_usd']:,.0f}/mo at risk",
    ]

    if not rv["go"]:
        who = ", ".join(f"{owner} ({n})" for owner, n in rv["holding_by_owner"].items())
        lines += ["", f"*Holding the launch:* {who}"]

    worst = [f for f in findings if f.severity == BLOCKING][:3]
    if worst:
        lines += ["", "*Worst open findings*"]
        for f in worst:
            link = issue_urls.get(f.key)
            name = f"<{link}|{f.title or f.isbn}>" if link else (f.title or f.isbn)
            money = f" · ${f.revenue_at_risk_usd:,.0f}/mo" if f.revenue_at_risk_usd else ""
            lines.append(f"• {name} — {f.detail}{money} · owner `{f.owner}`")

    if breaches:
        lines += ["", "*Past SLA*"]
        for b in breaches[:5]:
            lines.append(
                f"• <{b.url}|{b.identifier}> {b.title} — {b.age_days}d open against a "
                f"{b.sla_days}d SLA for `{b.owner}` (over by {b.over_by}d)"
            )

    lines += ["", "_Posted by the catalogue integrity gate. Re-runs update Linear in place._"]
    return "\n".join(lines)


# The sender name belongs in the payload, not in a field somebody set once in a
# settings page. Here it is versioned with the code that posts, and a reader can
# see who the channel will think is talking.
BOT_NAME = os.environ.get("SLACK_BOT_NAME", "catalogue-gate")
BOT_EMOJI = os.environ.get("SLACK_BOT_EMOJI", ":vertical_traffic_light:")


def post(text: str, webhook: str | None = None) -> None:
    webhook = webhook or os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook:
        raise SlackError(
            "Set SLACK_WEBHOOK_URL. Slack app -> Incoming Webhooks -> Add New "
            "Webhook to Workspace -> pick the ops channel."
        )
    req = urllib.request.Request(
        webhook,
        data=json.dumps(
            {
                "text": text,
                "mrkdwn": True,
                "username": BOT_NAME,
                "icon_emoji": BOT_EMOJI,
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode().strip()
    except urllib.error.HTTPError as e:
        raise SlackError(
            f"Slack returned HTTP {e.code}: {e.read()[:300]!r}. A 404 usually means "
            f"the webhook URL is wrong or was revoked."
        ) from e
    except urllib.error.URLError as e:
        raise SlackError(f"Could not reach Slack ({e.reason}).") from e
    if body != "ok":
        raise SlackError(f"Slack did not accept the message: {body!r}")
