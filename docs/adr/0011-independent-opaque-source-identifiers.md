# ADR 0011: Independent, opaque source identifiers

## Context

The synthetic generator derived every identifier from one shared payment
index: `pay_{i}`, `order_{i}`, `setl_{i//20}`, `RZP{i:012d}` (UTR),
`bank_{i}`, `led_{i}`, `INV-2026-{i}`. Ground truth was, in effect, "same
index". This made the synthetic world tidier than a real settlement feed,
where a gateway, a bank, and a merchant's accounting system each mint their
own opaque identifiers and the *only* legitimate cross-system keys are shared
evidence — the UTR (printed on both the settlement and the bank statement) and
the order_id (created by the merchant, passed to the gateway).

The correlation also silently distorted a benchmark. The old UTR format
`RZP{index:012d}` produces near-identical strings for adjacent payments, which
`difflib.SequenceMatcher` scores as ~0.94 similar. That, not any property of
fuzzy matching itself, is what made the fuzzy baseline post a ~27.70%
false-auto-book rate. The number was an artifact of the identifier format.

## Decision

Each identifier is minted independently from an opaque token
(`pay_<14>`, `order_<12>`, `setl_<10>`, `banktxn_<16>`, `led_<12>`), the UTR
is a random 16-character alphanumeric (RBI UTR shape) shared *only* between a
settlement and its true bank line, and invoice numbers are sequential within a
merchant's own books. No source's id is derivable from another's. The
matching engine already keyed on genuine evidence (UTR, order_id, amount,
date), not on a numeric suffix, so this is a data-realism change, not an
engine change. Ground truth remains the explicit relationship table it always
was (`GroundTruthRecord.true_bank_record_ids` / `true_merchant_record_id`), so
evaluation needed no structural change — only regeneration.

## Alternatives considered

- **Keep sequential ids, add noise.** Rejected: the correlation is the
  unrealism; masking it with noise does not remove it.
- **Reintroduce round-amount clustering to make a baseline look dangerous
  again.** Rejected as tuning-the-data-to-a-result — the same sin as the
  sequential UTRs. The honest move is to report that the benchmark no longer
  differentiates on an easy batch and move the danger demonstration to the
  per-scenario adversarial suite.

## Consequences

- Every published metric was regenerated in the same change (see README and
  `docs/ENGINEERING_AUDIT.md` §14). Headline: precision 1.0, recall 0.8449,
  F1 0.9159 (seed 42, 1,000 records).
- The fuzzy-baseline 27.70% false-auto-book claim is **retracted** as an
  identifier-format artifact.
- On realistic identifiers the naive baselines are as safe as SettleGraph on
  the standard batch; the safety difference lives in `datagen/adversarial.py`.
