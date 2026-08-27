# SettleGraph

SettleGraph reconciles settlement-related financial records across Razorpay, bank, and merchant systems while preserving accounting truth and surfacing unresolved exposure.

## Language

**Reconciliation Batch**:
A bounded set of records processed together into one reconciliation outcome.
_Avoid_: run, sync, execution

**Canonical Record**:
A source row transformed into SettleGraph's common schema for matching and verification.
_Avoid_: raw row, payload

**Match Group**:
Records inferred to represent one financial event or tightly coupled settlement reality.
_Avoid_: pair, join

**Evidence Chain**:
Ordered facts that justify a match, exception, or abstain decision.
_Avoid_: guess trail, model trace

**Auto Match**:
A match group accepted automatically because confidence and deterministic invariants both pass.
_Avoid_: confident guess

**Exception**:
A group requiring further investigation because evidence is conflicting or insufficient.
_Avoid_: failure, error

**Abstain**:
A deliberate refusal to force a match when safety thresholds are not met.
_Avoid_: unknown, skip

**Unexplained Amount**:
Monetary value not safely attributable to a verified match group in the current batch.
_Avoid_: confirmed loss, stolen money

**Ground Truth**:
Hidden linkage labels used only for evaluator-side measurement.
_Avoid_: runtime labels, production truth

