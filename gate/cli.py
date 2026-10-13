"""
Entry point.

    python -m gate.cli check                      # offline fixture
    python -m gate.cli check --source shopify     # live store
    python -m gate.cli check --format markdown > report.md
    python -m gate.cli sync --team RBM             # findings -> Linear issues

Exit code is 1 when anything blocking is open, so this can sit in CI as a
launch gate rather than as a report somebody reads on a good day.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from . import report
from .data import load_catalogue, load_rights
from .rules import BLOCKING, run_all


def _load(source: str):
    rights = load_rights()
    if source == "shopify":
        from .shopify import fetch_catalogue  # imported late: needs network

        return fetch_catalogue(), rights
    return load_catalogue(), rights


def _today(arg: str | None) -> date:
    return date.fromisoformat(arg) if arg else date.today()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gate", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--source", choices=("fixture", "shopify"), default="fixture",
        help="fixture reads data/catalogue.csv and needs no network (default)",
    )
    common.add_argument("--today", help="override the run date, e.g. 2026-11-04")

    c = sub.add_parser("check", parents=[common], help="run the rules and report")
    c.add_argument("--format", choices=("terminal", "json", "markdown"), default="terminal")

    s = sub.add_parser("sync", parents=[common], help="open/update a Linear issue per finding")
    s.add_argument("--team", required=True, help="Linear team key, e.g. RBM")
    s.add_argument("--dry-run", action="store_true", help="print what it would do")

    r = sub.add_parser(
        "readiness", parents=[common],
        help="launch readiness board; the catalogue gate feeds the `gate` rows",
    )
    r.add_argument("--format", choices=("terminal", "markdown"), default="terminal")

    d = sub.add_parser(
        "digest", parents=[common],
        help="sync Linear, age the board against SLAs, post the brief to Slack",
    )
    d.add_argument("--team", required=True, help="Linear team key, e.g. RBM")
    d.add_argument("--no-slack", action="store_true", help="print the message instead of posting")
    d.add_argument("--no-escalate", action="store_true", help="report SLA breaches without raising them")
    d.add_argument(
        "--as-of",
        help="evaluate SLA ages as if it were this date, e.g. 2026-10-20. The issues "
             "are real and so is the escalation; only the clock is moved.",
    )

    g = sub.add_parser(
        "page", parents=[common],
        help="render one self-contained HTML page from this run",
    )
    g.add_argument("--out", default="run.html", help="output path (default run.html)")

    i = sub.add_parser(
        "intake", parents=[common],
        help="triage a stakeholder demand against the source and the SOW",
    )
    i.add_argument("--demand", required=True, help="path to a demand JSON, e.g. demands/D-001.json")
    i.add_argument("--team", help="Linear team key; needed to link or open issues")
    i.add_argument("--open", action="store_true", help="open the Linear issue")
    i.add_argument("--slack", action="store_true", help="post the verdict to the ops channel")

    args = parser.parse_args(argv)
    today = _today(args.today)

    catalogue, rights = _load(args.source)
    findings = run_all(catalogue, rights, today)

    if args.cmd == "page":
        from .intake import Demand, triage
        from .page import render
        from .readiness import load_checks, resolve
        from pathlib import Path as _P

        checks = resolve(load_checks(), findings)
        triages = []
        for d in sorted(_P("demands").glob("*.json")) if _P("demands").is_dir() else []:
            triages.append(triage(Demand.from_json(d), rights, findings, today))
        _P(args.out).write_text(render(findings, checks, triages, today), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0

    if args.cmd == "intake":
        from .intake import (
            CHANGE_REQUEST, TRACKED, Demand, issue_body, issue_title,
            to_terminal, triage,
        )

        demand = Demand.from_json(args.demand)
        keys = {}
        if args.team:
            from .linear import open_keys

            keys = open_keys(args.team)
        t = triage(demand, rights, findings, today, open_issue_keys=keys)
        print(to_terminal(t))

        if args.open and t.opens_a_ticket:
            if not args.team:
                parser.error("--open needs --team")
            from .linear import _ensure_labels, _post, _resolve_team, _CREATE

            token_team = _resolve_team(__import__("os").environ["LINEAR_API_KEY"], args.team)
            names = {f"intake:{t.verdict}"} | ({f"owner:{t.sow.owner}"} if t.sow else set())
            labels = _ensure_labels(__import__("os").environ["LINEAR_API_KEY"], token_team, names)
            data = _post(
                __import__("os").environ["LINEAR_API_KEY"],
                _CREATE,
                {"input": {
                    "teamId": token_team,
                    "title": issue_title(t),
                    "description": issue_body(t),
                    "priority": 1 if t.verdict == CHANGE_REQUEST else 2,
                    "labelIds": list(labels.values()),
                }},
            )["issueCreate"]
            print(f"  linear: {data['issue']['identifier']}  {data['issue']['url']}")

        if args.slack:
            from .slack import post

            post(
                f"*Intake {demand.id} — {t.verdict.replace('_', ' ').upper()}*\n"
                f"from {demand.sender} · _{demand.subject}_\n"
                f"> {demand.ask}\n\n*Verdict:* {t.reason}"
                + ("\n\n*Acceptance*\n" + "\n".join(f"{n}. {a}" for n, a in enumerate(t.acceptance, 1)) if t.acceptance else "")
            )
            print("  slack: posted")
        return 0 if t.verdict != CHANGE_REQUEST else 1

    if args.cmd == "digest":
        from .linear import sync
        from .readiness import load_checks, resolve
        from .sla import check_slas
        from .slack import compose, post

        issues = sync(findings, team_key=args.team)
        urls = {i["key"]: i["url"] for i in issues}
        created = sum(1 for i in issues if i["action"] == "created")
        print(f"linear: {created} created, {len(issues) - created} updated")

        as_of = None
        if args.as_of:
            from datetime import datetime, time, timezone

            as_of = datetime.combine(date.fromisoformat(args.as_of), time(12), timezone.utc)
            print(f"sla:    ageing the board as of {args.as_of}")
        breaches = check_slas(args.team, now=as_of, escalate=not args.no_escalate)
        raised = sum(1 for b in breaches if b.escalated_now)
        print(f"sla:    {len(breaches)} past SLA, {raised} raised to urgent this run")

        checks = resolve(load_checks(), findings)
        text = compose(findings, checks, breaches, urls, run_date=str(today))
        if args.no_slack:
            print("\n--- slack message ---\n" + text)
        else:
            post(text)
            print("slack:  posted")
        return 0 if all(not c.holds_launch for c in checks) else 1

    if args.cmd == "readiness":
        from .readiness import load_checks, resolve, to_markdown, to_terminal, verdict

        checks = resolve(load_checks(), findings)
        print(to_markdown(checks) if args.format == "markdown" else to_terminal(checks))
        # No-go is a failure exit, same as a blocking finding: this is a gate.
        return 0 if verdict(checks)["go"] else 1

    if args.cmd == "check":
        if args.format == "json":
            print(report.to_json(findings, today))
        elif args.format == "markdown":
            print(report.to_markdown(findings, today))
        else:
            print(report.to_terminal(findings, today))
    else:
        if args.dry_run:
            for f in findings:
                print(f"would sync [{f.key}] {f.title or f.isbn} -> {f.owner}")
        else:
            from .linear import sync  # imported late: needs network

            for r in sync(findings, team_key=args.team):
                print(f"{r['action']:>7}  {r['identifier']}  {r['url']}")
            print(f"\n{len(findings)} finding(s) synced to Linear team {args.team}.")

    return 1 if any(f.severity == BLOCKING for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
