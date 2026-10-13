"""
One self-contained HTML page from a real run.

The useful parts of this tool are terminal output, and a terminal cannot be
screen-recorded by a browser-driven recorder — nor pasted into an email. So the
same data renders once, as a page: no external fonts, no CDN, no JavaScript
beyond what the anchors need. It opens from a file:// URL on a plane.
"""

from __future__ import annotations

import html
from datetime import date

from .intake import Triage, _HEAD as INTAKE_HEAD
from .readiness import Check, verdict as readiness_verdict
from .report import summary as findings_summary
from .rules import BLOCKING, HIGH, Finding

_CSS = """
:root{--bg:#0E1113;--card:#15191C;--line:#242A2F;--ink:#EDF1F3;--body:#A4AFB7;
--mute:#6E7A83;--ok:#3DD68C;--warn:#E3B341;--bad:#F07171;--acc:#7DD3FC;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--body);font-family:var(--sans);
font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:40px 28px 80px}
h1{font-size:26px;font-weight:600;color:var(--ink);margin:0 0 4px;letter-spacing:-.01em}
.sub{font-family:var(--mono);font-size:12.5px;color:var(--mute);margin:0 0 28px}
h2{font-size:13px;font-weight:600;color:var(--ink);text-transform:uppercase;
letter-spacing:.1em;margin:40px 0 14px;font-family:var(--mono)}
.verdict{display:inline-block;font-family:var(--mono);font-size:13px;font-weight:600;
padding:6px 12px;border-radius:4px;letter-spacing:.06em}
.verdict.go{background:rgba(61,214,140,.14);color:var(--ok);border:1px solid rgba(61,214,140,.35)}
.verdict.nogo{background:rgba(240,113,113,.12);color:var(--bad);border:1px solid rgba(240,113,113,.35)}
.kpis{display:flex;flex-wrap:wrap;gap:1px;background:var(--line);border:1px solid var(--line);margin-top:18px}
.kpi{background:var(--card);padding:14px 18px;flex:1 1 150px;min-width:0}
.kpi .n{font-family:var(--mono);font-size:22px;color:var(--ink);line-height:1.1}
.kpi .l{font-family:var(--mono);font-size:11px;color:var(--mute);text-transform:uppercase;
letter-spacing:.08em;margin-top:5px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
font-size:13.5px}
th{font-family:var(--mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.09em;
color:var(--mute);text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);font-weight:500}
td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
td.r{text-align:right;font-family:var(--mono);white-space:nowrap;color:var(--ink)}
.tag{font-family:var(--mono);font-size:11px;padding:2px 7px;border-radius:3px;white-space:nowrap}
.tag.blocking{background:rgba(240,113,113,.14);color:var(--bad)}
.tag.high{background:rgba(227,179,65,.14);color:var(--warn)}
.tag.medium{background:rgba(125,211,252,.12);color:var(--acc)}
.tag.pass{background:rgba(61,214,140,.14);color:var(--ok)}
.tag.fail{background:rgba(240,113,113,.14);color:var(--bad)}
.tag.unanswered{background:rgba(110,122,131,.18);color:var(--mute)}
code{font-family:var(--mono);font-size:12.5px;color:var(--acc)}
.t{color:var(--ink)}
.d{color:var(--body);font-size:13px}
.note{font-family:var(--mono);font-size:12px;color:var(--mute);margin-top:12px;line-height:1.7}
.card{background:var(--card);border:1px solid var(--line);padding:18px 20px;margin-top:12px}
.card h3{margin:0 0 6px;font-size:15px;color:var(--ink);font-weight:600}
.card .from{font-family:var(--mono);font-size:11.5px;color:var(--mute);margin:0 0 12px}
.card blockquote{margin:0 0 14px;padding-left:14px;border-left:2px solid var(--line);
color:var(--body);font-style:italic}
.card ol{margin:10px 0 0;padding-left:22px;font-size:13px}
.card li{margin-bottom:5px}
.ev{font-family:var(--mono);font-size:12px;color:var(--mute);margin-top:8px}
"""


def _e(s) -> str:
    return html.escape(str(s))


