# ADR 0007: Calibrated Abstention Over False-Positive Matches

## Context
In financial reconciliation, the cost asymmetry between errors is profound:
- A false positive (`AUTO_MATCH` that pairs unrelated transactions) permanently corrupts the general ledger, requires complex reversing journal entries, distorts tax liabilities, and risks fraudulent fund leakage.
- An abstention (`LIKELY_MATCH` or `EXCEPTION`) merely queues a record for human review or deferred settlement cutover.

## Decision
SettleGraph strictly enforces **Precision = 100.0%** as a non-negotiable hard invariant.
Whenever confidence falls below `config.auto_match_threshold` (0.95), or whenever another candidate exists within `config.ambiguity_margin` (0.05) on the same leg, the engine explicitly abstains by setting label to `LIKELY_MATCH` or `EXCEPTION` with a machine-readable explanation.

## Consequences
- False-auto-book rate is strictly 0.00%.
- Recall is sacrificed whenever evidence is ambiguous, which is the mathematically safe and legally compliant operating regime for fintech ledgers.
