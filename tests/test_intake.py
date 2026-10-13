"""
Intake tests. The four verdicts are the contract; the order they are decided in
is the design. These tests pin both.
"""

import json
from datetime import date

import pytest

from gate.intake import (
    CHANGE_REQUEST,
    IN_SCOPE,
    NOT_REPRODUCIBLE,
    TRACKED,
    Demand,
    SowLine,
    issue_body,
    issue_title,
    load_sow,
    triage,
)
from gate.rules import BLOCKING, HIGH, Finding, Right

TODAY = date(2026, 10, 13)

RIGHTS = [
    Right("9780000000102", "Saltmarsh", "Saltgrass Audio", "US",
          date(2025, 6, 16), None, 17.99, 260),
    Right("9780000000102", "Saltmarsh", "Saltgrass Audio", "GB",
          date(2025, 6, 16), date(2026, 6, 15), 17.99, 140),
    Right("9780000000108", "Hollow Season", "Northwind Audio", "US",
          date(2026, 5, 12), None, 23.99, 330),
]

SOW = [
    SowLine("SOW-1", "catalogue-integrity", "Catalogue and rights", 80, 52, "rights-ops", ""),
    SowLine("SOW-3", "entitlement", "Entitlement and delivery", 0, 0, "fulfilment-vendor", ""),
    SowLine("SOW-5", "reporting", "Reports", 20, 19, "rights-ops", ""),
]


def demand(**kw):
    base = dict(
        id="D-1",
        sender="don@example.com",
        subject="Saltmarsh still selling in the UK",
        ask="Take Saltmarsh off sale in the UK.",
        sow_tag="catalogue-integrity",
        claim={"type": "rights_expired", "isbn": "9780000000102", "territory": "GB"},
        needed_by="2026-10-13",
    )
    base.update(kw)
    return Demand(**base)


def finding(**kw):
    base = dict(
        rule="R1_TERRITORY_WITHOUT_RIGHTS",
        severity=BLOCKING,
        isbn="9780000000102",
        title="Saltmarsh",
        subject="saltmarsh",
        detail="On sale in GB but the licence expired.",
    )
    base.update(kw)
    return Finding(**base)


# ------------------------------------------------------------------ verdicts

def test_a_true_claim_in_scope_becomes_a_ticket_with_acceptance_criteria():
    t = triage(demand(), RIGHTS, [], TODAY, sow=SOW)
    assert t.verdict == IN_SCOPE
    assert t.sow.line == "SOW-1"
    assert "window ended 2026-06-15" in t.evidence[0]
    # Acceptance is the point: prose becomes something somebody can sign off.
    assert any("not buyable in the named territory" in a for a in t.acceptance)
    assert any("cart URL" in a for a in t.acceptance)


def test_a_claim_the_source_contradicts_is_not_reproducible():
    # Hollow Season is priced above its floor, so the complaint is wrong — and
    # the reply carries the evidence rather than an opinion.
    t = triage(
        demand(
            subject="Hollow Season priced wrong",
            ask="Hollow Season is under our floor in the US.",
            claim={"type": "price_wrong", "isbn": "9780000000108", "territory": "US"},
        ),
        RIGHTS, [], TODAY, sow=SOW,
    )
    assert t.verdict == NOT_REPRODUCIBLE
    assert "$23.99" in t.evidence[0]
    assert not t.opens_a_ticket


def test_an_uncontracted_line_is_a_change_request_not_a_task():
    # The move that earns the role: say it on the day it arrives.
    t = triage(demand(sow_tag="entitlement", claim={}), RIGHTS, [], TODAY, sow=SOW)
    assert t.verdict == CHANGE_REQUEST
    assert "no approved hours" in t.reason
    assert "change order" in t.reason


def test_an_unknown_tag_is_also_a_change_request():
    t = triage(demand(sow_tag="machine-learning-platform", claim={}), RIGHTS, [], TODAY, sow=SOW)
    assert t.verdict == CHANGE_REQUEST
    assert t.sow is None


def test_a_problem_already_on_the_board_is_linked_not_duplicated():
    f = finding()
    t = triage(demand(), RIGHTS, [f], TODAY, sow=SOW, open_issue_keys={f.key: "RBM-7"})
    assert t.verdict == TRACKED
    assert t.existing == "RBM-7"
    assert not t.opens_a_ticket


def test_tracked_is_decided_before_scope():
    # Even a demand with no SOW line should link to a known problem rather than
    # be escalated as a change request. Order of questions matters.
    f = finding()
    t = triage(
        demand(sow_tag="nonsense"), RIGHTS, [f], TODAY, sow=SOW,
        open_issue_keys={f.key: "RBM-7"},
    )
    assert t.verdict == TRACKED


def test_an_unverifiable_demand_is_not_treated_as_false():
    # No claim to check is not the same as a claim that failed.
    t = triage(demand(claim={}), RIGHTS, [], TODAY, sow=SOW)
    assert t.verdict == IN_SCOPE
    assert t.evidence == []


# --------------------------------------------------------------------- hours

def test_a_nearly_exhausted_sow_line_is_flagged_on_the_ticket():
    t = triage(demand(sow_tag="reporting", claim={}), RIGHTS, [], TODAY, sow=SOW)
    assert t.verdict == IN_SCOPE
    assert "1h left" in t.reason


def test_an_out_of_hours_line_is_flagged_before_work_starts():
    sow = [SowLine("SOW-9", "x", "scope", 10, 10, "someone", "")]
    t = triage(demand(sow_tag="x", claim={}), RIGHTS, [], TODAY, sow=sow)
    assert "out of hours" in t.reason


# -------------------------------------------------------------------- shapes

def test_demand_rejects_an_unknown_field(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({
        "id": "D-9", "sender": "a@b.c", "subject": "s", "ask": "a", "priority": "high",
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown field"):
        Demand.from_json(p)


def test_demand_requires_the_four_core_fields(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"id": "D-9", "sender": "a@b.c"}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing ask, subject"):
        Demand.from_json(p)


def test_change_request_title_and_body_say_so_plainly():
    t = triage(demand(sow_tag="entitlement", claim={}), RIGHTS, [], TODAY, sow=SOW)
    assert issue_title(t).startswith("Change request:")
    body = issue_body(t)
    assert "Blocked pending a change order" in body
    assert "No work starts on this line" in body


def test_the_shipped_sow_parses():
    sow = load_sow()
    tags = {s.tag for s in sow}
    assert "catalogue-integrity" in tags
    # At least one line is deliberately uncontracted, so the change-request
    # path is reachable with the shipped data.
    assert any(not s.contracted for s in sow)