def _findings_table(findings: list[Finding]) -> str:
    if not findings:
        return '<p class="note">No findings. Nothing holding the launch from the catalogue side.</p>'
    rows = []
    for f in findings:
        money = f"${f.revenue_at_risk_usd:,.0f}" if f.revenue_at_risk_usd else "—"
        rows.append(
            f"<tr><td><span class='tag {f.severity}'>{f.severity.upper()}</span></td>"
            f"<td><code>{_e(f.rule)}</code></td>"
            f"<td><span class='t'>{_e(f.title or f.isbn)}</span>"
            f"<div class='d'>{_e(f.detail)}</div></td>"
            f"<td class='r'>{money}</td>"
            f"<td><code>{_e(f.owner)}</code></td></tr>"
        )
    return (
        "<table><thead><tr><th>Severity</th><th>Rule</th><th>Finding</th>"
        "<th style='text-align:right'>$/mo at risk</th><th>Owner</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _readiness_table(checks: list[Check]) -> str:
    rows = []
    for c in sorted(checks, key=lambda c: (not c.holds_launch, c.area, c.id)):
        state = "pass" if c.ready else ("fail" if c.status == "fail" else "unanswered")
        label = {"pass": "PASS", "fail": "FAIL", "unanswered": "UNANSWERED"}[state]
        hold = "⛔" if c.holds_launch else ("✅" if c.ready else "⚠️")
        rows.append(
            f"<tr><td>{hold}</td><td><code>{_e(c.id)}</code></td>"
            f"<td><span class='t'>{_e(c.check)}</span>"
            + (f"<div class='ev'>{_e(c.evidence)}</div>" if c.evidence else "")
            + f"</td><td><span class='tag {state}'>{label}</span></td>"
            f"<td><code>{_e(c.owner)}</code></td></tr>"
        )
    return (
        "<table><thead><tr><th></th><th>ID</th><th>Check</th><th>Status</th>"
        "<th>Owner</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _intake_cards(triages: list[Triage]) -> str:
    if not triages:
        return ""
    out = []
    for t in triages:
        acc = (
            "<ol>" + "".join(f"<li>{_e(a)}</li>" for a in t.acceptance) + "</ol>"
            if t.acceptance
            else ""
        )
        ev = (
            "".join(f"<div class='ev'>{_e(e)}</div>" for e in t.evidence)
            if t.evidence
            else ""
        )
        sow = (
            f"<div class='ev'>SOW {_e(t.sow.line)} / {_e(t.sow.tag)} · "
            f"{t.sow.hours_used}/{t.sow.hours_approved}h used · owner {_e(t.sow.owner)}</div>"
            if t.sow
            else ""
        )
        tone = {
            "already_tracked": "medium",
            "validated_in_scope": "pass",
            "change_request": "blocking",
            "not_reproducible": "high",
        }[t.verdict]
        out.append(
            f"<div class='card'><h3>{_e(t.demand.subject)}</h3>"
            f"<p class='from'>{_e(t.demand.sender)} · intake {_e(t.demand.id)} · "
            f"needed by {_e(t.demand.needed_by or 'not stated')}</p>"
            f"<blockquote>{_e(t.demand.ask)}</blockquote>"
            f"<span class='tag {tone}'>{INTAKE_HEAD[t.verdict]}</span> "
            f"<span class='d'>{_e(t.reason)}</span>"
            f"{ev}{sow}{acc}</div>"
        )
    return "".join(out)


def render(
    findings: list[Finding],
    checks: list[Check],
    triages: list[Triage],
    today: date,
) -> str:
    rv = readiness_verdict(checks)
    fs = findings_summary(findings)
    go = rv["go"]
    who = (
        ", ".join(f"{o} ({n})" for o, n in rv["holding_by_owner"].items())
        if not go
        else ""
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Launch readiness — {today}</title>
<style>{_CSS}</style></head><body><div class="wrap">

<h1>Catalogue integrity &amp; launch readiness</h1>
<p class="sub">Run {today} · synthetic catalogue, invented imprints · generated by <code>gate.cli page</code></p>

<span class="verdict {'go' if go else 'nogo'}">{'GO' if go else 'NO-GO'}</span>

<div class="kpis">
  <div class="kpi"><div class="n">{rv['ready']}/{rv['checks']}</div><div class="l">checks ready</div></div>
  <div class="kpi"><div class="n">{rv['holding']}</div><div class="l">blocking not met</div></div>
  <div class="kpi"><div class="n">{rv['unanswered']}</div><div class="l">unanswered</div></div>
  <div class="kpi"><div class="n">{fs['blocking']}</div><div class="l">blocking findings</div></div>
  <div class="kpi"><div class="n">${fs['revenue_at_risk_usd']:,.0f}</div><div class="l">per month at risk</div></div>
</div>
{f'<p class="note">Holding the launch: {_e(who)}</p>' if who else ''}

<h2>Catalogue findings</h2>
{_findings_table(findings)}
<p class="note">Ranked blocking first, then by money. Revenue at risk is arithmetic over
estimated monthly units — exposure for the compliance rules, margin given away for
pricing, unrealised revenue for an unlisted licence.</p>

<h2>Launch readiness</h2>
{_readiness_table(checks)}
<p class="note">An unanswered blocking check holds the launch. Rows marked
<code>gate</code> in the source table are not human judgements — they resolve from the
catalogue findings on every run, which is how the two halves stay reconciled.</p>

<h2>What this is not</h2>
<div class="card">
  <h3>This is not observability. There is no telemetry here.</h3>
  <p class="d">Observability is about a system in flight: metrics, logs and traces;
  being able to ask production a question you did not think of before you shipped;
  service-level objectives with an error budget and somebody holding the pager. None
  of that is in this repository, and the readiness board above says so — two of its
  three <code>OBS-</code> rows are failing or unanswered.</p>
  <p class="d">What this <em>is</em>: a monitor over <strong>business invariants</strong>,
  running on a cadence, alerting where the people who can act already work. The
  distinction matters because it is the common gap. Teams instrument the
  infrastructure — CPU, latency, error rate — and leave the business rules
  uninstrumented. That is how a system fails while every dashboard stays green.</p>
</div>
<div class="card">
  <h3>What observability for this launch would actually require</h3>
  <p class="d">Named here because saying what is missing is more useful than
  claiming it is done. None of these are built:</p>
  <ol>
    <li>Checkout <code>p95</code> and error rate, split by territory — the three
    highest-volume markets separately, not one global average.</li>
    <li>Entitlement delivery failure rate: paid but not playable, visible within
    minutes rather than at the next support ticket.</li>
    <li>Order-to-delivery success as a single funnel number, so a regression anywhere
    along it shows up in one place.</li>
    <li>An error budget on checkout, with a named pager owner — a dashboard nobody is
    paged from is decoration.</li>
    <li>An alert on the silent path: today an unfulfillable order would be caught by
    rule <code>R5</code> before launch, but nothing would catch it after.</li>
  </ol>
  <p class="note" style="margin-top:14px">Item 5 is the one worth arguing about. A
  pre-launch gate and a production alert are the same question asked at two different
  times, and most projects only ever ask it once.</p>
</div>

<h2>Stakeholder intake</h2>
{_intake_cards(triages)}
<p class="note">A human fills the structured demand from the prose email; the gate
decides. A demand with no contracted SOW line is a change request on the day it
arrives, not a task somebody forgot.</p>

</div></body></html>
"""
