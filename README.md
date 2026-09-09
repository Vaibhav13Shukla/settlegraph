# SettleGraph

Evidence-first settlement reconciliation for Razorpay merchants.

SettleGraph compares gateway settlements, bank statements, and the merchant
ledger. It accepts a match only after deterministic checks pass. Ambiguous
records stay in review with a reason and an amount at risk.

## The decision path

```text
CSV sources
  -> validation and normalization
  -> candidate links
  -> weighted scoring
  -> global one-to-one assignment
  -> accounting invariant checks
  -> match, review, exception, and audit outputs
```

The core rule is simple: scoring proposes; verification decides. Monetary
values use integer paise. Ground truth is used by evaluation code only.

## Run it

Requirements: Python 3.11 or newer.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"

python -m settlegraph.cli generate --total-records 1000 --seed 42
python -m settlegraph.cli run
python -m settlegraph.cli serve --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080`.

The run writes `results/` locally. It includes assignments, exceptions,
evaluation metrics, revenue assurance, a digest, and an audit report.

Useful commands:

```powershell
python -m settlegraph.cli benchmark
python -m settlegraph.cli simulate
python -m settlegraph.cli replay
python scripts/run_e2e.py
python scripts/noise_sweep.py --records 500
python scripts/chaos_batch.py --records 400
python scripts/eval_holdout.py --records 600 --seed 20260905
```

## What the current batch shows

The seeded 1,000-record batch produces:

| Measure | SettleGraph |
| --- | ---: |
| Precision | 100.0% |
| Recall | 83.64% |
| F1 | 0.9109 |
| False positives | 0 |
| False auto-book rate | 0.00% |
| Invariant violations | 0 |
| Auto matches | 2,106 |
| Review holds | 175 |
| Unmatched records | 43 |

These are synthetic-data results. The baseline comparison is deliberately
included: the amount-plus-date baseline reaches higher recall on this batch,
while the fuzzy baseline records a 27.70% false auto-book rate. Run
`python -m settlegraph.cli benchmark` to reproduce the comparison.

The safety behavior has been checked beyond the main batch:

- A different seed holds precision at 100.0%; recall is 81.82%.
- Noise from 0% to 30% keeps precision at 100.0%; recall falls as data quality drops.
- Structural chaos from 0% to 50% keeps completed runs at 100.0% precision; at 50%
  chaos, recall is 13.32%.
- Replay reproduces 2,284 of 2,284 decisions on the seeded batch.
- Seven failure-injection scenarios complete without a false ledger entry.

## What is implemented

- Pydantic schemas for Razorpay, bank, merchant, GST, and Route records.
- Row-level quarantine for validation failures.
- SHA-256 duplicate interception for identical re-imports.
- Candidate generation with UTR, identity, amount, and date signals.
- Fellegi-Sunter-style weighted scoring.
- Global assignment with per-leg exclusivity and near-tie abstention.
- Invariant checks for amount, date, direction, and Razorpay record type.
- Exception categories with evidence, severity, remediation, and rupee exposure.
- Calibration, replay, drift, baseline, noise, chaos, and holdout tooling.
- Optional Claude reasoning for unresolved cases. It can propose a candidate;
  it cannot bypass verification or write a ledger entry.
- A single-file dashboard with a local HTTP API and a read-only FastAPI adapter.

## Known limits

This is a reconciliation controller for bounded batches, not a production
ledger or a multi-tenant service.

- All benchmark data is synthetic. No real merchant export has been used.
- One-to-many, many-to-one, and aggregated settlements become review cases.
- The AI resolver has failure-path tests but no quality benchmark over a labeled
  real exception corpus.
- Local and Docker runs use flat files and one writer. Concurrent workers need
  shared durable storage and job locking.
- A fresh Vercel run is read-only. The hosted site uses a committed snapshot
  when runtime artifacts are absent; it does not run reconciliation.
- Dangerous-miss and exception-recall rates use a small no-counterpart sample
  (`n=16` in the seeded batch).

These limits are part of the evaluation, not hidden behind the dashboard.
See [docs/RED_TEAM.md](docs/RED_TEAM.md) and [docs/EVALUATION.md](docs/EVALUATION.md).

## Deployment

For a stateful deployment:

```powershell
docker compose up --build -d
```

The container serves the dashboard on port `8080` and exposes `/healthz`.
Docker is the deployment path for fresh batches. Vercel serves the dashboard
and read-only snapshot API; `POST /api/run-reconciliation` returns `405` by
design.

## Project map

```text
src/settlegraph/engine/   reconciliation pipeline and safety checks
src/settlegraph/models/   typed source and normalized records
src/settlegraph/web/      self-contained dashboard
api/                      FastAPI deployment adapter and demo snapshot
datagen/                  seeded source and anomaly generator
data/splits/              train, calibration, test, and adversarial fixtures
tests/                    unit, integration, property, and safety tests
docs/                     contracts, architecture, evaluation, ADRs, red team
scripts/                  reproducibility and stress harnesses
```

## Development

```powershell
make format-check
make lint
make test
make e2e
```

The project treats a false match as more serious than a missed match. Changes
that improve recall by weakening precision or the invariant gate are rejected.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the test and review expectations.
