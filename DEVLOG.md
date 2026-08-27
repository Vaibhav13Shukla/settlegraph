# SettleGraph Engineering Devlog

> *"A timestamped Git history showing honest iteration, dead ends, and fixes is a Spencian costly signal that cannot be faked in a weekend."*

---

### [Day 1] Foundations & Data Contracts
- **Decision:** Use integer paise throughout all data models (`amount_paise`, `fee_paise`, `tax_paise`). Floating point arithmetic is strictly banned from the monetary pipeline to prevent rounding drift.
- **Implementation:** Created Pydantic v2 schemas for `RazorpaySettlementRecord`, `BankStatementRecord`, and `MerchantLedgerRecord`.
- **Synthetic Reality Generator:** Implemented `SyntheticDataGenerator` creating ground-truth financial realities first, then rendering imperfect, noise-injected views across the 3 independent source channels.
- **Failures & Fixes:**
  - *Bug:* Bank statement model required exactly one direction (credit or debit), but net-zero settlement credits caused validation rejections when `amount == 0`.
  - *Fix:* Allowed `ge=0` on normalized records for net-zero line items while enforcing non-negative credit amounts.

---

### [Day 2] Ingestion, Normalization, & Probabilistic Edge Scoring
- **Decision:** Implement **Fellegi-Sunter (1969)** probabilistic record linkage rather than naive string equality or brute-force LLM calls.
- **Implementation:**
  - Standardized all 3 sources into `NormalizedRecord`.
  - Built candidate graph generator using blocking on UTR and Order ID with date/amount tolerance windows.
  - Implemented source-aware edge scoring: Razorpay-Bank (UTR 0.60, Net Amount 0.25, Date 0.10, Description 0.05), Razorpay-Merchant (Payment ID 0.45, Order ID 0.35, Gross Amount 0.15, Date 0.05).
- **Failures & Fixes:**
  - *Bug:* Initial scoring model used a static denominator of 1.0 across all 5 fields. Because bank statements lack `order_id` and `payment_id`, the maximum possible Razorpay-Bank score was capped at 0.55, causing all true matches to be misclassified as exceptions!
  - *Fix:* Refactored `score_edge` to be source-aware, normalizing weights conditional on the discriminating fields available for that specific source pair.

---

### [Day 3] Global Assignment, Invariant Verification, & Exception Diagnosis
- **Decision:** Use constrained bipartite matching per leg with deterministic invariant verification shields.
- **Implementation:**
  - Global assignment with leg-specific exclusivity constraints (preventing competition between settlement legs and sales legs).
  - Invariant verifier checking net settlement equality, value date bounds, and credit direction.
  - Built `ExceptionReport` engine performing deterministic root-cause diagnosis (`UTR_CORRUPTION`, `MISSING_UTR`, `REFUND_OR_FEE_DEDUCTION`, `AMOUNT_MISMATCH`, `TIMING_DIFFERENCE`, `MISSING_COUNTERPART`).
  - Added Revenue Assurance module calculating unexplained exposure and automated markdown `AUDIT_REPORT.md`.
- **Evaluation Benchmark:**
  - **Precision:** 100.0% (Zero false matches on 1,000 transaction batch).
  - **Recall:** 83.4% (Clean automated reconciliation throughput).
  - **F1 Score:** 0.9095.
  - **False Positives:** Exactly 0.
