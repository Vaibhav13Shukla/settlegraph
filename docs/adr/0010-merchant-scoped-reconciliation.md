# ADR 0010: Merchant-scoped reconciliation (cross-merchant isolation)

## Context

The primary user is a Razorpay-internal reconciliation analyst managing a
*portfolio of merchants*. Until now no record carried a merchant identity: a
batch was one implicit tenant. That is unsafe for the stated user. Two
different merchants can legitimately have a settlement and a bank credit that
share an amount, a date, or — through a recycled or coincidentally-colliding
bank reference number — even a UTR. Nothing downstream (amount, date,
direction, record-type invariants) inspects *which merchant* a record belongs
to, so a coincidental cross-merchant collision could be scored, and in the
worst case booked, linking merchant A's settlement to merchant B's bank
account. That is a cross-tenant data-integrity failure, not a matching error.

## Decision

Every source and normalized record carries a `merchant_id`. The candidate
graph (`engine/match.py`) applies a hard isolation boundary *before scoring*:
a pair linking two different known merchants is dropped and never scored. This
is a filter on the fully-built candidate set, applied once at the return, so
no future candidate-generation branch can bypass it.

An unknown/defaulted merchant (`merch_unknown`, the field default) is treated
permissively, so legacy single-tenant batches and fixtures that never set a
merchant reconcile exactly as before. Once real merchant_ids are present
(every generated batch), the boundary is enforced.

## Alternatives considered

- **Scoring penalty for cross-merchant pairs.** Rejected: isolation is a hard
  correctness boundary, not a soft signal. A high-confidence coincidental
  collision must be *impossible* to book, not merely unlikely.
- **Access-control-level isolation only.** Deferred: authentication/authorization
  is out of scope for this prototype. Enforcing isolation in the matching
  engine protects the ledger regardless of who is looking at it.

## Consequences

- A new deterministic test (`test_candidate_graph_never_links_across_merchants`)
  proves a same-UTR cross-merchant pair never becomes a candidate.
- On the seeded 1,000-record, six-merchant batch the guard suppresses 23
  cross-merchant candidate pairs that would otherwise be scored — the feature
  does real work on real data, not just in a unit test.
- Isolation is enforced in the engine, not at an access boundary. Multi-tenant
  authentication/authorization remains explicitly unbuilt (see README limits).
