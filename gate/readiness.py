"""
Launch readiness as data, not as a document.

The job description names the areas it wants covered — vendors, infrastructure,
security, load testing, observability, checkout performance. A readiness
document gets written once and rots. A readiness *table* can be evaluated, can
block, and can tell you who is holding the launch.

Two statuses matter beyond pass/fail:

  unknown   Nobody has answered yet. Treated as not-ready on purpose: an
            unanswered check is the most common way a launch slips, and
            counting it as a pass is how teams lie to themselves.

  gate      Not a human judgement — evaluated by the catalogue gate at run
            time. That is how the two halves connect: catalogue integrity is
            one input to launch readiness, not a separate report.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .rules import BLOCKING, Finding

ROOT = Path(__file__).resolve().parent.parent
READINESS_CSV = ROOT / "data" / "readiness.csv"
LATENCY_JSON = ROOT / "data" / "latency.json"

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"
GATE = "gate"

# Printed state, after `gate` rows are resolved.
_LABEL = {PASS: "PASS", FAIL: "FAIL", UNKNOWN: "UNANSWERED"}


@dataclass
class Check:
    id: str
    area: str
    check: str
    owner: str
    blocking: bool
    status: str
    evidence: str

    @property
    def ready(self) -> bool:
        return self.status == PASS

    @property
    def holds_launch(self) -> bool:
        return self.blocking and not self.ready


def load_checks(path: Path | str = READINESS_CSV) -> list[Check]:
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            status = row["status"].strip().lower() or UNKNOWN
            if status not in (PASS, FAIL, UNKNOWN, GATE):
                raise ValueError(
                    f"{path}:{i} has status {status!r}; expected pass, fail, unknown or gate"
                )
            out.append(
                Check(
                    id=row["id"].strip(),
                    area=row["area"].strip(),
                    check=row["check"].strip(),
                    owner=row["owner"].strip(),
                    blocking=row["blocking"].strip().lower() in ("yes", "true", "1"),
                    status=status,
                    evidence=row["evidence"].strip(),
                )
            )
    return out


def _latency() -> dict | None:
    """The measured baseline, if one has been taken. Absent is not a pass."""
    import json

    try:
        return json.loads(LATENCY_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resolve(checks: list[Check], findings: list[Finding]) -> list[Check]:
    """Turn `gate` rows into pass/fail from measurement, never from a typed status."""
    blocking = [f for f in findings if f.severity == BLOCKING]
    r5 = [f for f in blocking if f.rule == "R5_MISSING_AUDIO_ASSET"]
    others = [f for f in blocking if f.rule != "R5_MISSING_AUDIO_ASSET"]

    lat = _latency()

    out = []
    for c in checks:
        if c.status != GATE:
            out.append(c)
            continue

        if c.id == "PERF-1":
            if lat is None:
                out.append(Check(**{**c.__dict__, "status": UNKNOWN,
                                    "evidence": "No baseline taken yet; run `python3 -m gate.latency`."}))
            else:
                ok = lat.get("meets_target")
                out.append(Check(**{**c.__dict__,
                                    "status": PASS if ok else FAIL,
                                    "evidence": (
                                        f"Measured {lat.get('measured_at','')}: worst p95 "
                                        f"{lat.get('worst_p95_ms')} ms against a "
                                        f"{lat.get('target_p95_ms')} ms target, at rest. "
                                        f"{lat.get('method','')}")}))
            continue

        if c.id == "FUL-1":
            relevant = r5
        else:
            relevant = others if c.area == "catalogue" else blocking
        out.append(
            Check(
                **{
                    **c.__dict__,
                    "status": FAIL if relevant else PASS,
                    "evidence": (
                        f"{len(relevant)} blocking finding(s) open: "
                        + ", ".join(sorted({f.rule for f in relevant}))
                        if relevant
                        else "No blocking findings at this run."
                    ),
                }
            )
        )
    return out


def verdict(checks: list[Check]) -> dict:
    holding = [c for c in checks if c.holds_launch]
    by_owner: dict[str, int] = {}
    for c in holding:
        by_owner[c.owner] = by_owner.get(c.owner, 0) + 1
    return {
        "go": not holding,
        "checks": len(checks),
        "ready": sum(1 for c in checks if c.ready),
        "holding": len(holding),
        "unanswered": sum(1 for c in checks if c.status == UNKNOWN),
        "holding_by_owner": dict(sorted(by_owner.items(), key=lambda kv: -kv[1])),
    }


def to_terminal(checks: list[Check]) -> str:
    v = verdict(checks)
    head = "GO" if v["go"] else "NO-GO"
    lines = [
        "",
        f"  Launch readiness — {head}",
        f"  {v['ready']}/{v['checks']} ready · {v['holding']} blocking checks not met · "
        f"{v['unanswered']} unanswered",
        "",
    ]
    area = None
    for c in sorted(checks, key=lambda c: (c.area, c.id)):
        if c.area != area:
            area = c.area
            lines.append(f"  {area.upper()}")
        mark = "ok " if c.ready else ("HOLD" if c.blocking else "warn")
        lines.append(f"    [{mark:>4}] {c.id}  {c.check}")
        lines.append(f"           {_LABEL.get(c.status, c.status)} · owner {c.owner}")
        if c.evidence:
            lines.append(f"           {c.evidence}")
        lines.append("")

    if not v["go"]:
        lines.append("  Holding the launch, by owner:")
        for owner, n in v["holding_by_owner"].items():
            lines.append(f"    {n:>2}  {owner}")
        lines.append("")
    return "\n".join(lines)


def to_markdown(checks: list[Check]) -> str:
    v = verdict(checks)
    out = [
        f"# Launch readiness — {'GO' if v['go'] else 'NO-GO'}",
        "",
        f"**{v['ready']}/{v['checks']} ready · {v['holding']} blocking checks not met · "
        f"{v['unanswered']} unanswered**",
        "",
        "| | ID | Area | Check | Status | Owner | Evidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in sorted(checks, key=lambda c: (not c.holds_launch, c.area, c.id)):
        mark = "✅" if c.ready else ("⛔" if c.blocking else "⚠️")
        out.append(
            f"| {mark} | `{c.id}` | {c.area} | {c.check} | {_LABEL.get(c.status, c.status)} "
            f"| `{c.owner}` | {c.evidence or '—'} |"
        )
    if not v["go"]:
        out += ["", "## Holding the launch", ""]
        for owner, n in v["holding_by_owner"].items():
            out.append(f"- **{owner}** — {n} check(s)")
    out.append("")
    return "\n".join(out)
