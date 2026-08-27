# SettleGraph Product Requirements Document

## Purpose

SettleGraph closes the settlement-reconciliation loop for a Razorpay merchant:
ingest gateway, bank, and internal-ledger records; prove safe matches; surface
unexplained money; and abstain where evidence is insufficient.

## Day 1 acceptance criteria

- Pydantic models reject invalid financial records.
- All monetary values are integer paise; floats never enter the money path.
- A seeded generator creates at least 1,000 ground-truth transactions and
  source-specific Razorpay, bank, and merchant views.
- Every generated source row has immutable provenance and a hidden truth link.
- Re-running the generator with the same seed produces identical CSV content.

## Functional roadmap

1. Ingest CSV/API sources idempotently and validate schemas.
2. Normalize dates, amounts, identifiers, and free-text references.
3. Generate candidate links without dropping a record.
4. Score and globally optimize assignments under hard constraints.
5. Verify accounting invariants, then emit `AUTO_MATCH`, `EXCEPTION`,
   `ABSTAIN`, or `UNMATCHED`.
6. Produce audit logs, reports, exports, and evaluation metrics.

## Non-negotiable rules

- Ground truth is evaluation-only and never available to runtime matching code.
- LLMs may parse text and summarize verified evidence only; they cannot decide
  matches, perform accounting arithmetic, create records, or override policy.
- Any batch-level invariant failure prevents publishing reconciled results.

