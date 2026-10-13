"""
Readiness tests. The interesting ones are about `unknown` and about how the
catalogue gate feeds the board — those are design decisions, not mechanics.
"""

from datetime import date

import pytest

from gate.readiness import (
    FAIL,
    GATE,
    PASS,
    UNKNOWN,
    Check,
    load_checks,
    resolve,
    to_markdown,
    verdict,
)
from gate.rules import BLOCKING, HIGH, Finding

TODAY = date(2026, 10, 13)


def check(**kw):
    base = dict(
        id="INF-1",
        area="infrastructure",
        check="CDN in front of the storefront",
        owner="platform-vendor",
        blocking=True,
        status=PASS,
        evidence="",
    )
    base.update(kw)
    return Check(**base)


def finding(**kw):
    base = dict(
        rule="R1_TERRITORY_WITHOUT_RIGHTS",
        severity=BLOCKING,
        isbn="9780000000101",
        title="The Long Quiet",
        subject="the-long-quiet",
        detail="On sale in JP but no licence on record for JP.",
    )
    base.update(kw)
    return Finding(**base)


def test_unanswered_is_not_ready():
    # The decision this pins: an unanswered blocking check holds the launch.
    # Counting it as a pass is the most common way a launch date slips.
    c = check(status=UNKNOWN)
    assert not c.ready
    assert c.holds_launch
    assert verdict([c])["go"] is False


def test_a_non_blocking_failure_does_not_hold_the_launch():
    c = check(status=FAIL, blocking=False)
    assert not c.ready
    assert not c.holds_launch
    assert verdict([c])["go"] is True


def test_gate_rows_fail_when_the_catalogue_has_blocking_findings():
    board = resolve([check(id="CAT-1", area="catalogue", status=GATE)], [finding()])
    assert board[0].status == FAIL
    assert "R1_TERRITORY_WITHOUT_RIGHTS" in board[0].evidence


def test_gate_rows_pass_on_a_clean_catalogue():
    board = resolve([check(id="CAT-1", area="catalogue", status=GATE)], [])
    assert board[0].status == PASS
    assert "No blocking findings" in board[0].evidence


def test_high_severity_findings_do_not_hold_the_launch():
    # Only blocking findings gate. Pricing drift is money, not a breach, and a
    # board that halts on everything gets ignored.
    board = resolve(
        [check(id="CAT-1", area="catalogue", status=GATE)],
        [finding(severity=HIGH, rule="R3_PRICE_BELOW_FLOOR")],
    )
    assert board[0].status == PASS


def test_fulfilment_row_only_watches_the_deliverable_rule():
    # FUL-1 is about deliverable files, so a territory breach must not land on
    # the fulfilment vendor's name.
    rows = [check(id="FUL-1", area="fulfilment", status=GATE, owner="fulfilment-vendor")]
    assert resolve(rows, [finding()])[0].status == PASS
    assert resolve(rows, [finding(rule="R5_MISSING_AUDIO_ASSET")])[0].status == FAIL


def test_verdict_counts_who_is_holding_the_launch():
    v = verdict(
        [
            check(id="A", owner="platform-vendor", status=FAIL),
            check(id="B", owner="platform-vendor", status=UNKNOWN),
            check(id="C", owner="rights-ops", status=FAIL),
            check(id="D", owner="rights-ops", status=PASS),
        ]
    )
    assert v["go"] is False
    assert v["holding"] == 3
    assert v["holding_by_owner"] == {"platform-vendor": 2, "rights-ops": 1}


def test_the_shipped_board_parses_and_is_not_accidentally_green():
    checks = load_checks()
    assert len(checks) >= 15
    assert {"infrastructure", "security", "load", "observability", "checkout"} <= {
        c.area for c in checks
    }
    # The fixture is meant to look like a real mid-flight launch, not a demo
    # where everything is already fine.
    assert verdict(resolve(checks, [finding()]))["go"] is False


def test_markdown_puts_the_blockers_first():
    rows = [check(id="OK-1", status=PASS), check(id="BAD-1", status=FAIL)]
    md = to_markdown(rows)
    assert md.index("BAD-1") < md.index("OK-1")


def test_bad_status_is_rejected_loudly(tmp_path):
    p = tmp_path / "readiness.csv"
    p.write_text(
        "id,area,check,owner,blocking,status,evidence\n"
        "X-1,infrastructure,something,platform-vendor,yes,maybe,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected pass, fail, unknown or gate"):
        load_checks(p)
