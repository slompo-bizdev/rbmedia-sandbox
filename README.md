# Catalogue integrity gate

A launch gate for a **digital-goods storefront with territorial rights**: it reads the
store, reconciles it against the rights table, and opens one owned ticket per problem.

Built as a sandbox in a morning, against **synthetic data and an invented imprint**.
It is not anyone's catalogue and carries no third-party branding. The claim here is
*this is how I would instrument the launch* — not *this is your system*.

## Why this and not a storefront

Selling audiobooks direct is not a storefront problem. Audio rights are licensed per
territory and per window, so a listing that is buyable where the licence does not reach
is not a UX bug — it is a breach with the rights holder. At catalogue scale (tens of
thousands of titles across a dozen imprints) nobody catches that by hand, and a
retailer feed tolerates gaps that a storefront does not.

So the interesting object is the gate, not the shop.

## The six rules

| Rule | What it catches | Severity |
|---|---|---|
| `R1_TERRITORY_WITHOUT_RIGHTS` | Buyable in a territory with no licence, or an expired one | blocking |
| `R2_LIVE_BEFORE_WINDOW` | Published before the first licence window opens | blocking |
| `R3_PRICE_BELOW_FLOOR` | Priced under the contractual floor for a territory it sells in | high |
| `R4_DUPLICATE_ISBN` | One ISBN on several listings — breaks retailer feeds and reporting | high / medium |
| `R5_MISSING_AUDIO_ASSET` | Buyable with no deliverable file; the order cannot be fulfilled | blocking |
| `R6_RIGHTS_NOT_LISTED` | Licence open, nothing on sale — revenue on the floor | medium |

Findings are ranked blocking first, then by money. Revenue at risk is arithmetic over
`est_monthly_units`, not a guess: exposure for the compliance rules, margin given away
for `R3`, unrealised revenue for `R6`.

## Design decision worth defending

**The rules are pure functions over plain dataclasses and are unit-tested with no
network. The Shopify and Linear adapters are thin and fail loudly.**

That split is the whole architecture. Everything that can be *wrong* — what counts as a
breach, which rule owns which cause, how risk is priced — is in `gate/rules.py` and is
pinned by tests. Everything that can be *broken* — tokens, rate limits, pagination — is
in the adapters, where a bad failure is obvious instead of silent.

One example of why the tests matter: an early version fired `R1` once per territory
*and* `R2` on the same title when a window had not opened yet, so a single unopened
window produced four tickets. There is now a regression test for it
(`test_r1_stays_quiet_when_the_licence_has_simply_not_started`).

## Linear, and why it is not optional

A report nobody owns is a report nobody works. `gate/linear.py` turns each finding into
an issue: labelled by rule, labelled by the vendor the SOW puts it on, priority derived
from severity, and **idempotent** — the finding's stable key sits in the issue title, so
running the gate every morning updates the board instead of flooding it.

That is vendor accountability as code rather than as a status meeting.

## Run it

Offline, no credentials, no network:

```bash
python -m pytest -q                  # 17 tests
python -m gate.cli check             # reads data/catalogue.csv
python -m gate.cli check --today 2026-11-04   # watch R2 and R6 change
python -m gate.cli check --format markdown > report.md
```

Against the live store and board:

```bash
cp .env.example .env     # then fill it in
set -a; source .env; set +a

python -m gate.cli check --source shopify
python -m gate.cli sync  --source shopify --team RBM
```

`check` exits **1** when anything blocking is open, so it belongs in CI as a gate, not
in an inbox as a report.

## Shopify shape it expects

Metafields in the `custom` namespace: `isbn`, `imprint`, `territories`
(comma separated, e.g. `US,CA,GB`), `audio_asset` (empty means no deliverable file).
A listing counts as buyable when it is `ACTIVE` **and** published to a channel.
Listings with no `isbn` are invisible to every rule, so the adapter warns about them
rather than skipping quietly.

## What is deliberately not here

No load testing — that needs a target and a budget, not a morning. No entitlement or
delivery layer. No real rights system; `data/rights.csv` stands in for one, which is
also the honest shape of the problem: the gate is only as good as the rights data, and
the first real question in any engagement like this is who owns that table.
