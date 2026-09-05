# ADR 0008: Deterministic Invariant Gates Post-AI Verification

## Context
LLMs and probabilistic models can hallucinate plausible-sounding financial justifications. Permitting an AI reasoner to directly commit entries to a financial ledger without hard deterministic verification breaches basic accounting controls.

## Decision
All candidate links—whether proposed by the Fellegi-Sunter scoring engine or hypothesized by the optional Claude AI Reasoner—must pass deterministic invariant gates (`verify_settlegraph_invariants`):
1. **Amount Invariant:** Net difference must be within `config.amount_tolerance_paise` (100 paise / ₹1.00).
2. **Date Invariant:** Transaction and value dates must be within `config.date_tolerance_days` (3 days).
3. **Direction Invariant:** Bank counterpart must be a credit line; debits are rejected with `INVARIANT_VIOLATION`.

The AI Reasoner is strictly an advisor proposing hypotheses. Promotion to `AI_RESOLVED_MATCH` occurs if and only if mathematical invariants hold.

## Consequences
Prompt injections, hallucinations, or model drift can never corrupt ledger entries.
