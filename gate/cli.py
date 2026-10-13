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

    args = parser.parse_args(argv)
    today = _today(args.today)

    catalogue, rights = _load(args.source)
    findings = run_all(catalogue, rights, today)

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
