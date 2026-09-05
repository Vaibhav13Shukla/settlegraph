# Execution Plan (Winning Path) — HISTORICAL

> **This document is superseded and kept only as a record of the original
> plan.** It was written on Day 2 and the project diverged from it almost
> immediately. Reading it as a description of the current system will
> mislead you.
>
> For what actually happened, day by day, including the defects found and how:
> **[`../DEVLOG.md`](../DEVLOG.md)**.
> For the current architecture: **[`ARCHITECTURE.md`](ARCHITECTURE.md)**.
> For what is measured and what is still open: **[`EVALUATION.md`](EVALUATION.md)**.
>
> Notable divergences: the "calibration pipeline" of Day 5 landed as
> `engine/calibration.py` on Day 8, not Day 5; the "active evidence
> acquisition policy" of Day 9 was never built; and the ablation report of
> Day 10 became `docs/EVALUATION.md`'s three-baseline comparison.

---

## Day 2 (completed in this iteration)
- Stabilize failing baseline tests.
- Lock domain language in `CONTEXT.md`.
- Record hard-to-reverse architecture decisions in ADRs.
- Upgrade synthetic generator to emit train/calibration/test/adversarial splits.

## Day 3
- Tight ingestion coercion and schema guarantees.
- Canonical normalization hardening for malformed IDs and dates.
- Exact-match baseline metrics on held-out split.

## Day 4
- Candidate graph with controlled blocking strategy and diagnostics.
- Edge feature extraction report.

## Day 5
- Pairwise scorer + calibration pipeline.
- Coverage-at-precision metrics.

## Day 6
- Global assignment optimizer + split/merge structures.
- Regression tests for exclusivity and conservation.

## Day 7
- Invariant verifier + confidence gate for AUTO_MATCH/EXCEPTION/ABSTAIN.

## Day 8
- Exception classifier + evidence-chain logging.

## Day 9
- Active evidence acquisition policy and cost/latency accounting.

## Day 10+
- Full ablation report, failure-injection demo, 5-minute pitch narrative.

