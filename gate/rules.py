"""
Catalogue integrity rules for a digital-goods storefront with territorial rights.

Each rule is a pure function: (catalogue, rights, today) -> list[Finding].
No network, no Shopify, no Linear. That is deliberate — the rules are the part
that has to be right, so they are the part that is unit-tested.

Domain note: audiobook rights are licensed per territory and per window. A title
that is buyable in a territory where the licence does not cover it is not a UX
bug, it is a breach of the agreement with the rights holder. That is why R1 and
R2 are blocking and the pricing rules are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Iterable

# Severity drives both the report ordering and the Linear priority.
BLOCKING = "blocking"   # do not launch / take it down now
HIGH = "high"
MEDIUM = "medium"

_PRIORITY = {BLOCKING: 1, HIGH: 2, MEDIUM: 3}


@dataclass(frozen=True)
class Product:
    """A storefront listing, as read from the Shopify Admin API."""

    handle: str
    title: str
    isbn: str
    imprint: str
    territories: tuple[str, ...]      # territories the listing is sellable in
    price_usd: float
    published: bool
    audio_asset: str                  # empty string == no deliverable file
    shopify_gid: str = ""


@dataclass(frozen=True)
class Right:
    """One licensed (title, territory) window, as held by the rights system."""

    isbn: str
    title: str
    imprint: str
    territory: str
    window_start: date
    window_end: date | None           # None == open-ended
    price_floor_usd: float
    est_monthly_units: int

    def covers(self, on: date) -> bool:
        if on < self.window_start:
            return False
        if self.window_end is not None and on > self.window_end:
            return False
        return True


@dataclass
class Finding:
    rule: str
    severity: str
    isbn: str
    title: str
    subject: str                      # product handle, or the ISBN for R6
    detail: str
    revenue_at_risk_usd: float = 0.0
    owner: str = ""                   # which vendor / team the SOW puts this on
    evidence: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identity, so re-running does not create duplicate issues."""
        return f"{self.rule}:{self.isbn}:{self.subject}"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


# Which vendor owns which failure class. In a real engagement this comes out of
# the SOW; here it is a constant so the Linear issues land on someone.
OWNER = {
    "R1_TERRITORY_WITHOUT_RIGHTS": "rights-ops",
    "R2_LIVE_BEFORE_WINDOW": "rights-ops",
    "R3_PRICE_BELOW_FLOOR": "pricing-vendor",
    "R4_DUPLICATE_ISBN": "catalogue-vendor",
    "R5_MISSING_AUDIO_ASSET": "fulfilment-vendor",
    "R6_RIGHTS_NOT_LISTED": "catalogue-vendor",
}


def _by_isbn(rights: Iterable[Right]) -> dict[str, list[Right]]:
    out: dict[str, list[Right]] = {}
    for r in rights:
        out.setdefault(r.isbn, []).append(r)
    return out


def _units(rights_for_isbn: list[Right], territory: str | None = None) -> int:
    if territory is not None:
        return sum(r.est_monthly_units for r in rights_for_isbn if r.territory == territory)
    return sum(r.est_monthly_units for r in rights_for_isbn)


# --------------------------------------------------------------------------- R1

def r1_territory_without_rights(catalogue, rights, today) -> list[Finding]:
    """Listing is sellable in a territory with no licence covering today."""
    idx = _by_isbn(rights)
    findings = []
    for p in catalogue:
        if not p.published:
            continue
        licensed = {r.territory for r in idx.get(p.isbn, []) if r.covers(today)}
        for t in p.territories:
            if t in licensed:
                continue
            held = [r for r in idx.get(p.isbn, []) if r.territory == t]
            # A licence that simply has not started yet is R2's finding, not R1's.
            # Firing both would open two tickets for one cause, and the ops team
            # would work the list twice.
            if held and all(today < r.window_start for r in held):
                continue
            if held:
                why = (
                    f"licence for {t} runs {held[0].window_start}"
                    f"–{held[0].window_end or 'open'}, which expired before {today}"
                )
            else:
                why = f"no licence on record for {t}"
            findings.append(
                Finding(
                    rule="R1_TERRITORY_WITHOUT_RIGHTS",
                    severity=BLOCKING,
                    isbn=p.isbn,
                    title=p.title,
                    subject=p.handle,
                    detail=f"On sale in {t} but {why}.",
                    # Exposure: a month of sales we would have to unwind.
                    revenue_at_risk_usd=round(p.price_usd * max(_units(idx.get(p.isbn, []), t), 1), 2),
                    owner=OWNER["R1_TERRITORY_WITHOUT_RIGHTS"],
                    evidence={"territory": t, "price_usd": p.price_usd},
                )
            )
    return findings


# --------------------------------------------------------------------------- R2

def r2_live_before_window(catalogue, rights, today) -> list[Finding]:
    """Listing is published before the earliest licence window opens."""
    idx = _by_isbn(rights)
    findings = []
    for p in catalogue:
        if not p.published:
            continue
        held = idx.get(p.isbn, [])
        if not held:
            continue  # R1 already covers "no licence at all"
        earliest = min(r.window_start for r in held)
        if today >= earliest:
            continue
        findings.append(
            Finding(
                rule="R2_LIVE_BEFORE_WINDOW",
                severity=BLOCKING,
                isbn=p.isbn,
                title=p.title,
                subject=p.handle,
                detail=(
                    f"Published and buyable, but the first licence window opens "
                    f"{earliest} ({(earliest - today).days} days from now)."
                ),
                revenue_at_risk_usd=round(p.price_usd * max(_units(held), 1), 2),
                owner=OWNER["R2_LIVE_BEFORE_WINDOW"],
                evidence={"window_start": str(earliest), "today": str(today)},
            )
        )
    return findings


