"""Findings -> terminal, JSON, or Markdown. No cleverness; it just has to read well."""

from __future__ import annotations

import json
from datetime import date

from .rules import BLOCKING, HIGH, Finding

_BADGE = {BLOCKING: "BLOCKING", HIGH: "HIGH", "medium": "MEDIUM"}


def summary(findings: list[Finding]) -> dict:
    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
    return {
        "findings": len(findings),
        "blocking": sum(1 for f in findings if f.severity == BLOCKING),
        "revenue_at_risk_usd": round(sum(f.revenue_at_risk_usd for f in findings), 2),
        "by_rule": dict(sorted(by_rule.items())),
    }


def to_terminal(findings: list[Finding], today: date) -> str:
    s = summary(findings)
    lines = [
        "",
        f"  Catalogue integrity gate — {today}",
        f"  {s['findings']} findings, {s['blocking']} blocking, "
        f"${s['revenue_at_risk_usd']:,.2f}/month at risk",
        "",
    ]
    if not findings:
        lines.append("  Clean. Nothing to hold the launch for.\n")
        return "\n".join(lines)

    for f in findings:
        money = f"  ${f.revenue_at_risk_usd:,.2f}/mo" if f.revenue_at_risk_usd else ""
        lines.append(f"  [{_BADGE.get(f.severity, f.severity.upper())}] {f.rule}{money}")
        lines.append(f"    {f.title or f.isbn}  ({f.subject})")
        lines.append(f"    {f.detail}")
        lines.append(f"    owner: {f.owner}")
        lines.append("")
    return "\n".join(lines)


def to_json(findings: list[Finding], today: date) -> str:
    return json.dumps(
        {
            "run_date": str(today),
            "summary": summary(findings),
            "findings": [f.as_dict() for f in findings],
        },
        indent=2,
    )


def to_markdown(findings: list[Finding], today: date) -> str:
    s = summary(findings)
    out = [
        f"# Catalogue integrity gate — {today}",
        "",
        f"**{s['findings']} findings · {s['blocking']} blocking · "
        f"${s['revenue_at_risk_usd']:,.2f}/month at risk**",
        "",
        "| Severity | Rule | Title | Detail | $/mo at risk | Owner |",
        "|---|---|---|---|---:|---|",
    ]
    for f in findings:
        detail = f.detail.replace("|", "\\|")
        money = f"{f.revenue_at_risk_usd:,.2f}" if f.revenue_at_risk_usd else "—"
        out.append(
            f"| {_BADGE.get(f.severity, f.severity)} | `{f.rule}` | {f.title or f.isbn} "
            f"| {detail} | {money} | `{f.owner}` |"
        )
    out.append("")
    return "\n".join(out)
