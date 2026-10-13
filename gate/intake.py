"""
Stakeholder intake — a demand arriving by email becomes a classified,
source-checked, acceptance-tested ticket, or it becomes a change request.

The division of labour matters and is deliberate:

  A human (with a model's help) reads the prose email and fills in a small
  structured demand. That is judgement, and judgement does not belong in a
  deterministic rule.

  This module then *decides*, deterministically, and is unit-tested. It answers
  four questions, in this order, and stops at the first that resolves:

    1. Is this already tracked?          -> link it, do not open a second ticket
    2. Does the claim survive the source? -> if not, reply with the evidence
    3. Which SOW line does it fall under? -> none means change request, not task
    4. What is the acceptance criterion?  -> prose becomes something testable

Question 3 is the one that earns the role. A demand with no SOW line is not a
task somebody forgot to do; it is a change in scope, and saying so in writing on
the day it arrives is the difference between a project that lands and a project
that quietly overruns.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .rules import Finding, Right

ROOT = Path(__file__).resolve().parent.parent
SOW_CSV = ROOT / "data" / "sow.csv"

TRACKED = "already_tracked"
IN_SCOPE = "validated_in_scope"
CHANGE_REQUEST = "change_request"
NOT_REPRODUCIBLE = "not_reproducible"


@dataclass
class SowLine:
    line: str
    tag: str
    scope: str
    hours_approved: int
    hours_used: int
    owner: str
    notes: str

    @property
    def hours_left(self) -> int:
        return self.hours_approved - self.hours_used

    @property
    def contracted(self) -> bool:
        return self.hours_approved > 0


@dataclass
class Demand:
    """What a human extracted from the email. Prose in, structure out."""

    id: str
    sender: str
    subject: str
    ask: str
    sow_tag: str = ""
    claim: dict = field(default_factory=dict)
    needed_by: str = ""

    @classmethod
    def from_json(cls, path: Path | str) -> "Demand":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown field(s) in the demand: {', '.join(sorted(unknown))}")
        missing = {"id", "sender", "subject", "ask"} - set(raw)
        if missing:
            raise ValueError(f"demand is missing {', '.join(sorted(missing))}")
        return cls(**raw)


@dataclass
class Triage:
    demand: Demand
    verdict: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    sow: SowLine | None = None
    existing: str = ""          # Linear identifier, when already tracked

    @property
    def opens_a_ticket(self) -> bool:
        return self.verdict in (IN_SCOPE, CHANGE_REQUEST)


def load_sow(path: Path | str = SOW_CSV) -> list[SowLine]:
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.append(
                SowLine(
                    line=row["line"].strip(),
                    tag=row["tag"].strip(),
                    scope=row["scope"].strip(),
                    hours_approved=int(row["hours_approved"]),
                    hours_used=int(row["hours_used"]),
                    owner=row["owner"].strip(),
                    notes=row["notes"].strip(),
                )
            )
    return out


# --------------------------------------------------------------------- claims

def _check_claim(claim: dict, rights: list[Right], findings: list[Finding],
                 today: date) -> tuple[bool | None, list[str]]:
    """Verify the demand's factual claim against the source, not the email.

    Returns (True, evidence) when the source agrees, (False, evidence) when it
    contradicts, and (None, []) when there is nothing checkable — an unverifiable
    demand is not a false one.
    """
    kind = claim.get("type", "")
    if not kind:
        return None, []

    isbn = claim.get("isbn", "")
    territory = (claim.get("territory") or "").upper()
    held = [r for r in rights if r.isbn == isbn]

    if kind == "rights_expired":
        for r in held:
            if r.territory != territory:
                continue
            if r.window_end is not None and r.window_end < today:
                return True, [
                    f"rights.csv: {isbn} / {territory} window ended {r.window_end}, "
                    f"before {today}."
                ]
            return False, [
                f"rights.csv: {isbn} / {territory} runs {r.window_start}"
                f"–{r.window_end or 'open'}, which still covers {today}."
            ]
        return False, [f"rights.csv has no {territory} licence on record for {isbn}."]

    if kind == "title_unlisted":
        hits = [f for f in findings if f.isbn == isbn and f.rule == "R6_RIGHTS_NOT_LISTED"]
        if hits:
            return True, [f"gate: {hits[0].rule} is open for {isbn} — {hits[0].detail}"]
        return False, [f"gate: no R6_RIGHTS_NOT_LISTED finding for {isbn} at this run."]

    if kind == "price_wrong":
        hits = [f for f in findings if f.isbn == isbn and f.rule == "R3_PRICE_BELOW_FLOOR"]
        if hits:
            return True, [f"gate: {hits[0].detail}"]
        floors = sorted({r.price_floor_usd for r in held})
        return False, [
            f"gate: no pricing finding for {isbn}. "
            f"Floor(s) on record: {', '.join(f'${f:.2f}' for f in floors) or 'none'}."
        ]

    return None, [f"No source check implemented for claim type {kind!r}."]


# ---------------------------------------------------------------- acceptance

_ACCEPTANCE = {
    "rights_expired": [
        "The listing is not buyable in the named territory.",
        "A direct cart URL for that variant is also refused, not just the UI.",
        "`gate.cli check` reports no R1 finding for this ISBN and territory.",
    ],
    "title_unlisted": [
        "A published listing exists for the ISBN in every in-window territory.",
        "`gate.cli check` reports no R6 finding for this ISBN.",
    ],
    "price_wrong": [
        "The listing price is at or above the contractual floor in every territory it sells in.",
        "`gate.cli check` reports no R3 finding for this ISBN.",
    ],
}

_GENERIC_ACCEPTANCE = [
    "The requested behaviour is demonstrated in a shared environment, not described.",
    "`gate.cli readiness` does not regress: no check moves from pass to fail.",
]


def triage(
    demand: Demand,
    rights: list[Right],
    findings: list[Finding],
    today: date,
    sow: list[SowLine] | None = None,
    open_issue_keys: dict[str, str] | None = None,
) -> Triage:
    sow = sow if sow is not None else load_sow()
    open_issue_keys = open_issue_keys or {}

    # 1. Already tracked? Opening a second ticket for a known problem is how a
    #    board loses the room's trust.
    isbn = demand.claim.get("isbn", "")
    if isbn:
        for f in findings:
            if f.isbn != isbn:
                continue
            if f.key in open_issue_keys:
                return Triage(
                    demand=demand,
                    verdict=TRACKED,
                    reason=(
                        f"Already open as {open_issue_keys[f.key]} ({f.rule}). "
                        f"Linked rather than duplicated."
                    ),
                    evidence=[f.detail],
                    existing=open_issue_keys[f.key],
                )

    # 2. Does the claim survive contact with the source?
    ok, evidence = _check_claim(demand.claim, rights, findings, today)
    if ok is False:
        return Triage(
            demand=demand,
            verdict=NOT_REPRODUCIBLE,
            reason="The claim does not match the source of truth. Replying with the evidence.",
            evidence=evidence,
        )

    # 3. Which SOW line? No line means a change request, said on day one.
    line = next((s for s in sow if s.tag == demand.sow_tag), None)
    if line is None or not line.contracted:
        why = (
            f"No SOW line carries the tag {demand.sow_tag!r}."
            if line is None
            else f"{line.line} ({line.tag}) exists but has no approved hours."
        )
        return Triage(
            demand=demand,
            verdict=CHANGE_REQUEST,
            reason=(
                f"{why} This is a change in scope, not an outstanding task. "
                f"It needs a change order before work starts."
            ),
            evidence=evidence,
            acceptance=_ACCEPTANCE.get(demand.claim.get("type", ""), _GENERIC_ACCEPTANCE),
            sow=line,
        )

    # 4. In scope. Acceptance criteria make it testable, which makes it finishable.
    note = ""
    if line.hours_left <= 0:
        note = f" {line.line} is out of hours ({line.hours_used}/{line.hours_approved}) — flag before starting."
    elif line.hours_left <= 10:
        note = f" {line.line} has {line.hours_left}h left of {line.hours_approved}h."

    return Triage(
        demand=demand,
        verdict=IN_SCOPE,
        reason=f"Falls under {line.line} — {line.scope}.{note}",
        evidence=evidence,
        acceptance=_ACCEPTANCE.get(demand.claim.get("type", ""), _GENERIC_ACCEPTANCE),
        sow=line,
    )


# -------------------------------------------------------------------- output

_HEAD = {
    TRACKED: "ALREADY TRACKED",
    IN_SCOPE: "VALIDATED — IN SCOPE",
    CHANGE_REQUEST: "CHANGE REQUEST",
    NOT_REPRODUCIBLE: "NOT REPRODUCIBLE",
}


def to_terminal(t: Triage) -> str:
    d = t.demand
    lines = [
        "",
        f"  Intake {d.id} — {_HEAD[t.verdict]}",
        f"  from {d.sender}  ·  {d.subject}",
        "",
        f"  Ask:    {d.ask}",
        f"  Needed: {d.needed_by or 'not stated'}",
        "",
        f"  Verdict: {t.reason}",
    ]
    if t.evidence:
        lines += ["", "  Source check:"]
        lines += [f"    - {e}" for e in t.evidence]
    if t.sow:
        lines += [
            "",
            f"  SOW:    {t.sow.line} / {t.sow.tag} · {t.sow.hours_used}/"
            f"{t.sow.hours_approved}h used · owner {t.sow.owner}",
            f"          {t.sow.notes}",
        ]
    if t.acceptance:
        lines += ["", "  Acceptance criteria:"]
        lines += [f"    {i}. {a}" for i, a in enumerate(t.acceptance, 1)]
    lines.append("")
    return "\n".join(lines)


def issue_title(t: Triage) -> str:
    prefix = "Change request" if t.verdict == CHANGE_REQUEST else "Intake"
    return f"{prefix}: {t.demand.ask}"[:250]


def issue_body(t: Triage) -> str:
    d = t.demand
    parts = [
        f"**Raised by** {d.sender} · **{d.subject}** · intake `{d.id}`",
        f"**Needed by** {d.needed_by or 'not stated'}",
        "",
        f"> {d.ask}",
        "",
        f"**Triage:** {_HEAD[t.verdict]} — {t.reason}",
    ]
    if t.evidence:
        parts += ["", "**Source check**", *[f"- {e}" for e in t.evidence]]
    if t.sow:
        parts += [
            "",
            f"**SOW** `{t.sow.line}` / `{t.sow.tag}` — {t.sow.hours_used}/"
            f"{t.sow.hours_approved}h used, owner `{t.sow.owner}`",
            f"_{t.sow.notes}_",
        ]
    if t.acceptance:
        parts += ["", "**Acceptance criteria**", *[f"{i}. {a}" for i, a in enumerate(t.acceptance, 1)]]
    if t.verdict == CHANGE_REQUEST:
        parts += [
            "",
            "**Blocked pending a change order.** No work starts on this line until "
            "hours are approved.",
        ]
    parts += ["", "---", "Opened from stakeholder intake. The claim was checked against the "
              "source before this ticket existed."]
    return "\n".join(parts)