# --------------------------------------------------------------------------- R3

def r3_price_below_floor(catalogue, rights, today) -> list[Finding]:
    """Listing price undercuts the contractual floor for a territory it sells in."""
    idx = _by_isbn(rights)
    findings = []
    for p in catalogue:
        if not p.published:
            continue
        for r in idx.get(p.isbn, []):
            if r.territory not in p.territories or not r.covers(today):
                continue
            if p.price_usd >= r.price_floor_usd:
                continue
            gap = r.price_floor_usd - p.price_usd
            findings.append(
                Finding(
                    rule="R3_PRICE_BELOW_FLOOR",
                    severity=HIGH,
                    isbn=p.isbn,
                    title=p.title,
                    subject=p.handle,
                    detail=(
                        f"Priced at ${p.price_usd:.2f} in {r.territory}; "
                        f"contractual floor is ${r.price_floor_usd:.2f}."
                    ),
                    # Margin given away, not gross revenue.
                    revenue_at_risk_usd=round(gap * max(r.est_monthly_units, 1), 2),
                    owner=OWNER["R3_PRICE_BELOW_FLOOR"],
                    evidence={
                        "territory": r.territory,
                        "price_usd": p.price_usd,
                        "floor_usd": r.price_floor_usd,
                    },
                )
            )
    return findings


# --------------------------------------------------------------------------- R4

def r4_duplicate_isbn(catalogue, rights, today) -> list[Finding]:
    """Same ISBN on more than one listing — breaks retailer feeds and reporting."""
    seen: dict[str, list[Product]] = {}
    for p in catalogue:
        seen.setdefault(p.isbn, []).append(p)
    findings = []
    for isbn, products in seen.items():
        if len(products) < 2:
            continue
        handles = sorted(p.handle for p in products)
        imprints = sorted({p.imprint for p in products})
        findings.append(
            Finding(
                rule="R4_DUPLICATE_ISBN",
                severity=HIGH if len(imprints) > 1 else MEDIUM,
                isbn=isbn,
                title=products[0].title,
                subject=handles[0],
                detail=(
                    f"ISBN appears on {len(products)} listings "
                    f"({', '.join(handles)}) across imprints {', '.join(imprints)}."
                ),
                owner=OWNER["R4_DUPLICATE_ISBN"],
                evidence={"handles": handles, "imprints": imprints},
            )
        )
    return findings


# --------------------------------------------------------------------------- R5

def r5_missing_audio_asset(catalogue, rights, today) -> list[Finding]:
    """Buyable listing with no deliverable file — the order cannot be fulfilled."""
    idx = _by_isbn(rights)
    findings = []
    for p in catalogue:
        if not p.published or p.audio_asset.strip():
            continue
        findings.append(
            Finding(
                rule="R5_MISSING_AUDIO_ASSET",
                severity=BLOCKING,
                isbn=p.isbn,
                title=p.title,
                subject=p.handle,
                detail="Published and buyable with no audio asset attached; the order cannot be fulfilled.",
                revenue_at_risk_usd=round(p.price_usd * max(_units(idx.get(p.isbn, [])), 1), 2),
                owner=OWNER["R5_MISSING_AUDIO_ASSET"],
                evidence={"price_usd": p.price_usd},
            )
        )
    return findings


# --------------------------------------------------------------------------- R6

def r6_rights_not_listed(catalogue, rights, today) -> list[Finding]:
    """Licence is open but nothing is on sale — revenue sitting on the floor."""
    listed = {p.isbn for p in catalogue if p.published}
    idx = _by_isbn(rights)
    findings = []
    for isbn, held in idx.items():
        if isbn in listed:
            continue
        open_now = [r for r in held if r.covers(today)]
        if not open_now:
            continue
        findings.append(
            Finding(
                rule="R6_RIGHTS_NOT_LISTED",
                severity=MEDIUM,
                isbn=isbn,
                title=open_now[0].title,
                subject=isbn,
                detail=(
                    f"Licensed and in window for {', '.join(sorted(r.territory for r in open_now))}, "
                    f"but no published listing exists."
                ),
                revenue_at_risk_usd=round(
                    sum(r.price_floor_usd * r.est_monthly_units for r in open_now), 2
                ),
                owner=OWNER["R6_RIGHTS_NOT_LISTED"],
                evidence={"territories": sorted(r.territory for r in open_now)},
            )
        )
    return findings


ALL_RULES = (
    r1_territory_without_rights,
    r2_live_before_window,
    r3_price_below_floor,
    r4_duplicate_isbn,
    r5_missing_audio_asset,
    r6_rights_not_listed,
)


def run_all(catalogue: list[Product], rights: list[Right], today: date) -> list[Finding]:
    """Every rule, ranked the way an ops team should work the list:
    blocking first, then by money."""
    findings: list[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(catalogue, rights, today))
    findings.sort(key=lambda f: (_PRIORITY[f.severity], -f.revenue_at_risk_usd, f.rule))
    return findings
