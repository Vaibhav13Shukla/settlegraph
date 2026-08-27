# Data contracts

## Money

All amounts are signed or unsigned integer paise. Decimal floats are forbidden.
Razorpay payment gross amounts are positive; fees and taxes are non-negative;
`net_amount_paise = amount_paise - fee_paise - tax_paise`.

## Provenance

Every normalized record retains a source, original source ID, raw payload and
source-row metadata. The normalizer must annotate missing data; it may not fill
it with invented defaults.

## Ground truth isolation

`GroundTruthRecord` is stored separately and only imported by evaluation code.
The engine package must not depend on it.

