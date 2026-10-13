# Load test specification

**Status:** written and unexecuted. That is the honest state, and it is also the normal
one — the party that executes this is the platform vendor, under `SOW-2`. What belongs
to the programme manager is everything on this page: the target, the scenario, the pass
criteria, and what counts as evidence. "Lead launch readiness, including load testing"
means specifying it and verifying the result, not running it.

Nothing here has been run. A measured *baseline* exists separately
(`python3 -m gate.latency`) and answers a different question: what normal looks like
today, so that a slow page on launch day can be recognised as new.

---

## 1. What we are protecting

A storefront launch for a catalogue of digital goods with territorial rights. The
failure that costs money is not a slow page — it is **a checkout that completes against
a listing that should not have been buyable**, under conditions where nobody is reading
dashboards. Load is the condition that makes every other defect likelier.

## 2. Peak target — to be agreed in writing

This is `LOAD-1` on the readiness board, and it is unanswered. It cannot be invented
here; it comes from the business. What we need from them is three numbers:

| Input | Who owns it | Why it decides the test |
|---|---|---|
| Expected peak concurrent sessions | client-product | Sets the plateau |
| The marketing beat that creates it | client-product | A newsletter send is a spike; a slow build is not |
| Acceptable revenue loss during a degradation | client-product | Decides whether we shed load or queue it |

**Proposed until contradicted:** a launch-day plateau of 400 concurrent sessions with a
3× spike for 10 minutes on the announcement, derived from the publisher's own email
list size rather than from an industry average. If the business disagrees, the number
changes and so does the test — but we do not start without one.

## 3. Scenarios

Run in this order. Each must pass before the next is meaningful.

| # | Scenario | Load | Duration | Pass |
|---|---|---|---|---|
| S1 | Browse: home → collection → product | 1× target | 15 min | p95 TTFB ≤ 800 ms, error rate < 0.5% |
| S2 | Browse + add to cart, 10% conversion to cart | 1× target | 15 min | as S1, cart ops p95 ≤ 1200 ms |
| S3 | Announcement spike | 3× target, ramp 60 s | 10 min | no 5xx; p95 ≤ 2000 ms; full recovery within 5 min |
| S4 | Soak | 0.5× target | 4 h | no memory or connection growth in any vendor-owned service |
| S5 | Territory enforcement under load | 1× target, 5% requests from blocked territories | 15 min | **zero** blocked-territory add-to-cart succeeds |

**S5 is the one that matters most and the one usually left out.** Rights enforcement is
the thing whose failure is a breach rather than a bad experience, and race conditions
appear under load, not in a quiet staging environment.

## 4. Out of scope, and why

- **Shopify-hosted checkout.** We do not load test it. It is not our infrastructure,
  third-party stress testing is not ours to authorise, and the result would measure
  Shopify rather than anything we can change. We measure *up to* checkout and take
  Shopify's own availability commitments for the rest.
- **The CDN edge.** Same reason. Cache behaviour is verified by configuration review
  (`INF-1`), not by traffic.
- **Anything in production before go-live.** The test runs against a production-shaped
  environment with a seeded catalogue of comparable size, never against the live store.

## 5. Evidence required from the vendor

A green summary is not evidence. Closing `LOAD-2` requires:

1. The test definition as code, in version control, not a screenshot of a tool.
2. Raw result files, with the time window, so the numbers can be re-derived.
3. The dataset description: catalogue size, how close to production shape.
4. Named failures observed and what was changed, or an explicit statement that none were.
5. A re-run after any fix, with the same definition.

Without 1 and 2 the result is unfalsifiable, and an unfalsifiable pass is worth less
than an honest failure.

## 6. What this unblocks

`LOAD-2` and `LOAD-3` on the readiness board, and with them `PERF-1` under load rather
than at rest. `SEC-3` — the cart accepting a variant the geo-gate hides in the UI — is
confirmed or cleared by **S5**, which is why a security row sits in a load test
specification.
