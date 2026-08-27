# Architecture

```text
Razorpay CSV/API ─┐
Bank statement ───┼─> ingestion -> normalization -> candidate graph
Merchant ledger ──┘                         -> scoring -> global assignment
                                                   -> invariant verifier
                                                   -> match / exception / abstain
                                                   -> audit and reporting
```

The Day 1 generator creates the financial reality first and renders three
imperfect source views second. This provides defensible hidden ground truth for
later matching and evaluation.

