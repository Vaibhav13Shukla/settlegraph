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
python scripts/eval_holdout.py --records 2000 --seed 20260905
```

## Enabling the optional AI features (local, personal use)

The reconciliation core runs with no LLM at all. Two optional add-ons — the AI
exception *reasoner* and the read-only *Q&A agent* — turn on only when a
credential is present, and both fail closed.

```powershell
pip install -e ".[dev,llm]"
```

Pick one credential (see `.env.example`):

- **Anthropic API key** — powers both features:
  `setx ANTHROPIC_API_KEY "sk-ant-..."` (new shell), then set
  `SETTLEGRAPH_LLM_PROVIDER=claude` to enable the reasoner.
- **Claude Pro/Max subscription token** — the Q&A agent only, via
  `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`. The Q&A endpoint
  (`server.py`) activates the Claude Agent SDK when either credential is set.

The subscription path is licensed for **individual/local use** and must not
back a public or multi-user service (Anthropic Agent SDK terms). Run the live
agent from your own machine (`http://127.0.0.1:8080`); the hosted deployment
stays read-only and serves a committed snapshot — it does not run the agent.
Never commit a token: `.env` is gitignored.

## What the current batch shows

The seeded 1,000-record batch produces:

| Measure | SettleGraph |
| --- | ---: |
| Precision | 100.0% |
| Recall | 84.49% |
| F1 | 0.9159 |
| False positives | 0 |
| False auto-book rate | 0.00% |
| Invariant violations | 0 |
| Auto matches | 2,140 |
| Review holds | 166 |
| Unmatched records | 40 |

These are synthetic-data results across six merchants (`merch_apollo`,
`merch_zomato`, …), each settling to its own bank account, with independently
generated source identifiers (ADR 0010, ADR 0011).

An honest note on the baselines: on this batch with realistic independent
identifiers, **all three naive baselines also reach 100% precision with zero
false auto-books, and each finds slightly more true positives than SettleGraph**
(A 854, B 866, C 872 vs 839) because they never abstain. SettleGraph is
deliberately more conservative, not a benchmark winner here. An earlier
version of this repo reported the fuzzy baseline at a 27.70% false-auto-book
rate; that figure was an artifact of the old sequential UTR format
(`RZP{index:012d}`), where unrelated references resemble each other, and it is
**retracted**. The safety difference now shows where it actually matters — the
per-scenario adversarial suite (`datagen/adversarial.py`), where naive matching
mis-books and the invariant gate, merchant isolation, and abstention do not.
Run `python -m settlegraph.cli benchmark` to reproduce the comparison.

The safety behavior has been checked beyond the main batch:

- A held-out corpus (a seed development never saw) holds precision at 100.0%; recall is 82.60%.
- Noise from 0% to 30% keeps precision at 100.0%; recall falls (92.6% → 76.0%) as data quality drops.
- Structural chaos from 0% to 50% keeps every completed run at 100.0% precision; at 50%
  chaos, recall is 10.61% and abstention rises to 31.2%.
- Replay reproduces 2,308 of 2,308 decisions on the seeded batch.
- Seven failure-injection scenarios complete without a false ledger entry.

## What is implemented

- Pydantic schemas for Razorpay, bank, merchant, GST, and Route records.
- Row-level quarantine for validation failures.
- SHA-256 duplicate interception for identical re-imports.
- Candidate generation with UTR, identity, amount, and date signals.
- Merchant-scoped reconciliation: the candidate graph refuses to link records
  across different merchants, and the invariant gate rejects a cross-merchant
  booking regardless of who proposed it — including the AI resolver's
  graph-bypassing widen path (ADR 0010).
- Fellegi-Sunter-style weighted scoring.
- Global assignment with per-leg exclusivity and near-tie abstention.
- Invariant checks for amount, date, direction, merchant, and Razorpay record
  type — the same gate every match must clear, exercised against reversal and
  chargeback scenarios in the adversarial corpus.
- Exception categories with evidence, severity, remediation, and rupee exposure.
- Amount-weighted exposure in evaluation (rupees booked onto a wrong
  counterpart, worst-case single auto-book) — the offline correctness view,
  kept distinct from the live revenue-assurance cash view (docs/EVALUATION.md).
- Calibration, replay, drift, baseline, noise, chaos, and holdout tooling.
- Optional Claude reasoning for unresolved cases. It can propose a candidate;
  it cannot bypass verification or write a ledger entry.
- A persisted operator review lifecycle: approve / reject / reclassify / resolve
  with an explicit state machine, an append-only audit trail (who, what, when,
  why, before/after), and optimistic-concurrency protection against two
  reviewers overwriting one another. Decisions live in a `review_state.json`
  overlay and never mutate the immutable batch artifacts (ADR 0012).
- A single-file dashboard with a local HTTP API and a read-only FastAPI adapter.
  Review actions are loopback-gated on the local console; the hosted snapshot
  exposes the queue read-only.

## Known limits

This is a reconciliation controller for bounded batches, not a production
ledger or a hardened multi-tenant service.

- Records carry a `merchant_id` and reconciliation is merchant-scoped, but there
  is no authentication or per-operator authorization yet: isolation is enforced
  in the matching engine, not at an access-control boundary.
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
