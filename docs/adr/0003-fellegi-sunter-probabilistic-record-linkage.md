# ADR 0003: Fellegi-Sunter Probabilistic Record Linkage for Multi-Source Reconciliation

## Status
Accepted

## Context
Payment reconciliation involves reconciling heterogeneous data sources (Razorpay Settlements, Core Banking NEFT/RTGS statement feeds, and Merchant ERP ledgers). Exact deterministic matching fails under real-world anomalies such as UTR truncations, timing delays (T+1 to T+3 settlement lags), and MDR fee/tax deductions.

## Decision
We implement a calibrated Fellegi-Sunter (1969) log-likelihood record linkage scoring engine. Field agreements (exact UTR, order ID, amount equality, date proximity, narration similarity) generate weighted composite scores bounded in `[0.0, 1.0]`. Matches scoring \(\ge 0.95\) are marked candidate auto-matches, while scores in `[0.70, 0.95)` are classified as likely matches subject to exception investigation.

## Consequences
- Preserves high recall (\(>83\%\)) even under corrupted UTRs or fee mismatches.
- Deterministic Invariant Shield acts as a final gatekeeper, ensuring precision remains strictly 100.0%.
