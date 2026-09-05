# ADR 0009: Hidden Ground Truth & Dataset Split Isolation

## Context
Tuning scoring thresholds or decision rules on the same dataset used to report performance violates the scientific method and creates overfitted, fragile systems. In financial systems, over-optimistic evaluation leads to real monetary loss.

## Decision
SettleGraph enforces a 4-split dataset partitioning scheme:
- `train` (40%): General development and model parameter testing.
- `calibration` (15%): Used strictly by `scripts/threshold_study.py` to evaluate confidence threshold choices.
- `test` (30%): Held-out split evaluated strictly by `scripts/eval_holdout.py` to measure unseen generalization.
- `adversarial` (15%): Edge cases, prompt injection, and boundary corruptions.

Data leakage between splits is guarded by `tests/test_splits.py`, asserting disjoint payment IDs. Ground truth files remain completely isolated from the runtime pipeline.
