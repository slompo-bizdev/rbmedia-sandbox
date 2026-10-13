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

## Launch readiness, and why it is a table

`data/readiness.csv` is the job description's own list — infrastructure, security, load,
observability, checkout performance — as rows with an owner, evidence, and a verdict.
A readiness *document* gets written once and rots; a readiness *table* can be evaluated,
can block, and can name who is holding the launch.

Two statuses carry a decision:

- **`unknown`** counts as not-ready. An unanswered blocking check is the most common way
  a launch date slips, and scoring it as a pass is how a team lies to itself.
- **`gate`** is not a human judgement. Those rows are resolved at run time from the
  catalogue findings, which is how the two halves connect: catalogue integrity is *one
  input to* launch readiness, not a separate report nobody reconciles.

## Ageing, escalation, and the morning brief

`gate/sla.py` puts a clock on every open issue, with the SLA taken from the owner — two
days for a live rights breach, five for a platform vendor, seven for a question the
client owes us. Past its SLA, an issue goes to Urgent, picks up an `sla-breach` label,
and gets one comment. **There is no state file: the Linear issue is the state.** Its
`createdAt` is when we first saw the problem and the label is what stops us escalating
the same thing twice.

`gate/slack.py` posts the brief into the ops channel: verdict, who is holding it, the
three worst findings, anything past SLA. Short on purpose — detail is one click away in
Linear. "Real-time communication during launches" means the people who can act find out
where they already work.

## Stakeholder intake — where the loop closes

A demand arrives by email. `gate/intake.py` decides what it is, in this order,
stopping at the first question that resolves:

1. **Already tracked?** Link it. Opening a second ticket for a known problem is how a
   board loses the room's trust.
2. **Does the claim survive the source?** The email says the GB licence expired; the
   rights table is what settles it. If the source disagrees, the reply carries the
   evidence rather than an opinion.
3. **Which SOW line?** None, or a line with no approved hours, means this is a **change
   request, not an outstanding task**. Saying that in writing on the day it arrives is
   the difference between a project that lands and one that quietly overruns.
4. **What is the acceptance criterion?** Prose becomes something a human can sign off,
   which is also the UAT seed.

The division of labour is deliberate. **A human, with a model's help, reads the prose
and fills in a small structured demand** (`demands/*.json`) — that is judgement, and
judgement does not belong inside a deterministic rule. This module then decides, and
*this* is the part that is tested.

```bash
python3 -m gate.cli intake --demand demands/D-003.json              # not reproducible
python3 -m gate.cli intake --demand demands/D-002.json              # change request
python3 -m gate.cli intake --demand demands/D-001.json --team RBM   # already tracked
python3 -m gate.cli intake --demand demands/D-002.json --team RBM --open --slack
```

`intake` exits **1** on a change request, so an automated pipeline cannot quietly
absorb new scope.

## Run it

Offline, no credentials, no network:

```bash
python3 -m pytest -q                            # 27 tests
python3 -m gate.cli check                       # reads data/catalogue.csv
python3 -m gate.cli check --today 2026-11-04    # watch R2 and R6 change
python3 -m gate.cli readiness                   # the go/no-go board
python3 -m gate.cli check --format markdown > report.md
```

Against the live board and channel:

```bash
cp .env.example .env     # then fill it in
set -a; source .env; set +a

python3 -m gate.cli sync   --team RBM             # one owned issue per finding
python3 -m gate.cli digest --team RBM             # sync + age + post the brief
python3 -m gate.cli digest --team RBM --no-slack  # print the brief instead
python3 -m gate.cli digest --team RBM --as-of 2026-10-20   # see the escalation path
```

`--as-of` moves only the clock: the issues and the escalation are real, which is the
only way to see the SLA path on a board whose tickets were opened this morning.

Every command exits **1** when something blocking is open, so these belong in CI as
gates, not in an inbox as reports.

## Shopify shape it expects

Metafields in the `custom` namespace: `isbn`, `imprint`, `territories`
(comma separated, e.g. `US,CA,GB`), `audio_asset` (empty means no deliverable file).
A listing counts as buyable when it is `ACTIVE` **and** published to a channel.
Listings with no `isbn` are invisible to every rule, so the adapter warns about them
rather than skipping quietly.

## This is not observability

Worth stating plainly, because the word gets stretched. There is no telemetry here —
no metrics, no traces, no error budget, nobody holding a pager. The readiness board
says as much: two of its three `OBS-` rows are failing or unanswered.

What this is, is a **monitor over business invariants**, running on a cadence and
alerting where the people who can act already work. The distinction is the common gap:
teams instrument the infrastructure and leave the business rules uninstrumented, which
is how a system fails while every dashboard stays green.

The generated page (`gate.cli page`) carries a section naming what observability for
this launch would actually require — checkout p95 by territory, entitlement delivery
failure rate, order-to-delivery success, an error budget with a named pager owner, and
an alert on the silent path. None of it is built. Saying what is missing is more useful
than claiming it is done.

## What is deliberately not here

No load testing — that needs a target and a budget, not a morning. No entitlement or
delivery layer. No real rights system; `data/rights.csv` stands in for one, which is
also the honest shape of the problem: the gate is only as good as the rights data, and
the first real question in any engagement like this is who owns that table.
