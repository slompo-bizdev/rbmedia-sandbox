"""
The rules are the part that has to be right, so they are tested without a
network. Every test states the failure it is pinning down.

Run: python -m pytest -q
"""

from datetime import date, timedelta

import pytest

from gate.rules import (
    BLOCKING,
    HIGH,
    MEDIUM,
    Product,
    Right,
    r1_territory_without_rights,
    r2_live_before_window,
    r3_price_below_floor,
    r4_duplicate_isbn,
    r5_missing_audio_asset,
    r6_rights_not_listed,
    run_all,
)

TODAY = date(2026, 10, 13)


def product(**kw):
    base = dict(
        handle="the-long-quiet",
        title="The Long Quiet",
        isbn="9780000000001",
        imprint="Northwind Audio",
        territories=("US", "CA"),
        price_usd=24.99,
        published=True,
        audio_asset="asset_001",
    )
    base.update(kw)
    return Product(**base)


def right(**kw):
    base = dict(
        isbn="9780000000001",
        title="The Long Quiet",
        imprint="Northwind Audio",
        territory="US",
        window_start=date(2026, 1, 1),
        window_end=None,
        price_floor_usd=19.99,
        est_monthly_units=100,
    )
    base.update(kw)
    return Right(**base)


# --------------------------------------------------------------------------- R1

def test_r1_flags_territory_with_no_licence_on_record():
    # Sellable in US and CA; only US is licensed.
    findings = r1_territory_without_rights([product()], [right(territory="US")], TODAY)
    assert [f.evidence["territory"] for f in findings] == ["CA"]
    assert findings[0].severity == BLOCKING
    assert "no licence on record for CA" in findings[0].detail


def test_r1_flags_licence_that_has_expired():
    expired = right(territory="CA", window_start=date(2025, 1, 1), window_end=date(2026, 1, 1))
    findings = r1_territory_without_rights(
        [product()], [right(territory="US"), expired], TODAY
    )
    assert len(findings) == 1
    assert "expired before 2026-10-13" in findings[0].detail


def test_r1_stays_quiet_when_the_licence_has_simply_not_started():
    # Regression: this used to open an R1 per territory *and* an R2, so one
    # unopened window produced four tickets. R2 owns this cause.
    not_yet = [
        right(territory="US", window_start=TODAY + timedelta(days=21)),
        right(territory="CA", window_start=TODAY + timedelta(days=21)),
    ]
    assert r1_territory_without_rights([product()], not_yet, TODAY) == []
    assert len(r2_live_before_window([product()], not_yet, TODAY)) == 1


def test_r1_ignores_unpublished_listings():
    # A draft cannot breach anything. Noise here is what makes ops stop reading.
    assert r1_territory_without_rights([product(published=False)], [], TODAY) == []


def test_r1_revenue_uses_the_offending_territory_only():
    rights = [
        right(territory="US", est_monthly_units=100),
        right(territory="GB", est_monthly_units=7),
    ]
    findings = r1_territory_without_rights(
        [product(territories=("US", "GB", "CA"))], rights, TODAY
    )
    assert len(findings) == 1  # only CA is unlicensed
    # No CA units on record, so it falls back to 1 unit rather than 0.
    assert findings[0].revenue_at_risk_usd == pytest.approx(24.99)


# --------------------------------------------------------------------------- R2

def test_r2_flags_a_listing_live_before_the_window_opens():
    future = right(window_start=TODAY + timedelta(days=30))
    findings = r2_live_before_window([product(territories=("US",))], [future], TODAY)
    assert findings[0].severity == BLOCKING
    assert "30 days from now" in findings[0].detail


def test_r2_silent_once_the_window_is_open():
    assert r2_live_before_window([product()], [right()], TODAY) == []


def test_r2_defers_to_r1_when_there_is_no_licence_at_all():
    # Two rules firing on one cause is a duplicated ticket, so R2 stays quiet.
    assert r2_live_before_window([product()], [], TODAY) == []


# --------------------------------------------------------------------------- R3

def test_r3_flags_price_under_the_contractual_floor():
    findings = r3_price_below_floor(
        [product(price_usd=14.99, territories=("US",))],
        [right(price_floor_usd=19.99, est_monthly_units=200)],
        TODAY,
    )
    assert findings[0].severity == HIGH
    # Margin given away, not gross revenue: (19.99 - 14.99) * 200
    assert findings[0].revenue_at_risk_usd == pytest.approx(1000.0)


def test_r3_only_considers_territories_the_listing_actually_sells_in():
    gb = right(territory="GB", price_floor_usd=39.99)
    assert r3_price_below_floor([product(territories=("US",))], [gb], TODAY) == []


# --------------------------------------------------------------------------- R4

def test_r4_is_high_when_the_duplicate_crosses_imprints():
    a = product(handle="a")
    b = product(handle="b", imprint="Saltgrass Audio")
    findings = r4_duplicate_isbn([a, b], [], TODAY)
    assert findings[0].severity == HIGH
    assert findings[0].evidence["handles"] == ["a", "b"]


def test_r4_is_medium_inside_one_imprint():
    findings = r4_duplicate_isbn([product(handle="a"), product(handle="b")], [], TODAY)
    assert findings[0].severity == MEDIUM


# --------------------------------------------------------------------------- R5

def test_r5_flags_a_buyable_listing_with_no_deliverable_file():
    findings = r5_missing_audio_asset([product(audio_asset="  ")], [right()], TODAY)
    assert findings[0].severity == BLOCKING
    assert "cannot be fulfilled" in findings[0].detail


# --------------------------------------------------------------------------- R6

def test_r6_flags_an_open_licence_with_nothing_on_sale():
    findings = r6_rights_not_listed([], [right(price_floor_usd=19.99, est_monthly_units=50)], TODAY)
    assert findings[0].severity == MEDIUM
    assert findings[0].revenue_at_risk_usd == pytest.approx(999.5)


def test_r6_does_not_fire_for_a_window_that_has_not_opened():
    future = right(window_start=TODAY + timedelta(days=10))
    assert r6_rights_not_listed([], [future], TODAY) == []


# --------------------------------------------------------------------- ordering

def test_run_all_puts_blocking_first_then_the_money():
    catalogue = [
        product(handle="cheap", isbn="9780000000002", price_usd=9.99, territories=("US",)),
        product(handle="unlicensed", isbn="9780000000003", territories=("US", "JP")),
    ]
    rights = [
        right(isbn="9780000000002", price_floor_usd=19.99, est_monthly_units=10),
        right(isbn="9780000000003", territory="US"),
    ]
    findings = run_all(catalogue, rights, TODAY)
    assert findings[0].severity == BLOCKING
    assert findings[0].rule == "R1_TERRITORY_WITHOUT_RIGHTS"
    assert findings[-1].rule == "R3_PRICE_BELOW_FLOOR"


def test_finding_key_is_stable_so_reruns_do_not_duplicate():
    f1 = r5_missing_audio_asset([product(audio_asset="")], [right()], TODAY)[0]
    f2 = r5_missing_audio_asset([product(audio_asset="")], [right()], TODAY)[0]
    assert f1.key == f2.key == "R5_MISSING_AUDIO_ASSET:9780000000001:the-long-quiet"
