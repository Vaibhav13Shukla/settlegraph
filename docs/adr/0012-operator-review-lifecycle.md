# ADR 0012: Operator review lifecycle as a decision overlay

## Context

The primary user is a reconciliation analyst, but the system had no way for a
human decision to persist. The dashboard was read-only over batch artifacts:
an operator could see an exception but could not approve, reject, reclassify or
resolve it, and nothing recorded who decided what. That is the gap the expert
review pressed hardest on, and the audit ranked it the highest-impact one.

Two hard constraints shape the design:

1. **The batch artifacts are immutable evidence.** `assignments.csv` and
   `exceptions.json` must re-derive byte-for-byte on replay (replay consistency
   is a headline property). A human decision must therefore not mutate them.
2. **The system is single-writer and bounded-batch.** Whatever stores
   decisions must be honest about that, not pretend to be a distributed
   datastore.

## Decision

Review decisions live in a **separate overlay** (`results/review_state.json`),
keyed by case id (the exception's record id), never touching the batch
artifacts. `engine/review.py` provides:

- An explicit **state machine** (`OPEN → APPROVED / REJECTED / RESOLVED`, with
  `RECLASSIFY` re-triaging in place and `REOPEN` un-closing). Legal transitions
  live in one table; anything else raises `IllegalTransition`, so the reachable
  states are provable rather than implied by scattered conditionals.
- An **append-only audit trail**: every transition records event id, case id,
  merchant, actor, action, previous/new status, reason, and timestamp.
- **Optimistic concurrency**: each case carries a `version`; a decision must
  cite the version it acted on, and a stale version is refused with
  `ConcurrencyConflict` (HTTP 409). Two reviewers cannot silently overwrite one
  another.
- Cases are created lazily on first decision (implicitly `OPEN` at version 0),
  so the overlay is proportional to reviewer activity, not batch size.

Endpoints: `GET /api/review-queue` (exceptions joined with status, filtered by
merchant/severity/amount, ranked severity-then-exposure) and `GET /api/audit`
are read-only. `POST /api/exceptions/{case_id}/{action}` applies a decision and
is behind the **same loopback gate** as pipeline re-runs — a decision is a
mutation. The hosted (Vercel) adapter exposes the read endpoints only; approve/
reject/resolve run on the local Docker/CLI console (the hosted demo is
read-only by design).

## Alternatives considered

- **Mutate `assignments.csv`/`exceptions.json` in place.** Rejected: it breaks
  replay determinism and conflates evidence with judgement.
- **A database now.** Deferred: single-writer file storage matches the current
  scope. `ReviewStore` is a narrow interface (`get`/`apply`/`audit_trail`) that
  a DB-backed repository can implement later without touching callers.
- **Invented per-user auth.** Rejected as fake security; the loopback gate is
  the real property that makes the local console safe, consistent with the
  existing run-reconciliation gate. `actor_id` is supplied by the caller and
  recorded for audit, not authenticated — documented as demo-only.

## Consequences

- Human decisions persist, are audited, and survive a reload; a resolved case
  leaves the open queue and reappears only with `include_closed`.
- Double-approval is prevented and tested (`tests/test_review.py`).
- No money moves and no match changes: the overlay records judgement about the
  engine's output; it never re-runs the engine or writes the ledger.
- Real authentication/authorization and multi-writer durability remain unbuilt
  (see README limits).
